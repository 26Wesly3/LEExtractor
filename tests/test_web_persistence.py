"""The Web and historical UI must restore the same backend facts."""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from litsearch.filters import RelevanceFilter, corpus_hash
from litsearch.models import Paper
from litsearch.persistence import load_state, save_state, state_from_dict, state_to_dict
from litsearch.prisma import ScreeningDecision
from litsearch.search import ReviewState
from litsearch.session_schema import MAX_HISTORY, SessionSchemaError, validate_session_dict
from litsearch.snowball import SnowballEngine, SnowballResult, SnowballRound
from litsearch.stop_reasons import StopReason


def paper(pid: str) -> Paper:
    return Paper(id=pid, doi=pid, title="Plant image evidence", year=2024, source="fixture")


def state_with_context() -> ReviewState:
    state = ReviewState("plant images", "plant images")
    positive, negative = paper("10.1000/positive"), paper("10.1000/negative")
    positive.relevance_score, negative.relevance_score = 0.9, 0.1
    for item in (positive, negative):
        item.score_context_id = "ctx-current"
    state.search_papers = [positive, negative]
    state.prisma.add_papers(state.search_papers)
    state.score_context_id = "ctx-current"
    state.score_context_stage = "systematic_search"
    state.score_context_history = [{"stage": "systematic_search", "context_id": "ctx-current",
                                   "previous_context_id": "", "previous_stage": "", "papers": 2}]
    state.calibration = RelevanceFilter.calibrate_threshold(
        [positive], [negative], query=state.topic,
        corpus_hash=corpus_hash(state.search_papers), score_context_id=state.score_context_id,
    )
    return state


def test_web_session_roundtrip_preserves_stop_http_and_scoring_audit(tmp_path):
    state = state_with_context()
    state.stop_reason = StopReason.API_FAILURE.value
    state.http_budget = {"requests": 7, "retries": 2, "cache_hits": 3,
                         "rate_limited": 1, "errors": 1, "canceled": False,
                         "elapsed_seconds": 1.5,
                         "by_source": {"openalex": {"requests": 7, "errors": 1}}}
    state.run_history = [{"stage": "systematic_search", "at": "2026-10-09T10:00:00+0800",
                          "stop_reason": state.stop_reason, "http": state.http_budget,
                          "provider_raw": 2, "returned": 2}]
    path = save_state(state, str(tmp_path / "session.json"))
    restored = load_state(path)
    assert restored is not None
    assert restored.stop_reason == state.stop_reason
    assert restored.http_budget == state.http_budget
    assert restored.run_history == state.run_history
    assert restored.score_context_id == state.score_context_id
    assert restored.score_context_stage == state.score_context_stage
    assert restored.score_context_history == state.score_context_history
    assert restored.calibration_context_id == "ctx-current"
    assert restored.calibration.to_dict() == state.calibration.to_dict()
    assert restored.calibration.usable
    restored.run_history[0]["http"]["requests"] = 999
    assert state.http_budget["requests"] == 7, "the restored audit must not alias live state"


def test_full_text_failed_retrieval_roundtrip_keeps_prisma_counts():
    state = ReviewState("plant", "plant")
    failed, untouched = paper("10.1000/failed"), paper("10.1000/untouched")
    state.search_papers = [failed, untouched]
    state.prisma.add_papers(state.search_papers)
    for item in state.search_papers:
        state.prisma.screen_paper(item.id, ScreeningDecision.ACCEPT)
    state.prisma.mark_full_text_retrieved(failed.id, False, reviewer="reviewer")
    failure_reason = state.prisma.records[failed.canonical_id].retrieval_failure_reason
    assert failure_reason
    before = asdict(state.prisma.generate_report())
    restored = state_from_dict(json.loads(json.dumps(state_to_dict(state))))
    after = asdict(restored.prisma.generate_report())
    assert after == before
    assert restored.prisma.records[failed.canonical_id].retrieval_attempted is True
    assert restored.prisma.records[failed.canonical_id].retrieval_failure_reason == failure_reason
    assert restored.prisma.records[untouched.canonical_id].retrieval_attempted is False
    assert restored.prisma.records[failed.canonical_id].full_text_decision is ScreeningDecision.PENDING
    assert restored.prisma.generate_report().reports_not_retrieved == 1
    assert restored.prisma.generate_report().reports_pending_retrieval == 1


