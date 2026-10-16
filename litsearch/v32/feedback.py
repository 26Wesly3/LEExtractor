"""Feedback query update (doc 04, "反馈").

The user's text intent is never silently rewritten: every recalculation starts
from ``q_initial`` and the *complete current* judgement set (never from a
previous ``q_new``), the feedback term is the contract's weighted blend

    v = alpha*q_initial + beta*mean(core) + gamma*mean(relevant)
        - delta*mean(explicit_not_relevant)

with each per-paper vector unit-normalised first and an empty term counted as
zero, ``q_new = unit(v)``, and a final 0.80 cosine floor against ``q_initial``
reached by bisecting the interpolation proportion the contract's
``clamp_iterations`` times.

Every constant lives in ``contracts/defaults.json`` and is read through
:mod:`litsearch.v32.contracts`.  A plain exclusion is *not* a default negative
sample: only ``exclusion_reason == "not_relevant"`` contributes negative weight.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence

from . import contracts as C
from .types import ExclusionReason, JudgementValue

__all__ = ["query_vector", "feedback_scores"]

_EPS = 1e-12


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _feedback_config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """``contracts.ranking().feedback`` with the caller's overrides merged in."""
    base = dict(C.ranking()["feedback"])
    if not config or not isinstance(config, Mapping):
        return base
    inner: Any = config
    if isinstance(config.get("ranking"), Mapping):
        inner = config["ranking"]
    if isinstance(inner, Mapping) and isinstance(inner.get("feedback"), Mapping):
        inner = inner["feedback"]
    if not isinstance(inner, Mapping):
        return base
    for key, value in inner.items():
        if key in base:
            base[key] = value
    return base


def _coerce_vector(vector: Any) -> list[float] | None:
    """A finite float list, or ``None`` when the vector is unusable."""
    if vector is None or isinstance(vector, (str, bytes)):
        return None
    try:
        values = [float(x) for x in vector]
    except (TypeError, ValueError):
        return None
    if not values:
        return None
    if not all(math.isfinite(x) for x in values):
        return None
    return values


def _sanitise_vector(vector: Any) -> list[float]:
    """``q_initial`` as finite floats; a non-finite entry becomes 0.0.

    The initial intent must never carry ``NaN``/infinity into the update, so it
    is sanitised rather than rejected.  A non-iterable input yields ``[]``.
    """
    if vector is None or isinstance(vector, (str, bytes)):
        return []
    try:
        raw = list(vector)
    except TypeError:
        return []
    out: list[float] = []
    for item in raw:
        try:
            value = float(item)
        except (TypeError, ValueError):
            value = 0.0
        out.append(value if math.isfinite(value) else 0.0)
    return out


def _unit(vector: Sequence[float] | None) -> list[float] | None:
    """Unit-normalise, or ``None`` when the vector has no direction."""
    if vector is None:
        return None
    norm = math.sqrt(sum(x * x for x in vector))
    if not math.isfinite(norm) or norm <= _EPS:
        return None
    return [x / norm for x in vector]


def _cosine(left: Sequence[float], right: Sequence[float]) -> float | None:
    """Cosine similarity of two equal-length finite vectors, or ``None``."""
    if len(left) != len(right) or not left:
        return None
    norm_left = math.sqrt(sum(x * x for x in left))
    norm_right = math.sqrt(sum(x * x for x in right))
    if norm_left <= _EPS or norm_right <= _EPS:
        return None
    value = sum(x * y for x, y in zip(left, right)) / (norm_left * norm_right)
    if not math.isfinite(value):
        return None
    return max(-1.0, min(1.0, value))


def _record_fields(record: Any) -> tuple[str | None, str | None]:
    if record is None:
        return None, None
    if isinstance(record, str):
        return (record or None), None
    if isinstance(record, Mapping):
        value = record.get("value")
        reason = record.get("exclusion_reason")
    else:
        value = getattr(record, "value", None)
        reason = getattr(record, "exclusion_reason", None)
    return (
        str(value) if value not in (None, "") else None,
        str(reason) if reason not in (None, "") else None,
    )


def _record_paper_id(record: Any) -> str | None:
    if isinstance(record, Mapping):
        pid = record.get("paper_id") or record.get("id")
    else:
        pid = getattr(record, "paper_id", None)
    return str(pid) if pid not in (None, "") else None


def _judgement_groups(judgements: Any) -> tuple[list[str], list[str], list[str]]:
    """Split judgements into ``(core, relevant, explicit_not_relevant)`` paper ids."""
    core: list[str] = []
    relevant: list[str] = []
    negative: list[str] = []
    if not judgements:
        return core, relevant, negative

    def _place(pid: str, record: Any) -> None:
        value, reason = _record_fields(record)
        if value == str(JudgementValue.CORE):
            core.append(pid)
        elif value == str(JudgementValue.RELEVANT):
            relevant.append(pid)
        elif value == str(JudgementValue.EXCLUDED):
            # A plain exclusion is not a negative sample; only an explicit
            # "not_relevant" reason acts as negative feedback (doc 02/D03).
            if reason == str(ExclusionReason.NOT_RELEVANT):
                negative.append(pid)

    if isinstance(judgements, Mapping):
        for pid, record in judgements.items():
            _place(str(pid), record)
        return core, relevant, negative
    for record in judgements:
        pid = _record_paper_id(record)
        if pid is None:
            continue
        _place(pid, record)
    return core, relevant, negative


