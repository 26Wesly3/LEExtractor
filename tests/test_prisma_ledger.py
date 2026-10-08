"""Spec E regression tests: identity bridging + PRISMA count ledger.

Written before the v0.9.1 fix. Every test here encodes behaviour the shipped
v0.9.0 code gets wrong (E1: `add_papers` merges at most one existing record) or
does not provide at all (the provider-raw count ledger). No network access.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from litsearch.filters import RelevanceFilter, corpus_hash
from litsearch.identifiers import PaperIdentifiers
from litsearch.models import Paper
from litsearch.prisma import (
    AutoScreeningRefused,
    PRISMATracker,
    ScreeningDecision,
    ScreeningStage,
)


@pytest.fixture
def workspace_tmp():
    """Sandbox-safe scratch directory (the system temp dir is ACL-blocked)."""
    path = Path(__file__).resolve().parent / "_workspace_tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def make(pid, title="Duplicated work", **kw):
    return Paper(id=pid, title=title, **kw)


def _bridge_fixture():
    """The exact E1 reproduction: one DOI record, one S2 record, one bridge."""
    doi_rec = make("10.4/dup", doi="10.4/dup", source="openalex")
    s2_rec = make(
        "S2CorpusId:99",
        source="semantic_scholar",
        identifiers=PaperIdentifiers(semantic_scholar_id="99"),
    )
    bridged = make(
        "10.4/dup",
        doi="10.4/dup",
        source="openalex",
        identifiers=PaperIdentifiers(doi="10.4/dup", semantic_scholar_id="99"),
    )
    return doi_rec, s2_rec, bridged


# ---------------------------------------------------------------------------
# E1 — add_papers must merge ALL matching records
# ---------------------------------------------------------------------------


def test_bridging_merges_every_matching_record_into_one_entity():
    doi_rec, s2_rec, bridged = _bridge_fixture()
    tracker = PRISMATracker()

    assert tracker.add_papers([doi_rec], source="database_search") == 1
    assert tracker.add_papers([s2_rec], source="database_search") == 1
    assert len(tracker.records) == 2, "two provider identities start out separate"

    tracker.add_papers([bridged], source="database_search")

    # v0.9.0 defect: only ONE existing record was matched, so this stayed 2.
    assert len(tracker.records) == 1, "bridging must collapse both records into one entity"

    survivor = next(iter(tracker.records.values()))
    # the surviving paper carries every provider identity + the DOI
    assert survivor.paper.doi == "10.4/dup"
    assert survivor.paper.identifiers.semantic_scholar_id == "99"
    # every alias (old record keys included) resolves to the survivor
    for alias in ("10.4/dup", "s2:99", "S2CorpusId:99", "s2:s2corpusid:99"):
        assert tracker._find_key(alias) in tracker.records
    # one entity in the ledger: 3 identified records, 2 duplicates removed
    report = tracker.generate_report()
    assert report.records_after_dedup == 1
    assert report.duplicates_removed == 2


def test_bridged_entity_can_be_screened_through_either_provider_id():
    doi_rec, s2_rec, bridged = _bridge_fixture()
    tracker = PRISMATracker()
    tracker.add_papers([doi_rec], source="database_search")
    tracker.add_papers([s2_rec], source="database_search")
    tracker.add_papers([bridged], source="database_search")

    tracker.screen_paper("s2:99", ScreeningDecision.ACCEPT, reviewer="alice")
    tracker.screen_paper("10.4/dup", ScreeningDecision.ACCEPT, reviewer="alice")

    assert len(tracker.get_included_papers()) == 1
    assert tracker.generate_report().preliminary_included == 1


def test_merging_all_matches_never_inflates_identification_counts():
    """Re-running the same merge must not turn one work into several."""
    doi_rec, s2_rec, bridged = _bridge_fixture()
    tracker = PRISMATracker()
    tracker.add_papers([doi_rec], source="database_search")
    tracker.add_papers([s2_rec], source="database_search")
    tracker.add_papers([bridged], source="database_search")
    first = tracker.generate_report()
    tracker.add_papers([bridged], source="database_search")
    tracker.add_papers([doi_rec, s2_rec], source="database_search")
    second = tracker.generate_report()
    assert len(tracker.records) == 1
    assert second.records_after_dedup == first.records_after_dedup == 1
    # identification counts only ever grow by real registrations, never by merges
    assert second.database_results == 6  # 3 + 1 + 2 registrations
    assert second.duplicates_removed == 5
    assert second.database_results - second.records_after_dedup == second.duplicates_removed


# ---------------------------------------------------------------------------
# E2 — conflicting decisions are recorded, never silently picked
# ---------------------------------------------------------------------------


def test_conflicting_decisions_after_bridge_are_recorded_and_need_review():
    doi_rec, s2_rec, bridged = _bridge_fixture()
    tracker = PRISMATracker()
    tracker.add_papers([doi_rec], source="database_search")
    tracker.screen_paper("10.4/dup", ScreeningDecision.ACCEPT, reason="on topic", reviewer="alice")
    tracker.add_papers([s2_rec], source="database_search")
    tracker.screen_paper("s2:99", ScreeningDecision.REJECT, reason="wrong species", reviewer="bob")

    tracker.add_papers([bridged], source="database_search")

    assert len(tracker.records) == 1
    record = next(iter(tracker.records.values()))
    assert len(record.conflicts) == 1, "contradicting decisions must be recorded as a conflict"
    conflict = record.conflicts[0]
    assert set(conflict) == {"stage", "existing", "incoming", "at", "note"}
    assert conflict["stage"] == "title_abstract"
    assert conflict["existing"] == "accept"
    assert conflict["incoming"] == "reject"
    assert conflict["at"]
    assert conflict["note"]
    # neither decision may be silently kept: the merged entity needs human review
    assert record.requires_manual_review is True
    assert record.screening_decision is ScreeningDecision.MAYBE
    report = tracker.generate_report()
    assert report.records_excluded_title_abstract == 0
    assert report.preliminary_included == 0
    assert len(tracker.get_actionable_queue()) == 1


def test_conflict_on_full_text_stage_is_recorded_too():
    doi_rec, s2_rec, bridged = _bridge_fixture()
    tracker = PRISMATracker()
    tracker.add_papers([doi_rec], source="database_search")
    tracker.screen_paper("10.4/dup", ScreeningDecision.ACCEPT)
    tracker.mark_full_text_retrieved("10.4/dup", True)
    tracker.screen_paper("10.4/dup", ScreeningDecision.REJECT, "wrong outcome",
                         stage=ScreeningStage.FULL_TEXT)
    tracker.add_papers([s2_rec], source="database_search")
    tracker.screen_paper("s2:99", ScreeningDecision.ACCEPT)
    tracker.mark_full_text_retrieved("s2:99", True)
    tracker.screen_paper("s2:99", ScreeningDecision.ACCEPT, stage=ScreeningStage.FULL_TEXT)

    tracker.add_papers([bridged], source="database_search")

    record = next(iter(tracker.records.values()))
    assert {c["stage"] for c in record.conflicts} == {"full_text"}
    assert record.requires_manual_review is True
    assert record.full_text_decision is ScreeningDecision.MAYBE
    assert tracker.get_included_papers() == []


def test_merge_without_disagreement_keeps_the_decided_state():
    doi_rec, s2_rec, bridged = _bridge_fixture()
    tracker = PRISMATracker()
    tracker.add_papers([doi_rec], source="database_search")
    tracker.screen_paper("10.4/dup", ScreeningDecision.ACCEPT, reviewer="alice")
    tracker.add_papers([s2_rec], source="database_search")
    tracker.add_papers([bridged], source="database_search")
    record = next(iter(tracker.records.values()))
    assert record.conflicts == []
    assert record.requires_manual_review is False
    assert record.screening_decision is ScreeningDecision.ACCEPT
    assert len(tracker.get_included_papers()) == 1


# ---------------------------------------------------------------------------
# E3 — the count ledger is built from provider raw returns
# ---------------------------------------------------------------------------


def test_ledger_reconstructs_raw_duplicate_unique_filtered_and_truncated():
    tracker = PRISMATracker()
    ledger = tracker.record_retrieval_ledger(
        provider_raw=3,
        cross_source_duplicates=1,
        unique_records=2,
        filtered_citations=0,
        truncated=1,
        query="plant phenotyping",
        sources={"openalex": 2, "semantic_scholar": 1},
        retrieval_id="search:plant:run-1",
    )
    assert ledger.provider_raw == 3
    assert ledger.cross_source_duplicates == 1
    assert ledger.unique_records == 2
    assert ledger.filtered_year == 0
    assert ledger.filtered_citations == 0
    assert ledger.truncated == 1
    assert ledger.unique_records == ledger.provider_raw - ledger.cross_source_duplicates

    # truncation is a *selection* decision, not "the database only had this many"
    assert ledger.truncation["dropped"] == 1
    assert ledger.truncation["kind"] == "selection"
    assert "候选集" in ledger.truncation["note"]
    assert "数据库" in ledger.truncation["note"]

    # the unit of counting is stated, not implied
    assert ledger.units == "record"
    data = ledger.to_dict()
    for key in (
        "provider_raw", "cross_source_duplicates", "unique_records",
        "filtered_year", "filtered_citations", "truncated",
        "records_screened", "preliminary_included", "final_included",
    ):
        assert key in data, key


def test_ledger_rejects_inconsistent_counts():
    tracker = PRISMATracker()
    with pytest.raises(ValueError):
        tracker.record_retrieval_ledger(provider_raw=3, cross_source_duplicates=1, unique_records=5)


def test_ledger_re_recording_the_same_retrieval_id_does_not_inflate_counts():
    tracker = PRISMATracker()
    for _ in range(3):
        tracker.record_retrieval_ledger(
            provider_raw=3, cross_source_duplicates=1, unique_records=2, truncated=1,
            retrieval_id="search:plant:run-1",
        )
    assert tracker.ledger.provider_raw == 3
    assert tracker.ledger.cross_source_duplicates == 1
    assert tracker.ledger.truncated == 1

    tracker.record_retrieval_ledger(
        provider_raw=4, unique_records=4, retrieval_id="search:plant:run-2"
    )
    assert tracker.ledger.provider_raw == 7
    assert tracker.ledger.unique_records == 6


def test_report_exposes_the_ledger_and_keeps_studies_included_as_an_alias():
    tracker = PRISMATracker()
    papers = [make(f"10.5/{c}", title=f"paper {c}") for c in "abc"]
    tracker.add_papers(papers, source="database_search")
    tracker.record_retrieval_ledger(
        provider_raw=3, unique_records=3, cross_source_duplicates=0, truncated=0,
        retrieval_id="search:plant:run-1",
    )
    tracker.screen_paper("10.5/a", ScreeningDecision.ACCEPT)
    tracker.screen_paper("10.5/b", ScreeningDecision.ACCEPT)
    tracker.screen_paper("10.5/c", ScreeningDecision.REJECT)

    report = tracker.generate_report()
    assert report.provider_raw == 3
    assert report.unique_records == 3
    assert report.records_screened == 3
    assert report.preliminary_included == 2
    assert report.final_included == 2
    assert report.studies_included == report.final_included, "legacy alias must stay in sync"
    assert report.units == "record"
    flow = report.to_flow_dict()
    assert flow["screening"]["preliminary_included"] == 2
    assert flow["included"]["final_included"] == 2
    assert flow["included"]["studies"] == 2
    assert flow["identification"]["provider_raw"] == 3
    assert flow["screening"]["truncation"]["kind"] == "selection"

    tracker.mark_full_text_retrieved("10.5/a", True)
    tracker.screen_paper("10.5/a", ScreeningDecision.ACCEPT, stage=ScreeningStage.FULL_TEXT)
    tracker.mark_full_text_retrieved("10.5/b", True)
    tracker.screen_paper("10.5/b", ScreeningDecision.REJECT, "wrong outcome",
                         stage=ScreeningStage.FULL_TEXT)
    after = tracker.generate_report()
    assert after.preliminary_included == 2, "preliminary stays preliminary"
    assert after.final_included == 1
    assert after.studies_included == 1


def test_merge_conflicts_survive_save_and_restore(workspace_tmp, monkeypatch):
    """A conflict found while bridging must still need review after a reload."""
    import litsearch.persistence as persistence
    from litsearch.search import ReviewState

    monkeypatch.setattr(persistence, "SESSION_DIR", str(workspace_tmp))
    doi_rec, s2_rec, bridged = _bridge_fixture()
    state = ReviewState(topic="duplicated work", research_direction="duplicated work")
    state.search_papers = [doi_rec, s2_rec]
    state.prisma.add_papers([doi_rec], source="database_search")
    state.prisma.screen_paper("10.4/dup", ScreeningDecision.ACCEPT, reviewer="alice")
    state.prisma.add_papers([s2_rec], source="database_search")
    state.prisma.screen_paper("s2:99", ScreeningDecision.REJECT, reviewer="bob")
    state.prisma.add_papers([bridged], source="database_search")
    persistence.save_state(state)

    restored = persistence.load_state()
    assert restored is not None
    record = next(iter(restored.prisma.records.values()))
    assert len(restored.prisma.records) == 1
    assert record.screening_decision is ScreeningDecision.MAYBE
    assert record.requires_manual_review is True
    assert record.conflicts[0]["stage"] == "title_abstract"
    assert record.conflicts[0]["existing"] == "accept"
    assert record.conflicts[0]["incoming"] == "reject"


def test_ledger_survives_save_and_restore(workspace_tmp, monkeypatch):
    import litsearch.persistence as persistence
    from litsearch.search import ReviewState

    monkeypatch.setattr(persistence, "SESSION_DIR", str(workspace_tmp))
    state = ReviewState(topic="plant", research_direction="plant phenotyping")
    papers = [make(f"10.6/{c}", title=f"paper {c}") for c in "abc"]
    state.search_papers = papers
    state.prisma.add_papers(papers, source="database_search")
    state.prisma.record_retrieval_ledger(
        provider_raw=3, unique_records=3, cross_source_duplicates=0, truncated=0,
        query="plant phenotyping", retrieval_id="search:plant:run-1",
    )
    state.prisma.screen_paper("10.6/a", ScreeningDecision.REJECT, reviewer="alice")
    persistence.save_state(state)

    restored = persistence.load_state()
    assert restored is not None
    assert restored.prisma.ledger.provider_raw == 3
    assert restored.prisma.ledger.truncation["kind"] == "selection"
    assert restored.prisma.records["10.6/a"].screening_decision is ScreeningDecision.REJECT
    assert restored.prisma.records["10.6/a"].history[0]["reviewer"] == "alice"


# ---------------------------------------------------------------------------
# Automatic screening must be calibration-gated (spec D, enforced here)
# ---------------------------------------------------------------------------


def _two_context_papers():
    a = make("10.7/a", doi="10.7/a", title="on topic",
             relevance_score=0.9, score_context_id="ctx-1")
    b = make("10.7/b", doi="10.7/b", title="off topic",
             relevance_score=0.05, score_context_id="ctx-1")
    return a, b


def test_auto_screening_without_a_calibration_refuses_and_changes_nothing():
    a, b = _two_context_papers()
    tracker = PRISMATracker()
    tracker.add_papers([a, b])

    with pytest.raises(AutoScreeningRefused):
        tracker.screen_by_calibrated_threshold(None)

    assert all(r.screening_decision is ScreeningDecision.PENDING for r in tracker.records.values())
    assert tracker.records["10.7/b"].history == []
    assert len(tracker.get_actionable_queue()) == 2


def test_auto_screening_with_a_single_class_calibration_is_refused():
    a, b = _two_context_papers()
    tracker = PRISMATracker()
    tracker.add_papers([a, b])
    insufficient = RelevanceFilter.calibrate_threshold([a], [], query="plant")
    assert insufficient.usable is False
    with pytest.raises(AutoScreeningRefused):
        tracker.screen_by_calibrated_threshold(insufficient, query="plant")


def test_auto_screening_refuses_a_stale_calibration():
    a, b = _two_context_papers()
    tracker = PRISMATracker()
    tracker.add_papers([a, b])
    calibration = RelevanceFilter.calibrate_threshold(
        [a], [b], query="plant", corpus_hash=corpus_hash([a, b]), score_context_id="ctx-1",
    )
    assert calibration.usable is True
    with pytest.raises(AutoScreeningRefused):
        tracker.screen_by_calibrated_threshold(
            calibration, query="a different topic", corpus_hash=corpus_hash([a, b]),
        )
    with pytest.raises(AutoScreeningRefused):
        tracker.screen_by_calibrated_threshold(
            calibration, query="plant", corpus_hash=corpus_hash([a]),
        )
    assert all(r.screening_decision is ScreeningDecision.PENDING for r in tracker.records.values())


def test_auto_screening_refuses_mixed_score_contexts():
    a, b = _two_context_papers()
    b.score_context_id = "ctx-2"  # same numbers, different normalisation batch
    tracker = PRISMATracker()
    tracker.add_papers([a, b])
    calibration = RelevanceFilter.calibrate_threshold([a], [b], query="plant")
    with pytest.raises(AutoScreeningRefused):
        tracker.screen_by_calibrated_threshold(calibration, query="plant")


def test_calibrated_auto_screening_is_audited_and_never_touches_full_text():
    a, b = _two_context_papers()
    tracker = PRISMATracker()
    tracker.add_papers([a, b])
    corpus = corpus_hash([a, b])
    calibration = RelevanceFilter.calibrate_threshold(
        [a], [b], query="plant", corpus_hash=corpus, score_context_id="ctx-1",
    )

    decided = tracker.screen_by_calibrated_threshold(
        calibration, query="plant", corpus_hash=corpus
    )

    assert {r.paper.id for r in decided} == {"10.7/a", "10.7/b"}
    assert tracker.records["10.7/a"].screening_decision is ScreeningDecision.ACCEPT
    assert tracker.records["10.7/b"].screening_decision is ScreeningDecision.REJECT
    entry = tracker.records["10.7/b"].history[-1]
    assert entry["reviewer"] == "auto:calibrated_threshold"
    assert entry["before"] == "pending"
    assert entry["after"] == "reject"
    assert entry["stage"] == "title_abstract"
    assert entry["reason"]
    assert entry["timestamp"]

    # the full-text decision is human-only by contract
    with pytest.raises(AutoScreeningRefused):
        tracker.screen_by_calibrated_threshold(
            calibration, stage=ScreeningStage.FULL_TEXT, query="plant", corpus_hash=corpus,
        )


def test_auto_screening_marks_non_separated_calibrations_for_manual_review():
    a, b = _two_context_papers()
    b.relevance_score = 0.95  # labels now overlap
    tracker = PRISMATracker()
    tracker.add_papers([a, b])
    corpus = corpus_hash([a, b])
    calibration = RelevanceFilter.calibrate_threshold(
        [a], [b], query="plant", corpus_hash=corpus, score_context_id="ctx-1",
    )
    assert calibration.usable is True
    assert calibration.reliable is False

    tracker.screen_by_calibrated_threshold(calibration, query="plant", corpus_hash=corpus)

    assert tracker.records["10.7/a"].requires_manual_review is True
    assert tracker.records["10.7/b"].requires_manual_review is True
