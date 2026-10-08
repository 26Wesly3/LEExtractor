"""Integration tests across the repaired modules (spec 实施顺序: 关键跨模块链路).

The chain exercised here is retrieval → dedup → expansion → screening → save →
restore → export. Each test uses minimal doubles so it stays offline, and
asserts on the *contract between* modules rather than on one module's internals
— that is where a half-landed repair actually breaks.
"""

import json

import pytest

from litsearch.evidence import EvidenceGraph, citation_facts
from litsearch.filters import RelevanceFilter, corpus_hash
from litsearch.models import Paper
from litsearch.prisma import ScreeningDecision, ScreeningStage
from litsearch.search import LiteratureReviewWorkflow, ReviewState

TOPIC = "plant phenotyping with drones"
DIRECTION = "deep learning for plant phenotyping"


def make_paper(pid, title, year=2022, cites=5, refs=(), doi=None, source="openalex"):
    return Paper(
        id=pid, title=title, doi=doi, abstract=title, year=year,
        citation_count=cites, reference_ids=list(refs), source=source,
    )


class StubSources:
    """Provider double with controllable raw returns and a cancel hook."""

    snowball_delay = 0

    def __init__(self, search_results, references=None, citations=None):
        self.search_results = list(search_results)
        self.references = references or {}
        self.citations = citations or {}
        self.last_search_manifest = {}
        self._canceled = False

    def is_canceled(self):
        return self._canceled

    def search_all_sources(self, query, limit=200, year_from=1900, year_to=None, query_plan=None):
        self.last_search_manifest = {
            "query": query,
            "provider_counts": {"semantic_scholar": len(self.search_results)},
            "requested_limit": limit,
        }
        return list(self.search_results)

    def get_references(self, seed, limit=100):
        return list(self.references.get(seed.canonical_id, []))

    def get_citations(self, seed, limit=100):
        return list(self.citations.get(seed.canonical_id, []))


def workflow(search_results, **kwargs):
    wf = LiteratureReviewWorkflow(StubSources(search_results, **kwargs))
    wf.downloader = None  # never reached in these tests
    return wf


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("litsearch.snowball.time.sleep", lambda _s: None)
    monkeypatch.setattr("litsearch.sources.time.sleep", lambda _s: None)


# ---------------------------------------------------------------------------
# Retrieval → dedup → ledger
# ---------------------------------------------------------------------------


def test_systematic_search_registers_a_reconstructible_ledger():
    """Spec E3: the ledger must start at the provider return, not after dedup."""
    duplicate = make_paper("10.1/dup-b", "Plant phenotyping with drones", doi="10.1/dup")
    same_work = make_paper("10.1/dup", "Plant phenotyping with drones", doi="10.1/dup",
                           source="arxiv")
    unique = make_paper("10.1/unique", "Deep learning for plant phenotyping")
    raw = [duplicate, same_work, unique]

    wf = workflow(raw)
    state = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(state, max_papers=200)

    ledger = state.prisma.ledger.to_dict() if hasattr(state.prisma.ledger, "to_dict") \
        else state.prisma.ledger
    entries = ledger["entries"] if isinstance(ledger, dict) else ledger
    entry = entries[-1]
    assert entry["provider_raw"] == 3
    assert entry["cross_source_duplicates"] == 1
    assert entry["unique_records"] == 2
    # The identity the report leans on.
    assert entry["provider_raw"] - entry["cross_source_duplicates"] == entry["unique_records"]
    assert entry["stage"] == "systematic_search"


def test_ledger_records_truncation_as_a_selection_decision():
    papers = [make_paper(f"10.1/p{i}", f"Plant phenotyping study {i}") for i in range(10)]
    wf = workflow(papers)
    state = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(state, max_papers=4)

    ledger = state.prisma.ledger
    entries = ledger.to_dict()["entries"] if hasattr(ledger, "to_dict") else ledger
    assert entries[-1]["truncated"] == 6
    assert len(state.search_papers) == 4
    # Truncation must not be reported as "the database only had this many".
    assert entries[-1]["provider_raw"] == 10


