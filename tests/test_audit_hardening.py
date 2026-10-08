"""Regression tests for the v0.9.1 independent audit findings (v0.9.2 hardening).

Every test names the defect it prevents. The audit's central point is that
several v0.9.1 checks asserted *arithmetic conservation* while the underlying
field semantics were inverted — so these tests assert what each field **means**,
not merely that a total adds up.

Reproduced on the v0.9.1 tree before these fixes:

* a snowball expansion that added 6 new papers from 8 raw observations recorded
  ``unique_records = 2`` and ``cross_source_duplicates = 6`` — exactly swapped;
* a coupling/co-citation stage that found 20 new papers recorded
  ``unique_records = 0``;
* ``screening_facts`` reported ``screening_status = "final_included"`` together
  with ``full_text_review_completed = True`` while two papers were still
  ``pending`` at title/abstract;
* two papers that each carried one blank reference were reported as sharing a
  reference, and a ``None`` reference raised ``AttributeError`` inside
  ``identifier_key``;
* ``max(per_page) = 200`` on the OpenAlex references path;
* a cancel that arrived mid-round was classified ``api_failure``;
* papers added by an expansion kept scores normalised inside their own small
  batch, so they were compared against the search batch on a different scale.
"""

import pytest

from litsearch.evidence import EvidenceGraph
from litsearch.filters import RelevanceFilter
from litsearch.identifiers import identifier_key
from litsearch.models import Paper
from litsearch.prisma import ScreeningDecision, ScreeningStage
from litsearch.search import LiteratureReviewWorkflow, ReviewState
from litsearch.server import screening_facts
from litsearch.snowball import SnowballEngine
from litsearch.sources import OA_PAGE_SIZE_LIMIT, OpenAlexSource
from litsearch.stop_reasons import StopReason

TOPIC = "plant phenotyping with drones"
DIRECTION = "deep learning for plant phenotyping"


def make(pid, title="Plant phenotyping study", year=2022, cites=5, refs=(), doi=None,
         source="openalex"):
    return Paper(id=pid, title=title, doi=doi, abstract=title, year=year,
                 citation_count=cites, reference_ids=list(refs), source=source)


class StubSources:
    """Provider double: search returns a fixed set, citations are configurable."""

    snowball_delay = 0

    def __init__(self, search_results=(), citations=None, references=None, canceled=False):
        self.search_results = list(search_results)
        self.citations = citations or {}
        self.references = references or {}
        self.last_search_manifest = {}
        self._canceled = canceled

    def is_canceled(self):
        return self._canceled

    def search_all_sources(self, query, limit=200, year_from=1900, year_to=None, query_plan=None):
        self.last_search_manifest = {"query": query, "provider_counts": {"stub": len(self.search_results)}}
        return list(self.search_results)

    def get_references(self, seed, limit=100):
        return list(self.references.get(seed.canonical_id, []))

    def get_citations(self, seed, limit=100):
        return list(self.citations.get(seed.canonical_id, []))


def workflow(sources):
    wf = LiteratureReviewWorkflow(sources)
    wf.downloader = None
    return wf


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("litsearch.snowball.time.sleep", lambda _s: None)
    monkeypatch.setattr("litsearch.sources.time.sleep", lambda _s: None)


def ledger_entry(state, stage):
    entries = state.prisma.ledger.to_dict()["entries"]
    match = [entry for entry in entries if entry["stage"] == stage]
    assert match, f"no {stage!r} ledger entry: {[e['stage'] for e in entries]}"
    return match[-1]


# ---------------------------------------------------------------------------
# P0-04 — PRISMA ledger field semantics (not just conservation)
# ---------------------------------------------------------------------------


def test_snowball_ledger_puts_new_papers_in_unique_records():
    """unique_records must be the *new* papers, duplicates the repeats."""
    seeds = [make("10.1/seed", doi="10.1/seed")]
    backward = [make(f"10.1/new{i}", doi=f"10.1/new{i}") for i in range(3)]
    forward = [make(f"10.1/fwd{i}", doi=f"10.1/fwd{i}") for i in range(3)]
    # Six raw observations, six distinct papers that the corpus did not hold,
    # so all six are unique records and none is a repeat.
    sources = StubSources(seeds, references={seeds[0].canonical_id: backward},
                          citations={seeds[0].canonical_id: forward})
    wf = workflow(sources)
    state = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(state, max_papers=50)
    wf.run_snowballing(state, num_seeds=1, max_rounds=1, seeds=seeds)

    entry = ledger_entry(state, "snowballing")
    assert entry["provider_raw"] == 6
    assert entry["unique_records"] == 6, (
        f"new papers must be the unique records, got {entry['unique_records']}"
    )
    assert entry["cross_source_duplicates"] == 0
    # The identity holds, and it is asserted *alongside* the semantics.
    assert entry["provider_raw"] - entry["cross_source_duplicates"] == entry["unique_records"]


