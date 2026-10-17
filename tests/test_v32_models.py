"""Local model discovery: find what is already on disk, never download.

A pinned model that is already present must be reported as available. The failure
this replaces: FastEmbed's cache lookup only knew a handful of default locations,
so a complete 224 MB copy sitting in a sibling ``embedding_cache`` directory was
reported as "missing" and the product silently fell back to lexical-only ranking
while telling the user a download was needed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from litsearch.v32 import models


def make_embedding_snapshot(root: Path, *, weights_mb: int = 4, companions: bool = True) -> Path:
    snapshot = root / "models--qdrant--paraphrase-multilingual-MiniLM-L12-v2-onnx-Q" / "snapshots" / "rev1"
    snapshot.mkdir(parents=True)
    (snapshot / "model_optimized.onnx").write_bytes(b"\0" * (weights_mb * 1024 * 1024))
    if companions:
        for name in ("config.json", "tokenizer.json", "tokenizer_config.json"):
            (snapshot / name).write_text("{}", encoding="utf-8")
    return snapshot


def make_translation(root: Path, *, complete: bool = True) -> Path:
    onnx = root / "opus-mt-zh-en" / "onnx"
    onnx.mkdir(parents=True)
    (onnx / "encoder_model_quantized.onnx").write_bytes(b"\0" * (2 * 1024 * 1024))
    if complete:
        (onnx / "decoder_model_quantized.onnx").write_bytes(b"\0" * (3 * 1024 * 1024))
    return onnx


def test_a_complete_snapshot_is_usable(tmp_path):
    snapshot = make_embedding_snapshot(tmp_path)
    report = models.discover_local_models([tmp_path])
    assert report.semantic_ready is True
    best = report.best("embedding")
    assert best is not None
    assert Path(best.path) == snapshot
    assert best.usable is True
    assert best.missing == []
    assert best.size_mb >= 4


def test_a_snapshot_without_its_tokenizer_is_not_usable(tmp_path):
    make_embedding_snapshot(tmp_path, companions=False)
    report = models.discover_local_models([tmp_path])
    assert report.semantic_ready is False
    assert report.embedding
    assert "tokenizer.json" in report.embedding[0].missing
    assert report.embedding[0].usable is False


def test_a_truncated_download_is_not_mistaken_for_a_model(tmp_path):
    """A 0-byte onnx is what a failed download leaves behind."""
    snapshot = make_embedding_snapshot(tmp_path, weights_mb=0)
    # write_bytes(b"") gives a 0-byte file; make sure it is genuinely empty.
    (snapshot / "model_optimized.onnx").write_bytes(b"")
    report = models.discover_local_models([tmp_path])
    assert report.semantic_ready is False
    assert any("truncated" in item for item in report.embedding[0].missing)


def test_a_complete_translation_model_is_reported(tmp_path):
    make_translation(tmp_path)
    report = models.discover_local_models([tmp_path])
    assert report.translation_ready is True
    assert report.best("translation").name == "opus-mt-zh-en"


def test_a_half_present_translation_model_is_not_usable(tmp_path):
    make_translation(tmp_path, complete=False)
    report = models.discover_local_models([tmp_path])
    assert report.translation_ready is False
    assert "decoder_model_quantized.onnx" in report.translation[0].missing


def test_the_extra_roots_environment_variable_is_honoured(tmp_path, monkeypatch):
    make_embedding_snapshot(tmp_path)
    monkeypatch.setenv("LEEXTRACTOR_EXTRA_MODEL_DIRS", str(tmp_path))
    roots = models.candidate_roots()
    assert tmp_path in roots
    assert models.discover_local_models().semantic_ready is True


def test_the_explicit_model_dir_wins_priority(tmp_path, monkeypatch):
    make_translation(tmp_path)
    monkeypatch.setenv("LEEXTRACTOR_MODEL_DIR", str(tmp_path))
    roots = models.candidate_roots()
    assert roots[0] == tmp_path


def test_discovery_never_downloads_anything():
    """Discovery is a read-only scan; it must not reach for the network.

    Checked as imports and calls, not as bare words: the module's own docstring
    legitimately names the cache directories it looks in.
    """
    source = Path(models.__file__).read_text(encoding="utf-8")
    import ast

    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    for banned in ("requests", "urllib", "http", "socket", "fastembed", "huggingface_hub"):
        assert banned not in imported, f"discovery must not import {banned}"
    for call in ("urlopen", "hf_hub_download", "TextEmbedding", "snapshot_download"):
        assert f"{call}(" not in source, f"discovery must not call {call}()"


def test_discovery_is_bounded_and_skips_dependency_trees(tmp_path):
    """A bounded scan is what keeps this safe to call when the app opens."""
    deep = tmp_path / "node_modules" / "pkg" / "models--x" / "snapshots" / "r"
    deep.mkdir(parents=True)
    (deep / "model_optimized.onnx").write_bytes(b"\0" * (2 * 1024 * 1024))
    found = models.discovered_extra_dirs(around=tmp_path)
    assert not any("node_modules" in str(path) for path in found)


def test_the_report_serialises_to_json_without_a_path_object(tmp_path):
    make_embedding_snapshot(tmp_path)
    make_translation(tmp_path)
    payload = models.discover_local_models([tmp_path]).to_dict()
    text = json.dumps(payload, ensure_ascii=False)
    assert '"semantic_ready": true' in text
    assert '"translation_ready": true' in text
    assert set(payload) >= {"checked_roots", "translation", "embedding", "best_embedding", "best_translation"}


def test_the_real_machine_report_is_consistent():
    """Smoke: whatever this machine has, the report must be self-consistent."""
    report = models.discover_local_models()
    for findings, ready in ((report.translation, report.translation_ready),
                            (report.embedding, report.semantic_ready)):
        if ready:
            assert any(item.usable for item in findings)
            for item in findings:
                if item.usable:
                    assert not item.missing
                    assert item.size_mb >= 1
        else:
            assert not any(item.usable for item in findings)
