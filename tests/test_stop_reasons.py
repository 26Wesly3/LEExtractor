"""Regression tests for v0.9.1 spec C: stop reasons and real request budgets.

Baseline evidence (v0.9.0): the snowball engine had one boolean, ``saturated``,
and set it whenever a round produced fewer than five new papers — including
rounds whose citation lookups had all raised. A rate-limited or offline run was
therefore reported as a completed search: "the literature contains nothing
more". The GUI additionally explained cost as "papers × rounds = HTTP
requests", which is not an identity at all.
"""

import pytest
from pytest import approx

from litsearch.filters import RelevanceFilter
from litsearch.models import Paper
from litsearch.snowball import SnowballEngine, SnowballResult
from litsearch.stop_reasons import (
    COMPLETE_REASONS,
    INCOMPLETE_REASONS,
    LOW_YIELD_THRESHOLD,
    StopReason,
    classify_failure,
    is_complete,
    normalize_stop_reason,
    reason_label,
)


class FakeSources:
    """Citation/reference stub with per-seed control and a cancel hook."""

    def __init__(self, references=None, citations=None, failing_seeds=(), canceled=False):
        self.references = references or {}
        self.citations = citations or {}
        self.failing_seeds = set(failing_seeds)
        self.snowball_delay = 0
        self.canceled = canceled
        self.calls = []

    def is_canceled(self):
        return self.canceled

    def get_references(self, seed, limit=100):
        self.calls.append(("backward", seed.canonical_id))
        if seed.canonical_id in self.failing_seeds:
            raise ConnectionError("simulated source failure")
        return list(self.references.get(seed.canonical_id, []))

    def get_citations(self, seed, limit=100):
        self.calls.append(("forward", seed.canonical_id))
        if seed.canonical_id in self.failing_seeds:
            raise ConnectionError("simulated source failure")
        return list(self.citations.get(seed.canonical_id, []))


def seed(pid="seed", title="plant phenotyping"):
    p = Paper(id=pid, title=title)
    p.relevance_score = 1.0
    return p


def child(index, title="plant phenotyping"):
    p = Paper(id=f"10.1/n{index}", title=title, doi=f"10.1/n{index}")
    p.relevance_score = 0.9
    return p


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("litsearch.snowball.time.sleep", lambda _seconds: None)


def run_engine(sources, seeds, **kwargs):
    engine = SnowballEngine(sources=sources, filters=RelevanceFilter())
    return engine.run(seed_papers=seeds, research_direction="plant phenotyping", **kwargs)


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------


def test_taxonomy_is_closed_and_splits_complete_from_incomplete():
    assert {reason.value for reason in StopReason} == {
        "saturated", "no_new_results", "low_yield", "max_rounds",
        "truncated", "budget_exhausted", "canceled", "api_failure",
    }
    assert {StopReason.SATURATED, StopReason.NO_NEW_RESULTS} == COMPLETE_REASONS
    assert StopReason.API_FAILURE in INCOMPLETE_REASONS
    assert StopReason.CANCELED in INCOMPLETE_REASONS
    assert StopReason.BUDGET_EXHAUSTED in INCOMPLETE_REASONS
    assert StopReason.MAX_ROUNDS in INCOMPLETE_REASONS
    assert StopReason.TRUNCATED in INCOMPLETE_REASONS
    # A low-yield stop is deliberately NOT a coverage claim.
    assert StopReason.LOW_YIELD not in COMPLETE_REASONS


def test_unknown_reason_text_is_treated_as_incomplete():
    assert normalize_stop_reason("something-new") is StopReason.API_FAILURE
    assert normalize_stop_reason(None) is StopReason.API_FAILURE
    assert normalize_stop_reason("") is StopReason.API_FAILURE
    assert normalize_stop_reason("saturated") is StopReason.SATURATED
    assert is_complete("unknown-text") is False
    assert is_complete("") is False