def test_snowball_ledger_counts_a_repeated_paper_as_a_duplicate():
    """A paper observed in both directions is one unique record plus a repeat."""
    seeds = [make("10.1/seed", doi="10.1/seed")]
    shared = [make(f"10.1/dup{i}", doi=f"10.1/dup{i}") for i in range(4)]
    sources = StubSources(seeds, references={seeds[0].canonical_id: shared},
                          citations={seeds[0].canonical_id: shared})
    wf = workflow(sources)
    state = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(state, max_papers=50)
    wf.run_snowballing(state, num_seeds=1, max_rounds=1, seeds=seeds)

    entry = ledger_entry(state, "snowballing")
    assert entry["provider_raw"] == 8, "both directions were observed"
    assert entry["unique_records"] == 4, "the 4 distinct new papers are the unique records"
    assert entry["cross_source_duplicates"] == 4, (
        "the second sighting of each paper is a repeat observation"
    )
    assert entry["provider_raw"] - entry["cross_source_duplicates"] == entry["unique_records"]


def test_similar_ledger_does_not_report_every_candidate_as_a_duplicate():
    """A stage that surfaced new papers must not report unique_records=0."""
    seeds = [make(f"10.1/s{i}", doi=f"10.1/s{i}") for i in range(3)]
    sources = StubSources(seeds)
    wf = workflow(sources)
    state = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(state, max_papers=50)

    # Inject candidates directly: coupling/co-citation output is a list of
    # (paper, score, method) triples.
    new_papers = [make(f"10.1/cand{i}", doi=f"10.1/cand{i}") for i in range(4)]
    wf.sources.snowball_delay = 0

    class Finder:
        """Models the real finder's interface, which the workflow now reads.

        ``find_similar`` used to be the only member ``LiteratureReviewWorkflow``
        touched; v0.9.4 also reads the finder's completeness state to decide
        whether the stage may be reported as complete and to record the
        per-retrieval outcome. A double that omits it no longer stands in for
        ``SimilarPaperFinder``, so these mirror the real attributes exactly
        (see ``SimilarPaperFinder.__init__``).
        """

        def __init__(self):
            self.retrieval_results = []
            self.incomplete = False
            self.stop_reason = ""

        def find_similar(self, seed_papers, top_k=30):
            return [(p, 0.5, "bibliographic_coupling") for p in new_papers]

    import litsearch.search as search_module
    original = search_module.SimilarPaperFinder
    search_module.SimilarPaperFinder = lambda **kwargs: Finder()
    try:
        wf.find_similar(state)
    finally:
        search_module.SimilarPaperFinder = original

    entry = ledger_entry(state, "similar")
    assert entry["unique_records"] == 4, (
        "every coupling candidate was reported as a duplicate"
    )
    assert entry["cross_source_duplicates"] == 0


def test_ledger_identical_search_twice_is_idempotent_not_additive():
    papers = [make(f"10.1/p{i}", doi=f"10.1/p{i}") for i in range(3)]
    wf = workflow(StubSources(papers))
    state = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(state, max_papers=50)
    wf.systematic_search(state, max_papers=50)
    assert len(state.prisma.ledger.to_dict()["entries"]) == 1


# ---------------------------------------------------------------------------
# P0-05 — screening state cannot be "completed" and "pending" at once
# ---------------------------------------------------------------------------


def _state_with(n=3):
    state = ReviewState(TOPIC, DIRECTION)
    papers = [make(f"10.1/{i}", doi=f"10.1/{i}") for i in range(n)]
    state.search_papers = papers
    state.prisma.add_papers(papers)
    return state, papers


