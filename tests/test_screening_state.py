"""Spec D regression tests: ranking/screening separation and calibration state.

These encode the v0.9.0 rules:
  * an uncalibrated relative score is for RANKING only — it must never reject;
  * single-class labels are `insufficient_labels`, not a binary calibration;
  * a calibration is bound to query + corpus + scoring version + score context;
  * every decision change is audited, and pending/maybe stay actionable.
No network access.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import litsearch.persistence as persistence
from litsearch.filters import (
    ALGORITHM_VERSION,
    SCORE_VERSION,
    CalibrationRecord,
    RelevanceFilter,
    corpus_hash,
)
from litsearch.models import Paper
from litsearch.prisma import AutoScreeningRefused, PRISMATracker, ScreeningDecision
from litsearch.search import ReviewState


@pytest.fixture
def workspace_tmp():
    """Sandbox-safe scratch directory (the system temp dir is ACL-blocked)."""
    path = Path(__file__).resolve().parent / "_workspace_tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def scored(pid, score, context="ctx-1", title=None, **kw):
    paper = Paper(
        id=pid,
        title=title or f"paper {pid}",
        doi=pid if pid.startswith("10.") else None,
        **kw,
    )
    paper.relevance_score = score
    paper.score_context_id = context
    return paper


# ---------------------------------------------------------------------------
# D2/D3 — the calibration record and its insufficiency states
# ---------------------------------------------------------------------------


def test_calibration_record_matches_the_frozen_contract_fields():
    rel, irr = scored("10.1/r", 0.8), scored("10.1/i", 0.2)
    record = RelevanceFilter.calibrate_threshold(
        [rel], [irr], query="plant phenotyping", corpus_hash=corpus_hash([rel, irr]),
    )
    assert set(record.to_dict()) == {
        "labels", "positive_count", "negative_count", "query", "corpus_hash",
        "algorithm_version", "score_version", "threshold", "created_at",
        "evaluation", "status",
    }
    assert record.labels == {"relevant": 1, "irrelevant": 1}
    assert record.positive_count == 1
    assert record.negative_count == 1
    assert record.query == "plant phenotyping"
    assert record.corpus_hash == corpus_hash([rel, irr])
    assert record.algorithm_version == ALGORITHM_VERSION
    assert record.score_version == SCORE_VERSION
    assert record.created_at and "T" in record.created_at
    assert record.status == "calibrated"
    assert record.threshold == pytest.approx(0.5)
    assert CalibrationRecord.from_dict(record.to_dict()).to_dict() == record.to_dict()


def test_single_class_labels_are_insufficient_not_a_calibration():
    only_relevant = RelevanceFilter.calibrate_threshold([scored("10.2/r", 0.6)], [])
    only_irrelevant = RelevanceFilter.calibrate_threshold([], [scored("10.2/i", 0.3)])
    no_labels = RelevanceFilter.calibrate_threshold([], [])

    for record, expected_pos, expected_neg in (
        (only_relevant, 1, 0), (only_irrelevant, 0, 1), (no_labels, 0, 0),
    ):
        assert record.status == "insufficient_labels"
        assert record.threshold is None, "an uncalibrated record must not carry a usable cut-off"
        assert record.evaluation["separation"] is False
        assert record.evaluation["reliable_binary_calibration"] is False
        assert record.usable is False
        assert record.reliable is False
        assert record.labels == {"relevant": expected_pos, "irrelevant": expected_neg}
        assert record.evaluation["note"]


def test_two_class_labels_calibrate_and_report_separation_and_margin():
    separated = RelevanceFilter.calibrate_threshold(
        [scored("10.3/r1", 0.8), scored("10.3/r2", 0.62)], [scored("10.3/i", 0.2)],
    )
    assert separated.status == "calibrated"
    assert separated.threshold == pytest.approx(0.41)
    assert separated.evaluation["separation"] is True
    assert separated.evaluation["margin"] > 0
    assert separated.evaluation["needs_manual_review"] == 0
    assert separated.usable is True and separated.reliable is True

    overlapping = RelevanceFilter.calibrate_threshold(
        [scored("10.3/r1", 0.8), scored("10.3/r2", 0.62)], [scored("10.3/i", 0.7)],
    )
    assert overlapping.status == "calibrated"
    assert overlapping.evaluation["separation"] is False
    assert overlapping.evaluation["needs_manual_review"] > 0
    assert overlapping.usable is True
    assert overlapping.reliable is False, "overlapping labels are not a reliable boundary"


def test_calibration_is_invalidated_by_query_corpus_or_scoring_change():
    rel, irr = scored("10.4/r", 0.8), scored("10.4/i", 0.2)
    corpus = corpus_hash([rel, irr])
    record = RelevanceFilter.calibrate_threshold(
        [rel], [irr], query="plant phenotyping", corpus_hash=corpus,
        score_context_id="ctx-1",
    )
    assert record.is_valid_for(
        query="plant phenotyping", corpus_hash=corpus,
        algorithm_version=ALGORITHM_VERSION, score_version=SCORE_VERSION,
        score_context_id="ctx-1",
    )
    assert record.is_valid_for(), "no dimension requested means nothing to check"

    assert record.is_valid_for(query="plant phenotyping") is True
    assert record.is_valid_for(query="other topic") is False
    assert "query" in record.invalidated_by(query="other topic")
    assert record.is_valid_for(corpus_hash=corpus_hash([rel])) is False
    assert "corpus" in record.invalidated_by(corpus_hash=corpus_hash([rel]))
    assert record.is_valid_for(algorithm_version="lexical_v4") is False
    assert "algorithm" in record.invalidated_by(algorithm_version="lexical_v4")
    assert record.is_valid_for(score_version="v2") is False
    assert record.is_valid_for(score_context_id="ctx-2") is False
    # an unscored caller (empty dimension) is not silently accepted as a match
    assert record.is_valid_for(score_context_id="") is False


def test_build_calibration_wrapper_and_legacy_tuple_path():
    rel, irr = scored("10.5/r", 0.8), scored("10.5/i", 0.2)
    record = RelevanceFilter.build_calibration(
        {"relevant": [rel], "irrelevant": [irr]}, query="q",
    )
    assert record.status == "calibrated"
    assert record.threshold == pytest.approx(0.5)
    with pytest.raises(ValueError):
        RelevanceFilter.build_calibration({"maybe": [rel]})

    # deprecated two-tuple path still serves existing callers
    value, note = RelevanceFilter.calibrate_threshold([rel], [irr])
    assert value == pytest.approx(record.threshold)
    assert isinstance(note, str) and note
    legacy_default, _ = RelevanceFilter.calibrate_threshold([], [])
    assert legacy_default == 0.15


# ---------------------------------------------------------------------------
# D5 — score contexts
# ---------------------------------------------------------------------------


def test_each_scoring_batch_gets_its_own_score_context_id():
    filters = RelevanceFilter()
    first = [Paper(id="10.6/a", title="deep learning plant phenotyping")]
    second = [Paper(id="10.6/b", title="deep learning plant phenotyping")]
    filters.compute_relevance(first, "deep learning plant phenotyping")
    filters.compute_relevance(second, "deep learning plant phenotyping")

    assert first[0].score_context_id
    assert second[0].score_context_id
    assert first[0].score_context_id != second[0].score_context_id

    explicit = [Paper(id="10.6/c", title="deep learning plant phenotyping")]
    filters.compute_relevance(explicit, "deep learning plant phenotyping",
                             score_context_id="ctx-fixed")
    assert explicit[0].score_context_id == "ctx-fixed"


def test_scores_from_different_batches_are_not_comparable_or_mergeable():
    same_batch = [scored("10.7/a", 0.3, "ctx-a"), scored("10.7/b", 0.35, "ctx-a")]
    mixed = [*same_batch, scored("10.7/c", 0.3, "ctx-b")]
    assert RelevanceFilter.scores_comparable(same_batch) is True
    assert RelevanceFilter.scores_comparable(mixed) is False
    assert RelevanceFilter.score_contexts_of(mixed) == {"ctx-a", "ctx-b"}

    keep = scored("10.8/a", 0.10, "ctx-a")
    RelevanceFilter.merge_metadata(keep, scored("10.8/a", 0.90, "ctx-b"))
    assert keep.relevance_score == 0.10, "max() across score contexts is meaningless"
    assert keep.score_context_id == "ctx-a"

    RelevanceFilter.merge_metadata(keep, scored("10.8/a", 0.90, "ctx-a"))
    assert keep.relevance_score == 0.90

    legacy = scored("10.8/b", 0.2, "")
    RelevanceFilter.merge_metadata(legacy, scored("10.8/b", 0.8, ""))
    assert legacy.relevance_score == 0.8, "unscored legacy papers keep the old behaviour"

    never_scored = Paper(id="10.8/c", title="never scored")
    RelevanceFilter.merge_metadata(never_scored, scored("10.8/c", 0.7, "ctx-b"))
    assert (never_scored.relevance_score, never_scored.score_context_id) == (0.7, "ctx-b")


def test_tracker_relevance_stays_in_sync_with_the_paper_and_context():
    tracker = PRISMATracker()
    paper = scored("10.9/a", 0.2, "ctx-a")
    tracker.add_papers([paper])
    record = tracker.records["10.9/a"]
    assert record.relevance_score == 0.2
    assert record.score_context_id == "ctx-a"

    paper.relevance_score = 0.7  # re-scored in a later batch
    paper.score_context_id = "ctx-b"
    tracker.sync_relevance_scores()
    assert record.relevance_score == 0.7
    assert record.score_context_id == "ctx-b"


# ---------------------------------------------------------------------------
# D1/D6 — uncalibrated never auto-rejects; decisions are auditable
# ---------------------------------------------------------------------------


def test_uncalibrated_scores_never_produce_an_automatic_reject():
    tracker = PRISMATracker()
    papers = [scored("10.10/a", 0.9), scored("10.10/b", 0.01)]
    tracker.add_papers(papers)

    single_class = RelevanceFilter.calibrate_threshold([papers[0]], [])
    assert single_class.status == "insufficient_labels"

    with pytest.raises(AutoScreeningRefused):
        tracker.screen_by_calibrated_threshold(single_class)

    assert all(r.screening_decision is ScreeningDecision.PENDING for r in tracker.records.values())
    assert len(tracker.get_actionable_queue()) == 2, "no paper may be silently dropped"


def test_every_decision_change_is_audited_with_reviewer_time_stage_before_after_reason():
    tracker = PRISMATracker()
    tracker.add_papers([scored("10.11/a", 0.5)])

    tracker.screen_paper("10.11/a", ScreeningDecision.MAYBE, reason="need full text",
                         reviewer="alice")
    tracker.screen_paper("10.11/a", ScreeningDecision.ACCEPT, reason="abstract is on topic",
                         reviewer="bob")

    record = tracker.records["10.11/a"]
    assert len(record.history) == 2
    first, second = record.history
    assert set(first) == {"reviewer", "timestamp", "stage", "before", "after", "reason"}
    assert (first["reviewer"], first["stage"], first["before"], first["after"],
            first["reason"]) == ("alice", "title_abstract", "pending", "maybe",
                                 "need full text")
    assert "T" in first["timestamp"]
    assert (second["reviewer"], second["before"], second["after"], second["reason"]) == (
        "bob", "maybe", "accept", "abstract is on topic")

    # repeating the identical decision is not a change and must not fabricate history
    tracker.screen_paper("10.11/a", ScreeningDecision.ACCEPT, reason="abstract is on topic",
                         reviewer="bob")
    assert len(record.history) == 2


def test_pending_and_maybe_stay_actionable_and_are_counted_separately():
    tracker = PRISMATracker()
    tracker.add_papers([scored("10.12/a", 0.9), scored("10.12/b", 0.5), scored("10.12/c", 0.1)])
    tracker.screen_paper("10.12/a", ScreeningDecision.ACCEPT)
    tracker.screen_paper("10.12/b", ScreeningDecision.MAYBE)

    queue = tracker.get_actionable_queue()
    assert [r.paper.id for r in queue] == ["10.12/b", "10.12/c"]

    report = tracker.generate_report()
    assert report.records_screened == 2
    assert report.records_maybe == 1
    assert report.records_pending_screening == 1
    assert report.preliminary_included == 1
    assert report.records_excluded_title_abstract == 0


# ---------------------------------------------------------------------------
# D4 — calibration lives in ReviewState and never survives a stale corpus
# ---------------------------------------------------------------------------


def _state_with_calibration(query="deep learning plant phenotyping"):
    state = ReviewState(topic="plant phenotyping", research_direction=query)
    papers = [scored("10.13/a", 0.9), scored("10.13/b", 0.05)]
    state.search_papers = papers
    state.prisma.add_papers(papers, source="database_search")
    state.calibration = RelevanceFilter.calibrate_threshold(
        [papers[0]], [papers[1]], query=query, corpus_hash=corpus_hash(papers),
        score_context_id="ctx-1",
    )
    return state


def test_review_state_persists_calibration_across_save_and_load(workspace_tmp, monkeypatch):
    monkeypatch.setattr(persistence, "SESSION_DIR", str(workspace_tmp))
    state = _state_with_calibration()
    persistence.save_state(state)

    restored = persistence.load_state()
    assert restored is not None
    assert restored.calibration is not None
    assert restored.calibration.to_dict() == state.calibration.to_dict()
    assert restored.calibration.usable is True
    assert restored.calibration.labels == {"relevant": 1, "irrelevant": 1}
    assert restored.calibration.query == state.research_direction
    assert restored.calibration.corpus_hash == corpus_hash(restored.search_papers)
    assert restored.calibration.threshold == pytest.approx(state.calibration.threshold)


def test_stale_calibration_is_invalidated_on_load_and_cannot_screen(workspace_tmp, monkeypatch):
    monkeypatch.setattr(persistence, "SESSION_DIR", str(workspace_tmp))
    state = _state_with_calibration()
    path = persistence.save_state(state)

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    data["calibration"]["corpus_hash"] = "0" * 32  # corpus changed after calibration
    Path(path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    restored = persistence.load_state()
    assert restored is not None
    assert restored.calibration is not None
    assert restored.calibration.usable is False
    assert "corpus" in restored.calibration.evaluation["invalidated_reason"]

    with pytest.raises(AutoScreeningRefused):
        restored.prisma.screen_by_calibrated_threshold(
            restored.calibration,
            query=restored.research_direction,
            corpus_hash=corpus_hash(restored.search_papers),
        )
    assert all(r.screening_decision is ScreeningDecision.PENDING
               for r in restored.prisma.records.values())


def test_calibration_is_dropped_when_query_or_scoring_version_moves_on(workspace_tmp, monkeypatch):
    monkeypatch.setattr(persistence, "SESSION_DIR", str(workspace_tmp))

    # Same corpus, different research question: the old threshold must not apply.
    state = _state_with_calibration(query="deep learning plant phenotyping")
    path = persistence.save_state(state)
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    data["topic"] = "marine sediment transport"
    data["research_direction"] = "marine sediment transport"
    Path(path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    restored = persistence.load_state()
    assert restored.calibration.usable is False
    assert "query" in restored.calibration.evaluation["invalidated_reason"]

    # Same session, but the scores came from an older scoring algorithm.
    state = _state_with_calibration()
    path = persistence.save_state(state)
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    data["calibration"]["algorithm_version"] = "lexical_v0"
    Path(path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    restored = persistence.load_state()
    assert restored.calibration.usable is False
    assert "scoring version" in restored.calibration.evaluation["invalidated_reason"]


def test_old_v3_session_files_still_load_without_calibration_or_ledger():
    """v0.8.0 session files must not lose decisions when the new fields are absent."""
    state = _state_with_calibration()
    state.prisma.screen_paper("10.13/a", ScreeningDecision.ACCEPT, reason="on topic",
                              reviewer="alice")
    data = persistence.state_to_dict(state)
    data["version"] = 3
    data.pop("calibration", None)
    data["prisma"].pop("ledger", None)
    for record in data["prisma"]["records"]:
        for key in ("score_context_id", "requires_manual_review", "conflicts", "history"):
            record.pop(key, None)

    restored = persistence.state_from_dict(data)

    assert restored.calibration is None, "a v3 file has no calibration to restore"
    assert restored.prisma.records["10.13/a"].screening_decision is ScreeningDecision.ACCEPT
    assert restored.prisma.records["10.13/a"].history == []
    assert restored.prisma.ledger.provider_raw == 0
    assert persistence.state_to_dict(restored)["version"] == persistence.SCHEMA_VERSION


def test_corpus_hash_is_order_independent_and_based_on_canonical_identity():
    a, b = scored("10.14/a", 0.5), scored("10.14/b", 0.6)
    assert corpus_hash([a, b]) == corpus_hash([b, a])
    assert corpus_hash([a, b]) != corpus_hash([a])
    assert corpus_hash([]) == corpus_hash([])
    renamed = Paper(id="10.14/a", title="a different title entirely", doi="10.14/a")
    assert corpus_hash([renamed]) == corpus_hash([a]), "hash keys on identity, not titles"


def test_relevance_score_is_not_reused_across_a_new_query(workspace_tmp, monkeypatch):
    """A new query must not silently keep an old threshold."""
    monkeypatch.setattr(persistence, "SESSION_DIR", str(workspace_tmp))
    state = _state_with_calibration(query="plant phenotyping")
    old = state.calibration
    new_query = "marine sediment transport"
    assert old.is_valid_for(query=new_query) is False
    state.calibration = RelevanceFilter.calibrate_threshold(
        [scored("10.15/a", 0.9)], [],   # single class after the query change
        query=new_query, corpus_hash=corpus_hash(state.search_papers),
    )
    assert state.calibration.status == "insufficient_labels"
    assert state.calibration.usable is False