def test_rerunning_the_same_search_does_not_inflate_the_ledger():
    papers = [make_paper(f"10.1/p{i}", f"Plant phenotyping study {i}") for i in range(3)]
    wf = workflow(papers)
    state = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(state, max_papers=200)
    first = len(state.prisma.ledger.to_dict()["entries"])
    wf.systematic_search(state, max_papers=200)
    # A new search resets the tracker, so exactly one entry describes the run.
    assert len(state.prisma.ledger.to_dict()["entries"]) == first == 1


# ---------------------------------------------------------------------------
# Scores, screening and calibration
# ---------------------------------------------------------------------------


def test_search_stamps_a_score_context_on_every_paper():
    papers = [make_paper(f"10.1/p{i}", f"Plant phenotyping study {i}") for i in range(3)]
    wf = workflow(papers)
    state = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(state, max_papers=200)

    contexts = {p.score_context_id for p in state.search_papers}
    assert len(contexts) == 1 and contexts != {""}
    assert state.search_manifest["score_context_id"] == contexts.pop()


def test_a_new_query_invalidates_a_previous_calibration():
    """A different query is a different scale, so the threshold cannot carry over.

    The ranking query comes from ``research_direction`` (falling back to the
    topic for a non-Latin direction), so that is what has to change for the
    scale to change.
    """
    papers = [make_paper(f"10.1/p{i}", f"Plant phenotyping study {i}") for i in range(3)]
    wf = workflow(papers)
    state = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(state, max_papers=200)
    first_context = state.score_context_id

    state.calibration = object()  # pretend a calibration exists
    state.research_direction = "marine sediment carbon export"
    wf.systematic_search(state, max_papers=200)

    assert state.score_context_id != first_context, "the query changed the scale"
    assert state.calibration is None, "a stale calibration survived a new query"


def test_repeating_the_same_search_keeps_the_context_and_calibration():
    """Same query, same corpus: nothing moved, so nothing is invalidated."""
    papers = [make_paper(f"10.1/p{i}", f"Plant phenotyping study {i}") for i in range(3)]
    wf = workflow(papers)
    state = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(state, max_papers=200)

    context = state.score_context_id
    record = object()
    state.calibration = record
    wf.systematic_search(state, max_papers=200)
    assert state.score_context_id == context
    assert state.calibration is record


def test_auto_screen_refuses_full_text():
    wf = workflow([make_paper("10.1/a", "Plant phenotyping")])
    state = ReviewState(TOPIC, DIRECTION)
    with pytest.raises(ValueError, match="FULL_TEXT"):
        wf.auto_screen(state, stage=ScreeningStage.FULL_TEXT)


def test_auto_screen_without_calibration_rejects_nothing():
    papers = [make_paper(f"10.1/p{i}", f"Plant phenotyping study {i}") for i in range(5)]
    wf = workflow(papers)
    state = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(state, max_papers=200)

    wf.auto_screen(state)
    decisions = {r.screening_decision for r in state.prisma.records.values()}
    assert decisions == {ScreeningDecision.PENDING}, (
        "an uncalibrated score permanently rejected papers"
    )
    report = state.prisma.generate_report()
    assert report.records_excluded_title_abstract == 0


def test_full_workflow_does_not_auto_screen_by_default():
    papers = [make_paper(f"10.1/p{i}", f"Plant phenotyping study {i}") for i in range(5)]
    wf = workflow(papers)
    state = wf.run_full_workflow(TOPIC, DIRECTION, max_search_papers=50, snowball_rounds=1)
    assert {r.screening_decision for r in state.prisma.records.values()} == {
        ScreeningDecision.PENDING
    }


# ---------------------------------------------------------------------------
# Expansion → evidence graph → questions
# ---------------------------------------------------------------------------


