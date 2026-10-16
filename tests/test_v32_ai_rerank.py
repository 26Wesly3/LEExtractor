"""M3 AI local rerank: window, validation, merge and honest degradation.

No network and no model: every AI path is driven by an injected adapter, and
the real-provider path is reported as blocked rather than faked.  The contract
schema is loaded from ``litsearch/contracts/ai_rerank.schema.json`` through
:mod:`litsearch.v32.contracts`, and every budget comes from
``contracts/defaults.json``.
"""

from __future__ import annotations

import copy
import json

import pytest

from litsearch.v32 import ai_rerank as A
from litsearch.v32 import contracts as C

SCHEMA_VERSION = C.ai_rerank_schema()["properties"]["schema_version"]["const"]
CONTEXT_HASH = "ctx-1"


class SpyAdapter:
    """Counts calls and replays queued answers; never touches the network."""

    def __init__(self, answers=None, model_revision="spy-1"):
        self.answers = list(answers or [])
        self.calls = []
        self.model_revision = model_revision

    def rerank(self, request):
        self.calls.append(request)
        if self.answers:
            return self.answers.pop(0)
        return {"malformed": True}


def _inputs(ids, *, abstract=None, year=2020, title="title", doc_type="article"):
    return [
        A.RerankInput(pid, title=f"{title} {pid}", abstract=abstract, year=year, doc_type=doc_type)
        for pid in ids
    ]


def _window(ids=("p-a", "p-b", "p-c"), *, context_hash=CONTEXT_HASH, config=None, abstract=None):
    return A.build_window(
        _inputs(list(ids), abstract=abstract),
        context_hash=context_hash,
        intent="找到图神经网络在医疗影像上的代表工作",
        judgements_summary="core=1, not_relevant=0",
        parent_snapshot_id="snap-1",
        config=config,
    )


def _valid_raw(ids=("p-a", "p-b", "p-c"), *, context_hash=CONTEXT_HASH):
    return {
        "schema_version": SCHEMA_VERSION,
        "context_hash": context_hash,
        "ordered_paper_ids": list(ids),
        "judgments": [
            {
                "paper_id": pid,
                "priority_score": 3,
                "direct_relevance": 3,
                "method_relevance": 2,
                "role": "representative",
                "reason": "基于给定摘要支持当前任务。",
                "evidence_ids": [],
            }
            for pid in ids
        ],
    }


# --------------------------------------------------------------------------
# the shipped fixtures
# --------------------------------------------------------------------------


def test_valid_fixture_is_accepted():
    window = _window(("p-a", "p-b"), context_hash="example-context-hash")
    ok, error_code, parsed = A.validate_output(C.ai_valid_fixture(), window)
    assert ok is True
    assert error_code == ""
    assert parsed == C.ai_valid_fixture()


def test_semantic_fixture_merge_order_is_reproduced():
    cases = C.ai_semantic_cases()
    window = A.build_window(
        _inputs(cases["input_ids"]),
        context_hash=cases["context_hash"],
        intent="i",
        judgements_summary="",
        parent_snapshot_id="snap-1",
    )
    ok, _, _ = A.validate_output(C.ai_valid_fixture(), window)
    assert ok is True
    merged = A.apply_window(cases["outer_order"], cases["valid_order"], parent_snapshot_id="snap-1")
    assert merged == cases["expected_merged_order"]
    for invalid in cases["invalid_orders"]:
        assert (
            A.apply_window(cases["outer_order"], invalid, parent_snapshot_id="snap-1")
            == cases["outer_order"]
        )


# --------------------------------------------------------------------------
# schema + semantic rejection: the whole batch is rejected
# --------------------------------------------------------------------------


