from __future__ import annotations

from litsearch.cache import Cache
from litsearch.filters import (
    SCORE_VERSION,
    CalibrationRecord,
    RelevanceFilter,
    corpus_hash,
)
from litsearch.models import Paper
from litsearch.prisma import PRISMATracker
from litsearch.retrieval import RetrievalResult, RetrievalStatus
from litsearch.screening import apply_calibrated_screening
from litsearch.search import (
    LiteratureReviewWorkflow,
    ReviewState,
    current_scored_corpus,
    ranking_query,
)
from litsearch.similar import SimilarPaperFinder
from litsearch.snowball import SnowballEngine, SnowballResult
from litsearch.sources import SourceManager
from litsearch.stop_reasons import StopReason


def paper(pid: str, title: str = "plant phenotyping", abstract: str = "deep learning", year: int = 2024) -> Paper:
    return Paper(id=pid, doi=pid if pid.startswith("10.") else None, title=title, abstract=abstract, year=year)


def test_score_context_changes_when_ranker_input_changes_but_ids_do_not():
    wf = LiteratureReviewWorkflow(source_manager=object())
    state = ReviewState("plant", "plant phenotyping")
    a = paper("10.1/a", "plant phenotyping", "vision")
    b = paper("10.1/b", "chemistry", "molecule")
    state.search_papers = [a, b]
    c1 = wf.build_score_context(state, [a, b])
    # Same identities, different title/abstract — the actual ranker input moved.
    a.title, b.title = b.title, a.title
    a.abstract, b.abstract = b.abstract, a.abstract
    c2 = wf.build_score_context(state, [a, b])
    assert corpus_hash([a, b]) in c1 and corpus_hash([a, b]) in c2
    assert c1 != c2


def test_current_scored_corpus_includes_expansions_and_prisma_without_duplicates():
    state = ReviewState("plant", "plant phenotyping")
    a, b, c = paper("10.1/a"), paper("10.1/b"), paper("10.1/c")
    state.search_papers = [a, b]
    state.snowball_result = SnowballResult(all_papers={a.canonical_id: a, c.canonical_id: c})
    state.similar_papers = [(c, 0.8, "bibliographic_coupling")]
    state.prisma = PRISMATracker()
    state.prisma.add_papers([a, b, c], source="test")
    corpus = current_scored_corpus(state)
    assert {p.canonical_id for p in corpus} == {a.canonical_id, b.canonical_id, c.canonical_id}


def test_old_algorithm_calibration_is_not_kept_even_if_context_string_matches():
    wf = LiteratureReviewWorkflow(source_manager=object())
    state = ReviewState("plant", "plant phenotyping")
    papers = [paper("10.1/a"), paper("10.1/b", "chemistry")]
    state.search_papers = papers
    context = wf.build_score_context(state, papers)
    state.score_context_id = context
    record = CalibrationRecord(
        threshold=0.5,
        status="calibrated",
        query="plant phenotyping",
        corpus_hash=corpus_hash(papers),
        algorithm_version="old-ranker",
        score_version=SCORE_VERSION,
        evaluation={"separation": True, "score_context_id": context},
    )
    state._calibration = record
    state.calibration_context_id = ""  # force semantic validity check
    assert wf._keep_calibration_or_none(state, papers, context) is None


class _RelationProvider:
    def __init__(self, name: str, status: RetrievalStatus, papers=None, error: str | None = None):
        self.name = name
        self.status = status
        self.papers = list(papers or [])
        self.error = error

    def _get_references_result(self, _pid, _limit):
        return RetrievalResult(
            papers=self.papers, provider=self.name, status=self.status,
            complete=self.status in {RetrievalStatus.SUCCESS, RetrievalStatus.SUCCESS_EMPTY},
            error=self.error,
            truncated=self.status is RetrievalStatus.TRUNCATED,
        )

    def _get_citations_result(self, _pid, _limit):
        return self._get_references_result(_pid, _limit)

    def is_canceled(self):
        return False

    def set_cancel_check(self, _predicate):
        pass


class _UnusedProvider:
    name = "unused"

    def is_canceled(self):
        return False

    def set_cancel_check(self, _predicate):
        pass


def manager_with_relation_status(tmp_path, status: RetrievalStatus, papers=None, error=None) -> SourceManager:
    manager = SourceManager(Cache(str(tmp_path / "cache.db")))
    manager.s2 = _RelationProvider("semantic_scholar", status, papers, error)
    manager.oa = _RelationProvider("openalex", status, papers, error)
    manager.cr = _UnusedProvider()
    manager.arxiv = _UnusedProvider()
    return manager


def test_source_manager_relation_failure_is_not_flattened_to_empty(tmp_path):
    manager = manager_with_relation_status(
        tmp_path, RetrievalStatus.PROVIDER_ERROR, error="boom"
    )
    result = manager.get_references_result(paper("10.1/seed"), limit=10)
    assert result.papers == []
    assert result.failed is True
    assert result.status is RetrievalStatus.PROVIDER_ERROR
    # Legacy wrapper is intentionally lossy; the workflow must use the result API.
    assert manager.get_references(paper("10.1/seed"), limit=10) == []