def test_old_session_missing_runtime_fields_does_not_invent_outcomes():
    state = state_with_context()
    state.prisma.screen_paper(state.search_papers[0].id, ScreeningDecision.ACCEPT)
    payload = state_to_dict(state)
    for field in ("stop_reason", "http_budget", "run_history", "score_context_id",
                  "score_context_stage", "score_context_history", "calibration_context_id"):
        payload.pop(field)
    for record in payload["prisma"]["records"]:
        record.pop("retrieval_attempted")
        record.pop("retrieval_failure_reason")
    # Existing full_text_retrieved=False is no evidence of an attempt/failure.
    payload["prisma"]["full_text_stage_enabled"] = True
    restored = state_from_dict(payload)
    assert restored.stop_reason == ""
    assert restored.http_budget == {}
    assert restored.run_history == []
    assert restored.score_context_stage == ""
    assert restored.score_context_history == []
    assert restored.prisma.generate_report().reports_not_retrieved == 0
    assert restored.prisma.generate_report().reports_pending_retrieval == 1
    assert all(not rec.retrieval_attempted for rec in restored.prisma.records.values())
    # The old reader's recovery from one already recorded paper scoring context
    # is preserved; it does not manufacture a new scoring event.
    assert restored.score_context_id == "ctx-current"
    assert restored.calibration_context_id == "ctx-current"
    assert restored.calibration.usable


def test_saved_stale_calibration_context_is_not_restamped_as_current():
    state = state_with_context()
    state.calibration_context_id = "ctx-old"
    restored = state_from_dict(state_to_dict(state))
    assert restored.score_context_id == "ctx-current"
    assert restored.calibration_context_id == "ctx-old"
    assert restored.calibration.usable is False
    assert "context" in restored.calibration.evaluation["invalidated_reason"]


def test_snowball_checkpoint_retains_round_failure_cancel_and_seed_queue():
    seed = paper("10.1000/seed")
    state = ReviewState("plant", "plant")
    state.snowball_result = SnowballResult(
        rounds=[SnowballRound(1, [seed.id], [], "both", failed_seeds=1,
                              seed_ids=[seed.canonical_id], pending_seeds=[seed.canonical_id],
                              canceled=True)],
        all_papers={seed.canonical_id: seed}, initial_ids=[seed.canonical_id],
        next_seed_ids=[seed.canonical_id], stop_reason=StopReason.CANCELED.value,
    )
    state.snowball_result.seed_ids = [seed.canonical_id]
    restored = state_from_dict(state_to_dict(state)).snowball_result
    assert restored.stop_reason == StopReason.CANCELED.value
    assert restored.completed is False and not restored.is_complete
    assert restored.seed_ids == [seed.canonical_id]
    assert restored.next_seed_ids == [seed.canonical_id]
    assert restored.failed_seed_count == 1
    assert restored.rounds[0].canceled
    assert restored.rounds[0].seed_ids == [seed.canonical_id]
    assert restored.rounds[0].pending_seeds == [seed.canonical_id]