def test_screening_never_reports_completed_while_title_stage_is_pending():
    state, _ = _state_with(3)
    state.prisma.screen_paper("10.1/0", ScreeningDecision.ACCEPT)
    facts = screening_facts(state)
    assert facts["full_text_review_completed"] is False
    assert facts["screening_status"] != "final_included"


def test_screening_never_reports_final_while_another_paper_is_pending():
    """The exact v0.9.1 contradiction: 1 accepted at full text, 2 still pending."""
    state, _ = _state_with(3)
    state.prisma.screen_paper("10.1/0", ScreeningDecision.ACCEPT)
    state.prisma.screen_paper("10.1/0", ScreeningDecision.ACCEPT, stage=ScreeningStage.FULL_TEXT)

    facts = screening_facts(state)
    assert facts["screening_status"] != "final_included", (
        "final_included was reported while two papers had no title/abstract decision"
    )
    assert facts["full_text_review_completed"] is False
    assert facts["requires_manual_review"] is True
    assert facts["title_abstract_pending"] == 2


def test_maybe_is_actionable_and_blocks_completion():
    state, _ = _state_with(2)
    for paper in ("10.1/0", "10.1/1"):
        state.prisma.screen_paper(paper, ScreeningDecision.ACCEPT)
    state.prisma.screen_paper("10.1/1", ScreeningDecision.MAYBE)
    facts = screening_facts(state)
    assert facts["screening_status"] != "final_included"
    assert facts["requires_manual_review"] is True
    assert facts["title_abstract_pending"] >= 1


def test_completion_requires_every_stage_to_be_decided():
    state, papers = _state_with(2)
    for paper in papers:
        state.prisma.screen_paper(paper.canonical_id, ScreeningDecision.ACCEPT)
    assert screening_facts(state)["full_text_review_completed"] is False  # stage not run

    state.prisma.screen_paper(papers[0].canonical_id, ScreeningDecision.ACCEPT,
                              stage=ScreeningStage.FULL_TEXT)
    assert screening_facts(state)["full_text_review_completed"] is False  # one still open

    state.prisma.screen_paper(papers[1].canonical_id, ScreeningDecision.REJECT,
                              stage=ScreeningStage.FULL_TEXT)
    facts = screening_facts(state)
    assert facts["full_text_review_completed"] is True
    assert facts["screening_status"] == "final_included"
    assert facts["requires_manual_review"] is False


def test_an_empty_session_is_not_started_not_excluded():
    state = ReviewState(TOPIC, DIRECTION)
    assert screening_facts(state)["screening_status"] == "not_started"


# ---------------------------------------------------------------------------
# P1-06 — unusable references must not become witnesses
# ---------------------------------------------------------------------------


def test_blank_references_do_not_create_a_shared_reference():
    a = make("10.1/a", refs=["", "   "])
    b = make("10.1/b", refs=["", ""])
    graph = EvidenceGraph([a, b], edge_types=("bibliographic_coupling", "co_citation"))
    assert graph.relation_pairs() == [], (
        "two papers with only blank references were reported as sharing one"
    )


def test_a_none_reference_does_not_crash_the_graph():
    a = make("10.1/a", refs=[None, "10.1/real"])
    b = make("10.1/b", refs=[None, "10.1/real"])
    graph = EvidenceGraph([a, b], edge_types=("bibliographic_coupling",))
    rows = graph.relations_for("10.1/a")
    assert len(rows) == 1
    assert rows[0]["witness_count"] == 1, "the real shared reference must still count"
    assert all(row["evidence"] == [{"shared_reference": "10.1/real"}] for row in rows)


def test_identifier_key_rejects_unusable_values_instead_of_raising():
    assert identifier_key(None) == ""
    assert identifier_key("") == ""
    assert identifier_key("   ") == ""
    assert identifier_key(123) == ""
    assert identifier_key("10.1/real") == "10.1/real"


def test_co_citation_ignores_blank_references_in_a_reference_list():
    """A paper citing nothing usable must not link two blanks together."""
    a = make("10.1/a", doi="10.1/a")
    b = make("10.1/b", doi="10.1/b")
    citer = make("10.1/c", refs=["", None, "   "])
    graph = EvidenceGraph([a, b, citer], edge_types=("co_citation",))
    assert graph.relation_pairs() == []


# ---------------------------------------------------------------------------
# P1-02 — OpenAlex page size
# ---------------------------------------------------------------------------