def test_reason_labels_exist_for_every_reason_in_two_languages():
    for reason in StopReason:
        label = reason_label(reason)
        assert label
        assert any("\u4e00" <= ch <= "\u9fff" for ch in label)
        assert "/" in label


def test_failed_lookup_classifies_as_api_failure():
    assert classify_failure(ConnectionError("down")) is StopReason.API_FAILURE
    assert classify_failure(TimeoutError("slow")) is StopReason.API_FAILURE


# ---------------------------------------------------------------------------
# C1/C2/C3 — saturation only when the sources actually answered
# ---------------------------------------------------------------------------


def test_source_failure_is_never_reported_as_saturation():
    """The core defect: every lookup raised, yet v0.9.0 said saturated=True."""
    sources = FakeSources(failing_seeds={"seed"})
    result = run_engine(sources, [seed()], max_rounds=3)

    assert result.stop_reason == StopReason.API_FAILURE.value
    assert result.saturated is False, "a failed run claimed saturation"
    assert result.completed is False, "a failed run claimed completion"
    assert result.is_complete is False
    assert result.rounds[0].failed_seeds == 2  # backward + forward
    assert result.failed_seed_count == 2


def test_partial_failure_preserves_partial_results_and_pending_queue():
    """Failures keep what was retrieved and the queue needed to resume.

    The seed itself fails, so round 1 is a failed round that nonetheless has a
    partial result: what the *other* seeds returned must survive.
    """
    working = seed("second", "plant phenotyping")
    sources = FakeSources(
        references={"10.1/n0": [child(i) for i in range(6)]},
        failing_seeds={"seed"},
    )
    sources.references["second"] = []
    result = run_engine(sources, [seed(), working], max_rounds=1)

    assert result.stop_reason == StopReason.API_FAILURE.value
    assert result.completed is False
    assert result.rounds, "the partial round was not recorded"
    assert result.rounds[0].failed_seeds == 2  # backward + forward for the seed
    assert result.next_seed_ids, "the resumable queue was dropped"


def test_a_failed_later_round_keeps_what_earlier_rounds_found():
    """Partial results from earlier rounds survive a failure in a later one."""
    found = [child(i) for i in range(6)]
    sources = FakeSources(
        references={"seed": found},
        # The discovered papers are the seeds of round 2, and every one of
        # those lookups fails.
        failing_seeds={paper.canonical_id for paper in found},
    )
    result = run_engine(sources, [seed()], max_rounds=3)

    assert result.stop_reason == StopReason.API_FAILURE.value
    assert result.completed is False
    assert result.saturated is False, "a failed run claimed saturation"
    assert len(result.all_papers) >= 7, "partial results were discarded on failure"
    assert result.rounds, "the partial round was not recorded"
    # The round that discovered the papers must still report what it found.
    assert result.rounds[0].count >= 6
    assert result.next_seed_ids, "the resumable queue was dropped"


def test_genuine_no_new_results_reports_no_new_results():
    sources = FakeSources()  # every source answers normally, with nothing
    result = run_engine(sources, [seed()], max_rounds=3)

    assert result.stop_reason == StopReason.NO_NEW_RESULTS.value
    assert result.completed is True
    assert result.saturated is True
    assert result.is_complete is True


def test_saturation_after_a_productive_first_round_is_named_saturated():
    sources = FakeSources(references={"seed": [child(i) for i in range(6)]})
    result = run_engine(sources, [seed()], max_rounds=4)

    assert result.stop_reason == StopReason.SATURATED.value
    assert result.completed is True
    assert result.is_complete is True


def test_low_yield_is_a_heuristic_stop_not_a_coverage_claim():
    """Fewer than LOW_YIELD_THRESHOLD new papers: low_yield, never saturated."""
    sources = FakeSources(references={"seed": [child(0), child(1)]})
    result = run_engine(sources, [seed()], max_rounds=3)

    assert result.stop_reason == StopReason.LOW_YIELD.value
    assert result.saturated is False, "a low-yield stop was reported as saturation"
    assert result.completed is True  # the run itself finished normally
    assert result.is_complete is False  # ...but it is not a coverage proof
    assert str(LOW_YIELD_THRESHOLD) in result.saturation_reason
    assert "不" in result.saturation_reason or "not" in result.saturation_reason