def test_snowball_provider_failure_is_api_failure_not_saturation(tmp_path):
    manager = manager_with_relation_status(
        tmp_path, RetrievalStatus.PROVIDER_ERROR, error="boom"
    )
    seed = paper("10.1/seed")
    result = SnowballEngine(manager, RelevanceFilter()).run(
        [seed], "plant phenotyping", max_rounds=1, max_per_direction=10,
        year_from=2000, year_to=2026,
    )
    assert result.stop_reason == StopReason.API_FAILURE.value
    assert result.completed is False
    assert result.saturated is False
    assert result.failed_seed_count > 0


def test_snowball_truncated_relation_is_usable_not_api_failure(tmp_path):
    candidate = paper("10.1/candidate")
    manager = manager_with_relation_status(
        tmp_path, RetrievalStatus.TRUNCATED, papers=[candidate]
    )
    result = SnowballEngine(manager, RelevanceFilter()).run(
        [paper("10.1/seed")], "plant phenotyping", max_rounds=1,
        max_per_direction=1, year_from=2000, year_to=2026,
    )
    assert result.stop_reason != StopReason.API_FAILURE.value
    assert result.failed_seed_count == 0


def test_similar_marks_provider_failure_incomplete(tmp_path):
    manager = manager_with_relation_status(
        tmp_path, RetrievalStatus.PROVIDER_ERROR, error="boom"
    )
    seed1 = paper("10.1/a")
    seed2 = paper("10.1/b")
    finder = SimilarPaperFinder(manager, api_budget=10)
    assert finder.find_similar([seed1, seed2], top_k=5) == []
    assert finder.incomplete is True
    assert finder.stop_reason == StopReason.API_FAILURE.value
    assert any(r.failed for r in finder.retrieval_results)


class _ScopingManager:
    last_search_manifest = {}

    def search_all_sources(self, *args, **kwargs):
        raise AssertionError("legacy list API must not be used by scope_topic")

    def search_all_sources_result(self, *_args, **_kwargs):
        return [RetrievalResult(
            papers=[], provider="semantic_scholar",
            status=RetrievalStatus.PROVIDER_ERROR, complete=False, error="down",
        )]


def test_scoping_consumes_retrieval_result_and_records_failure():
    state = LiteratureReviewWorkflow(_ScopingManager()).scope_topic(
        "plant", "plant phenotyping", initial_limit=5, use_query_plan=False,
    )
    assert state.scoping_papers == []
    assert state.stop_reason == StopReason.API_FAILURE.value
    assert state.run_history[-1]["provider_results"][0]["status"] == "provider_error"


def test_truncated_and_partial_have_distinct_semantics():
    p = paper("10.1/a")
    truncated = RetrievalResult(
        papers=[p], status=RetrievalStatus.TRUNCATED,
        complete=False, truncated=True,
    )
    partial = RetrievalResult(
        papers=[p], status=RetrievalStatus.PARTIAL,
        complete=False, error="timeout after first page",
    )
    assert truncated.ok is True and truncated.failed is False
    assert partial.ok is False and partial.failed is True
    assert truncated.status is not partial.status


def test_calibrated_screening_uses_the_whole_scored_corpus_not_search_only():
    wf = LiteratureReviewWorkflow(source_manager=object())
    state = ReviewState("plant", "plant phenotyping")
    a = paper("10.1/a", "plant phenotyping", "vision")
    b = paper("10.1/b", "chemistry", "molecule")
    c = paper("10.1/c", "plant phenotyping cultivar", "vision morphology")
    state.search_papers = [a, b]
    state.snowball_result = SnowballResult(
        all_papers={a.canonical_id: a, b.canonical_id: b, c.canonical_id: c}
    )
    state.prisma.add_papers([a, b, c], source="test")
    wf.rerank_corpus(state, stage="test")
    corpus = current_scored_corpus(state)
    record = RelevanceFilter.build_calibration(
        {"relevant": [a], "irrelevant": [b]},
        query=ranking_query(state),
        corpus_hash=corpus_hash(corpus),
        score_context_id=state.score_context_id,
    )
    state.calibration = record
    note = apply_calibrated_screening(state, record)
    assert "refused" not in note.lower()
    assert record.corpus_hash == corpus_hash(corpus)
    assert c.canonical_id in {r.paper.canonical_id for r in state.prisma.records.values()}


def test_search_only_calibration_is_refused_after_snowball_expands_corpus():
    wf = LiteratureReviewWorkflow(source_manager=object())
    state = ReviewState("plant", "plant phenotyping")
    a = paper("10.1/a", "plant phenotyping", "vision")
    b = paper("10.1/b", "chemistry", "molecule")
    c = paper("10.1/c", "plant phenotyping cultivar", "vision morphology")
    state.search_papers = [a, b]
    state.snowball_result = SnowballResult(
        all_papers={a.canonical_id: a, b.canonical_id: b, c.canonical_id: c}
    )
    state.prisma.add_papers([a, b, c], source="test")
    wf.rerank_corpus(state, stage="test")
    stale = RelevanceFilter.build_calibration(
        {"relevant": [a], "irrelevant": [b]},
        query=ranking_query(state),
        corpus_hash=corpus_hash(state.search_papers),
        score_context_id=state.score_context_id,
    )
    note = apply_calibrated_screening(state, stale)
    assert "refused" in note.lower() or "不可用于" in note
