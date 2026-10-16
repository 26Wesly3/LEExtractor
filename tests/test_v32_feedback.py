"""M2 acceptance tests: the feedback query update (doc 04, "反馈").

The properties under test are the ones the specification calls out explicitly:
the text intent is not rewritten when there is nothing to learn from, each
per-paper vector is unit-normalised before it is averaged, only an explicit
``not_relevant`` exclusion acts as a negative sample, the 0.80 cosine floor is
honoured by bisection, and a genuinely unknown similarity is omitted rather
than reported as ``0``.

No network, no product code outside ``litsearch.v32``.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from litsearch.v32 import contracts as C
from litsearch.v32.feedback import feedback_scores, query_vector


def _cos(left, right):
    norm_left = math.sqrt(sum(x * x for x in left))
    norm_right = math.sqrt(sum(x * x for x in right))
    return sum(x * y for x, y in zip(left, right)) / (norm_left * norm_right)


def _norm(vector):
    return math.sqrt(sum(x * x for x in vector))


def test_feedback_constants_come_from_the_contract():
    feedback = C.ranking()["feedback"]
    assert feedback["alpha"] == 1.0
    assert feedback["beta"] == 0.6
    assert feedback["gamma"] == 0.2
    assert feedback["delta"] == 0.1
    assert feedback["min_anchor_cosine"] == 0.8
    assert feedback["clamp_iterations"] == 30


def test_empty_judgements_return_q_initial_unchanged():
    q_initial = [3.0, 4.0]
    assert query_vector(q_initial, None, {}) == q_initial
    assert query_vector(q_initial, {}, {}) == q_initial
    assert query_vector(q_initial, {"p1": {"value": "unjudged"}}, {"p1": [0.0, 1.0]}) == q_initial
    # Judgements exist but no comparable vector does: nothing is rewritten either.
    assert query_vector(q_initial, {"p1": {"value": "core"}}, {}) == q_initial
    assert query_vector(q_initial, {"p1": {"value": "core"}}, {"p1": [0.0, 0.0]}) == q_initial
    assert query_vector(q_initial, {"p1": {"value": "core"}}, {"p1": [1.0, 0.0, 0.0]}) == q_initial


def test_core_and_relevant_pull_the_vector_toward_the_judged_papers():
    q_initial = [1.0, 0.0]
    vectors = {"p1": [0.0, 1.0], "p2": [0.0, 2.0]}

    core = query_vector(q_initial, {"p1": {"value": "core"}}, vectors)
    assert core[1] > 0.0, "a core judgement must move the query toward that paper"
    assert _norm(core) == pytest.approx(1.0, abs=1e-12)

    relevant = query_vector(q_initial, {"p2": {"value": "relevant"}}, vectors)
    assert relevant[1] > 0.0
    assert _norm(relevant) == pytest.approx(1.0, abs=1e-12)

    # A unit-normalised duplicate must not change the mean of the group.
    duplicated = query_vector(
        q_initial,
        {"p1": {"value": "core"}, "p1b": {"value": "core"}},
        {"p1": [0.0, 1.0], "p1b": [0.0, 4.0]},
    )
    assert duplicated == pytest.approx(core, abs=1e-12)


def test_only_not_relevant_contributes_negative_weight():
    q_initial = [1.0, 0.0]
    vectors = {"p1": [0.0, 1.0]}

    for reason in ("unspecified", "read", "duplicate", "not_now"):
        untouched = query_vector(
            q_initial, {"p1": {"value": "excluded", "exclusion_reason": reason}}, vectors
        )
        assert untouched == [1.0, 0.0], reason

    negative = query_vector(
        q_initial, {"p1": {"value": "excluded", "exclusion_reason": "not_relevant"}}, vectors
    )
    assert negative[1] < 0.0, "an explicit not_relevant must push the query away"
    assert _cos(negative, q_initial) < 1.0
    assert _norm(negative) == pytest.approx(1.0, abs=1e-12)


def test_cosine_floor_clamp_engages_and_keeps_the_intent():
    floor = C.ranking()["feedback"]["min_anchor_cosine"]
    q_initial = [1.0, 0.0]
    # beta + gamma = 0.8 along the orthogonal axis would give cos ~0.781 < 0.80.
    vectors = {"c": [0.0, 1.0], "r": [0.0, 1.0]}
    out = query_vector(q_initial, {"c": {"value": "core"}, "r": {"value": "relevant"}}, vectors)

    assert _cos(out, q_initial) >= floor - 1e-9
    assert _cos(out, q_initial) < 1.0, "the clamp must keep some feedback"
    assert _norm(out) == pytest.approx(1.0, abs=1e-9)
    assert all(math.isfinite(value) for value in out)


def test_floor_is_read_from_the_contract_config():
    q_initial = [1.0, 0.0]
    vectors = {"c": [0.0, 1.0], "r": [0.0, 1.0]}
    judgements = {"c": {"value": "core"}, "r": {"value": "relevant"}}

    strict = query_vector(
        q_initial, judgements, vectors, config={"feedback": {"min_anchor_cosine": 0.99}}
    )
    assert _cos(strict, q_initial) >= 0.99 - 1e-9

    nested = query_vector(
        q_initial,
        judgements,
        vectors,
        config={"ranking": {"feedback": {"min_anchor_cosine": 0.95}}},
    )
    assert _cos(nested, q_initial) >= 0.95 - 1e-9


def test_judgement_record_shapes_are_all_accepted():
    q_initial = [1.0, 0.0]
    vectors = {"p1": [0.0, 1.0]}
    mapping = query_vector(q_initial, {"p1": {"value": "core"}}, vectors)
    sequence = query_vector(q_initial, [{"paper_id": "p1", "value": "core"}], vectors)
    objects = query_vector(
        q_initial, [SimpleNamespace(paper_id="p1", value="core", exclusion_reason=None)], vectors
    )
    shorthand = query_vector(q_initial, {"p1": "core"}, vectors)
    assert mapping == sequence == objects == shorthand


def test_no_nan_or_infinity_escapes_the_query_update():
    hostile = query_vector(
        [float("nan"), float("inf")],
        {"c": {"value": "core"}},
        {"c": [0.0, 1.0]},
    )
    assert all(math.isfinite(value) for value in hostile)

    finite = query_vector([1.0, 0.0], {"c": {"value": "core"}}, {"c": [float("nan"), 1.0]})
    assert finite == [1.0, 0.0], "a non-finite paper vector is not comparable"


def test_feedback_scores_are_cosines_and_unknowns_are_omitted():
    scores = feedback_scores(
        [1.0, 0.0],
        {
            "a": [1.0, 0.0],
            "b": [0.0, 5.0],
            "zero": [0.0, 0.0],
            "wrong-dimension": [1.0],
            "non-finite": [float("nan"), 1.0],
        },
    )
    assert scores["a"] == pytest.approx(1.0)
    assert scores["b"] == pytest.approx(0.0), "a real zero cosine is reported as 0"
    assert "zero" not in scores, "an undefined cosine is unknown, never 0"
    assert "wrong-dimension" not in scores
    assert "non-finite" not in scores
    assert all(math.isfinite(value) for value in scores.values())
    assert -1.0 <= min(scores.values()) and max(scores.values()) <= 1.0


def test_feedback_scores_is_deterministic():
    vectors = {"b": [0.0, 1.0], "a": [1.0, 0.0], "c": [0.5, 0.5]}
    first = feedback_scores([2.0, 1.0], vectors)
    second = feedback_scores([2.0, 1.0], vectors)
    assert first == second
    assert first["a"] > first["b"]
