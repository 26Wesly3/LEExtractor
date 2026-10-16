"""M4 evidence-based overview: templates, branches, leads and adapter fallback.

The AI path is exercised with injected stub adapters only — there is no provider
key and no network access in this suite.  The output is validated against the
real ``$defs/Overview`` contract in ``litsearch/contracts/domain.schema.json``.
"""

from __future__ import annotations

import json

from jsonschema import Draft202012Validator

from litsearch.v32 import contracts as C
from litsearch.v32 import overview as O

OVERVIEW_KEYS = {
    "snapshot_id",
    "status",
    "statements",
    "leads",
    "coverage",
    "branches",
    "timeline",
    "trend_status",
    "trends",
    "recommended_paper_ids",
}

_TEMPLATE_KEYS = {"text", "type", "paper_ids", "evidence_ids", "scope", "limitations"}


def _paper(index, year=2020):
    return {
        "paper_id": f"p{index:02d}",
        "title": f"paper {index} on graph learning",
        "abstract": f"abstract {index}",
        "year": year,
        "citation_count": None,
    }


def _snapshot(papers, *, recommended=5):
    return {
        "snapshot_id": "snap-1",
        "items": [
            {
                "paper_id": paper["paper_id"],
                "eligibility": "recommended" if index < recommended else "exploration",
                "core_score_raw": 0.5,
            }
            for index, paper in enumerate(papers)
        ],
        "papers": papers,
    }


def _rich_snapshot():
    """Six complete years with prior volume ≥ the contract minimum."""
    papers = []
    for year, count in ((2020, 1), (2021, 1), (2022, 3), (2023, 3), (2024, 4), (2025, 5)):
        papers.extend(_paper(len(papers), year) for _ in range(count))
    return _snapshot(papers)


def _overview_schema():
    return {"$ref": "#/$defs/Overview", "$defs": C.domain_schema()["$defs"]}


def _validate(overview):
    Draft202012Validator(_overview_schema()).validate(overview)
    assert set(overview) == OVERVIEW_KEYS
    assert json.dumps(overview, allow_nan=False)
    return overview


def _project_ids(snapshot):
    return {paper["paper_id"] for paper in snapshot["papers"]}


# --------------------------------------------------------------------------
# template path with no AI key
# --------------------------------------------------------------------------


def test_overview_without_ai_key_is_non_empty_and_closed():
    snapshot = _snapshot([_paper(index, 2020 + index % 4) for index in range(8)])
    overview = _validate(O.build_overview(snapshot))

    assert overview["statements"]
    project = _project_ids(snapshot)
    for statement in overview["statements"]:
        assert set(statement) == _TEMPLATE_KEYS
        assert statement["type"] in ("fact", "inference", "hypothesis")
        assert statement["text"].strip()
        assert set(statement["paper_ids"]) <= project
        assert set(statement["evidence_ids"]) <= project
        assert statement["scope"]
        assert isinstance(statement["limitations"], list)
    assert overview["branches"] == []
    assert overview["timeline"]
    assert overview["trends"] == []
    assert overview["leads"] == []
    assert overview["recommended_paper_ids"]
    assert overview["recommended_paper_ids"] == [f"p{index:02d}" for index in range(5)]


def test_empty_snapshot_still_returns_statements_and_arrays():
    overview = _validate(O.build_overview({"snapshot_id": "snap-0", "items": []}))

    assert overview["status"] == "insufficient_data"
    assert overview["statements"]
    assert overview["coverage"]["status"] == "no_results"
    assert overview["branches"] == []
    assert overview["timeline"] == []
    assert overview["trends"] == []
    assert overview["leads"] == []
    assert overview["recommended_paper_ids"] == []


def test_snapshot_without_items_falls_back_to_paper_metadata():
    papers = [_paper(index, 2021 + index) for index in range(4)]
    overview = _validate(O.build_overview({"snapshot_id": "snap-2", "papers": papers}))

    assert overview["statements"]
    assert len(overview["timeline"]) == 4
    assert set(overview["recommended_paper_ids"]) == {paper["paper_id"] for paper in papers}