def test_restored_failed_snowball_actually_retries_the_owed_seed():
    seed, discovered = paper("10.1000/seed"), paper("10.1000/discovered")

    class Sources:
        snowball_delay = 0

        def __init__(self, fail):
            self.fail = fail
            self.calls = []

        def get_references(self, item, limit):
            self.calls.append(item.canonical_id)
            if self.fail:
                raise TimeoutError("fixture timeout")
            return [discovered]

        def get_citations(self, item, limit):
            return []

    state = ReviewState("plant", "plant")
    failed = Sources(fail=True)
    engine = SnowballEngine(failed, RelevanceFilter())
    state.snowball_result = engine.run([seed], "plant", max_rounds=1, year_to=2026)
    assert state.snowball_result.stop_reason == StopReason.API_FAILURE.value
    restored = state_from_dict(state_to_dict(state))
    working = Sources(fail=False)
    resumed = SnowballEngine(working, RelevanceFilter()).run(
        [seed], "plant", max_rounds=2, year_to=2026, resume=restored.snowball_result,
    )
    assert seed.canonical_id in working.calls
    assert discovered.canonical_id in resumed.all_papers
    assert resumed.rounds[0].failed_seeds == 1, "earlier failures remain part of the audit"


@pytest.mark.parametrize(("path", "invalid"), [
    (("stop_reason",), "completed-ish"),
    (("stop_reason",), []),
    (("http_budget",), []),
    (("http_budget", "requests"), True),
    (("http_budget", "errors"), -1),
    (("http_budget", "elapsed_seconds"), float("nan")),
    (("http_budget", "elapsed_seconds"), -0.5),
    (("http_budget", "canceled"), "false"),
    (("http_budget", "by_source"), []),
    (("http_budget", "by_source", "openalex", "requests"), 1.5),
    (("run_history",), {}),
    (("run_history",), [1]),
    (("run_history",), [{"http": {"retries": -1}}]),
    (("run_history",), [{"nested": {"value": float("inf")}}]),
    (("run_history",), [{"incomplete": 1}]),
    (("run_history",), [{"stop_reason": "done"}]),
    (("score_context_id",), None),
    (("score_context_stage",), 1),
    (("score_context_history",), [{"papers": True}]),
    (("calibration_context_id",), []),
    (("prisma", "records", 0, "retrieval_attempted"), 1),
    (("prisma", "records", 0, "retrieval_failure_reason"), None),
    (("snowball_result", "stop_reason"), "success"),
    (("snowball_result", "rounds", 0, "failed_seeds"), -1),
    (("snowball_result", "rounds", 0, "pending_seeds"), [False]),
    (("snowball_result", "rounds", 0, "canceled"), "false"),
])
def test_malformed_runtime_fields_are_rejected_on_every_import_path(path, invalid):
    state = state_with_context()
    state.http_budget = {"by_source": {"openalex": {"requests": 0}}}
    state.snowball_result = SnowballResult(rounds=[SnowballRound(1, [], [], "both")])
    payload = state_to_dict(state)
    target = payload
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = invalid
    with pytest.raises(SessionSchemaError):
        validate_session_dict(payload)
    with pytest.raises(SessionSchemaError):
        state_from_dict(payload)


def test_runtime_audit_containers_are_bounded():
    payload = state_to_dict(ReviewState("plant", "plant"))
    payload["run_history"] = [{}] * (MAX_HISTORY + 1)
    with pytest.raises(SessionSchemaError, match="too_large"):
        state_from_dict(payload)
    nested = {"leaf": 1}
    for _ in range(20):
        nested = {"child": nested}
    payload["run_history"] = [nested]
    with pytest.raises(SessionSchemaError, match="too_deep"):
        state_from_dict(payload)


def test_direct_import_still_refuses_a_future_schema():
    payload = state_to_dict(ReviewState("plant", "plant"))
    payload["version"] += 1
    with pytest.raises(SessionSchemaError, match="future_version"):
        state_from_dict(payload)


@pytest.mark.parametrize("version", [1, 2, 3])
def test_historical_migrations_keep_working(version):
    restored = state_from_dict({"version": version, "topic": "legacy", "phase": "scoping"})
    assert restored.topic == "legacy"
    assert restored.stop_reason == "" and restored.http_budget == {}
    assert restored.run_history == []
    assert restored.calibration is None
