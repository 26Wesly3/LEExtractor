"""Portable model setup and exact-snapshot handoff integrity."""

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import package_handoff  # noqa: E402
import prepare_models  # noqa: E402


@pytest.mark.parametrize('custom', [False, True])
def test_prepare_selects_bundled_models_and_respects_custom_paths(tmp_path, monkeypatch, custom):
    from litsearch.local_models import LocalEmbedding, LocalTranslator

    monkeypatch.setattr(prepare_models, 'ROOT', tmp_path)
    bundled = tmp_path / 'models'
    (bundled / 'opus-mt-zh-en').mkdir(parents=True)
    (bundled / 'opus-mt-zh-en/config.json').write_text('{}')
    (bundled / 'embedding').mkdir()
    for key in ['LEEXTRACTOR_MODEL_DIR', 'LEEXTRACTOR_EMBEDDING_CACHE']:
        monkeypatch.delenv(key, raising=False)
        if custom:
            monkeypatch.setenv(key, 'chosen-by-user')
    events = []
    monkeypatch.setattr(LocalTranslator, '_load', lambda self: events.append('translation'))
    monkeypatch.setattr(LocalEmbedding, 'ensure_ready', lambda self: events.append('embedding'))
    assert prepare_models.prepare()
    assert events == ['translation', 'embedding']
    assert prepare_models.os.environ['LEEXTRACTOR_MODEL_DIR'] == ('chosen-by-user' if custom else str(bundled))
    assert prepare_models.os.environ['LEEXTRACTOR_EMBEDDING_CACHE'] == ('chosen-by-user' if custom else str(bundled / 'embedding'))


def test_failed_translation_still_prepares_embedding_without_printing_sensitive_exception(monkeypatch, capsys):
    from litsearch.local_models import LocalEmbedding, LocalTranslator

    def fail(self):
        raise RuntimeError('private-provider-token')

    events = []
    monkeypatch.setattr(LocalTranslator, '_load', fail)
    monkeypatch.setattr(LocalEmbedding, 'ensure_ready', lambda self: events.append('embedding'))
    assert not prepare_models.prepare()
    assert events == ['embedding']
    assert 'private-provider-token' not in capsys.readouterr().out


def fixture_models(tmp_path, monkeypatch):
    project, translation, embedding = [tmp_path / name for name in ['project', 'translation', 'embedding']]
    (project / 'litsearch').mkdir(parents=True)
    (project / 'litsearch/version.py').write_text('APP_NAME = "LEExtractor"\n__version__ = "0.9.11"\n')
    revision = 'a' * 40
    (project / 'litsearch/local_models.py').write_text(f'EMBEDDING_REPOSITORY = "test/model"\nEMBEDDING_REVISION = "{revision}"\nTRANSLATION_REVISION = "{revision}"\n')
    for kind, parent, name in [('translation', translation / 'opus-mt-zh-en', 'config.json'),
                                ('embedding', embedding / 'models--test--model/snapshots' / revision, 'model_optimized.onnx')]:
        parent.mkdir(parents=True)
        (parent / name).write_bytes(kind.encode())
        (parent / 'private-cache.db').write_bytes(b'not-for-handoff')
        pins = {'revision': revision, 'files': {name: {'bytes': len(kind), 'sha256': hashlib.sha256(kind.encode()).hexdigest()}}}
        (project / f'litsearch/{kind}_pins.json').write_text(json.dumps(pins))
    (project / '.env.local').write_text('PRIVATE_KEY=not-for-handoff')
    monkeypatch.setattr(package_handoff, 'ROOT', project)
    return project, translation, embedding


def test_handoff_is_self_contained_and_manifest_covers_only_declared_models(tmp_path, monkeypatch):
    _, translation, embedding = fixture_models(tmp_path, monkeypatch)
    output = tmp_path / 'output'
    package_handoff.main(['--translation-cache', str(translation), '--embedding-cache', str(embedding), '--output', str(output)])
    path = next(output.glob('*_含模型.zip'))
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        assert not any('private' in name or '.env.local' in name for name in names)
        assert len([name for name in names if '/models/' in name]) == 2
        for line in archive.read('MANIFEST.sha256').decode().splitlines():
            checksum, name = line.split('  ', 1)
            assert hashlib.sha256(archive.read(name)).hexdigest() == checksum
    assert path.with_suffix('.zip.sha256').read_text(encoding='utf-8').split()[0] == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize('missing', [False, True])
def test_handoff_rejects_missing_or_corrupt_model_before_creating_archive(tmp_path, monkeypatch, missing):
    _, translation, embedding = fixture_models(tmp_path, monkeypatch)
    model = translation / 'opus-mt-zh-en/config.json'
    if missing:
        model.unlink()
    else:
        model.write_bytes(b'corrupted!!')
    with pytest.raises(SystemExit, match='Missing or corrupt translation file'):
        package_handoff.main(['--translation-cache', str(translation), '--embedding-cache', str(embedding), '--output', str(tmp_path / 'output')])
    assert not list((tmp_path / 'output').glob('*.zip'))
