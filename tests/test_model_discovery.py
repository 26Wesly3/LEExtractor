"""Model discovery: reuse the models already on this machine before downloading.

v0.9.11 只让启动器 (``scripts/prepare_models.py``) 设置 ``LEEXTRACTOR_MODEL_DIR`` /
``LEEXTRACTOR_EMBEDDING_CACHE``，直接起后端时那段不会执行；Embedding 又只探测单个
``tempfile.gettempdir()/fastembed_cache``，而该目录并不总是 ``%TEMP%``。下面的用例固定
「先找本地、再下载」这条链，以及损坏副本必须被跳过。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import numpy as np
import pytest

from litsearch import local_models


def write_pinned(directory: Path, pins: dict) -> None:
    for name, info in pins["files"].items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = b"model-bytes:" + name.encode()
        path.write_bytes(payload)
        info["sha256"] = hashlib.sha256(payload).hexdigest()
        info["bytes"] = len(payload)


def test_translation_is_found_in_a_handoff_package_layout(tmp_path):
    pins = {"files": {"config.json": {}, "onnx/encoder_model_quantized.onnx": {}}}
    write_pinned(tmp_path / "models" / "opus-mt-zh-en", pins)
    assert local_models.find_translation_dir(pins, roots=[tmp_path / "models"]) == tmp_path / "models" / "opus-mt-zh-en"


def test_translation_is_found_in_a_huggingface_hub_layout(tmp_path):
    pins = {"files": {"config.json": {}}}
    snapshot = tmp_path / "hub" / "models--Xenova--opus-mt-zh-en" / "snapshots" / local_models.TRANSLATION_REVISION
    write_pinned(snapshot, pins)
    assert local_models.find_translation_dir(pins, roots=[tmp_path / "hub"]) == snapshot


def test_a_corrupted_copy_is_skipped_and_a_later_root_wins(tmp_path):
    pins = {"files": {"config.json": {}}}
    write_pinned(tmp_path / "first" / "opus-mt-zh-en", pins)
    write_pinned(tmp_path / "second" / "opus-mt-zh-en", pins)
    (tmp_path / "first" / "opus-mt-zh-en" / "config.json").write_bytes(b"corrupted")
    assert local_models.find_translation_dir(pins, roots=[tmp_path / "first", tmp_path / "second"]) == tmp_path / "second" / "opus-mt-zh-en"


def test_translation_lookup_returns_nothing_when_no_root_has_the_model(tmp_path):
    pins = {"files": {"config.json": {}}}
    (tmp_path / "empty" / "opus-mt-zh-en").mkdir(parents=True)
    assert local_models.find_translation_dir(pins, roots=[tmp_path / "empty"]) is None


def test_embedding_snapshot_is_found_locally_without_network(tmp_path):
    cache = tmp_path / "fastembed_cache"
    snapshot = cache / "models--qdrant--paraphrase-multilingual-MiniLM-L12-v2-onnx-Q" / "snapshots" / local_models.EMBEDDING_REVISION
    snapshot.mkdir(parents=True)
    (snapshot / "model_optimized.onnx").write_bytes(b"weights")
    seen = []

    def download(**options):
        seen.append(options)
        return str(snapshot)

    assert local_models.find_embedding_snapshot(download, cache_dirs=[cache]) == (str(snapshot), str(cache))
    assert seen and seen[0]["local_files_only"] is True
    assert Path(seen[0]["cache_dir"]) == cache
    assert seen[0]["revision"] == local_models.EMBEDDING_REVISION


def test_embedding_snapshot_without_weights_is_rejected(tmp_path):
    cache = tmp_path / "cache"
    snapshot = cache / "models--qdrant--paraphrase-multilingual-MiniLM-L12-v2-onnx-Q" / "snapshots" / local_models.EMBEDDING_REVISION
    snapshot.mkdir(parents=True)

    def download(**options):
        return str(snapshot)

    assert local_models.find_embedding_snapshot(download, cache_dirs=[cache]) is None


def test_embedding_candidates_include_the_package_subfolder(tmp_path):
    cache = tmp_path / "models"
    (cache / "embedding").mkdir(parents=True)
    assert tmp_path / "models" / "embedding" in local_models.embedding_candidates([cache])


def test_search_roots_put_the_explicit_override_first_and_stay_unique(tmp_path, monkeypatch):
    explicit = tmp_path / "explicit"
    project = tmp_path / "project"
    explicit.mkdir()
    project.mkdir()
    monkeypatch.setenv("LEEXTRACTOR_MODEL_DIR", str(explicit))
    monkeypatch.setattr(local_models, "project_model_dir", lambda: project)
    roots = local_models.model_search_roots()
    assert roots[0] == explicit.resolve()
    assert project.resolve() in roots
    assert len(roots) == len(set(roots))


def test_a_malformed_proxy_is_retried_without_it_and_restored(monkeypatch):
    calls = []

    def fetch(**options):
        calls.append(dict(options))
        if len(calls) == 1:
            raise RuntimeError("Invalid port: ':1]'")
        return "downloaded"

    monkeypatch.setenv("HTTP_PROXY", "http://[::1]")
    assert local_models.download_through_proxy_fallback(fetch, repo_id="x") == "downloaded"
    assert len(calls) == 2
    assert os.environ.get("HTTP_PROXY") == "http://[::1]"


def test_a_real_download_error_is_not_retried(monkeypatch):
    calls = []

    def fetch(**options):
        calls.append(options)
        raise RuntimeError("404 Client Error")

    with pytest.raises(RuntimeError):
        local_models.download_through_proxy_fallback(fetch, repo_id="x")
    assert len(calls) == 1


def test_vector_cache_falls_back_to_a_writable_location(tmp_path, monkeypatch):
    blocked = tmp_path / "blocked-file"
    blocked.write_text("not a directory", encoding="utf-8")
    writable = tmp_path / "writable" / "embeddings.db"
    monkeypatch.setattr(local_models, "vector_cache_candidates", lambda: [blocked / "embeddings.db", writable])
    assert local_models.vector_cache_path() == writable


def test_a_readonly_cache_never_raises_and_returns_vectors(tmp_path, monkeypatch):
    """The cache is an optimisation: a read-only/corrupt store must not fail ranking."""
    broken = tmp_path / "embeddings.db"
    broken.write_bytes(b"definitely not a sqlite database")

    class StubModel:
        def embed(self, texts, batch_size=16):
            return [np.array([0.5] * 4, dtype=np.float32) for _ in texts]

    embedding = local_models.LocalEmbedding()
    embedding.model = StubModel()
    embedding._cache_db = broken
    vectors = embedding.vectors(["alpha", "beta"])
    assert vectors.shape == (2, 4)
    assert np.allclose(vectors[0], 0.5)


def test_generated_vectors_are_reused_from_the_cache(tmp_path, monkeypatch):
    calls = []

    class CountingModel:
        def embed(self, texts, batch_size=16):
            calls.append(list(texts))
            return [np.array([1.0, 0.0], dtype=np.float32) for _ in texts]

    embedding = local_models.LocalEmbedding()
    embedding.model = CountingModel()
    embedding._cache_db = tmp_path / "embeddings.db"
    embedding.vectors(["alpha"])
    embedding._cache_db = tmp_path / "embeddings.db"
    embedding.vectors(["alpha", "beta"])
    assert calls == [["alpha"], ["beta"]]