def _corrupt(name, raw):
    if name == "duplicate_order":
        raw["ordered_paper_ids"] = [raw["ordered_paper_ids"][0], raw["ordered_paper_ids"][0]]
    elif name == "unknown_order":
        raw["ordered_paper_ids"] = [raw["ordered_paper_ids"][0], "p-z"]
    elif name == "missing_order":
        raw["ordered_paper_ids"] = raw["ordered_paper_ids"][:2]
        raw["judgments"] = raw["judgments"][:2]
    elif name == "wrong_context_hash":
        raw["context_hash"] = "some-other-hash"
    elif name == "score_out_of_range":
        raw["judgments"][0]["priority_score"] = 5
    elif name == "unknown_role":
        raw["judgments"][0]["role"] = "expert"
    elif name == "extra_top_property":
        raw["model_revision"] = "forged"
    elif name == "extra_judgment_property":
        raw["judgments"][0]["confidence"] = 0.99
    elif name == "duplicate_judgment":
        raw["judgments"] = [raw["judgments"][0], raw["judgments"][0], raw["judgments"][2]]
    elif name == "missing_judgment":
        raw["judgments"] = raw["judgments"][:2]
    elif name == "fabricated_evidence":
        raw["judgments"][0]["evidence_ids"] = ["p-z"]
    else:  # pragma: no cover - test table is fixed
        raise AssertionError(name)
    return raw


@pytest.mark.parametrize(
    ("name", "ids", "expected_code"),
    [
        ("duplicate_order", ("p-a", "p-b"), A.ERR_SCHEMA),
        ("unknown_order", ("p-a", "p-b"), A.ERR_UNKNOWN_ID),
        ("missing_order", ("p-a", "p-b", "p-c"), A.ERR_MISSING_ID),
        ("wrong_context_hash", ("p-a", "p-b"), A.ERR_CONTEXT_HASH),
        ("score_out_of_range", ("p-a", "p-b"), A.ERR_SCHEMA),
        ("unknown_role", ("p-a", "p-b"), A.ERR_SCHEMA),
        ("extra_top_property", ("p-a", "p-b"), A.ERR_SCHEMA),
        ("extra_judgment_property", ("p-a", "p-b"), A.ERR_SCHEMA),
        ("duplicate_judgment", ("p-a", "p-b", "p-c"), A.ERR_DUPLICATE_JUDGMENT),
        ("missing_judgment", ("p-a", "p-b", "p-c"), A.ERR_MISSING_JUDGMENT),
        ("fabricated_evidence", ("p-a", "p-b"), A.ERR_EVIDENCE),
    ],
)
def test_invalid_output_rejects_whole_batch_and_keeps_local_order(name, ids, expected_code):
    window = _window(ids)
    raw = _corrupt(name, copy.deepcopy(_valid_raw(ids)))

    ok, error_code, parsed = A.validate_output(raw, window)
    assert ok is False, name
    assert error_code == expected_code, name
    assert parsed is None

    outcome = A.rerank(window, SpyAdapter([raw]), parent_snapshot_id="snap-1")
    assert outcome.status == "rejected", name
    assert outcome.error_code == expected_code, name
    assert outcome.ordered_paper_ids == list(window.ordered_paper_ids), name
    assert outcome.rank_stage == "local_enhanced", name


def test_accepting_adapter_output_never_retries():
    spy = SpyAdapter([_valid_raw(("p-a", "p-b"))])
    window = _window(("p-a", "p-b"))
    outcome = A.rerank(window, spy, parent_snapshot_id="snap-1")
    assert outcome.status == "applied"
    assert len(spy.calls) == 1


# --------------------------------------------------------------------------
# window construction
# --------------------------------------------------------------------------


def test_fewer_than_minimum_skips_without_calling_the_adapter():
    spy = SpyAdapter([_valid_raw(("p-a", "p-b"))])
    window = _window(("p-a",))
    assert len(window.ordered_paper_ids) < int(C.ai_defaults()["minimum_n"])
    outcome = A.rerank(window, spy, parent_snapshot_id="snap-1")
    assert outcome.status == "skipped"
    assert spy.calls == []
    assert outcome.ordered_paper_ids == ["p-a"]


def test_window_caps_at_top_n_and_preserves_snapshot_order():
    top_n = int(C.ai_defaults()["top_n"])
    ids = [f"p{i:02d}" for i in range(top_n + 10)]
    window = _window(tuple(ids), abstract="short abstract")
    assert len(window.ordered_paper_ids) == top_n
    assert window.ordered_paper_ids == ids[:top_n]
    assert window.dropped_ids == ids[top_n:]
    assert window.truncated is True
    assert [paper["paper_id"] for paper in window.payload] == ids[:top_n]


