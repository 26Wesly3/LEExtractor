"""M0: the v3.2 machine contracts load, and the fixtures are the fixed ones.

These tests exist because the specification makes ``contracts/`` authoritative
("字段、枚举、默认值以 contracts/ 为准", START_HERE.md).  If a contract file is
missing from the installed package, or its internal invariants drift, the whole
v3.2 layer silently falls back to whatever Python literals it happens to carry —
which is exactly the failure mode this guards against.

No network, no product code: only the contract loader.
"""

from __future__ import annotations

import json

import pytest

from litsearch.v32 import CONTRACT_VERSION
from litsearch.v32 import contracts as C


def test_contract_files_ship_inside_the_installed_package():
    """The contracts must live next to the module, not only in the source tree."""
    assert C.CONTRACTS_DIR.is_dir()
    for name in (
        "defaults.json",
        "job_states.json",
        "access_links.json",
        "domain.schema.json",
        "ai_rerank.schema.json",
        "openapi.json",
    ):
        assert (C.CONTRACTS_DIR / name).is_file(), name


def test_validate_contracts_reports_the_expected_shape():
    report = C.validate_contracts()
    assert report["document_version"] == "3.2"
    assert report["document_version"] == CONTRACT_VERSION
    assert report["weights_sum"] == 1.0
    assert report["job_states"] == 9
    assert report["access_links"] == 3
    assert report["domain_definitions"] > 25


def test_defaults_are_the_documented_engineering_starting_points():
    ranking = C.ranking()
    assert ranking["rrf_k"] == 60
    assert C.weights() == {
        "topic": 0.6,
        "impact": 0.1,
        "graph": 0.1,
        "seed": 0.1,
        "feedback": 0.1,
    }
    assert ranking["topic_gate_fraction"] == 0.6
    assert ranking["missing_rank"] == "midpoint"
    assert ranking["tie_method"] == "average"
    assert ranking["tie_round_decimals"] == 12
    assert ranking["starter_size"] == 10
    assert ranking["starter_pool"] == 50
    assert ranking["mmr_lambda"] == 0.75
    assert ranking["pagerank_alpha"] == 0.85

    pdf = C.pdf_defaults()
    assert pdf["batch_max_files"] == 10
    assert pdf["file_max_mib"] == 20
    assert pdf["batch_max_mib"] == 100
    assert pdf["ocr_enabled"] is False
    assert pdf["match_weights"] == {"title": 0.75, "authors": 0.15, "year": 0.1}
    assert pdf["auto_match_score"] == 0.9
    assert pdf["minimum_match_margin"] == 0.1

    ai = C.ai_defaults()
    assert ai["enabled_by_default"] is False
    assert ai["top_n"] == 30
    assert ai["minimum_n"] == 2
    assert ai["merge_strategy"] == "permute_window_only"
    assert ai["abstract_max_chars"] == 2000
    assert ai["abstract_min_chars"] == 256
    assert ai["input_token_budget"] == 12000

    discovery = C.discovery()
    assert discovery["candidate_cap"] == 300
    assert discovery["auto_rounds"] == 1
    assert discovery["round_budget_seconds"] == 45
    assert discovery["provider_concurrency"] == 3
    assert discovery["query_variants"] == 3

    assert C.credentials_defaults()["return_secret"] is False
    assert C.access_defaults() == {
        "application_sso_enabled": False,
        "federation_sp_enabled": False,
        "external_resource_links_enabled": True,
    }
    assert C.zotero_defaults()["upload_fulltext_by_default"] is False
    assert C.runtime_defaults()["default_mode"] == "server_basic"


def test_job_state_machine_matches_the_contract():
    assert C.initial_state() == "queued"
    assert C.can_transition("queued", "running")
    assert C.can_transition("running", "cancel_requested")
    assert C.can_transition("cancel_requested", "cancelled")
    assert C.can_transition("running", "superseded")
    assert C.can_transition("running", "interrupted")
    # Terminal states are immutable: no outgoing edges at all.
    for state in C.terminal_states():
        assert C.job_states()["transitions"][state] == []
        assert not C.can_transition(state, "running")
    # A queued job may be cancelled or superseded before it ever starts.
    assert C.can_transition("queued", "cancelled")
    assert C.can_transition("queued", "superseded")
    # finished is a stage, never a state.
    assert "finished" not in C.job_states()["transitions"]
    with pytest.raises(C.ContractError):
        C.assert_transition("succeeded", "running")
    with pytest.raises(C.ContractError):
        C.assert_transition("queued", "succeeded")


