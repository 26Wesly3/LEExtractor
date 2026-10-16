"""Domain map, timeline and trend layer for LEExtractor v3.2 (doc 17, M4).

The map layer is deliberately honest about what the corpus can support:

* citation, co-citation, bibliographic coupling and text similarity stay
  **separate and labelled by source** (:func:`edge_layers`); a text-similarity
  edge is never treated as a citation edge (AC-31),
* fewer than ``mapping.min_nodes`` nodes, fewer than ``mapping.min_edges`` valid
  relations, or an isolate fraction above ``mapping.max_isolate_fraction``
  degrades the layer instead of forcing a community conclusion (AC-19),
* the communities are NetworkX Louvain with the contract seed and resolution,
  so identical input gives byte-identical output,
* near-duplicate folding is a **view** concern only: :func:`fold_view` maps ids
  onto representatives and never deletes an identity or an evidence edge.

Timeline counts real publication years and lists unknown years separately.
Trends compare the last two *complete* years against the two before them using
the contract's smoothed growth rate, and say "暂不足以判断趋势" when the corpus
is too thin (AC-20).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from . import contracts as C
from .types import round_tie, utc_now

try:  # dependency boundary — a missing NetworkX degrades, it does not crash
    import networkx as _nx
    from networkx.algorithms.community import louvain_communities as _louvain_communities

    NETWORKX_VERSION = str(_nx.__version__)
except Exception:  # pragma: no cover - exercised only when the pin is absent
    _nx = None
    _louvain_communities = None
    NETWORKX_VERSION = "unavailable"

__all__ = [
    "Community",
    "MapResult",
    "STRUCTURAL_RELATIONS",
    "KNOWN_RELATIONS",
    "RELATION_DIRECTED",
    "ALGORITHM_NAME",
    "MAX_LABEL_KEYWORDS",
    "NETWORKX_VERSION",
    "mapping_config",
    "build_map",
    "edge_layers",
    "timeline",
    "trends",
    "fold_view",
]

#: Relations that describe an *observed* structure between two papers.  Text
#: similarity and version links are derived/identity relations and never take
#: part in community detection.
STRUCTURAL_RELATIONS = ("citation", "co_citation", "bibliographic_coupling")

#: Every relation the domain schema knows, in schema order.
KNOWN_RELATIONS = (
    "citation",
    "co_citation",
    "bibliographic_coupling",
    "text_similarity",
    "version_link",
)

#: doc 17: citation edges are directed, every other relation is undirected.
RELATION_DIRECTED = {
    "citation": True,
    "co_citation": False,
    "bibliographic_coupling": False,
    "text_similarity": False,
    "version_link": False,
}

#: The verified default community algorithm (doc 17 "图与标签实现选择").
ALGORITHM_NAME = "networkx_louvain"

#: doc 17: at most three keywords per community label.
MAX_LABEL_KEYWORDS = 3

_LATIN = re.compile(r"[a-z0-9]+")
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "over",
        "that",
        "the",
        "their",
        "this",
        "to",
        "using",
        "via",
        "was",
        "were",
        "with",
        "we",
        "our",
    }
)


def mapping_config(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The ``mapping`` contract section with optional caller overrides."""
    base: dict[str, Any] = C.mapping_defaults()
    if isinstance(config, Mapping):
        nested = config.get("mapping")
        if isinstance(nested, Mapping):
            base.update(nested)
        for key in list(base):
            if key in config and not isinstance(config[key], Mapping):
                base[key] = config[key]
    return base


