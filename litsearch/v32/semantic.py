"""Semantic vectors for the v3.2 ranking, from the pinned local model only (doc 04, 12, 15).

The ranking already has seams for vectors (``V32State.vectors`` and
``V32State.query_vector``) but nothing filled them. This module fills them when the
pinned embedding weights are already on disk. It never downloads; when the weights
are missing or the model fails, every function returns ``None`` and the caller keeps
the lexical signal, with the reason recorded.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from typing import Any

_LOCK = threading.Lock()
_EMBEDDER: Any = None
_UNAVAILABLE_REASON: str | None = None
_TEXT_CHARS = 1500


def model_reason() -> str | None:
    """None when the pinned model is usable, otherwise a short reason code."""
    global _UNAVAILABLE_REASON
    if _EMBEDDER is not None:
        return None
    if _UNAVAILABLE_REASON is not None:
        return _UNAVAILABLE_REASON
    try:
        from litsearch.local_models import first_embedding_snapshot_dir
    except ImportError:
        _UNAVAILABLE_REASON = "fastembed_not_installed"
        return _UNAVAILABLE_REASON
    if first_embedding_snapshot_dir() is None:
        _UNAVAILABLE_REASON = "model_not_on_disk"
        return _UNAVAILABLE_REASON
    return None


def _embedder():
    global _EMBEDDER
    with _LOCK:
        if _EMBEDDER is None:
            from litsearch.local_models import LocalEmbedding

            _EMBEDDER = LocalEmbedding()
    return _EMBEDDER


def paper_text(record: Mapping[str, Any]) -> str:
    title = str(record.get("title") or "").strip()
    abstract = str(record.get("abstract") or "").strip()[:_TEXT_CHARS]
    return f"{title}. {abstract}" if abstract else title


def _unit(matrix):
    import numpy as np

    array = np.asarray(matrix, dtype=float)
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return array / norms


def embed(texts: Sequence[str]):
    """Unit vectors for ``texts``, or ``None`` when the model is unavailable or fails."""
    if not texts or model_reason() is not None:
        return None
    try:
        return _unit(_embedder().vectors(list(texts)))
    except Exception:  # model load/inference problems degrade to lexical, never crash a ranking
        return None


def paper_and_query_vectors(query: str, records: Sequence[Mapping[str, Any]]):
    """``(query_vector, {paper_id: vector})`` or ``None``.

    The query and the papers are embedded in one batch so both live in the same space.
    """
    query = (query or "").strip()
    rows = [r for r in records if r.get("paper_id")]
    if not query or not rows:
        return None
    matrix = embed([query] + [paper_text(r) for r in rows])
    if matrix is None:
        return None
    query_vector = matrix[0]
    vectors = {str(r["paper_id"]): matrix[i + 1] for i, r in enumerate(rows)}
    return query_vector, vectors


def semantic_topic_scores(query_vector, vectors: Mapping[str, Any]) -> dict[str, float]:
    """Cosine to the intent, clipped to [0, 1]; a paper with no vector gets no score."""
    scores: dict[str, float] = {}
    for paper_id, vector in vectors.items():
        value = float(sum(float(a) * float(b) for a, b in zip(query_vector, vector, strict=True)))
        scores[paper_id] = min(1.0, max(0.0, value))
    return scores