def _group_mean(
    paper_ids: Iterable[str],
    vectors: Mapping[str, Any],
    dimension: int,
) -> list[float] | None:
    """Mean of the unit-normalised per-paper vectors, or ``None`` when empty.

    Vectors that are not comparable with ``q_initial`` (wrong dimension or
    non-finite) are skipped rather than silently coerced; the caller still
    reports the update as unchanged when nothing is left.
    """
    accumulated: list[float] | None = None
    count = 0
    for pid in paper_ids:
        vector = _unit(_coerce_vector(vectors.get(pid)))
        if vector is None or len(vector) != dimension:
            continue
        if accumulated is None:
            accumulated = [0.0] * dimension
        for index in range(dimension):
            accumulated[index] += vector[index]
        count += 1
    if accumulated is None or count == 0:
        return None
    return [value / count for value in accumulated]


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------


def query_vector(
    q_initial: Sequence[float],
    judgements: Any,
    vectors: Mapping[str, Any],
    *,
    config: Mapping[str, Any] | None = None,
) -> list[float]:
    """The updated query representation ``q_new``.

    Returns ``q_initial`` unchanged when there is no usable judgement vector at
    all (the text intent is not rewritten) and when the blend has no direction.
    Otherwise the blend is unit-normalised and, if its cosine with ``q_initial``
    falls below the contract floor, the largest feedback proportion that keeps
    the floor is found by bisection and re-normalised.
    """
    cfg = _feedback_config(config)
    alpha = float(cfg["alpha"])
    beta = float(cfg["beta"])
    gamma = float(cfg["gamma"])
    delta = float(cfg["delta"])
    floor = float(cfg["min_anchor_cosine"])
    iterations = max(0, int(cfg["clamp_iterations"]))

    initial = _sanitise_vector(q_initial)
    if not initial:
        return []
    dimension = len(initial)
    base = [alpha * value for value in initial]

    paper_vectors = vectors if isinstance(vectors, Mapping) else {}
    core_ids, relevant_ids, negative_ids = _judgement_groups(judgements)
    mean_core = _group_mean(core_ids, paper_vectors, dimension)
    mean_relevant = _group_mean(relevant_ids, paper_vectors, dimension)
    mean_negative = _group_mean(negative_ids, paper_vectors, dimension)

    if mean_core is None and mean_relevant is None and mean_negative is None:
        # No valid judgement vector: the raw intent is returned untouched.
        return list(initial)

    blended = list(base)
    if mean_core is not None:
        for index in range(dimension):
            blended[index] += beta * mean_core[index]
    if mean_relevant is not None:
        for index in range(dimension):
            blended[index] += gamma * mean_relevant[index]
    if mean_negative is not None:
        for index in range(dimension):
            blended[index] -= delta * mean_negative[index]

    q_new = _unit(blended)
    if q_new is None:
        return list(initial)

    initial_unit = _unit(initial)
    if initial_unit is None:
        return q_new

    current = _cosine(q_new, initial)
    if current is None or current >= floor:
        return q_new

    # Bisect the feedback proportion: t = 1 is pure q_initial (no feedback),
    # t = 0 is pure q_new (full feedback).  Keep the smallest t that still
    # honours the cosine floor -- i.e. the largest admissible feedback share.
    low, high = 0.0, 1.0
    for _ in range(iterations):
        middle = (low + high) / 2.0
        candidate = _unit(
            [
                (1.0 - middle) * q_new[index] + middle * initial_unit[index]
                for index in range(dimension)
            ]
        )
        if candidate is None:
            low = middle
            continue
        candidate_cosine = _cosine(candidate, initial)
        if candidate_cosine is not None and candidate_cosine >= floor:
            high = middle
        else:
            low = middle

    clamped = _unit(
        [
            (1.0 - high) * q_new[index] + high * initial_unit[index]
            for index in range(dimension)
        ]
    )
    if clamped is None:
        return initial_unit

    # Guard the last bit of floating-point drift at the boundary.
    guard = 0
    while guard < 1000:
        cosine = _cosine(clamped, initial)
        if cosine is not None and cosine >= floor - 1e-12:
            break
        if high >= 1.0:
            break
        high = min(1.0, high + 1e-6)
        nxt = _unit(
            [
                (1.0 - high) * q_new[index] + high * initial_unit[index]
                for index in range(dimension)
            ]
        )
        if nxt is None:
            break
        clamped = nxt
        guard += 1
    return clamped


def feedback_scores(q_new: Sequence[float], vectors: Mapping[str, Any]) -> dict[str, float]:
    """Cosine of each paper vector against ``q_new``.

    A paper whose vector is missing, non-finite, zero-length or of a different
    dimension is omitted: its similarity is genuinely unknown, and an unknown
    number is never reported as ``0``.
    """
    query = _unit(_coerce_vector(q_new))
    if query is None or not isinstance(vectors, Mapping):
        return {}
    scores: dict[str, float] = {}
    for key in sorted(vectors, key=str):
        vector = _unit(_coerce_vector(vectors[key]))
        if vector is None or len(vector) != len(query):
            continue
        cosine = _cosine(query, vector)
        if cosine is None:
            continue
        scores[str(key)] = cosine
    return scores
