"""Evidence-based research overview for LEExtractor v3.2 (doc 17, M4).

Every statement carries ``text``, ``type`` (fact/inference/hypothesis),
``paper_ids``, ``evidence_ids``, ``scope`` and ``limitations``.  A fact points at
a real record or statistic; an inference is labelled as an inference.  Without
an AI key the overview is still built from templates and statistics — never an
empty page (AC-01 requires an initial overview with zero user marks).

``build_overview`` returns exactly the ten keys ``$defs/Overview`` declares, and
empty structures are empty arrays rather than omitted fields (doc 08).

The optional ``adapter`` may only *write over the given evidence*: a statement
that references a paper or an evidence id outside the project is rejected and
the whole overview falls back to the deterministic template.  An adapter that
raises, times out or returns invalid JSON does the same, so the local ranking
and the overview both survive a broken model (doc 17 "概览证据契约").
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from . import contracts as C
from .ai_rerank import ai_config, call_model_adapter
from .mapping import STRUCTURAL_RELATIONS, edge_layers, mapping_config
from .mapping import timeline as _timeline
from .mapping import trends as _trends

__all__ = [
    "build_overview",
    "derive_leads",
    "evidence_statements",
    "STATEMENT_TYPES",
    "LEAD_VERIFICATION_STATUSES",
    "MAX_LEADS",
]

STATEMENT_TYPES = ("fact", "inference", "hypothesis")
LEAD_VERIFICATION_STATUSES = ("checked_in_current_sources", "needs_verification")

#: doc 17 lists at most a handful of checkable leads; a cap is not a quota.
MAX_LEADS = 3

_DEFAULT_SCOPE = "当前检索集"


def _get(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def _dedupe(values: Sequence[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _string_list(value: Any) -> list[str] | None:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return None
    out: list[str] = []
    for item in value:
        if item is None:
            return None
        out.append(str(item))
    return out


def _year_of(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    return year if 1000 <= year <= 3000 else None


_PAPER_FIELDS = (
    "paper_id",
    "title",
    "abstract",
    "year",
    "citation_count",
    "doc_type",
    "eligibility",
    "core_score_raw",
    "rank",
)


def _paper_source(entry: Any) -> dict:
    if isinstance(entry, Mapping):
        return {key: entry[key] for key in entry}
    out: dict[str, Any] = {}
    for key in _PAPER_FIELDS:
        value = getattr(entry, key, None)
        if value is not None:
            out[key] = value
    return out


def _snapshot_view(snapshot: Any) -> dict:
    """Normalise a RankingSnapshot (or a plain paper list) into paper records."""
    if snapshot is None:
        return {"snapshot_id": None, "papers": [], "items": [], "coverage": None}
    if isinstance(snapshot, Mapping):
        snapshot_id = _get(snapshot, "snapshot_id")
        items = list(_get(snapshot, "items") or [])
        meta = list(_get(snapshot, "papers") or _get(snapshot, "project_papers") or [])
        coverage = _get(snapshot, "coverage")
    elif isinstance(snapshot, Sequence) and not isinstance(snapshot, (str, bytes)):
        snapshot_id, items, meta, coverage = None, list(snapshot), [], None
    else:
        return {"snapshot_id": None, "papers": [], "items": [], "coverage": None}

    meta_by_id: dict[str, dict] = {}
    for entry in meta:
        paper_id = _get(entry, "paper_id")
        if paper_id is not None:
            meta_by_id[str(paper_id)] = _paper_source(entry)

    papers: list[dict] = []
    seen: set[str] = set()
    for entry in items:
        paper_id = _get(entry, "paper_id")
        if paper_id is None:
            continue
        paper_id = str(paper_id)
        merged = dict(meta_by_id.get(paper_id, {}))
        merged.update(_paper_source(entry))
        merged["paper_id"] = paper_id
        merged["year"] = _year_of(merged.get("year"))
        if paper_id in seen:
            continue
        seen.add(paper_id)
        papers.append(merged)

    if not papers:
        for paper_id, record in meta_by_id.items():
            record = dict(record)
            record["paper_id"] = paper_id
            record["year"] = _year_of(record.get("year"))
            if paper_id not in seen:
                seen.add(paper_id)
                papers.append(record)

    return {
        "snapshot_id": None if snapshot_id is None else str(snapshot_id),
        "papers": papers,
        "items": items,
        "coverage": coverage,
    }


def _coverage_dict(raw: Any, paper_count: int) -> dict:
    valid = ("within_budget", "partial", "no_results", "failed")
    if isinstance(raw, Mapping):
        status = str(_get(raw, "status") or "")
        if status not in valid:
            status = "no_results" if paper_count == 0 else "partial"
        candidate = _get(raw, "candidate_count")
        if isinstance(candidate, bool) or not isinstance(candidate, (int, float)) or candidate < 0:
            candidate = paper_count
        failed = _string_list(_get(raw, "failed_sources")) or []
        stops = _string_list(_get(raw, "stop_reasons")) or []
        scope = _get(raw, "scope")
        return {
            "status": status,
            "candidate_count": int(candidate),
            "failed_sources": failed,
            "stop_reasons": stops,
            "scope": str(scope) if isinstance(scope, str) and scope else _DEFAULT_SCOPE,
        }
    return {
        "status": "no_results" if paper_count == 0 else "within_budget",
        "candidate_count": paper_count,
        "failed_sources": [],
        "stop_reasons": [],
        "scope": _DEFAULT_SCOPE,
    }


# --------------------------------------------------------------------------
# statements
# --------------------------------------------------------------------------


def evidence_statements(
    snapshot: Any,
    *,
    timeline: Sequence[Mapping[str, Any]] | None = None,
    coverage: Mapping[str, Any] | None = None,
) -> list[dict]:
    """Rule-generated statements; every claim traces to a record or statistic."""
    view = _snapshot_view(snapshot)
    papers = view["papers"]
    project_ids = _dedupe([paper.get("paper_id") for paper in papers])
    cov = _coverage_dict(coverage if coverage is not None else view["coverage"], len(papers))
    points = list(timeline) if timeline is not None else _timeline(papers)

    statements: list[dict] = []
    corpus_limits = ["仅覆盖本次检索集", "未包含未被检索到的文献"]

    if not papers:
        statements.append(
            {
                "text": "当前检索集为空（0 篇文献），暂无可用证据统计。",
                "type": "fact",
                "paper_ids": [],
                "evidence_ids": [],
                "scope": _DEFAULT_SCOPE,
                "limitations": corpus_limits,
            }
        )
    else:
        statements.append(
            {
                "text": f"当前检索集共 {len(papers)} 篇文献。",
                "type": "fact",
                "paper_ids": project_ids,
                "evidence_ids": [],
                "scope": _DEFAULT_SCOPE,
                "limitations": corpus_limits,
            }
        )

        known = [
            (paper["paper_id"], paper["year"]) for paper in papers if paper.get("year") is not None
        ]
        unknown = [paper["paper_id"] for paper in papers if paper.get("year") is None]
        if known:
            years = sorted({year for _, year in known})
            statements.append(
                {
                    "text": (
                        f"其中 {len(known)} 篇可确定出版年份，覆盖 {len(years)} 个年份"
                        f"（{years[0]}–{years[-1]}）；{len(unknown)} 篇年份未知。"
                    ),
                    "type": "fact",
                    "paper_ids": _dedupe([paper_id for paper_id, _ in known]),
                    "evidence_ids": [],
                    "scope": _DEFAULT_SCOPE,
                    "limitations": ["年份来自检索记录，可能存在缺失或错误"],
                }
            )
        else:
            statements.append(
                {
                    "text": f"当前检索集的 {len(papers)} 篇文献均无可靠出版年份记录。",
                    "type": "fact",
                    "paper_ids": list(unknown),
                    "evidence_ids": [],
                    "scope": _DEFAULT_SCOPE,
                    "limitations": ["缺少年份字段，无法给出时间分布"],
                }
            )

        known_points = [
            point for point in points if point.get("year") is not None and point.get("paper_ids")
        ]
        if known_points:
            peak = sorted(
                known_points, key=lambda point: (-int(point["count"]), int(point["year"]))
            )[0]
            statements.append(
                {
                    "text": f"出版量最集中的年份为 {peak['year']}（{peak['count']} 篇）。",
                    "type": "fact",
                    "paper_ids": sorted(str(pid) for pid in peak["paper_ids"]),
                    "evidence_ids": [],
                    "scope": _DEFAULT_SCOPE,
                    "limitations": ["仅统计当前检索集内可确定年份的文献"],
                }
            )

    statements.append(
        {
            "text": (
                f"检索覆盖状态为 {cov['status']}，候选 {cov['candidate_count']} 篇，"
                f"失败来源 {len(cov['failed_sources'])} 个，停止原因 {len(cov['stop_reasons'])} 条。"
            ),
            "type": "fact",
            "paper_ids": [],
            "evidence_ids": [],
            "scope": cov["scope"],
            "limitations": ["覆盖状态来自本次检索记录，不代表完整检索"],
        }
    )

    statements.append(
        {
            "text": "以上分布只描述当前检索集内的时间构成，不能直接外推为领域整体趋势；缺少独立外部基线时不下热度结论。",
            "type": "inference",
            "paper_ids": [],
            "evidence_ids": [],
            "scope": _DEFAULT_SCOPE,
            "limitations": ["推断基于检索集统计", "缺少外部基线"],
        }
    )
    return statements


# --------------------------------------------------------------------------
# leads
# --------------------------------------------------------------------------


def _community_papers(raw: Any) -> tuple[str, str, list[str]]:
    community_id = _get(raw, "community_id")
    if community_id is None:
        community_id = _get(raw, "branch_id")
    if community_id is None:
        community_id = ""
    label = _get(raw, "label")
    papers = _string_list(_get(raw, "paper_ids")) or []
    return str(community_id), str(label or ""), papers


def _nearest_work(paper_ids: Sequence[str], degree: Mapping[str, int]) -> str:
    return sorted(paper_ids, key=lambda pid: (-degree.get(pid, 0), pid))[0]


def _verification_status(adapter: Any, query: str) -> str:
    """Search the proposed combination once; without a search hook, be honest."""
    if adapter is None:
        return "needs_verification"
    search = getattr(adapter, "search", None)
    if not callable(search):
        return "needs_verification"
    try:
        result = search(query)
    except Exception:  # noqa: BLE001 - a broken search never fabricates a check
        return "needs_verification"
    if result is None:
        return "needs_verification"
    return "checked_in_current_sources"


def derive_leads(
    snapshot: Any,
    *,
    communities: Any = None,
    edges: Any = None,
    adapter: Any = None,
    config: Mapping[str, Any] | None = None,
) -> list[dict]:
    """Checkable hypotheses from at least two evidenced branches.

    Insufficient evidence returns an **empty list** — never a fixed set of
    "innovation points".  A small citation count is not used as a gap signal.
    """
    view = _snapshot_view(snapshot)
    project_ids = _dedupe([paper.get("paper_id") for paper in view["papers"]])
    project_set = set(project_ids)
    min_size = max(2, int(mapping_config(config)["min_community_size"]))

    normalised: list[dict] = []
    for raw in communities or []:
        community_id, label, papers = _community_papers(raw)
        if community_id == "ungrouped" or label == "未分组":
            continue
        papers = [pid for pid in _dedupe(papers) if pid in project_set]
        if len(papers) < min_size:
            continue
        normalised.append(
            {
                "id": community_id or f"c{len(normalised) + 1:02d}",
                "label": label or community_id or f"分支 {len(normalised) + 1}",
                "paper_ids": papers,
            }
        )
    if len(normalised) < 2:
        return []
    normalised.sort(key=lambda item: (item["id"], item["label"]))

    layers = edge_layers(edges, project_ids)
    records = [record for relation in STRUCTURAL_RELATIONS for record in layers.get(relation, [])]
    degree: dict[str, int] = {}
    member_of: dict[str, int] = {}
    for index, community in enumerate(normalised):
        for paper_id in community["paper_ids"]:
            member_of[paper_id] = index
    cross: dict[tuple[int, int], int] = {}
    for record in records:
        if record["relation"] not in STRUCTURAL_RELATIONS:
            continue
        left = member_of.get(record["from"])
        right = member_of.get(record["to"])
        degree[record["from"]] = degree.get(record["from"], 0) + 1
        degree[record["to"]] = degree.get(record["to"], 0) + 1
        if left is None or right is None or left == right:
            continue
        key = (min(left, right), max(left, right))
        cross[key] = cross.get(key, 0) + 1

    pairs = [
        (cross.get((left, right), 0), left, right)
        for left in range(len(normalised))
        for right in range(left + 1, len(normalised))
    ]
    pairs.sort(key=lambda item: (item[0], normalised[item[1]]["id"], normalised[item[2]]["id"]))

    leads: list[dict] = []
    for count, left_index, right_index in pairs[:MAX_LEADS]:
        left = normalised[left_index]
        right = normalised[right_index]
        query = f"{left['label']} {right['label']}"
        leads.append(
            {
                "claim_type": "hypothesis",
                "text": (
                    f"假设：{left['label']} 与 {right['label']} 之间在当前检索集中的显式关系较少"
                    f"（{count} 条），可能存在尚未被覆盖的组合方向。"
                ),
                "paper_ids": _dedupe(left["paper_ids"] + right["paper_ids"])[:8],
                "nearest_work_ids": _dedupe(
                    [
                        _nearest_work(left["paper_ids"], degree),
                        _nearest_work(right["paper_ids"], degree),
                    ]
                ),
                "reason": (
                    f"{left['label']}（{len(left['paper_ids'])} 篇）与 {right['label']}"
                    f"（{len(right['paper_ids'])} 篇）均有当前检索集内的文献证据；"
                    f"跨分支显式关系仅 {count} 条。少量引文不构成研究空白的证据，此线索需要检索验证。"
                ),
                "coverage_scope": _DEFAULT_SCOPE,
                "next_queries": [query, f"{left['label']} {right['label']} 交叉方法"],
                "verification_status": _verification_status(adapter, query),
            }
        )
    return leads


# --------------------------------------------------------------------------
# branches
# --------------------------------------------------------------------------


def _branches(branches: Any, project_ids: Sequence[str]) -> list[dict]:
    project_set = set(project_ids)
    out: list[dict] = []
    for raw in branches or []:
        papers = [
            pid for pid in _dedupe(_string_list(_get(raw, "paper_ids")) or []) if pid in project_set
        ]
        if not papers:
            continue
        paper_set = set(papers)
        representatives = [
            pid
            for pid in _dedupe(_string_list(_get(raw, "representative_paper_ids")) or [])
            if pid in paper_set
        ]
        if not representatives:
            # A representative must belong to the branch; the branch itself is
            # the only evidence we have, so use its first members.
            representatives = papers[: min(3, len(papers))]
        evidence = [
            pid
            for pid in _dedupe(_string_list(_get(raw, "evidence_ids")) or [])
            if pid in project_set
        ]
        if not evidence:
            evidence = list(representatives)
        branch_id = _get(raw, "branch_id")
        if branch_id is None:
            branch_id = _get(raw, "community_id")
        label = _get(raw, "label")
        origin = _get(raw, "label_origin")
        limitations = _string_list(_get(raw, "limitations")) or []
        method = _get(raw, "method")
        out.append(
            {
                "branch_id": str(branch_id or f"b{len(out) + 1:02d}"),
                "label": str(label or f"分支 {len(out) + 1}"),
                "label_origin": str(origin)
                if origin in ("text_statistics", "model_inference")
                else "text_statistics",
                "paper_ids": papers,
                "representative_paper_ids": representatives,
                "evidence_ids": evidence,
                "method": str(method)
                if isinstance(method, str) and method
                else "community_detection",
                "limitations": limitations,
            }
        )
    return out


# --------------------------------------------------------------------------
# optional model polishing (validated, template fallback)
# --------------------------------------------------------------------------

_STATEMENT_KEYS = frozenset({"text", "type", "paper_ids", "evidence_ids", "scope", "limitations"})


def _valid_statement(raw: Any, allowed: set[str]) -> dict | None:
    if not isinstance(raw, Mapping):
        return None
    if {str(key) for key in raw} != set(_STATEMENT_KEYS):
        return None
    text = raw.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    if raw.get("type") not in STATEMENT_TYPES:
        return None
    scope = raw.get("scope")
    if not isinstance(scope, str) or not scope:
        return None
    paper_ids = _string_list(raw.get("paper_ids"))
    evidence_ids = _string_list(raw.get("evidence_ids"))
    limitations = _string_list(raw.get("limitations"))
    if paper_ids is None or evidence_ids is None or limitations is None:
        return None
    if any(pid not in allowed for pid in paper_ids):
        return None
    if any(evidence_id not in allowed for evidence_id in evidence_ids):
        return None
    return {
        "text": text,
        "type": str(raw["type"]),
        "paper_ids": paper_ids,
        "evidence_ids": evidence_ids,
        "scope": scope,
        "limitations": limitations,
    }


def _polish_statements(
    adapter: Any,
    view: Mapping[str, Any],
    base: list[dict],
    coverage: Mapping[str, Any],
    timeline_points: Sequence[Mapping[str, Any]],
    trend_list: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any] | None,
) -> list[dict]:
    """Let the model rewrite the statements; anything invalid falls back."""
    if adapter is None:
        return [dict(statement) for statement in base]
    allowed = {paper["paper_id"] for paper in view["papers"]}
    request = {
        "kind": "overview",
        "schema_version": C.document_version(),
        "snapshot_id": view.get("snapshot_id"),
        "papers": [
            {
                key: paper.get(key)
                for key in ("paper_id", "title", "abstract", "year", "citation_count", "doc_type")
            }
            for paper in view["papers"]
        ],
        "statements": [dict(statement) for statement in base],
        "coverage": dict(coverage),
        "timeline": [dict(point) for point in timeline_points],
        "trends": [dict(trend) for trend in trend_list],
        "allowed_paper_ids": sorted(allowed),
        "allowed_evidence_ids": sorted(allowed),
    }
    try:
        result, _revision = call_model_adapter(
            adapter,
            request,
            timeout_seconds=float(ai_config(config).get("call_budget_seconds") or 0),
            methods=("overview", "generate", "complete", "write"),
        )
    except Exception:  # noqa: BLE001 - a broken model must not blank the page
        return [dict(statement) for statement in base]

    candidate = result.get("statements") if isinstance(result, Mapping) else result
    if not isinstance(candidate, list) or not candidate:
        return [dict(statement) for statement in base]
    validated: list[dict] = []
    for raw in candidate:
        statement = _valid_statement(raw, allowed)
        if statement is None:
            return [dict(statement) for statement in base]
        validated.append(statement)
    return validated


def _recommended_ids(view: Mapping[str, Any], project_ids: Sequence[str]) -> list[str]:
    items = list(view.get("items") or [])
    has_eligibility = any(_get(item, "eligibility") is not None for item in items)
    if has_eligibility:
        return _dedupe(
            [
                paper["paper_id"]
                for paper in view["papers"]
                if str(paper.get("eligibility") or "") == "recommended"
            ]
        )
    return list(project_ids)


def _clean_leads(leads: Any, project_ids: Sequence[str]) -> list[dict]:
    project_set = set(project_ids)
    out: list[dict] = []
    for raw in leads or []:
        paper_ids = [
            pid for pid in _dedupe(_string_list(_get(raw, "paper_ids")) or []) if pid in project_set
        ]
        nearest = [
            pid
            for pid in _dedupe(_string_list(_get(raw, "nearest_work_ids")) or [])
            if pid in project_set
        ]
        queries = _string_list(_get(raw, "next_queries"))
        if _get(raw, "claim_type") != "hypothesis" or queries is None or not queries:
            continue
        status = _get(raw, "verification_status")
        if status not in LEAD_VERIFICATION_STATUSES:
            status = "needs_verification"
        out.append(
            {
                "claim_type": "hypothesis",
                "text": str(_get(raw, "text") or ""),
                "paper_ids": paper_ids,
                "nearest_work_ids": nearest,
                "reason": str(_get(raw, "reason") or ""),
                "coverage_scope": str(_get(raw, "coverage_scope") or _DEFAULT_SCOPE),
                "next_queries": queries,
                "verification_status": status,
            }
        )
    return out


# --------------------------------------------------------------------------
# overview
# --------------------------------------------------------------------------


def build_overview(
    snapshot: Any,
    *,
    branches: Any = None,
    timeline: Sequence[Mapping[str, Any]] | None = None,
    trends: Mapping[str, Any] | None = None,
    leads: Any = None,
    coverage: Mapping[str, Any] | None = None,
    adapter: Any = None,
    config: Mapping[str, Any] | None = None,
) -> dict:
    """The closed ``$defs/Overview`` object, never an empty page."""
    view = _snapshot_view(snapshot)
    papers = view["papers"]
    project_ids = _dedupe([paper.get("paper_id") for paper in papers])

    points = list(timeline) if timeline is not None else _timeline(papers)
    trend_report = trends if trends is not None else _trends(papers)
    if isinstance(trend_report, Mapping):
        trend_status = str(trend_report.get("trend_status") or "insufficient_data")
        trend_list = [dict(trend) for trend in (trend_report.get("trends") or [])]
    else:
        trend_status, trend_list = "insufficient_data", []
    if trend_status not in ("available", "insufficient_data"):
        trend_status = "insufficient_data"

    cov = _coverage_dict(coverage if coverage is not None else view.get("coverage"), len(papers))
    branch_list = _branches(branches, project_ids)
    base_statements = evidence_statements(snapshot, timeline=points, coverage=cov)
    statements = _polish_statements(adapter, view, base_statements, cov, points, trend_list, config)

    if leads is None:
        lead_list = derive_leads(
            snapshot, communities=branches, edges=None, adapter=adapter, config=config
        )
    else:
        lead_list = _clean_leads(leads, project_ids)

    if not papers:
        status = "insufficient_data"
    elif trend_status == "available" and branch_list:
        status = "ready"
    else:
        status = "partial"

    return {
        "snapshot_id": view["snapshot_id"],
        "status": status,
        "statements": statements,
        "leads": lead_list,
        "coverage": cov,
        "branches": branch_list,
        "timeline": [dict(point) for point in points],
        "trend_status": trend_status,
        "trends": trend_list,
        "recommended_paper_ids": _recommended_ids(view, project_ids),
    }
