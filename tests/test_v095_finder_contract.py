"""v0.9.5: `find_similar` must not require a finder's extra state.

`LiteratureReviewWorkflow.find_similar` builds a `SimilarPaperFinder` and then
reads its completeness state. In v0.9.4 those reads were bare attribute
accesses, so a finder that implemented only ``find_similar`` — the interface
the workflow used before v0.9.4 — raised

    AttributeError: 'Finder' object has no attribute 'incomplete'

*after* the expensive search had already run. That is a real defect with a real
cost: the work is done, then the whole stage is lost to an error that says
nothing about the user's problem. The minimal-double test in
``test_audit_hardening.py`` hit it on the v0.9.4 package.

The rule these tests pin: a finder that cannot report incompleteness is read as
"reported nothing incomplete". It must never be assumed to have reported it, and
it must never crash the stage.
"""

import pytest

from litsearch.models import Paper
from litsearch.search import LiteratureReviewWorkflow, ReviewState

TOPIC = "plant phenotyping with drones"
DIRECTION = "deep learning for plant phenotyping"


def make(pid):
    return Paper(id=pid, title="plant phenotyping study", doi=pid,
                 abstract="plant phenotyping", source="openalex")


class StubSources:
    snowball_delay = 0

    def __init__(self, papers=()):
        self.papers = list(papers)
        self.last_search_manifest = {}

    def is_canceled(self):
        return False

    def search_all_sources(self, *a, **k):
        return list(self.papers)


def workflow(papers):
    wf = LiteratureReviewWorkflow(StubSources(papers))
    wf.downloader = None
    return wf


class MinimalFinder:
    """Only the interface the workflow used before v0.9.4 existed."""

    def __init__(self, candidates):
        self._candidates = candidates

    def find_similar(self, seed_papers, top_k=30):
        return [(p, 0.5, "bibliographic_coupling") for p in self._candidates]


@pytest.fixture
def patched_finder(monkeypatch):
    """Install a finder factory, and give back a handle to set candidates."""
    box = {}

    def install(finder):
        box["finder"] = finder
        monkeypatch.setattr("litsearch.search.SimilarPaperFinder",
                            lambda **kwargs: finder)
        return finder

    return install


def test_a_finder_without_completeness_state_does_not_crash_the_stage(patched_finder):
    """The v0.9.4 AttributeError, reproduced and then closed."""
    candidates = [make(f"10.1/cand{i}") for i in range(3)]
    wf = workflow([make("10.1/seed")])
    state = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(state, max_papers=50)

    patched_finder(MinimalFinder(candidates))
    wf.find_similar(state)  # must not raise

    assert len(state.similar_papers) == 3, "the candidates were lost"
    assert state.stop_reason != "api_failure", (
        "a finder that cannot report failure must not be read as having failed"
    )


def test_the_candidates_are_still_recorded_in_the_ledger(patched_finder):
    """Crashing after the search also discarded the accounting; both are fixed."""
    candidates = [make(f"10.1/cand{i}") for i in range(3)]
    wf = workflow([make("10.1/seed")])
    state = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(state, max_papers=50)

    patched_finder(MinimalFinder(candidates))
    wf.find_similar(state)

    entry = state.prisma.ledger.to_dict()["entries"]
    similar_entries = [e for e in entry if e["stage"] == "similar"]
    assert similar_entries, "the similar stage recorded nothing"
    assert similar_entries[-1]["unique_records"] == 3

    run = [r for r in state.run_history if r.get("stage") == "similar"]
    assert run, "the run history is missing the similar stage"
    assert run[-1]["incomplete"] is False
    assert run[-1]["stop_reason"] == ""
    assert run[-1]["retrieval_results"] == []


def test_a_finder_that_does_report_incompleteness_is_still_honoured(patched_finder):
    """The defensive read must not swallow a genuine report."""
    from litsearch.retrieval import RetrievalResult, RetrievalStatus
    from litsearch.stop_reasons import StopReason

    class ReportingFinder(MinimalFinder):
        def __init__(self, candidates):
            super().__init__(candidates)
            self.incomplete = True
            self.stop_reason = StopReason.API_FAILURE.value
            self.retrieval_results = [RetrievalResult(
                papers=[], provider="semantic_scholar",
                status=RetrievalStatus.PROVIDER_ERROR, complete=False, error="boom",
            )]

        def find_similar(self, seed_papers, top_k=30):
            return []

    wf = workflow([make("10.1/seed")])
    state = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(state, max_papers=50)

    patched_finder(ReportingFinder([]))
    wf.find_similar(state)

    assert state.stop_reason == StopReason.API_FAILURE.value
    run = [r for r in state.run_history if r.get("stage") == "similar"][-1]
    assert run["incomplete"] is True
    assert run["retrieval_results"] and run["retrieval_results"][0]["failed"] is True


def test_the_real_finder_still_reports_its_state():
    """The defensive read must not change what the real finder reports."""
    from litsearch.similar import SimilarPaperFinder

    finder = SimilarPaperFinder(StubSources())
    assert finder.incomplete is False
    assert finder.stop_reason == ""
    assert finder.retrieval_results == []