def test_openalex_reference_batches_stay_within_the_page_limit(monkeypatch):
    source = OpenAlexSource(cache=None)
    source.get_paper = lambda _pid: make("W1", refs=[f"W{i}" for i in range(2, 200)])
    seen = []

    class Resp:
        status_code = 200
        headers = {}
        url = "u"

        def json(self):
            return {"results": []}

        @property
        def text(self):
            return ""

    def fake_transport(method, url, **kwargs):
        seen.append(kwargs.get("params") or {})
        return Resp()

    monkeypatch.setattr(source, "_transport_request", fake_transport)
    source.get_references("W1")

    assert seen, "no requests were made"
    sizes = [int(params.get("per_page", 0)) for params in seen]
    assert max(sizes) <= OA_PAGE_SIZE_LIMIT, f"per_page exceeded the cap: {sizes}"
    # Every chunk must fit one page, or a chunk would silently lose records.
    for params in seen:
        ids = str(params.get("filter", "")).split(":")[-1].split("|")
        assert len(ids) <= OA_PAGE_SIZE_LIMIT


def test_every_provider_retrieval_goes_through_the_counting_transport():
    """No *provider* may reach the network through a bare session call.

    Parsed per class rather than per file: the rule is about the retrieval
    adapters, and naming them exactly keeps the check enforceable instead of
    something that gets relaxed the first time an unrelated helper offends it.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path("litsearch/sources.py").read_text(encoding="utf-8"))
    base_classes = {
        node.name for node in tree.body if isinstance(node, ast.ClassDef)
    }
    assert "BaseSource" in base_classes

    offenders = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name == "BaseSource" or "BaseSource" not in {
            b.id for b in node.bases if isinstance(b, ast.Name)
        }:
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            func = child.func
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "_session"
                and func.attr in {"get", "post", "request"}
            ):
                offenders.append(f"{node.name}:{child.lineno}: self._session.{func.attr}()")

    assert offenders == [], (
        "provider calls bypass the transport layer, so HTTP budget, retry and "
        f"cancellation accounting miss them: {offenders}"
    )


# ---------------------------------------------------------------------------
# P1-04 — mid-round cancellation is a cancel, not a source failure
# ---------------------------------------------------------------------------


class CancelMidRound(StubSources):
    """Cancels itself once the first round's lookups have begun."""

    def __init__(self, search_results, references):
        super().__init__(search_results, references=references)
        self.polls = 0

    def is_canceled(self):
        self.polls += 1
        # Allow the run to start, then cancel from inside the first round.
        return self.polls > 2


def test_cancellation_during_a_round_is_reported_as_canceled():
    seeds = [make("10.1/seed", doi="10.1/seed")]
    fresh = [make(f"10.1/n{i}", doi=f"10.1/n{i}") for i in range(6)]
    sources = CancelMidRound(seeds, {seeds[0].canonical_id: fresh})
    engine = SnowballEngine(sources, RelevanceFilter())
    result = engine.run(seed_papers=seeds, research_direction=DIRECTION, max_rounds=3)

    assert result.stop_reason == StopReason.CANCELED.value, (
        f"a mid-round cancel was reported as {result.stop_reason!r}"
    )
    assert result.stop_reason != StopReason.API_FAILURE.value
    assert result.completed is False
    assert result.saturated is False


def test_cancellation_is_recorded_on_the_round_it_interrupted():
    seeds = [make("10.1/seed", doi="10.1/seed")]
    sources = CancelMidRound(seeds, {seeds[0].canonical_id: [make("10.1/n0", doi="10.1/n0")]})
    engine = SnowballEngine(sources, RelevanceFilter())
    result = engine.run(seed_papers=seeds, research_direction=DIRECTION, max_rounds=2)
    if result.rounds:  # the round may be recorded before the cancel is seen
        assert result.rounds[-1].canceled is True


# ---------------------------------------------------------------------------
# P0-01 / P0-02 — one scoring context for the whole corpus
# ---------------------------------------------------------------------------