def test_thin_corpus_reports_insufficient_trend_data():
    snapshot = _snapshot([_paper(index, 2024 + index % 2) for index in range(6)])
    overview = _validate(O.build_overview(snapshot))

    assert overview["trend_status"] == "insufficient_data"
    assert overview["trends"] == []
    assert overview["status"] in ("partial", "insufficient_data")


def test_trends_available_when_the_corpus_supports_it():
    snapshot = _rich_snapshot()
    branches = [
        {"community_id": "c01", "label": "gnn", "paper_ids": ["p00", "p01", "p02"]},
        {"community_id": "c02", "label": "causal", "paper_ids": ["p03", "p04", "p05"]},
    ]
    overview = _validate(O.build_overview(snapshot, branches=branches))

    assert overview["trend_status"] == "available"
    assert overview["trends"]
    assert overview["status"] == "ready"


# --------------------------------------------------------------------------
# branches and leads
# --------------------------------------------------------------------------


def test_branch_representatives_belong_to_their_branch_and_references_exist():
    snapshot = _snapshot([_paper(index) for index in range(6)])
    branches = [
        {
            "branch_id": "b1",
            "label": "branch one",
            "label_origin": "made_up",
            "paper_ids": ["p00", "p01", "p99"],
            "representative_paper_ids": ["p01", "p05", "p99"],
            "evidence_ids": ["p04"],
            "limitations": ["limited"],
        }
    ]
    overview = _validate(O.build_overview(snapshot, branches=branches))

    branch = overview["branches"][0]
    assert branch["paper_ids"] == ["p00", "p01"]
    assert branch["representative_paper_ids"] == ["p01"]  # p05 is not in the branch
    assert branch["label_origin"] == "text_statistics"
    assert set(branch["evidence_ids"]) <= _project_ids(snapshot)
    assert set(branch["paper_ids"]) <= _project_ids(snapshot)


def test_leads_are_empty_when_evidence_is_insufficient():
    snapshot = _snapshot([_paper(index) for index in range(6)])
    assert O.derive_leads(snapshot, communities=None) == []
    assert O.build_overview(snapshot)["leads"] == []

    one_branch = [{"community_id": "c01", "label": "only", "paper_ids": ["p00", "p01", "p02"]}]
    assert O.derive_leads(snapshot, communities=one_branch) == []
    thin = [
        {"community_id": "c01", "label": "a", "paper_ids": ["p00"]},
        {"community_id": "c02", "label": "b", "paper_ids": ["p01"]},
    ]
    assert O.derive_leads(snapshot, communities=thin) == []


def test_leads_from_two_evidenced_branches_are_checkable_hypotheses():
    snapshot = _snapshot([_paper(index) for index in range(8)])
    branches = [
        {"community_id": "c01", "label": "gnn", "paper_ids": ["p00", "p01", "p02"]},
        {"community_id": "c02", "label": "causal inference", "paper_ids": ["p03", "p04", "p05"]},
    ]
    leads = O.derive_leads(snapshot, communities=branches)

    assert leads
    project = _project_ids(snapshot)
    for lead in leads:
        assert lead["claim_type"] == "hypothesis"
        assert lead["verification_status"] == "needs_verification"
        assert lead["next_queries"]
        assert set(lead["paper_ids"]) <= project
        assert set(lead["nearest_work_ids"]) <= project
        assert "少量引文不构成研究空白" in lead["reason"]

    overview = _validate(O.build_overview(snapshot, branches=branches))
    assert overview["leads"] == leads


def test_leads_are_checked_when_a_search_hook_runs():
    snapshot = _snapshot([_paper(index) for index in range(8)])

    class Searching:
        def __init__(self):
            self.queries = []

        def search(self, query):
            self.queries.append(query)
            return []

    adapter = Searching()
    leads = O.derive_leads(
        snapshot,
        communities=[
            {"community_id": "c01", "label": "gnn", "paper_ids": ["p00", "p01", "p02"]},
            {"community_id": "c02", "label": "causal", "paper_ids": ["p03", "p04", "p05"]},
        ],
        adapter=adapter,
    )
    assert leads and adapter.queries
    assert all(lead["verification_status"] == "checked_in_current_sources" for lead in leads)