def test_abstract_is_capped_at_the_contract_limit():
    cap = int(C.ai_defaults()["abstract_max_chars"])
    window = _window(("p-a", "p-b"), abstract="x" * (cap + 3000))
    assert window.payload
    for paper in window.payload:
        assert len(paper["abstract"]) == cap
        assert set(paper) == {"paper_id", "title", "abstract", "year", "doc_type"}


def test_unknown_year_is_null_not_zero():
    items = [
        A.RerankInput("p-a", title="t", abstract=None, year=None, doc_type=None),
        A.RerankInput("p-b", title="t2", abstract=None, year=None, doc_type=None),
    ]
    window = A.build_window(
        items,
        context_hash=CONTEXT_HASH,
        intent="i",
        judgements_summary="",
        parent_snapshot_id="snap-1",
    )
    assert len(window.payload) == 2
    for paper in window.payload:
        assert paper["year"] is None
        assert paper["abstract"] is None
        assert paper["doc_type"] is None


def test_over_budget_shrinks_abstracts_first_then_n():
    budget = 500
    floor = int(C.ai_defaults()["abstract_min_chars"])
    cap = int(C.ai_defaults()["abstract_max_chars"])
    window = A.build_window(
        _inputs([f"p{i}" for i in range(5)], abstract="x" * cap),
        context_hash=CONTEXT_HASH,
        intent="i",
        judgements_summary="",
        parent_snapshot_id="snap-1",
        config={"ai": {"input_token_budget": budget}},
    )
    assert 2 <= len(window.ordered_paper_ids) < 5
    assert window.truncated is True
    assert window.estimated_tokens <= budget
    for paper in window.payload:
        assert floor <= len(paper["abstract"]) < cap


def test_over_budget_below_minimum_n_cancels_the_call():
    spy = SpyAdapter([_valid_raw(("p-a", "p-b"))])
    window = A.build_window(
        _inputs(
            [f"p{i}" for i in range(6)], abstract="x" * int(C.ai_defaults()["abstract_max_chars"])
        ),
        context_hash=CONTEXT_HASH,
        intent="i",
        judgements_summary="",
        parent_snapshot_id="snap-1",
        config={"ai": {"input_token_budget": 20}},
    )
    assert window.ordered_paper_ids == []
    assert window.truncated is True
    assert window.dropped_ids
    outcome = A.rerank(window, spy, parent_snapshot_id="snap-1")
    assert outcome.status == "skipped"
    assert spy.calls == []


def test_token_estimate_is_deterministic_and_conservative():
    first = _window(("p-a", "p-b"), abstract="汉字与 english words")
    second = _window(("p-a", "p-b"), abstract="汉字与 english words")
    assert first.estimated_tokens == second.estimated_tokens
    assert first.estimated_tokens > 0
    assert json.dumps(first.payload, ensure_ascii=False, sort_keys=True) == json.dumps(
        second.payload, ensure_ascii=False, sort_keys=True
    )
    # Conservative: at most three bytes are charged per estimated token.
    assert A.estimate_tokens("汉字") == A.estimate_tokens("汉字")
    assert A.estimate_tokens("") == 0
    assert A.estimate_tokens("abc") == 1
    assert A.estimate_tokens("汉字") >= 2


# --------------------------------------------------------------------------
# merging and rerank outcomes
# --------------------------------------------------------------------------


def test_apply_window_keeps_out_of_window_order_and_scores_untouched():
    local = ["p-a", "p-b", "p-c", "p-d", "p-e"]
    scores = {pid: index / 10.0 for index, pid in enumerate(local)}
    snapshot = list(local)

    merged = A.apply_window(local, ["p-b", "p-a"], parent_snapshot_id="snap-1")

    assert merged == ["p-b", "p-a", "p-c", "p-d", "p-e"]
    assert local == snapshot  # the caller's list is not mutated
    assert sorted(merged) == sorted(local)  # ids are permuted, never added
    assert [scores[pid] for pid in merged] == [
        scores[pid] for pid in ["p-b", "p-a", "p-c", "p-d", "p-e"]
    ]


