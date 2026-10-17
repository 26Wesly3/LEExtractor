"""M2 acceptance tests: the CoreRank RRF engine (doc 04, AC-06).

The five cases in ``fixtures/ranking_cases.json`` are the acceptance oracle and
are driven end to end: expected order, expected recommendation, the raw fusion
expressions parsed as arithmetic, the per-signal ranks and the "all scores
null" cases.  Everything else guards the rules the fixture cannot show --
null-vs-zero, inactive signals, the topic gate, ordering independence, graph
degeneracy and the starter list.

No network, no product code outside ``litsearch.v32``.
"""

from __future__ import annotations

import ast
import json
import math

import pytest

from litsearch.v32 import contracts as C
from litsearch.v32.core_rank import (
    SIGNAL_ORDER,
    CandidateSignals,
    age_adjusted_impact,
    core_rank,
    pagerank_signal,
    starter_list,
)

CASES = {case["id"]: case for case in C.ranking_cases()}


def _candidate(paper_id, spec):
    if spec is None or isinstance(spec, (int, float)):
        spec = {"topic": spec}
    return CandidateSignals(
        paper_id,
        topic=spec.get("topic"),
        impact=spec.get("impact"),
        graph=spec.get("graph"),
        seed=spec.get("seed"),
        feedback=spec.get("feedback"),
        excluded=bool(spec.get("excluded", False)),
        user_anchor=bool(spec.get("user_anchor", False)),
    )


def _candidates(payload):
    return [_candidate(pid, spec) for pid, spec in payload.items()]