def test_expansion_re_ranks_the_whole_corpus_into_one_context():
    seeds = [make(f"10.1/s{i}", doi=f"10.1/s{i}") for i in range(2)]
    fresh = [make(f"10.1/new{i}", doi=f"10.1/new{i}") for i in range(4)]
    sources = StubSources(seeds, references={seeds[0].canonical_id: fresh})
    wf = workflow(sources)
    state = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(state, max_papers=50)
    search_context = state.score_context_id
    assert search_context

    wf.run_snowballing(state, num_seeds=1, max_rounds=1, seeds=seeds)

    contexts = {p.score_context_id for p in wf._corpus_for_ranking(state)}
    assert contexts == {state.score_context_id}, (
        f"papers are scored in different contexts and cannot be compared: {contexts}"
    )
    assert state.score_context_id != search_context, (
        "the corpus changed, so the scoring context must have moved on"
    )


def test_expansion_invalidates_a_calibration_fitted_to_the_old_scale():
    """A calibration must not survive a change to the corpus it was fitted on."""
    seeds = [make(f"10.1/s{i}", doi=f"10.1/s{i}") for i in range(2)]
    sources = StubSources(seeds, references={seeds[0].canonical_id: [make("10.1/new", doi="10.1/new")]})
    wf = workflow(sources)
    state = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(state, max_papers=50)

    state.calibration = object()  # a threshold fitted to the search batch
    wf.run_snowballing(state, num_seeds=1, max_rounds=1, seeds=seeds)
    assert state.calibration is None, (
        "a threshold fitted to the old scale survived a corpus expansion"
    )


def test_re_ranking_an_unchanged_corpus_keeps_the_calibration():
    """Re-scoring the same corpus does not invalidate anything: nothing moved."""
    papers = [make(f"10.1/p{i}", doi=f"10.1/p{i}") for i in range(3)]
    wf = workflow(StubSources(papers))
    state = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(state, max_papers=50)

    record = object()
    state.calibration = record
    context_before = state.score_context_id
    wf.rerank_corpus(state, stage="test")

    assert state.score_context_id == context_before, "the corpus did not change"
    assert state.calibration is record, (
        "re-scoring an unchanged corpus threw away a still-valid calibration"
    )


def test_a_calibration_that_cannot_prove_validity_is_dropped():
    """'I cannot tell whether this still applies' must mean 'do not use it'."""
    papers = [make(f"10.1/q{i}", doi=f"10.1/q{i}") for i in range(4)]
    wf = workflow(StubSources(papers))
    state = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(state, max_papers=50)

    class Opaque:
        """No is_valid_for: it cannot answer whether the context still matches."""

    state.calibration = Opaque()
    assert state.calibration_context_id == state.score_context_id, "stamped on assign"

    # Shrink the candidate set and prune, so the scored corpus really changes.
    state.search_papers = state.search_papers[:2]
    wf.prune_corpus(state)
    wf.rerank_corpus(state, stage="test")
    assert state.calibration is None


def test_the_tracker_score_follows_a_re_ranking():
    papers = [make(f"10.1/p{i}", doi=f"10.1/p{i}") for i in range(3)]
    wf = workflow(StubSources(papers))
    state = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(state, max_papers=50)

    wf.rerank_corpus(state, stage="test")
    for record in state.prisma.records.values():
        assert record.relevance_score == pytest.approx(record.paper.relevance_score), (
            "the tracker kept a stale score from the previous ranking"
        )
        assert record.score_context_id == state.score_context_id


def test_a_single_paper_batch_cannot_claim_full_relevance_after_re_ranking():
    """The audit's core example: min-max makes the best of a tiny batch look perfect."""
    strong = [make(f"10.1/strong{i}", "deep learning plant phenotyping imaging") for i in range(5)]
    weak = [make("10.1/weak", "marine sediment carbon export dynamics")]
    wf = workflow(StubSources(strong + weak))
    state = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(state, max_papers=50)

    by_id = {p.canonical_id: p for p in state.search_papers}
    if "10.1/weak" in by_id:
        assert by_id["10.1/weak"].relevance_score < by_id["10.1/strong0"].relevance_score


def test_snowball_relevant_count_is_not_a_fixed_threshold_share():
    """No expansion round may label papers with a hardcoded relevance cut-off."""
    from pathlib import Path

    text = Path("litsearch/snowball.py").read_text(encoding="utf-8")
    assert "relevance_score >= 0.15" not in text, (
        "the expansion loop still labels papers relevant with an uncalibrated cut-off"
    )