def test_applied_outcome_exposes_ai_refined_stage_and_request_context():
    ids = ("p-a", "p-b")
    adapter = SpyAdapter([{"output": _valid_raw(ids), "model_revision": "model-7"}])
    window = _window(ids)
    outcome = A.rerank(window, adapter, parent_snapshot_id="snap-1")

    assert outcome.status == "applied"
    assert outcome.ordered_paper_ids == ["p-a", "p-b"]
    assert outcome.rank_stage == "ai_refined"
    assert outcome.model_revision == "model-7"
    assert outcome.error_code is None
    assert len(outcome.judgments) == 2

    request = adapter.calls[0]
    assert request["context_hash"] == CONTEXT_HASH
    assert request["parent_snapshot_id"] == "snap-1"
    assert request["allowed_paper_ids"] == list(ids)
    assert request["response_schema"]["additionalProperties"] is False
    assert request["timeout_seconds"] == float(C.ai_defaults()["call_budget_seconds"])


def test_model_roles_are_inferences_and_never_a_probability():
    ids = ("p-a", "p-b")
    window = _window(ids)
    outcome = A.rerank(window, SpyAdapter([_valid_raw(ids)]), parent_snapshot_id="snap-1")

    roles = A.judgment_roles(outcome)
    assert set(roles) == set(ids)
    for paper_id, assignment in roles.items():
        assert assignment["role"] == "representative", paper_id
        assert assignment["role_origin"] == "model_inference"
        assert set(assignment) == {"role", "role_origin", "priority_score"}
        assert assignment["priority_score"] in (0, 1, 2, 3, 4)
    # A rejected batch contributes no display roles at all.
    rejected = A.rerank(window, SpyAdapter([{"bad": True}]), parent_snapshot_id="snap-1")
    assert A.judgment_roles(rejected) == {}


def test_default_path_without_ai_key_is_skipped_and_local_order_stands():
    window = _window(("p-a", "p-b"))
    outcome = A.rerank(window, None, parent_snapshot_id="snap-1")
    assert outcome.status == "skipped"
    assert outcome.error_code == A.ERR_NOT_CONFIGURED
    assert outcome.ordered_paper_ids == ["p-a", "p-b"]
    assert outcome.rank_stage == "local_enhanced"


def test_adapter_exception_is_failed_and_local_order_stands():
    class Broken:
        model_revision = "broken-1"

        def rerank(self, request):
            raise RuntimeError("provider exploded")

    window = _window(("p-a", "p-b"))
    outcome = A.rerank(window, Broken(), parent_snapshot_id="snap-1")
    assert outcome.status == "failed"
    assert outcome.error_code == A.ERR_ADAPTER
    assert outcome.ordered_paper_ids == ["p-a", "p-b"]
    assert outcome.rank_stage == "local_enhanced"


def test_non_json_answer_is_rejected():
    window = _window(("p-a", "p-b"))
    outcome = A.rerank(window, SpyAdapter(["not json at all"]), parent_snapshot_id="snap-1")
    assert outcome.status == "rejected"
    assert outcome.error_code == A.ERR_INVALID_JSON


def test_no_nan_or_infinity_escapes_any_window_or_outcome():
    window = _window(("p-a", "p-b"), abstract="x" * 50)
    payload = {
        "window": window.payload,
        "ids": window.ordered_paper_ids,
        "estimated_tokens": window.estimated_tokens,
        "dropped": window.dropped_ids,
    }
    assert json.dumps(payload, allow_nan=False)
    outcome = A.rerank(window, None, parent_snapshot_id="snap-1")
    assert json.dumps({"status": outcome.status, "ids": outcome.ordered_paper_ids}, allow_nan=False)


def test_validate_output_accepts_an_explicit_schema_argument():
    window = _window(("p-a", "p-b"))
    ok, error_code, parsed = A.validate_output(
        _valid_raw(("p-a", "p-b")), window, schema=C.ai_rerank_schema()
    )
    assert ok and error_code == "" and parsed is not None

    class NotAnObject:
        pass

    ok, error_code, _ = A.validate_output(NotAnObject(), window)
    assert ok is False
    assert error_code == A.ERR_INVALID_JSON
