"""Build a source-and-model handoff from exact, verified local model snapshots.

Only declared runtime model files are included. Provider keys, project state,
vector databases and other cached models are excluded.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path

from package_release import ROOT, package, read_version


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def model_files(translation: Path, embedding: Path) -> list[tuple[Path, str, str]]:
    constants = {}
    for node in ast.parse((ROOT / 'litsearch/local_models.py').read_text(encoding='utf-8')).body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = node.value.value
    files = []
    for kind, parent, pins_file in [
        ('translation', translation / 'opus-mt-zh-en', 'translation_pins.json'),
        ('embedding', embedding / ('models--' + constants['EMBEDDING_REPOSITORY'].replace('/', '--')) / 'snapshots' / constants['EMBEDDING_REVISION'], 'embedding_pins.json'),
    ]:
        pins = json.loads((ROOT / 'litsearch' / pins_file).read_text(encoding='utf-8'))
        assert pins['revision'] == constants[kind.upper() + '_REVISION']
        for name, info in pins['files'].items():
            source = parent / name
            if not source.is_file() or source.stat().st_size != info['bytes'] or digest(source) != info['sha256']:
                raise SystemExit(f'Missing or corrupt {kind} file: {source}')
            target = ('LEExtractor/models/' + source.relative_to(translation).as_posix()) if kind == 'translation' else ('LEExtractor/models/embedding/' + source.relative_to(embedding).as_posix())
            files.append((source, target, info['sha256']))
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'deliverables')
    parser.add_argument('--translation-cache', type=Path, default=Path(os.environ.get('LEEXTRACTOR_MODEL_DIR', Path.home() / '.cache/leextractor')))
    parser.add_argument('--embedding-cache', type=Path, default=Path(os.environ.get('LEEXTRACTOR_EMBEDDING_CACHE', Path(tempfile.gettempdir()) / 'fastembed_cache')))
    args = parser.parse_args(argv)
    files = model_files(args.translation_cache, args.embedding_cache)
    source_summary = package(ROOT, args.output)
    version = read_version(ROOT / 'litsearch/version.py')
    destination = args.output / f'LEExtractor_v{version}_交接包_含模型.zip'
    with zipfile.ZipFile(source_summary['path']) as source, zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        manifest = source.read('MANIFEST.sha256').decode('utf-8')
        for entry in source.infolist():
            if entry.filename != 'MANIFEST.sha256':
                archive.writestr(entry.filename, source.read(entry))
        for path, target, checksum in files:
            archive.write(path, target)
            manifest += f'{checksum}  {target}\n'
        archive.writestr('MANIFEST.sha256', manifest)
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None, 'Archive failed CRC check'
    checksum = digest(destination)
    destination.with_suffix('.zip.sha256').write_text(f'{checksum}  {destination.name}\n', encoding='utf-8')
    summary = {'path': str(destination), 'version': version, 'bytes': destination.stat().st_size,
               'sha256': checksum, 'model_files': len(files), 'model_bytes': sum(p.stat().st_size for p, _, _ in files)}
    print(json.dumps(summary, ensure_ascii=True))


if __name__ == '__main__':
    main()