def _get(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


# --------------------------------------------------------------------------
# edges
# --------------------------------------------------------------------------


def _edge_weight(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 1.0
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        return 1.0
    return number


def _edge_records(edges: Any, paper_ids: set[str]) -> list[dict]:
    """Normalise edges into records, dropping anything not usable.

    Both endpoints must be known papers, the relation must be a contract
    relation and self loops are dropped.  ``directed`` is taken from the
    contract, not from the caller, so a mislabelled edge cannot change the
    graph's semantics.
    """
    records: list[dict] = []
    for raw in edges or []:
        source = _get(raw, "from_paper_id")
        if source is None:
            source = _get(raw, "source")
        if source is None:
            source = _get(raw, "from")
        target = _get(raw, "to_paper_id")
        if target is None:
            target = _get(raw, "target")
        if target is None:
            target = _get(raw, "to")
        relation = _get(raw, "relation")
        if source is None or target is None or relation is None:
            continue
        source = str(source)
        target = str(target)
        relation = str(relation)
        if relation not in KNOWN_RELATIONS:
            continue
        if source not in paper_ids or target not in paper_ids:
            continue
        if source == target:
            continue
        records.append(
            {
                "from": source,
                "to": target,
                "relation": relation,
                "directed": RELATION_DIRECTED[relation],
                "weight": _edge_weight(_get(raw, "weight", 1.0)),
            }
        )
    records.sort(key=lambda record: (record["relation"], record["from"], record["to"]))
    return records


def edge_layers(edges: Any, paper_ids: Sequence[str] | None = None) -> dict[str, list[dict]]:
    """The edge set split by relation, each layer labelled by its source.

    This is the only supported way to read more than one relation at a time:
    nothing in the module silently merges a similarity layer into the citation
    layer.
    """
    known = {str(pid) for pid in (paper_ids or [])}
    if not known:
        for raw in edges or []:
            for key in ("from_paper_id", "to_paper_id", "source", "target", "from", "to"):
                value = _get(raw, key)
                if value is not None:
                    known.add(str(value))
    layers: dict[str, list[dict]] = {relation: [] for relation in KNOWN_RELATIONS}
    for record in _edge_records(edges, known):
        layers[record["relation"]].append(dict(record))
    return layers


# --------------------------------------------------------------------------
# communities
# --------------------------------------------------------------------------


@dataclass
class Community:
    community_id: str
    label: str
    paper_ids: list[str]
    label_origin: str


@dataclass
class MapResult:
    communities: list[Community]
    isolated_paper_ids: list[str]
    isolate_fraction: float
    degraded: bool
    degraded_reason: str | None
    algorithm: str
    algorithm_version: str
    graph_hash: str


def _graph_hash(nodes: Sequence[str], records: Sequence[Mapping[str, Any]]) -> str:
    payload = {
        "nodes": sorted(str(node) for node in nodes),
        "edges": sorted(
            [
                [
                    str(record["from"]),
                    str(record["to"]),
                    str(record["relation"]),
                    bool(record["directed"]),
                    round_tie(record["weight"]),
                ]
                for record in records
            ]
        ),
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _tokens(text: str | None) -> list[str]:
    """Deterministic tokens: latin words plus CJK character bigrams."""
    if not text:
        return []
    normalised = unicodedata.normalize("NFKC", str(text)).casefold()
    tokens: list[str] = []
    for match in _LATIN.finditer(normalised):
        token = match.group(0)
        if len(token) >= 2 and token not in _STOPWORDS:
            tokens.append(token)
    for match in _CJK.finditer(normalised):
        chunk = match.group(0)
        if len(chunk) == 1:
            tokens.append(chunk)
        else:
            tokens.extend(chunk[index : index + 2] for index in range(len(chunk) - 1))
    return tokens


def _texts_map(texts: Any, nodes: Sequence[str]) -> dict[str, str]:
    if texts is None:
        return {}
    if isinstance(texts, Mapping):
        return {str(key): str(value) for key, value in texts.items() if value is not None}
    mapping: dict[str, str] = {}
    if isinstance(texts, Sequence) and not isinstance(texts, (str, bytes)):
        for index, entry in enumerate(texts):
            if isinstance(entry, Mapping):
                paper_id = entry.get("paper_id")
                if paper_id is None:
                    continue
                text = entry.get("text")
                if text is None:
                    text = " ".join(str(entry.get(field) or "") for field in ("title", "abstract"))
                mapping[str(paper_id)] = str(text)
            elif index < len(nodes):
                mapping[str(nodes[index])] = str(entry)
    return mapping


def _c_tfidf_labels(
    groups: Sequence[Sequence[str]], texts: Mapping[str, str]
) -> list[tuple[str, str]]:
    """c-TF-IDF over each community's title/abstract; deterministic tie-break.

    Implemented locally on purpose (doc 17: do not add a heavy runtime
    dependency just for labels).
    """
    token_lists = [
        _tokens(" ".join(texts.get(paper_id, "") for paper_id in group)) for group in groups
    ]
    communities = len(groups)
    document_frequency: dict[str, int] = {}
    for tokens in token_lists:
        for term in set(tokens):
            document_frequency[term] = document_frequency.get(term, 0) + 1

    labels: list[tuple[str, str]] = []
    for tokens in token_lists:
        if not tokens:
            labels.append(("", "unlabeled"))
            continue
        counts: dict[str, int] = {}
        for term in tokens:
            counts[term] = counts.get(term, 0) + 1
        total = float(len(tokens))
        scored: list[tuple[float, str]] = []
        for term, count in counts.items():
            tf = count / total
            idf = math.log(1.0 + (communities / float(document_frequency.get(term, 1))))
            value = round_tie(tf * idf)
            scored.append((-(value if value is not None else 0.0), term))
        scored.sort()
        keywords = [term for _, term in scored[:MAX_LABEL_KEYWORDS]]
        labels.append((" / ".join(keywords), "text_statistics"))
    return labels


def build_map(
    edges: Any,
    paper_ids: Sequence[str],
    *,
    texts: Any = None,
    config: Mapping[str, Any] | None = None,
) -> MapResult:
    """Community structure, or an explicit degradation — never a forced answer."""
    cfg = mapping_config(config)
    min_nodes = int(cfg["min_nodes"])
    min_edges = int(cfg["min_edges"])
    max_isolates = float(cfg["max_isolate_fraction"])
    seed = int(cfg["random_seed"])
    resolution = float(cfg["resolution"])
    min_size = int(cfg["min_community_size"])

    nodes = sorted({str(pid) for pid in paper_ids if str(pid)})
    node_set = set(nodes)
    records = _edge_records(edges, node_set)
    structural = [record for record in records if record["relation"] in STRUCTURAL_RELATIONS]

    degree: dict[str, int] = dict.fromkeys(nodes, 0)
    for record in structural:
        degree[record["from"]] = degree.get(record["from"], 0) + 1
        degree[record["to"]] = degree.get(record["to"], 0) + 1
    isolates = sorted(node for node in nodes if degree.get(node, 0) == 0)
    fraction = (len(isolates) / float(len(nodes))) if nodes else 0.0

    graph_hash = _graph_hash(nodes, records)
    reasons: list[str] = []
    if len(nodes) < min_nodes:
        reasons.append(f"节点 {len(nodes)} < {min_nodes}")
    if len(structural) < min_edges:
        reasons.append(f"有效关系 {len(structural)} < {min_edges}")
    if fraction > max_isolates:
        reasons.append(f"孤立率 {fraction:.2f} > {max_isolates:.2f}")
    if _nx is None or _louvain_communities is None:  # pragma: no cover
        reasons.append("缺少可用的社区发现依赖 NetworkX")

    if reasons:
        return MapResult(
            communities=[],
            isolated_paper_ids=isolates,
            isolate_fraction=float(round_tie(fraction)),
            degraded=True,
            degraded_reason="关系数据不足：" + "；".join(reasons),
            algorithm=ALGORITHM_NAME,
            algorithm_version=NETWORKX_VERSION,
            graph_hash=graph_hash,
        )

    graph = _nx.Graph()
    graph.add_nodes_from(nodes)
    weights: dict[tuple[str, str], float] = {}
    for record in structural:
        left, right = sorted((record["from"], record["to"]))
        weights[(left, right)] = weights.get((left, right), 0.0) + record["weight"]
    for (left, right), weight in weights.items():
        graph.add_edge(left, right, weight=weight if weight > 0 else 1.0)

    groups = [
        sorted(group)
        for group in _louvain_communities(graph, weight="weight", resolution=resolution, seed=seed)
    ]
    groups = [group for group in groups if group]
    groups.sort(key=lambda group: (-len(group), group[0]))

    kept = [group for group in groups if len(group) >= min_size]
    ungrouped = [paper_id for group in groups if len(group) < min_size for paper_id in group]
    labels = _c_tfidf_labels(kept, _texts_map(texts, nodes))

    communities: list[Community] = []
    for index, (group, (label, origin)) in enumerate(zip(kept, labels, strict=True), start=1):
        communities.append(
            Community(
                community_id=f"c{index:02d}",
                label=label or f"分支 {index}",
                paper_ids=list(group),
                label_origin=origin,
            )
        )
    if ungrouped:
        communities.append(
            Community(
                community_id="ungrouped",
                label="未分组",
                paper_ids=sorted(ungrouped),
                label_origin="unlabeled",
            )
        )

    return MapResult(
        communities=communities,
        isolated_paper_ids=isolates,
        isolate_fraction=float(round_tie(fraction)),
        degraded=False,
        degraded_reason=None,
        algorithm=ALGORITHM_NAME,
        algorithm_version=NETWORKX_VERSION,
        graph_hash=graph_hash,
    )


# --------------------------------------------------------------------------
# folding (view concern only)
# --------------------------------------------------------------------------


def fold_view(paper_ids: Sequence[str], groups: Sequence[Sequence[str]]) -> dict:
    """Map near-duplicate groups onto representatives **without deleting ids**.

    The returned ``representative_of`` maps every folded id to its group's
    representative, and ``paper_ids`` still lists every identity.  Folding is a
    display decision; it never merges paper identities or evidence edges.
    """
    ordered = [str(pid) for pid in paper_ids]
    known = set(ordered)
    representative_of: dict[str, str] = {}
    for group in groups or []:
        members = [str(pid) for pid in group if str(pid) in known]
        if len(members) < 2:
            continue
        representative = sorted(members)[0]
        for member in members:
            representative_of.setdefault(member, representative)
    return {
        "paper_ids": ordered,
        "representative_of": representative_of,
        "representatives": sorted({representative_of.get(pid, pid) for pid in ordered}),
    }


# --------------------------------------------------------------------------
# timeline and trends
# --------------------------------------------------------------------------


def _valid_year(value: Any) -> int | None:
    """A real publication year per ``$defs/Paper`` (1000..3000), else unknown."""
    if value is None or isinstance(value, bool):
        return None
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    return year if 1000 <= year <= 3000 else None


def _paper_records(papers: Any) -> list[dict]:
    records: list[dict] = []
    for raw in papers or []:
        paper_id = _get(raw, "paper_id")
        if paper_id is None:
            continue
        records.append(
            {
                "paper_id": str(paper_id),
                "year": _valid_year(_get(raw, "year")),
            }
        )
    return records


def timeline(papers: Any, *, config: Mapping[str, Any] | None = None) -> list[dict]:
    """``[{year, count, paper_ids}]`` over the current retrieved set.

    Unknown years are listed separately as one ``year: null`` entry (only when
    such papers exist).  Years with no papers are not invented.
    """
    by_year: dict[int, list[str]] = {}
    unknown: list[str] = []
    for record in _paper_records(papers):
        if record["year"] is None:
            unknown.append(record["paper_id"])
        else:
            by_year.setdefault(record["year"], []).append(record["paper_id"])

    points = [
        {"year": year, "count": len(ids), "paper_ids": sorted(ids)}
        for year, ids in sorted(by_year.items())
    ]
    if unknown:
        points.append({"year": None, "count": len(unknown), "paper_ids": sorted(unknown)})
    return points


def trends(papers: Any, *, config: Mapping[str, Any] | None = None) -> dict:
    """Smoothed growth over complete years, or an honest insufficient-data note."""
    cfg = mapping_config(config)
    requested_year = cfg.get("as_of_year")
    if isinstance(requested_year, bool) or not isinstance(requested_year, (int, float)):
        current_year = utc_now().year
    else:
        current_year = int(requested_year)
    min_prior = int(cfg["trend_min_prior_count"])
    complete_years_required = int(cfg["trend_complete_years"])

    counts: dict[int, int] = {}
    for record in _paper_records(papers):
        year = record["year"]
        if year is None or year >= current_year:
            # The unfinished current year is never compared with a complete year.
            continue
        counts[year] = counts.get(year, 0) + 1

    complete = sorted(counts)
    if len(complete) < complete_years_required:
        return {
            "trend_status": "insufficient_data",
            "trends": [],
            "note": "暂不足以判断趋势",
            "reason_code": "fewer_than_required_complete_years",
            "years_covered": len(complete),
        }

    # doc 17: the most recent half of the covered complete years against the half
    # before it.  The window width follows the contract, it is not a literal.
    window_years_count = max(1, complete_years_required // 2)
    recent_years = complete[-window_years_count:]
    prior_years = complete[-2 * window_years_count : -window_years_count]
    recent = sum(counts[year] for year in recent_years)
    prior = sum(counts[year] for year in prior_years)
    if prior < min_prior:
        return {
            "trend_status": "insufficient_data",
            "trends": [],
            "note": "暂不足以判断趋势",
            "reason_code": "prior_window_too_small",
            "years_covered": len(complete),
        }

    growth = round_tie((recent + 1) / float(prior + 1) - 1.0)
    window = sorted(recent_years + prior_years)
    return {
        "trend_status": "available",
        "trends": [
            {
                "branch_id": "all",
                "recent_count": recent,
                "prior_count": prior,
                "growth": growth,
                "window_years": window,
                "scope": f"仅统计当前检索集的完整年份 {window[0]}–{window[-1]}",
            }
        ],
        "note": "",
        "reason_code": None,
        "years_covered": len(complete),
    }
