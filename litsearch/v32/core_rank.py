"""CoreRank: the deterministic local RRF ranking engine (doc 04).

The module implements exactly the rules of ``docs/v3.2/04_ALGORITHM_DESIGN.md``
and happens to be judged against ``fixtures/ranking_cases.json`` (AC-06), so
the algorithm is written for reproducibility rather than for elegance:

* every constant (``k``, the five weights, the topic-gate fraction, the
  PageRank parameters, the graph edge floor, the starter budget, the MMR
  lambda) is read from :mod:`litsearch.v32.contracts` -- nothing is re-typed as
  a Python literal;
* per signal, ranks are computed over the *known* candidates only (average
  ranks for ties) and then mapped onto the ``N`` non-excluded candidate scale;
  a missing value gets the neutral midpoint rank, never ``0`` and never last
  place;
* a signal with fewer than two distinct valid values is inactive for the whole
  ranking;
* ``core_score_raw`` is a cumulative sum in contract signal order so the same
  inputs produce the same float;
* ties are compared after :func:`litsearch.v32.types.round_tie`, so the order
  can never depend on the arrival order of the candidates.

No number that escapes this module is ``NaN`` or infinite: an unknown value is
``None``, which is JSON ``null``, and never ``0``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from . import contracts as C
from .types import (
    Eligibility,
    JudgementValue,
    Role,
    RoleOrigin,
    Signal,
    round_tie,
    safe_float,
)

__all__ = [
    "SIGNAL_ORDER",
    "CandidateSignals",
    "Breakdown",
    "RankedPaper",
    "RankingResult",
    "core_rank",
    "starter_list",
    "age_adjusted_impact",
    "pagerank_signal",
]

#: The contract order of the RRF signals (``defaults.json`` -> ranking.weights).
#: The cumulative fusion sum must follow it for the float result to be stable.
SIGNAL_ORDER: tuple[str, ...] = tuple(
    str(signal)
    for signal in (Signal.TOPIC, Signal.IMPACT, Signal.GRAPH, Signal.SEED, Signal.FEEDBACK)
)

_TOPIC = str(Signal.TOPIC)

_EPS = 1e-12

#: A signal that is not backed by at least two distinct valid values is turned
#: off for the whole ranking (doc 04: "某信号没有至少 2 个不同的有效值时整项停用").
_MIN_DISTINCT = 2

#: The recommendation roles that get one quota slot each when evidence exists,
#: in the contract's priority order (doc 04).
_QUOTA_ROLES: tuple[str, ...] = (
    str(Role.REVIEW),
    str(Role.FOUNDATIONAL),
    str(Role.REPRESENTATIVE),
    str(Role.FRONTIER),
)

_TOKEN_RE = re.compile(r"[0-9a-z]+")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")


# --------------------------------------------------------------------------
# data transfer objects
# --------------------------------------------------------------------------


@dataclass
class CandidateSignals:
    """One candidate's already-computed signal values.

    A value of ``None`` means "genuinely unknown" and is ranked as the neutral
    midpoint; ``0`` is a real value when the producing signal defines it (a
    zero lexical topic score, a paper with no citation yet).  ``excluded`` and
    ``user_anchor`` carry the judgement state the ranking must honour without
    re-reading the judgement store.
    """

    paper_id: str
    topic: float | None = None
    impact: float | None = None
    graph: float | None = None
    seed: float | None = None
    feedback: float | None = None
    excluded: bool = False
    user_anchor: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "topic": _json_number(self.topic),
            "impact": _json_number(self.impact),
            "graph": _json_number(self.graph),
            "seed": _json_number(self.seed),
            "feedback": _json_number(self.feedback),
            "excluded": bool(self.excluded),
            "user_anchor": bool(self.user_anchor),
        }


@dataclass
class Breakdown:
    """Per-signal detail of one ranked paper (``$defs/Breakdown``)."""

    signal: str
    raw_value: float | None
    known_count: int
    available: bool
    rank: float | None
    weight: float
    contribution: float | None
    missing_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal": self.signal,
            "raw_value": _json_number(self.raw_value),
            "known_count": int(self.known_count),
            "available": bool(self.available),
            "rank": _json_number(self.rank),
            "weight": float(self.weight),
            "contribution": _json_number(self.contribution),
            "missing_reason": self.missing_reason,
        }


@dataclass
class RankedPaper:
    """One row of a ranking snapshot (``$defs/RankedPaper``)."""

    paper_id: str
    rank: int
    core_score_raw: float | None
    core_score: float | None
    core_role: str
    role_origin: str
    eligibility: str
    breakdown: list[Breakdown] = field(default_factory=list)
    recommendation_evidence: list[dict[str, Any]] = field(default_factory=list)
    #: Contract-required, but not produced by CoreRank itself (the enhancement
    #: stage owns the relevance share).  Kept last so the documented positional
    #: construction stays valid.
    relevance_score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "rank": int(self.rank),
            "core_score_raw": _json_number(self.core_score_raw),
            "core_score": _json_number(self.core_score),
            "relevance_score": _json_number(self.relevance_score),
            "core_role": self.core_role,
            "role_origin": self.role_origin,
            "eligibility": self.eligibility,
            "breakdown": [entry.to_dict() for entry in self.breakdown],
            "recommendation_evidence": [dict(entry) for entry in self.recommendation_evidence],
        }


@dataclass
class RankingResult:
    """The local ranking snapshot the API layer serialises."""

    items: list[RankedPaper]
    recommended_paper_ids: list[str]
    starter_paper_ids: list[str]
    coverage: dict[str, Any]
    snapshot_fields: dict[str, Any]

    @property
    def order(self) -> list[str]:
        """The paper ids in final local order."""
        return [item.paper_id for item in self.items]

    def by_id(self, paper_id: str) -> RankedPaper | None:
        for item in self.items:
            if item.paper_id == paper_id:
                return item
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [item.to_dict() for item in self.items],
            "recommended_paper_ids": list(self.recommended_paper_ids),
            "starter_paper_ids": list(self.starter_paper_ids),
            "coverage": dict(self.coverage),
            "snapshot_fields": dict(self.snapshot_fields),
        }


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


def _json_number(value: Any) -> float | None:
    """A finite float or ``None``; never NaN, never infinity."""
    return safe_float(value)


def _ceil_eps(value: float) -> int:
    """``ceil`` that is immune to the binary representation of ``0.6 * N``."""
    if value <= 0:
        return 0
    return int(math.ceil(value - 1e-9))


def _ranking_config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """``contracts.ranking()`` with the caller's explicit overrides merged in.

    ``config`` may be the ranking section itself, a whole defaults document, or
    ``{"ranking": {...}}``; only keys the contract declares are accepted, so a
    typo cannot silently introduce an un-contracted parameter.
    """
    base = C.ranking()
    if not config or not isinstance(config, Mapping):
        return base
    inner = config.get("ranking") if isinstance(config.get("ranking"), Mapping) else config
    if not isinstance(inner, Mapping):
        return base
    for key, value in inner.items():
        if key not in base:
            continue
        if isinstance(value, Mapping) and isinstance(base.get(key), Mapping):
            merged = dict(base[key])
            merged.update(value)
            base[key] = merged
        else:
            base[key] = value
    return base


def _record_fields(record: Any) -> tuple[str | None, str | None]:
    """``(value, exclusion_reason)`` from a judgement record in any shape."""
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


def _judgement_map(judgements: Any) -> dict[str, tuple[str | None, str | None]]:
    """Normalise a judgement mapping or sequence to ``{paper_id: (value, reason)}``."""
    out: dict[str, tuple[str | None, str | None]] = {}
    if not judgements:
        return out
    if isinstance(judgements, Mapping):
        for pid, record in judgements.items():
            out[str(pid)] = _record_fields(record)
        return out
    for record in judgements:
        pid = _record_paper_id(record)
        if pid is None:
            continue
        out[pid] = _record_fields(record)
    return out


def _normalise_ids(values: Any) -> set[str]:
    """Paper ids out of a mixed iterable of ids, mappings or objects."""
    out: set[str] = set()
    if not values:
        return out
    if isinstance(values, str):
        return {values}
    for item in values:
        if item is None:
            continue
        if isinstance(item, str):
            out.add(item)
        elif isinstance(item, Mapping):
            pid = item.get("paper_id") or item.get("seed_paper_id") or item.get("id")
            if pid:
                out.add(str(pid))
        elif isinstance(item, (int, float)):
            continue
        else:
            pid = getattr(item, "paper_id", None)
            if pid:
                out.add(str(pid))
    return out


def _average_ranks(known: Sequence[tuple[str, float]]) -> dict[str, float]:
    """1-based average ranks over the known values, descending."""
    ordered = sorted(
        ((str(pid), float(value)) for pid, value in known),
        key=lambda pair: (-round_tie(pair[1]), pair[0]),
    )
    ranks: dict[str, float] = {}
    index = 0
    while index < len(ordered):
        end = index
        value_key = round_tie(ordered[index][1])
        while end + 1 < len(ordered) and round_tie(ordered[end + 1][1]) == value_key:
            end += 1
        average = ((index + 1) + (end + 1)) / 2.0
        for position in range(index, end + 1):
            ranks[ordered[position][0]] = average
        index = end + 1
    return ranks


def _signal_ranks(known: Sequence[tuple[str, float]], *, scale_to: int) -> dict[str, float]:
    """Map average ranks over the known candidates onto the ``N`` candidate scale."""
    known_count = len(known)
    if known_count <= 0:
        return {}
    average = _average_ranks(known)
    if known_count > 1:
        span = (scale_to - 1) / (known_count - 1)
        return {pid: 1.0 + (rank - 1.0) * span for pid, rank in average.items()}
    neutral = (scale_to + 1) / 2.0
    return {pid: neutral for pid in average}


# --------------------------------------------------------------------------
# age adjusted impact
# --------------------------------------------------------------------------


def age_adjusted_impact(citation_count: Any, year: Any, as_of_year: Any) -> float | None:
    """``log1p(C) / sqrt(max(1, as_of_year - Y + 1))`` or ``None``.

    A missing citation count is *not* zero, a missing or future year disables
    the signal, and an unknown input returns ``None`` rather than a fabricated
    number.  The result is the "年龄调整后的引用影响", never a citation velocity.
    """
    count = safe_float(citation_count)
    if count is None or count < 0:
        return None
    for raw in (year, as_of_year):
        if raw is None or isinstance(raw, bool):
            return None
    try:
        published = int(year)
        reference = int(as_of_year)
    except (TypeError, ValueError):
        return None
    if published > reference:
        return None
    age = max(1, reference - published + 1)
    value = math.log1p(count) / math.sqrt(age)
    return value if math.isfinite(value) else None


# --------------------------------------------------------------------------
# graph signal (PageRank over observed citation edges only)
# --------------------------------------------------------------------------

_CITATION_RELATIONS = frozenset({"citation"})


def _iter_edges(edges: Iterable[Any]) -> list[tuple[str, str, str | None, bool | None]]:
    """``(citing, cited, relation, directed)`` for every usable edge record."""
    out: list[tuple[str, str, str | None, bool | None]] = []
    for edge in edges or ():
        source = target = relation = None
        directed: bool | None = None
        if isinstance(edge, Mapping):
            source = (
                edge.get("from_paper_id")
                or edge.get("source")
                or edge.get("src")
                or edge.get("citing")
                or edge.get("from")
            )
            target = (
                edge.get("to_paper_id")
                or edge.get("target")
                or edge.get("dst")
                or edge.get("cited")
                or edge.get("to")
            )
            relation = (
                edge.get("relation")
                or edge.get("kind")
                or edge.get("edge_type")
                or edge.get("type")
            )
            if "directed" in edge:
                directed = bool(edge.get("directed"))
        elif isinstance(edge, (tuple, list)):
            if len(edge) >= 2:
                source, target = edge[0], edge[1]
            if len(edge) >= 3:
                relation = edge[2]
            if len(edge) >= 4:
                directed = bool(edge[3])
        else:
            source = getattr(edge, "from_paper_id", None) or getattr(edge, "source", None)
            target = getattr(edge, "to_paper_id", None) or getattr(edge, "target", None)
            relation = getattr(edge, "relation", None) or getattr(edge, "kind", None)
            if hasattr(edge, "directed"):
                directed = bool(getattr(edge, "directed"))
        if source in (None, "") or target in (None, ""):
            continue
        out.append(
            (
                str(source),
                str(target),
                str(relation) if relation not in (None, "") else None,
                directed,
            )
        )
    return out


def _pagerank(
    ids: Sequence[str],
    adjacency: Mapping[str, Sequence[str]],
    alpha: float,
    max_iter: int,
    tol: float,
) -> dict[str, float] | None:
    """PageRank; ``None`` when the iteration does not converge or goes non-finite."""
    count = len(ids)
    if count == 0:
        return {}
    out_degree = {pid: len(adjacency.get(pid, ())) for pid in ids}
    dangling = [pid for pid in ids if out_degree[pid] == 0]
    current = {pid: 1.0 / count for pid in ids}
    for _ in range(max(0, max_iter)):
        nxt = {pid: (1.0 - alpha) / count for pid in ids}
        if dangling:
            dangling_mass = 0.0
            for pid in dangling:
                dangling_mass += current[pid]
            share_all = alpha * dangling_mass / count
            for pid in ids:
                nxt[pid] += share_all
        for pid in ids:
            degree = out_degree[pid]
            if degree == 0:
                continue
            share = alpha * current[pid] / degree
            for target in adjacency.get(pid, ()):
                nxt[target] += share
        if not all(math.isfinite(value) for value in nxt.values()):
            return None
        diff = max(abs(nxt[pid] - current[pid]) for pid in ids)
        current = nxt
        if diff < tol:
            return current
    return None


def pagerank_signal(
    edges: Iterable[Any], paper_ids: Iterable[str], *, config: Mapping[str, Any] | None = None
) -> dict[str, float | None]:
    """A PageRank value per paper, ``None`` where the paper has no observed edge.

    Only observed citation edges take part (``relation == "citation"``,
    direction citing -> cited).  Text-similarity, co-citation and
    bibliographic-coupling edges are never citation edges.  Fewer valid edges
    than the contract's floor, or a non-converging iteration, disables the whole
    signal (every value ``None``); isolated nodes are missing individually.
    """
    cfg = _ranking_config(config)
    alpha = float(cfg["pagerank_alpha"])
    max_iter = int(cfg["pagerank_max_iter"])
    tol = float(cfg["pagerank_tol"])
    min_edges = int(cfg["graph_min_edges"])

    ids: list[str] = []
    seen: set[str] = set()
    for raw in paper_ids or ():
        pid = str(raw)
        if pid not in seen:
            seen.add(pid)
            ids.append(pid)
    empty = {pid: None for pid in ids}
    if not ids:
        return empty

    adjacency: dict[str, list[str]] = {pid: [] for pid in ids}
    outgoing: dict[str, set[str]] = {pid: set() for pid in ids}
    valid_edges = 0
    for source, target, relation, directed in _iter_edges(edges):
        if relation is not None and relation not in _CITATION_RELATIONS:
            continue
        if directed is False:
            continue
        if source not in seen or target not in seen or source == target:
            continue
        if target in outgoing[source]:
            continue
        outgoing[source].add(target)
        valid_edges += 1

    if valid_edges < min_edges:
        return empty

    for pid in ids:
        adjacency[pid] = sorted(outgoing[pid])
    scores = _pagerank(ids, adjacency, alpha, max_iter, tol)
    if scores is None:
        return empty

    connected: set[str] = set()
    for pid in ids:
        if outgoing[pid]:
            connected.add(pid)
        for target in outgoing[pid]:
            connected.add(target)
    result: dict[str, float | None] = {}
    for pid in ids:
        value = scores.get(pid)
        if pid in connected and value is not None and math.isfinite(value):
            result[pid] = float(value)
        else:
            result[pid] = None
    return result


# --------------------------------------------------------------------------
# the ranking engine
# --------------------------------------------------------------------------


def core_rank(
    candidates: Iterable[CandidateSignals],
    *,
    judgements: Any = None,
    seeds: Any = (),
    as_of_year: int | None = None,
    config: Mapping[str, Any] | None = None,
) -> RankingResult:
    """Rank the candidate set with the deterministic local RRF fusion.

    ``candidates`` carries the already-computed signal values; ``judgements``
    (mapping or sequence) supplies exclusions and confirmed core anchors, and
    ``seeds`` names the confirmed seed papers that may enter the recommendation
    without topic evidence.  The result order never depends on the input order.
    """
    cfg = _ranking_config(config)
    rrf_k = float(cfg["rrf_k"])
    fraction = float(cfg["topic_gate_fraction"])
    decimals = int(cfg["tie_round_decimals"])
    raw_weights = cfg["weights"]
    weights = {signal: float(raw_weights[signal]) for signal in SIGNAL_ORDER}

    # -- identity, dedupe (first wins) and judgement state -----------------
    records: list[CandidateSignals] = []
    known_ids: set[str] = set()
    for candidate in candidates or ():
        if not isinstance(candidate, CandidateSignals):
            continue
        pid = str(candidate.paper_id)
        if not pid or pid in known_ids:
            continue
        known_ids.add(pid)
        records.append(candidate)

    judged = _judgement_map(judgements)
    anchor_ids = _normalise_ids(seeds)
    for pid, record in judged.items():
        if record[0] == str(JudgementValue.CORE):
            anchor_ids.add(pid)

    excluded_ids: set[str] = set()
    anchors: set[str] = set()
    for candidate in records:
        pid = str(candidate.paper_id)
        value, _reason = judged.get(pid, (None, None))
        if candidate.excluded or value == str(JudgementValue.EXCLUDED):
            excluded_ids.add(pid)
        if candidate.user_anchor or pid in anchor_ids:
            anchors.add(pid)

    non_excluded = [c for c in records if str(c.paper_id) not in excluded_ids]
    non_excluded_ids = [str(c.paper_id) for c in non_excluded]
    total = len(non_excluded)

    values: dict[str, dict[str, float | None]] = {}
    for candidate in records:
        pid = str(candidate.paper_id)
        values[pid] = {signal: safe_float(getattr(candidate, signal, None)) for signal in SIGNAL_ORDER}

    # -- per signal: known set, activity, mapped ranks ---------------------
    state: dict[str, dict[str, Any]] = {}
    neutral = (total + 1) / 2.0 if total else None
    for signal in SIGNAL_ORDER:
        known = [
            (pid, values[pid][signal])
            for pid in non_excluded_ids
            if values[pid][signal] is not None
        ]
        known_count = len(known)
        distinct = {round_tie(value) for _, value in known}
        active = known_count >= 2 and len(distinct) >= _MIN_DISTINCT
        state[signal] = {
            "known_count": known_count,
            "active": active,
            "neutral": neutral,
            "ranks": _signal_ranks(known, scale_to=total) if active else {},
        }

    active_signals = [signal for signal in SIGNAL_ORDER if state[signal]["active"]]
    active_weight_sum = 0.0
    for signal in SIGNAL_ORDER:
        if state[signal]["active"]:
            active_weight_sum += weights[signal]

    # -- raw fusion and display score -------------------------------------
    raws: dict[str, float | None] = {}
    contributions: dict[str, dict[str, float | None]] = {}
    for candidate in records:
        pid = str(candidate.paper_id)
        contributions[pid] = {signal: None for signal in SIGNAL_ORDER}
        if pid in excluded_ids or not active_signals:
            raws[pid] = None
            continue
        total_raw = 0.0
        for signal in SIGNAL_ORDER:
            info = state[signal]
            if not info["active"]:
                continue
            rank = info["ranks"].get(pid, info["neutral"])
            piece = weights[signal] / (rrf_k + float(rank))
            total_raw += piece
            contributions[pid][signal] = piece / active_weight_sum
        raws[pid] = total_raw / active_weight_sum

    def _display(raw: float | None) -> float | None:
        if raw is None:
            return None
        score = (rrf_k + 1.0) * raw
        if not math.isfinite(score):
            return None
        return min(1.0, max(0.0, score))

    # -- topic gate --------------------------------------------------------
    gate_pool: list[tuple[str, float]] = []
    for pid in non_excluded_ids:
        topic = values[pid][_TOPIC]
        if topic is not None and topic > 0:
            gate_pool.append((pid, topic))
    gate_total = len(gate_pool)
    gate_size = _ceil_eps(fraction * gate_total)
    gated: set[str] = set()
    if gate_pool and gate_size > 0:
        ordered_gate = sorted(gate_pool, key=lambda pair: (-round_tie(pair[1]), pair[0]))
        threshold = round_tie(ordered_gate[gate_size - 1][1])
        for pid, topic in gate_pool:
            if round_tie(topic) >= threshold:  # boundary ties enter together
                gated.add(pid)

    anchor_extra: set[str] = set()
    for pid in sorted(anchors):
        if pid in excluded_ids or pid not in known_ids:
            continue
        if pid not in gated:
            gated.add(pid)
            anchor_extra.add(pid)

    # -- evidence + final order -------------------------------------------
    groups: dict[str, int] = {}
    for pid in known_ids:
        if pid in excluded_ids:
            groups[pid] = 2
        elif pid in gated:
            groups[pid] = 0
        else:
            groups[pid] = 1

    default_role = str(Role.UNKNOWN)
    default_origin = str(RoleOrigin.UNKNOWN)

    def _evidence(pid: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        topic = values[pid][_TOPIC]
        if pid in anchor_extra:
            text = "用户指定起点：用户显式指定进入推荐"
            if topic is None or topic <= 0:
                text = "用户指定起点：缺少主题证据，由用户显式指定进入推荐"
            out.append(
                {"code": "user_anchor", "text": text, "paper_ids": [pid], "evidence_ids": []}
            )
        elif pid in gated:
            out.append(
                {
                    "code": "topic_gate",
                    "text": (
                        f"主题信号位于本候选集主题门槛前 {fraction:.0%}"
                        "（相对候选集的工程启发式，不代表绝对相关度）"
                    ),
                    "paper_ids": [pid],
                    "evidence_ids": [],
                }
            )
        if active_signals and neutral is not None:
            for signal, code, label in (
                (str(Signal.IMPACT), "impact_age_adjusted", "年龄调整后的引用影响"),
                (str(Signal.GRAPH), "graph_percentile", "已观察引用图中的 PageRank"),
                (str(Signal.SEED), "seed_similarity", "与已确认种子向量的最大余弦相似度"),
                (str(Signal.FEEDBACK), "feedback_similarity", "与更新后查询向量的余弦相似度"),
            ):
                info = state[signal]
                if not info["active"]:
                    continue
                rank = info["ranks"].get(pid)
                if rank is None or rank >= neutral:
                    continue
                out.append(
                    {
                        "code": code,
                        "text": f"{label}高于候选集中位（第 {rank:.1f} 名）",
                        "paper_ids": [pid],
                        "evidence_ids": [],
                    }
                )
        return out

    def _sort_key(pid: str) -> tuple[Any, ...]:
        group = groups[pid]
        if group == 2 or not active_signals:
            return (group, 0.0, 0.0, pid)
        raw = raws.get(pid)
        raw_key = -(round_tie(raw) if raw is not None else float("-inf"))
        topic = values[pid][_TOPIC]
        topic_key = -(round_tie(topic) if topic is not None else float("-inf"))
        return (group, raw_key, topic_key, pid)

    ordered_ids = sorted(known_ids, key=_sort_key)

    items: list[RankedPaper] = []
    for index, pid in enumerate(ordered_ids, start=1):
        if pid in excluded_ids:
            eligibility = str(Eligibility.EXCLUDED)
        elif pid in gated:
            eligibility = str(Eligibility.RECOMMENDED)
        else:
            eligibility = str(Eligibility.EXPLORATION)
        breakdown: list[Breakdown] = []
        for signal in SIGNAL_ORDER:
            info = state[signal]
            value = values[pid][signal]
            available = bool(info["active"]) and pid not in excluded_ids
            rank = info["ranks"].get(pid, info["neutral"]) if available else None
            reason: str | None = None
            if pid in excluded_ids:
                reason = "候选被排除，不参与核心排序"
            elif not info["active"]:
                reason = "少于 2 个不同有效值，该信号整项停用"
            elif value is None:
                reason = "该论文缺少此信号的有效值，使用中性名次"
            breakdown.append(
                Breakdown(
                    signal=signal,
                    raw_value=value,
                    known_count=int(info["known_count"]),
                    available=available,
                    rank=rank,
                    weight=float(weights[signal]),
                    contribution=contributions[pid][signal] if available else None,
                    missing_reason=reason,
                )
            )
        items.append(
            RankedPaper(
                paper_id=pid,
                rank=index,
                core_score_raw=raws.get(pid),
                core_score=_display(raws.get(pid)),
                core_role=default_role,
                role_origin=default_origin,
                eligibility=eligibility,
                breakdown=breakdown,
                recommendation_evidence=_evidence(pid) if eligibility == str(Eligibility.RECOMMENDED) else [],
            )
        )

    recommended = [item.paper_id for item in items if item.eligibility == str(Eligibility.RECOMMENDED)]

    coverage = {
        "status": "no_results" if not records else "within_budget",
        "candidate_count": len(records),
        "failed_sources": [],
        "stop_reasons": [] if records else ["no_candidates"],
        "scope": "current_candidate_set",
    }

    enhanced = any(
        signal in active_signals
        for signal in (str(Signal.GRAPH), str(Signal.SEED), str(Signal.FEEDBACK))
    )
    snapshot_fields = {
        "algorithm_version": C.algorithm_version(),
        "document_version": C.document_version(),
        "ranking_stage": "local_enhanced" if enhanced else "fast",
        "as_of_year": as_of_year if isinstance(as_of_year, int) and not isinstance(as_of_year, bool) else None,
        "rrf_k": rrf_k,
        "tie_round_decimals": decimals,
        "active_signals": list(active_signals),
        "inactive_signals": [signal for signal in SIGNAL_ORDER if signal not in active_signals],
        "signal_weights": {signal: float(weights[signal]) for signal in SIGNAL_ORDER},
        "candidate_count": len(records),
        "recommended_count": len(recommended),
        "evidence_insufficient": not active_signals,
        "canonical_order": sorted(known_ids),
    }

    result = RankingResult(
        items=items,
        recommended_paper_ids=recommended,
        starter_paper_ids=[],
        coverage=coverage,
        snapshot_fields=snapshot_fields,
    )
    result.starter_paper_ids = starter_list(result)
    return result


# --------------------------------------------------------------------------
# starter reading list (selection only; never rewrites a core_score)
# --------------------------------------------------------------------------


def _vector_cosine(left: Any, right: Any) -> float | None:
    """Cosine of two vectors, or ``None`` when they are not comparable."""
    try:
        a = [float(x) for x in left]
        b = [float(x) for x in right]
    except (TypeError, ValueError):
        return None
    if not a or len(a) != len(b):
        return None
    if not all(math.isfinite(x) for x in a) or not all(math.isfinite(x) for x in b):
        return None
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a <= _EPS or norm_b <= _EPS:
        return None
    cosine = sum(x * y for x, y in zip(a, b)) / (norm_a * norm_b)
    if not math.isfinite(cosine):
        return None
    return max(-1.0, min(1.0, cosine))


def _title_tokens(text: Any) -> set[str]:
    """Deterministic title tokens: latin words plus CJK character bigrams."""
    if not isinstance(text, str) or not text:
        return set()
    lowered = text.casefold()
    tokens = set(_TOKEN_RE.findall(lowered))
    for run in _CJK_RE.findall(lowered):
        if len(run) == 1:
            tokens.add(run)
        else:
            for index in range(len(run) - 1):
                tokens.add(run[index : index + 2])
    return tokens


def _title_jaccard(left: Any, right: Any) -> float:
    a = _title_tokens(left)
    b = _title_tokens(right)
    if not a or not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def starter_list(
    result: Any,
    *,
    vectors: Mapping[str, Sequence[float]] | None = None,
    titles: Mapping[str, str] | None = None,
    roles: Mapping[str, str] | None = None,
    pool: Iterable[str] | None = None,
) -> list[str]:
    """The first-reading list: MMR over the recommended pool, at most 10 of 50.

    Relevance is the linear percentile of the final pool order, redundancy is
    ``max(0, cosine)`` when vectors exist and the normalised title-token
    Jaccard otherwise.  At most one paper per evidenced role
    (review, foundational, representative, frontier) is seeded first, then MMR
    fills the rest.  This is selection only: it never mutates a ``core_score``.
    """
    cfg = C.ranking()
    size = max(0, int(cfg["starter_size"]))
    max_pool = max(0, int(cfg["starter_pool"]))
    lam = float(cfg["mmr_lambda"])

    items = list(getattr(result, "items", result) or [])
    # Only recommended papers are eligible: doc 04 "不为填满清单扩大到明显不相关
    # 论文" -- exploration (below the topic gate) and excluded papers never pad
    # the list, even when they are named in ``pool``.
    non_excluded = [
        item for item in items if str(getattr(item, "eligibility", "")) == str(Eligibility.RECOMMENDED)
    ]

    order = [str(item.paper_id) for item in non_excluded]
    if pool is not None:
        allowed = set(order)
        seen: set[str] = set()
        requested: list[str] = []
        for raw in pool:
            pid = str(raw)
            if pid in allowed and pid not in seen:
                seen.add(pid)
                requested.append(pid)
        order = requested
    order = order[:max_pool]

    count = len(order)
    if count == 0 or size == 0:
        return []

    relevance = {
        pid: ((count - 1 - index) / (count - 1) if count > 1 else 1.0)
        for index, pid in enumerate(order)
    }

    role_of: dict[str, str] = {
        str(item.paper_id): str(getattr(item, "core_role", str(Role.UNKNOWN))) for item in non_excluded
    }
    if isinstance(roles, Mapping):
        for key, value in roles.items():
            role_of[str(key)] = str(value)

    vector_map: dict[str, Any] = {}
    if isinstance(vectors, Mapping):
        vector_map = {str(key): value for key, value in vectors.items()}
    title_map: dict[str, str] = {}
    if isinstance(titles, Mapping):
        title_map = {str(key): value for key, value in titles.items()}

    def _redundancy(pid: str, other: str) -> float:
        left = vector_map.get(pid)
        right = vector_map.get(other)
        if left is not None and right is not None:
            cosine = _vector_cosine(left, right)
            if cosine is not None:
                return max(0.0, cosine)
        left_title = title_map.get(pid)
        right_title = title_map.get(other)
        if left_title is not None and right_title is not None:
            return _title_jaccard(left_title, right_title)
        return 0.0

    limit = min(size, count)
    selected: list[str] = []

    # One quota slot per evidenced role, in the contract's priority order.
    for role in _QUOTA_ROLES:
        if len(selected) >= limit:
            break
        for pid in order:
            if pid in selected:
                continue
            if role_of.get(pid) == role:
                selected.append(pid)
                break

    while len(selected) < limit:
        best: str | None = None
        best_key: tuple[float, int] | None = None
        for index, pid in enumerate(order):
            if pid in selected:
                continue
            redundancy = 0.0
            for chosen in selected:
                redundancy = max(redundancy, _redundancy(pid, chosen))
            mmr = lam * relevance[pid] - (1.0 - lam) * redundancy
            rounded = round_tie(mmr)
            key = (-(rounded if rounded is not None else float("-inf")), index)
            if best_key is None or key < best_key:
                best_key = key
                best = pid
        if best is None:
            break
        selected.append(best)

    return selected