def test_access_registry_is_the_fixed_official_entry_set():
    registry = C.access_link_registry()
    assert set(registry) == {"carsi", "zju_library", "zju_sciencedirect"}
    for entry_id, entry in registry.items():
        assert entry["url"].startswith("https://"), entry_id
        assert entry["verified"] is True
        assert entry["source_url"].startswith("https://")
        assert entry["kind"] == "institution_link"
    # The deferred decisions are pinned in the same contract file.
    assert C.access_defaults()["application_sso_enabled"] is False
    cas = registry["carsi"]
    assert cas["auth_required"] is True
    assert cas["availability"] == "entry_only"
    assert registry["zju_library"]["auth_required"] is False


def test_ranking_fixture_is_the_one_the_algorithm_must_satisfy():
    cases = {case["id"]: case for case in C.ranking_cases()}
    assert set(cases) == {
        "rank_topic_beats_citation",
        "rank_missing_is_neutral",
        "rank_tie_stable",
        "rank_no_distinct_signal",
        "rank_single",
    }
    # AC-06 names this fixture explicitly ("结果符合 ranking_cases.json").
    assert cases["rank_topic_beats_citation"]["expected_raw_expressions"]["a"] == (
        "(0.6/61+0.1/63)/0.7"
    )
    assert cases["rank_topic_beats_citation"]["expected_recommended"] == ["a", "b"]


def test_acceptance_fixture_covers_every_case_and_defers_exactly_one():
    cases = {case["id"]: case for case in C.acceptance_cases()}
    assert len(cases) == 32
    assert set(cases) == {f"AC-{n:02d}" for n in range(1, 33)}
    deferred = {cid for cid, case in cases.items() if case["status"] == "deferred"}
    assert deferred == {"AC-29"}
    required = {cid for cid, case in cases.items() if case["required_this_release"]}
    assert deferred.isdisjoint(required)
    assert len(required) == 31
    for case in cases.values():
        assert case["expected"], case["id"]


def test_ai_output_schema_is_closed_and_bounded():
    schema = C.ai_rerank_schema()
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == "3.2"
    assert schema["required"] == [
        "schema_version",
        "context_hash",
        "ordered_paper_ids",
        "judgments",
    ]
    order = schema["properties"]["ordered_paper_ids"]
    assert order["uniqueItems"] is True
    assert order["minItems"] == 2
    judgment = schema["properties"]["judgments"]["items"]
    assert judgment["additionalProperties"] is False
    assert judgment["properties"]["priority_score"] == {
        "type": "integer",
        "minimum": 0,
        "maximum": 4,
    }
    assert judgment["properties"]["role"]["enum"][-1] == "unknown"


def test_domain_schema_declares_the_v32_objects():
    definitions = C.domain_schema()["$defs"]
    for name in (
        "Paper",
        "ProjectPaper",
        "ResearchAnchor",
        "Judgement",
        "EvidenceEdge",
        "RankingSnapshot",
        "ImportItem",
        "Job",
        "AccessOption",
        "VenueMetric",
        "Overview",
        "Lead",
        "Branch",
        "TimelinePoint",
        "Trend",
        "Coverage",
        "Capability",
        "RankedPaper",
        "Breakdown",
    ):
        assert name in definitions, name
    # Contract rule: the domain objects are closed.
    assert definitions["RankedPaper"]["additionalProperties"] is False
    assert definitions["Overview"]["additionalProperties"] is False


def test_openapi_declares_the_25_incremental_operations():
    paths = C.openapi_schema()["paths"]
    operations = [
        (method, path)
        for path, item in paths.items()
        for method in item
        if method in {"get", "post", "put", "delete", "patch"}
    ]
    assert len(operations) == 25
    assert ("post", "/api/projects/{pid}/judgements") in operations
    assert ("put", "/api/settings/credentials/{provider_id}") in operations


def test_contract_accessors_hand_out_copies_not_shared_state():
    """A caller that mutates a returned document must not poison later readers."""
    first = C.defaults()
    first["ranking"]["rrf_k"] = 1
    assert C.ranking()["rrf_k"] == 60

    weights = C.weights()
    weights["topic"] = 99.0
    assert C.weights()["topic"] == 0.6
    assert C.validate_contracts()["weights_sum"] == 1.0

    links = C.access_links()
    links["links"].clear()
    assert len(C.access_link_registry()) == 3


def test_fixtures_are_loaded_from_the_repository_fixtures_directory():
    assert C.FIXTURES_DIR.is_dir()
    raw = json.loads((C.FIXTURES_DIR / "ranking_cases.json").read_text(encoding="utf-8"))
    assert len(raw["cases"]) == len(C.ranking_cases())
    with pytest.raises(C.ContractError):
        C.fixture("does_not_exist.json")