def test_provided_leads_are_sanitised():
    snapshot = _snapshot([_paper(index) for index in range(4)])
    leads = [
        {
            "claim_type": "fact",
            "text": "not a hypothesis",
            "paper_ids": ["p00"],
            "nearest_work_ids": [],
            "reason": "",
            "coverage_scope": "scope",
            "next_queries": ["q"],
            "verification_status": "checked_in_current_sources",
        },
        {
            "claim_type": "hypothesis",
            "text": "hypothesis text",
            "paper_ids": ["p00", "p-unknown"],
            "nearest_work_ids": ["p01", "p-unknown"],
            "reason": "reason",
            "coverage_scope": "scope",
            "next_queries": ["query"],
            "verification_status": "something_else",
        },
    ]
    overview = _validate(O.build_overview(snapshot, leads=leads))

    assert len(overview["leads"]) == 1
    lead = overview["leads"][0]
    assert lead["paper_ids"] == ["p00"]
    assert lead["nearest_work_ids"] == ["p01"]
    assert lead["verification_status"] == "needs_verification"


# --------------------------------------------------------------------------
# injected adapter: validated, template fallback
# --------------------------------------------------------------------------


class StubAdapter:
    model_revision = "stub-1"

    def __init__(self, statements):
        self.statements = statements
        self.calls = []

    def overview(self, request):
        self.calls.append(request)
        return {"statements": self.statements}


def _statement(**overrides):
    statement = {
        "text": "模型在给定证据上的总结。",
        "type": "inference",
        "paper_ids": ["p00"],
        "evidence_ids": [],
        "scope": "当前检索集",
        "limitations": ["仅基于给定摘要"],
    }
    statement.update(overrides)
    return statement


def test_fabricated_evidence_id_from_the_adapter_is_rejected():
    snapshot = _snapshot([_paper(index) for index in range(4)])
    adapter = StubAdapter([_statement(evidence_ids=["evidence-does-not-exist"], text="捏造的结论")])

    overview = _validate(O.build_overview(snapshot, adapter=adapter))

    templates = O.evidence_statements(snapshot)
    assert overview["statements"] == templates
    assert "捏造的结论" not in json.dumps(overview, ensure_ascii=False)


def test_fabricated_paper_id_from_the_adapter_is_rejected():
    snapshot = _snapshot([_paper(index) for index in range(4)])
    adapter = StubAdapter([_statement(paper_ids=["p-unknown"])])

    overview = _validate(O.build_overview(snapshot, adapter=adapter))

    assert overview["statements"] == O.evidence_statements(snapshot)


def test_adapter_may_only_write_over_given_evidence():
    snapshot = _snapshot([_paper(index) for index in range(4)])
    statement = _statement()
    adapter = StubAdapter([statement])

    overview = _validate(O.build_overview(snapshot, adapter=adapter))

    assert overview["statements"] == [statement]
    request = adapter.calls[0]
    assert request["kind"] == "overview"
    assert request["allowed_paper_ids"] == sorted(_project_ids(snapshot))
    assert request["allowed_evidence_ids"] == sorted(_project_ids(snapshot))


def test_adapter_failure_falls_back_to_templates():
    snapshot = _snapshot([_paper(index) for index in range(4)])

    class Broken:
        def overview(self, request):
            raise RuntimeError("timeout")

    overview = _validate(O.build_overview(snapshot, adapter=Broken()))

    assert overview["statements"] == O.evidence_statements(snapshot)


def test_adapter_returning_an_empty_or_malformed_page_falls_back():
    snapshot = _snapshot([_paper(index) for index in range(4)])
    templates = O.evidence_statements(snapshot)

    assert O.build_overview(snapshot, adapter=StubAdapter([]))["statements"] == templates
    assert (
        O.build_overview(snapshot, adapter=StubAdapter([{"text": "only text"}]))["statements"]
        == templates
    )