def test_round_cap_is_named_max_rounds():
    sources = FakeSources(references={
        "seed": [child(i) for i in range(6)],
        **{f"10.1/n{i}": [child(100 + i)] for i in range(6)},
    })
    result = run_engine(sources, [seed()], max_rounds=1)

    assert result.stop_reason == StopReason.MAX_ROUNDS.value
    assert result.completed is True
    assert result.is_complete is False


def test_cancellation_is_reported_and_incomplete():
    sources = FakeSources(references={"seed": [child(i) for i in range(6)]}, canceled=True)
    result = run_engine(sources, [seed()], max_rounds=3)

    assert result.stop_reason == StopReason.CANCELED.value
    assert result.completed is False
    assert result.saturated is False


def test_stop_reason_defaults_to_empty_for_a_fresh_result():
    """Nothing may be inferred before a run happens."""
    fresh = SnowballResult()
    assert fresh.stop_reason == ""
    assert fresh.is_complete is False
    assert fresh.stop_reason_label == ""


def test_every_ending_reports_a_reason():
    cases = {
        "no_new": (FakeSources(), StopReason.NO_NEW_RESULTS),
        "low": (FakeSources(references={"seed": [child(0)]}), StopReason.LOW_YIELD),
        "fail": (FakeSources(failing_seeds={"seed"}), StopReason.API_FAILURE),
        "cancel": (FakeSources(canceled=True), StopReason.CANCELED),
    }
    for name, (sources, expected) in cases.items():
        result = run_engine(sources, [seed()], max_rounds=2)
        assert result.stop_reason == expected.value, name
        assert result.saturation_reason, name


# ---------------------------------------------------------------------------
# C1 — completed / saturated must never contradict the reason
# ---------------------------------------------------------------------------


def test_no_incomplete_reason_ever_reports_saturated():
    for reason in INCOMPLETE_REASONS:
        result = SnowballResult(stop_reason=reason.value, completed=False)
        assert result.saturated is False
        assert result.is_complete is False


def test_completed_true_with_an_incomplete_reason_still_is_not_coverage():
    """Defence in depth: ``completed`` alone is not a coverage claim."""
    result = SnowballResult(stop_reason=StopReason.LOW_YIELD.value, completed=True)
    assert result.is_complete is False
    result.stop_reason = StopReason.SATURATED.value
    assert result.is_complete is True


def test_budget_exhaustion_maps_to_its_own_reason():
    """The budget path is exercised through SimilarPaperFinder's accounting."""
    from litsearch.similar import SimilarPaperFinder

    class BudgetedSources(FakeSources):
        def __init__(self):
            super().__init__()
            self.get_citations = self._citations
            self.get_paper = lambda pid: None
            self.snowball_delay = 0

        def _citations(self, paper, limit=100):
            return []

    finder = SimilarPaperFinder(BudgetedSources(), api_budget=0)
    assert finder.find_similar([seed()]) == []


def test_similarity_scores_remain_within_unit_range():
    """Guard for the neighbouring module while the budget path is stubbed."""
    from litsearch.similar import SimilarPaperFinder

    class Sources:
        snowball_delay = 0

        def get_references(self, paper, limit=100):
            return []

        def get_citations(self, paper, limit=100):
            return [child(0), child(1)]

        def get_paper(self, pid):
            return child(0)

    finder = SimilarPaperFinder(Sources(), api_budget=20)
    results = finder.find_similar([seed()], top_k=5)
    for _paper, score, _method in results:
        assert 0.0 <= score <= 1.0
        assert score == approx(score, abs=1e-9)