def _eval_arith(expression: str) -> float:
    """Evaluate the fixture's arithmetic without ``eval``."""
    tree = ast.parse(expression, mode="eval")

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            value = walk(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.BinOp):
            left, right = walk(node.left), walk(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
        raise ValueError(f"unsupported expression: {expression!r}")

    return walk(tree)


def _breakdown(item, signal):
    for entry in item.breakdown:
        if entry.signal == signal:
            return entry
    raise AssertionError(f"no breakdown for {signal!r}")


def _no_non_finite(payload) -> None:
    if isinstance(payload, dict):
        for value in payload.values():
            _no_non_finite(value)
    elif isinstance(payload, list):
        for value in payload:
            _no_non_finite(value)
    elif isinstance(payload, float):
        assert math.isfinite(payload), "NaN/Infinity escaped into the result"


# --------------------------------------------------------------------------
# the fixed cases (AC-06)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_ranking_fixture_case(case_id):
    case = CASES[case_id]
    result = core_rank(_candidates(case["input"]))

    assert [item.paper_id for item in result.items] == case["expected_order"]

    if "expected_recommended" in case:
        assert result.recommended_paper_ids == case["expected_recommended"]

    for paper_id, expression in (case.get("expected_raw_expressions") or {}).items():
        item = result.by_id(paper_id)
        assert item is not None, paper_id
        assert item.core_score_raw == pytest.approx(_eval_arith(expression), abs=1e-12)

    for signal, expected in (case.get("expected_signal_ranks") or {}).items():
        for paper_id, expected_rank in expected.items():
            entry = _breakdown(result.by_id(paper_id), signal)
            assert entry.rank == pytest.approx(expected_rank, abs=1e-12)
            assert entry.available is True

    if case.get("expected_all_scores_null"):
        for item in result.items:
            assert item.core_score_raw is None
            assert item.core_score is None


def test_input_order_never_changes_the_result():
    for case_id, case in CASES.items():
        forward = core_rank(_candidates(case["input"]))
        backward = core_rank(_candidates(dict(reversed(list(case["input"].items())))))
        assert forward.to_dict() == backward.to_dict(), case_id


def test_expected_raw_expressions_are_the_contract_k_and_weights():
    """The fixture's arithmetic must be reproducible from the contract alone."""
    rrf_k = C.ranking()["rrf_k"]
    weights = C.weights()
    assert rrf_k == 60
    value = _eval_arith("(0.6/61+0.1/63)/0.7")
    manual = (weights["topic"] / (rrf_k + 1) + weights["impact"] / (rrf_k + 3)) / (
        weights["topic"] + weights["impact"]
    )
    assert value == pytest.approx(manual, abs=1e-15)


# --------------------------------------------------------------------------
# null vs zero, inactive signals, no NaN
# --------------------------------------------------------------------------


def test_unknown_values_are_null_not_zero_and_the_result_is_json_safe():
    candidates = [
        CandidateSignals("p1", topic=0.9),
        CandidateSignals("p2", topic=0.4, impact=3.0),
        CandidateSignals("p3", topic=0.2, impact=7.0),
    ]
    result = core_rank(candidates, as_of_year=2024)

    unknown = _breakdown(result.by_id("p1"), "impact")
    assert unknown.raw_value is None, "an unknown citation signal must not become 0"
    assert unknown.available is True
    assert unknown.rank == pytest.approx((3 + 1) / 2), "missing value uses the neutral rank"
    assert unknown.contribution is not None, "a known-active signal still contributes at midpoint"

    payload = result.to_dict()
    text = json.dumps(payload, allow_nan=False)
    assert "NaN" not in text and "Infinity" not in text
    _no_non_finite(payload)


def test_missing_abstract_and_unknown_metadata_stay_null():
    """A missing abstract/topic is an unknown signal, never a zero."""
    candidates = [
        CandidateSignals("with_topic", topic=0.8),
        CandidateSignals(
            "no_abstract", topic=None, impact=None, graph=None, seed=None, feedback=None
        ),
    ]
    result = core_rank(candidates)
    bare = result.by_id("no_abstract")
    assert all(entry.raw_value is None for entry in bare.breakdown)
    assert all(entry.raw_value != 0.0 for entry in result.by_id("with_topic").breakdown)
    assert bare.core_score_raw is None and bare.core_score is None
    json.dumps(result.to_dict(), allow_nan=False)


def test_empty_candidate_set_is_safe():
    result = core_rank([])
    assert result.items == []
    assert result.recommended_paper_ids == []
    assert result.starter_paper_ids == []
    assert result.coverage["status"] == "no_results"
    json.dumps(result.to_dict(), allow_nan=False)


def test_explicit_zero_is_a_real_value_not_unknown():
    candidates = [
        CandidateSignals("a", topic=0.5, impact=0.0),
        CandidateSignals("b", topic=0.4, impact=5.0),
    ]
    result = core_rank(candidates)
    entry = _breakdown(result.by_id("a"), "impact")
    assert entry.raw_value == 0.0
    assert entry.rank is not None


def test_signal_with_one_distinct_value_is_inactive():
    candidates = [CandidateSignals("a", topic=0.5), CandidateSignals("b", topic=0.5)]
    result = core_rank(candidates)
    for item in result.items:
        entry = _breakdown(item, "topic")
        assert entry.available is False
        assert entry.rank is None
        assert entry.contribution is None
        assert entry.known_count == 2
        assert entry.raw_value == 0.5, "the real value is kept even when the signal is off"
        assert entry.missing_reason
        assert item.core_score_raw is None and item.core_score is None


def test_no_active_signal_orders_canonically_and_reports_insufficient_evidence():
    result = core_rank(
        [
            CandidateSignals("z", topic=0.0),
            CandidateSignals("a", topic=0.0),
            CandidateSignals("m", topic=0.0),
        ]
    )
    assert result.order == ["a", "m", "z"]
    assert result.recommended_paper_ids == []
    assert result.snapshot_fields["evidence_insufficient"] is True
    assert result.snapshot_fields["active_signals"] == []


# --------------------------------------------------------------------------
# the topic gate
# --------------------------------------------------------------------------


def test_high_citation_count_cannot_buy_a_recommendation():
    candidates = [
        CandidateSignals("strong_topic", topic=0.9),
        CandidateSignals("citation_only", topic=0.0, impact=1e9),
    ]
    result = core_rank(candidates)
    assert result.recommended_paper_ids == ["strong_topic"]
    assert result.by_id("citation_only").eligibility == "exploration"
    assert result.by_id("citation_only").recommendation_evidence == []
    assert result.by_id("citation_only") is not None, "exploration papers are kept, not deleted"


def test_topic_gate_takes_ceil_of_the_fraction_and_includes_boundary_ties():
    topics = {"a": 0.9, "b": 0.8, "c": 0.7, "d": 0.7, "e": 0.1}
    result = core_rank([CandidateSignals(pid, topic=t) for pid, t in topics.items()])
    # ceil(0.60 * 5) = 3, and the 0.7 pair straddles the boundary together.
    assert set(result.recommended_paper_ids) == {"a", "b", "c", "d"}
    assert len(result.recommended_paper_ids) == 4

    clean = {"a": 0.9, "b": 0.8, "c": 0.7, "d": 0.3, "e": 0.1}
    tight = core_rank([CandidateSignals(pid, topic=t) for pid, t in clean.items()])
    assert set(tight.recommended_paper_ids) == {"a", "b", "c"}


def test_gate_fraction_is_read_from_the_contract_not_hard_coded():
    candidates = [CandidateSignals(f"p{i}", topic=(10 - i) / 10) for i in range(5)]
    default = core_rank(candidates)
    assert len(default.recommended_paper_ids) == 3  # ceil(0.60 * 5)
    narrower = core_rank(candidates, config={"ranking": {"topic_gate_fraction": 0.2}})
    assert len(narrower.recommended_paper_ids) == 1


def test_user_anchor_enters_without_topic_evidence():
    candidates = [
        CandidateSignals("topic_hit", topic=0.9),
        CandidateSignals("anchor", topic=None, user_anchor=True),
    ]
    result = core_rank(candidates)
    assert "anchor" in result.recommended_paper_ids
    evidence = result.by_id("anchor").recommendation_evidence
    assert any(
        entry["code"] == "user_anchor" and "手动选进" in entry["text"] for entry in evidence
    )


def test_confirmed_seed_and_core_judgement_enter_the_gate():
    candidates = [
        CandidateSignals("topic_hit", topic=0.9),
        CandidateSignals("seed", topic=0.0, impact=5.0),
        CandidateSignals("judged", topic=0.0),
    ]
    by_seed = core_rank(candidates, seeds=["seed"])
    assert "seed" in by_seed.recommended_paper_ids

    by_judgement = core_rank(candidates, judgements={"judged": {"value": "core"}})
    assert "judged" in by_judgement.recommended_paper_ids


def test_excluded_candidate_leaves_the_ranked_set_but_is_kept():
    candidates = [CandidateSignals("a", topic=0.9), CandidateSignals("b", topic=0.5)]
    result = core_rank(
        candidates,
        judgements={"a": {"value": "excluded", "exclusion_reason": "not_relevant"}},
    )
    excluded = result.by_id("a")
    assert excluded is not None, "an excluded paper keeps its record and evidence"
    assert excluded.eligibility == "excluded"
    assert excluded.core_score is None and excluded.core_score_raw is None
    assert "a" not in result.recommended_paper_ids
    assert result.order[-1] == "a"


# --------------------------------------------------------------------------
# breakdown / schema shape
# --------------------------------------------------------------------------


def test_breakdown_matches_the_contract_and_contributions_sum_to_the_raw_score():
    result = core_rank(
        [
            CandidateSignals("a", topic=0.9, impact=10.0),
            CandidateSignals("b", topic=0.4, impact=100.0),
        ]
    )
    weights = C.weights()
    item = result.by_id("a")
    assert [entry.signal for entry in item.breakdown] == list(SIGNAL_ORDER)
    for entry in item.breakdown:
        assert entry.weight == pytest.approx(weights[entry.signal])
    active = [entry for entry in item.breakdown if entry.available]
    assert sum(entry.contribution for entry in active) == pytest.approx(
        item.core_score_raw, abs=1e-15
    )
    assert item.core_score == pytest.approx(
        min(1.0, (C.ranking()["rrf_k"] + 1) * item.core_score_raw)
    )


def test_serialised_objects_carry_exactly_the_schema_fields():
    result = core_rank(
        [
            CandidateSignals("a", topic=0.9, impact=10.0),
            CandidateSignals("b", topic=0.4, impact=100.0),
        ]
    )
    item = result.by_id("a")
    assert set(item.breakdown[0].to_dict()) == {
        "signal",
        "raw_value",
        "known_count",
        "available",
        "rank",
        "weight",
        "contribution",
        "missing_reason",
    }
    assert set(item.to_dict()) == {
        "paper_id",
        "rank",
        "core_score_raw",
        "core_score",
        "relevance_score",
        "core_role",
        "role_origin",
        "eligibility",
        "breakdown",
        "recommendation_evidence",
    }
    assert item.relevance_score is None
    assert item.core_role == "unknown" and item.role_origin == "unknown"
    assert set(result.to_dict()) == {
        "items",
        "recommended_paper_ids",
        "starter_paper_ids",
        "coverage",
        "snapshot_fields",
    }
    assert set(result.coverage) == {
        "status",
        "candidate_count",
        "failed_sources",
        "stop_reasons",
        "scope",
    }


def test_ranks_are_renumbered_without_gaps():
    result = core_rank([CandidateSignals(f"p{i}", topic=1.0 - i * 0.1) for i in range(4)])
    assert [item.rank for item in result.items] == [1, 2, 3, 4]


# --------------------------------------------------------------------------
# age adjusted impact
# --------------------------------------------------------------------------


def test_age_adjusted_impact_formula_and_unknowns():
    assert age_adjusted_impact(100, 2020, 2024) == pytest.approx(
        math.log1p(100) / math.sqrt(5)
    )
    assert age_adjusted_impact(0, 2024, 2024) == 0.0
    assert age_adjusted_impact(0, 2024, 2025) == 0.0
    assert age_adjusted_impact(None, 2020, 2024) is None, "unknown citations are not C=0"
    assert age_adjusted_impact(10, None, 2024) is None
    assert age_adjusted_impact(10, 2030, 2024) is None, "a future year disables the signal"
    assert age_adjusted_impact(10, 2020, None) is None
    assert age_adjusted_impact(-1, 2020, 2024) is None
    for value in (
        age_adjusted_impact(10, 2020, 2024),
        age_adjusted_impact(0, 2020, 2024),
    ):
        assert value is None or math.isfinite(value)


# --------------------------------------------------------------------------
# graph signal
# --------------------------------------------------------------------------


def _citation_edges(count, prefix="n"):
    return [
        {
            "from_paper_id": f"{prefix}{i}",
            "to_paper_id": f"{prefix}{i + 1}",
            "relation": "citation",
            "directed": True,
        }
        for i in range(count)
    ]


def test_fewer_than_the_contract_edge_floor_disables_the_signal():
    ids = [f"n{i}" for i in range(6)]
    out = pagerank_signal(_citation_edges(4), ids)
    assert all(value is None for value in out.values())
    assert set(out) == set(ids)


def test_isolated_nodes_get_a_missing_graph_signal():
    ids = [f"n{i}" for i in range(8)]
    out = pagerank_signal(_citation_edges(5), ids)
    for pid in ("n0", "n1", "n5"):
        assert out[pid] is not None and math.isfinite(out[pid])
    assert out["n6"] is None and out["n7"] is None


def test_text_similarity_and_co_citation_edges_are_never_citation_edges():
    ids = [f"n{i}" for i in range(7)]
    text_edges = [
        {
            "from_paper_id": f"n{i}",
            "to_paper_id": f"n{i + 1}",
            "relation": "text_similarity",
            "directed": False,
        }
        for i in range(6)
    ]
    assert all(value is None for value in pagerank_signal(text_edges, ids).values())

    co_citation = [
        {
            "from_paper_id": f"n{i}",
            "to_paper_id": f"n{i + 1}",
            "relation": "co_citation",
            "directed": False,
        }
        for i in range(6)
    ]
    assert all(value is None for value in pagerank_signal(co_citation, ids).values())

    undirected_citation = [
        {
            "from_paper_id": f"n{i}",
            "to_paper_id": f"n{i + 1}",
            "relation": "citation",
            "directed": False,
        }
        for i in range(6)
    ]
    assert all(value is None for value in pagerank_signal(undirected_citation, ids).values())


def test_graph_signal_is_deterministic_and_disabled_when_it_does_not_converge():
    ids = [f"n{i}" for i in range(8)]
    edges = _citation_edges(5) + [("n4", "n0"), ("n2", "n5")]
    first = pagerank_signal(edges, ids)
    assert first == pagerank_signal(edges, ids)

    stuck = pagerank_signal(edges, ids, config={"ranking": {"pagerank_max_iter": 0}})
    assert all(value is None for value in stuck.values())


def test_graph_parameters_come_from_the_contract():
    ranking = C.ranking()
    assert ranking["pagerank_alpha"] == 0.85
    assert ranking["graph_min_edges"] == 5
    ids = [f"n{i}" for i in range(7)]
    # Exactly the contract floor is enough; one below is not.
    assert any(value is not None for value in pagerank_signal(_citation_edges(5), ids).values())
    assert all(value is None for value in pagerank_signal(_citation_edges(4), ids).values())


# --------------------------------------------------------------------------
# starter list
# --------------------------------------------------------------------------


def _starter_fixture(count=300, *, all_recommended=False):
    # The starter list draws only from recommended papers (doc 04).  The small
    # selection fixtures tie every topic so the whole set passes the topic gate
    # (ties across the gate are admitted together) and the order stays p000..
    if all_recommended:
        return core_rank([CandidateSignals(f"p{i:03d}", topic=1.0) for i in range(count)])
    return core_rank([CandidateSignals(f"p{i:03d}", topic=1.0 - i * 0.001) for i in range(count)])


def test_starter_list_is_bounded_deterministic_and_read_only():
    result = _starter_fixture()
    before = [(item.paper_id, item.core_score_raw, item.core_score) for item in result.items]

    first = starter_list(result)
    second = starter_list(result)
    assert first == second
    assert len(first) <= C.ranking()["starter_size"]

    everything = [item.paper_id for item in result.items]
    assert len(starter_list(result, pool=everything)) <= 10
    small = starter_list(result, pool=["p005", "p006", "p007"])
    assert set(small) <= {"p005", "p006", "p007"}
    assert small == starter_list(result, pool=["p005", "p006", "p007"])

    after = [(item.paper_id, item.core_score_raw, item.core_score) for item in result.items]
    assert before == after, "selection must never rewrite a global core_score"
    assert result.starter_paper_ids == starter_list(result)


def test_starter_list_never_exceeds_the_contract_pool_cap():
    result = _starter_fixture()
    everything = [item.paper_id for item in result.items]
    picked = starter_list(result, pool=everything)
    assert len(picked) <= C.ranking()["starter_size"]
    # Everything after the contract pool cap is unreachable, even when explicitly listed.
    beyond = everything[C.ranking()["starter_pool"] :]
    assert not (set(picked) & set(beyond))


def test_starter_list_picks_one_paper_per_evidenced_role_first():
    result = _starter_fixture(6, all_recommended=True)
    roles = {"p003": "review", "p004": "foundational", "p005": "representative"}
    picked = starter_list(result, roles=roles)
    assert picked[:3] == ["p003", "p004", "p005"]
    assert len(picked) == 6 and len(set(picked)) == 6

    plain = starter_list(result)
    assert len(plain) == 6
    assert plain == starter_list(result, roles={})


def test_starter_list_redundancy_uses_cosine_when_vectors_exist():
    result = _starter_fixture(6, all_recommended=True)
    vectors = {
        "p000": [1.0, 0.0],
        "p001": [1.0, 0.0],  # a duplicate of p000
        "p002": [0.0, 1.0],
        "p003": [0.0, 1.0],
        "p004": [0.5, 0.5],
        "p005": [0.5, -0.5],
    }
    picked = starter_list(result, vectors=vectors)
    assert picked[0] == "p000"
    assert picked[1] == "p002", "a diverse paper must beat a redundant one"


def test_starter_list_redundancy_falls_back_to_title_tokens():
    result = _starter_fixture(6, all_recommended=True)
    titles = {
        "p000": "graph neural networks for citation recommendation",
        "p001": "graph neural networks for citation recommendation",
        "p002": "protein folding with diffusion models",
    }
    picked = starter_list(result, titles=titles)
    assert picked[0] == "p000"
    assert picked[1] == "p002"


def test_starter_list_draws_only_from_recommended_papers():
    # Doc 04 "阅读清单": the list is not padded with papers outside the
    # recommendation.  Ten candidates, so the 0.60 topic gate admits six; the
    # four below the gate are exploration and must never be picked, even when
    # the list could otherwise be filled to its full size or they are named
    # in an explicit pool.
    result = core_rank([CandidateSignals(f"p{i:03d}", topic=1.0 - i * 0.1) for i in range(10)])
    recommended = {item.paper_id for item in result.items if item.eligibility == "recommended"}
    exploration = {item.paper_id for item in result.items if item.eligibility == "exploration"}
    assert len(recommended) == 6 and len(exploration) == 4

    picked = starter_list(result)
    assert set(picked) == recommended
    assert not set(picked) & exploration
    assert result.starter_paper_ids == picked

    roles = {pid: "review" for pid in exploration}
    assert not set(starter_list(result, roles=roles)) & exploration
    assert not set(starter_list(result, pool=sorted(exploration))) & exploration