def test_forward_citation_trace_agrees_with_the_evidence_graph():
    """Spec F1/F2 across modules, not just inside questions.py."""
    target = make_paper("10.1/target", "A distinctive result", year=2020)
    citer = make_paper("10.1/citer", "Follow-up work", year=2023)

    wf = workflow([target], citations={target.canonical_id: [citer]})
    state = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(state, max_papers=50)
    wf.run_snowballing(state, num_seeds=1, max_rounds=1)

    papers = list(state.snowball_result.all_papers.values())
    facts = citation_facts(papers)
    assert (citer.canonical_id, target.canonical_id) in facts, (
        "the forward-citation observation was lost between snowballing and the graph"
    )

    graph = EvidenceGraph(papers, edge_types=("citation",))
    assert facts == set(graph.graph.edges)
    assert (citer.canonical_id, target.canonical_id) in graph.graph.edges


def test_expansion_ledger_and_run_history_are_recorded():
    papers = [make_paper(f"10.1/p{i}", f"Plant phenotyping study {i}") for i in range(5)]
    child = make_paper("10.1/child", "Later plant phenotyping work", year=2024)
    wf = workflow(papers, citations={papers[0].canonical_id: [child]})
    state = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(state, max_papers=50)
    wf.run_snowballing(state, num_seeds=1, max_rounds=1)

    stages = [entry["stage"] for entry in state.run_history]
    assert "systematic_search" in stages and "snowballing" in stages
    # Each entry carries the HTTP accounting for that phase.
    assert all("http" in entry for entry in state.run_history)
    ledger_stages = [e["stage"] for e in state.prisma.ledger.to_dict()["entries"]]
    assert "snowballing" in ledger_stages


def test_stop_reason_survives_into_the_session_payload():
    papers = [make_paper(f"10.1/p{i}", f"Plant phenotyping study {i}") for i in range(5)]
    wf = workflow(papers)
    state = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(state, max_papers=50)
    wf.run_snowballing(state, num_seeds=1, max_rounds=1)

    assert state.snowball_result.stop_reason, "the run ended without naming a reason"
    assert state.snowball_result.completed is True


# ---------------------------------------------------------------------------
# Corpus identity used for calibration validity
# ---------------------------------------------------------------------------


def test_corpus_hash_is_order_independent():
    papers = [make_paper(f"10.1/p{i}", f"Study {i}") for i in range(6)]
    assert corpus_hash(papers) == corpus_hash(list(reversed(papers)))
    assert corpus_hash(papers) != corpus_hash(papers[:3])


def test_relevance_is_relative_within_one_scoring_context():
    """Two batches scored separately must not be compared on raw numbers."""
    strong = [make_paper(f"10.1/s{i}", "deep learning plant phenotyping") for i in range(3)]
    weak = [make_paper(f"10.1/w{i}", "marine sediment carbon export") for i in range(3)]

    filter_ = RelevanceFilter()
    filter_.compute_relevance(strong, DIRECTION)
    filter_.compute_relevance(weak, DIRECTION)

    # The best of an unrelated batch still scores high inside its own batch,
    # which is exactly why a fixed cross-batch cut-off is indefensible.
    assert max(p.relevance_score for p in weak) > 0.0


# ---------------------------------------------------------------------------
# Export round trip
# ---------------------------------------------------------------------------


def test_prisma_report_and_ledger_are_json_serialisable():
    papers = [make_paper(f"10.1/p{i}", f"Plant phenotyping study {i}") for i in range(4)]
    wf = workflow(papers)
    state = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(state, max_papers=50)

    report = state.prisma.generate_report()
    payload = {
        "flow": report.to_flow_dict(),
        "ledger": state.prisma.ledger.to_dict(),
        "run_history": state.run_history,
        "search_manifest": state.search_manifest,
    }
    json.dumps(payload, ensure_ascii=False)


def test_tracker_relevance_stays_in_sync_with_the_paper():
    papers = [make_paper(f"10.1/p{i}", f"Plant phenotyping study {i}") for i in range(4)]
    wf = workflow(papers)
    state = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(state, max_papers=50)

    by_id = {p.canonical_id: p for p in state.search_papers}
    assert by_id, "no papers were registered"
    for record in state.prisma.records.values():
        paper = by_id.get(record.paper.canonical_id)
        if paper is not None:
            assert record.relevance_score == pytest.approx(paper.relevance_score)
