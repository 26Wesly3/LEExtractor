"""Systematic literature review workflow: Scoping → Search → Snowballing → PRISMA.

Gold-standard review methodology:
- Gusenbauer (2024): multi-database systematic search
- TARCiS (2024): formal citation searching guidance
- PRISMA 2020: standard flow diagram for reporting
"""

import hashlib
import logging
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import Enum

from litsearch.config import current_year, year_from_years_back
from litsearch.downloader import PaperDownloader
from litsearch.filters import (
    ALGORITHM_VERSION,
    SCORE_VERSION,
    RelevanceFilter,
    corpus_hash,
    ranking_input_hash,
)
from litsearch.intent import database_topic, plan_payload
from litsearch.models import DiscoveryTrace, Paper
from litsearch.prisma import (
    PRISMAReport,
    PRISMATracker,
    ScreeningDecision,
    ScreeningStage,
)
from litsearch.similar import SimilarPaperFinder
from litsearch.snowball import SnowballEngine, SnowballResult
from litsearch.sources import SourceManager
from litsearch.stop_reasons import (
    StopReason,
    http_budget_snapshot,
    reset_http_budget,
)

logger = logging.getLogger(__name__)



def current_scored_corpus(state: "ReviewState", filters: RelevanceFilter | None = None) -> list[Paper]:
    """Return the single candidate corpus whose relevance scores are comparable.

    Search results, snowball discoveries, similar-paper proposals and PRISMA
    records are views of one evidence corpus.  Calibration, screening and score
    context validation must all use this exact set; using ``search_papers``
    alone lets a threshold fitted on A+B silently screen snowball paper C.
    """
    papers: list[Paper] = list(state.search_papers)
    if state.snowball_result:
        papers.extend(state.snowball_result.all_papers.values())
    papers.extend(paper for paper, _score, _method in state.similar_papers)
    papers.extend(record.paper for record in state.prisma.records.values())
    if not papers:
        papers.extend(state.scoping_papers)
    return (filters or RelevanceFilter()).deduplicate_by_doi(papers)


def ranking_query(state: "ReviewState") -> str:
    """Keep the research goal verbatim; use English keywords if a Chinese-only
    goal is paired with an English database query. This is not translation."""
    direction = state.research_direction
    return direction if re.search(r"[a-zA-Z]{3,}", direction) else database_topic(state.topic)


def research_plan(state: "ReviewState", year_from: int, year_to: int) -> dict:
    """Parse the research question and derive one query per provider.

    Recorded in the search manifest so the review can report, per database, the
    exact string that was actually run (PRISMA 2020 item 7).
    """
    return plan_payload(state.topic, state.research_direction, year_from, year_to)


class ReviewPhase(Enum):
    SCOPING = "scoping"
    SYSTEMATIC_SEARCH = "systematic_search"
    SNOWBALLING = "snowballing"
    SCREENING = "screening"
    COMPLETE = "complete"


@dataclass
class ReviewState:
    """Complete mutable state of a literature review session."""

    topic: str
    research_direction: str
    phase: ReviewPhase = ReviewPhase.SCOPING

    # Phase 1 — Scoping
    scoping_papers: list[Paper] = field(default_factory=list)
    topic_clusters: dict[str, list[str]] = field(default_factory=dict)
    key_journals: list[tuple[str, int]] = field(default_factory=list)
    key_authors: list[tuple[str, int]] = field(default_factory=list)
    year_distribution: dict[int, int] = field(default_factory=dict)

    # Phase 2 — Systematic search
    search_papers: list[Paper] = field(default_factory=list)

    # Phase 3 — Snowballing + similar
    snowball_result: SnowballResult | None = None
    similar_papers: list[tuple[Paper, float, str]] = field(default_factory=list)

    # Phase 4 — PRISMA
    prisma: PRISMATracker = field(default_factory=PRISMATracker)

    # Stats
    start_time: float = 0.0
    search_manifest: dict = field(default_factory=dict)

    #: Screening threshold calibration. ``None`` means "never calibrated", in
    #: which case relevance scores rank candidates but decide nothing.
    #:
    #: This is a property: assigning a record also stamps the scoring context it
    #: was fitted in (``calibration_context_id``), so a threshold can never
    #: quietly outlive the scale it was derived from. Plain
    #: ``state.calibration = record`` is the intended usage.
    _calibration: "object | None" = None

    #: The scoring context the current calibration was fitted in. Empty means
    #: "not grounded in any context", which is treated as unusable.
    calibration_context_id: str = ""

    @property
    def calibration(self):
        return self._calibration

    @calibration.setter
    def calibration(self, record):
        self._calibration = record
        # Stamp on assignment; ``None`` clears the stamp too.
        self.calibration_context_id = self.score_context_id if record is not None else ""

    #: The single scoring context the current corpus is scored under. Any
    #: corpus change re-ranks everything and moves this on.
    score_context_id: str = ""

    #: Which step last produced the context (provenance only — never part of the
    #: id, so the same corpus stays comparable across routes).
    score_context_stage: str = ""

    #: Every context this session has passed through, oldest first.
    score_context_history: list[dict] = field(default_factory=list)

    #: Why the last retrieval phase ended, and what it really cost.
    stop_reason: str = ""
    http_budget: dict = field(default_factory=dict)

    #: Appended once per retrieval/expansion phase so the run history is not
    #: collapsed into only the first search manifest.
    run_history: list[dict] = field(default_factory=list)

    def record_run(self, stage: str, **fields) -> dict:
        """Append one expansion/retrieval record to the run history."""
        entry = {
            "stage": stage,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "http": http_budget_snapshot(),
            **fields,
        }
        self.run_history.append(entry)
        return entry

    @property
    def elapsed(self) -> float:
        return time.time() - self.start_time if self.start_time else 0.0

    @property
    def total_papers_tracked(self) -> int:
        report = self.prisma.generate_report()
        return report.records_after_dedup

    @property
    def total_included(self) -> int:
        report = self.prisma.generate_report()
        return report.studies_included


class LiteratureReviewWorkflow:
    """4-phase systematic literature review workflow.

    Phase 1 — Scoping: Broad search, topic landscape, refine strategy
    Phase 2 — Systematic Search: Multi-database keyword search
    Phase 3 — Snowballing: Citation tracing + similar paper discovery
    Phase 4 — Screening + PRISMA: Title/abstract screening, flow diagram

    Implements the gold-standard review methodology described in
    Gusenbauer (2024) and the TARCiS statement, with PRISMA 2020 reporting.
    """

    def __init__(self, source_manager: SourceManager | None = None, downloader: PaperDownloader | None = None):
        self.sources = source_manager or SourceManager()
        self.filters = RelevanceFilter()
        self.downloader = downloader if downloader is not None else PaperDownloader()

    # ------------------------------------------------------------------
    # Phase 1: Scoping
    # ------------------------------------------------------------------

    def scope_topic(
        self,
        topic: str,
        research_direction: str = "",
        years_back: int = 20,
        initial_limit: int = 50,
        use_query_plan: bool = True,
        year_from: int | None = None,
        year_to: int | None = None,
    ) -> ReviewState:
        """Phase 1: Broad scoping search to understand the field landscape.

        Returns a ReviewState with topic clusters, key journals/authors,
        and year distribution to help refine the search strategy.
        """
        state = ReviewState(
            topic=topic,
            research_direction=research_direction or topic,
            phase=ReviewPhase.SCOPING,
            start_time=time.time(),
        )

        year_from = year_from if year_from is not None else year_from_years_back(years_back, floor=1990)
        year_to = year_to if year_to is not None else current_year()
        if not 1990 <= year_from <= year_to <= current_year():
            raise ValueError("Invalid publication year range")
        plan = research_plan(state, year_from, year_to) if use_query_plan else None

        reset_http_budget()
        papers, provider_results, retrieval_failure = self._retrieve(
            state, plan, initial_limit, year_from, year_to
        )
        if retrieval_failure:
            state.stop_reason = StopReason.API_FAILURE.value
            logger.warning("Scoping retrieval incomplete: %s", retrieval_failure)
        papers = self.filters.deduplicate_by_doi(papers)

        self.filters.compute_relevance(papers, ranking_query(state))
        papers = papers[:initial_limit]

        # Topic clusters from keyword co-occurrence
        state.topic_clusters = self._cluster_topics(papers)

        # Journal distribution
        journal_counts: Counter = Counter()
        for p in papers:
            if p.venue:
                journal_counts[p.venue.strip()] += 1
        state.key_journals = journal_counts.most_common(15)

        # Author distribution
        author_counts: Counter = Counter()
        for p in papers:
            for a in p.authors[:3]:
                author_counts[a.name] += 1
        state.key_authors = author_counts.most_common(15)

        # Year distribution
        year_dist: Counter = Counter()
        for p in papers:
            if p.year:
                year_dist[p.year] += 1
        state.year_distribution = dict(sorted(year_dist.items()))

        state.scoping_papers = papers
        state.search_manifest = dict(getattr(self.sources, "last_search_manifest", {}))
        if plan:
            state.search_manifest["research_intent"] = plan
        state.record_run(
            "scoping",
            papers=len(papers),
            stop_reason=state.stop_reason,
            provider_results=[r.to_dict() for r in provider_results],
        )
        logger.info(
            "Scoping complete: topic='%s', papers=%d, journals=%d, authors=%d",
            topic, len(papers), len(state.key_journals), len(state.key_authors),
        )
        return state

    def _cluster_topics(self, papers: list[Paper]) -> dict[str, list[str]]:
        """Simple keyword co-occurrence clustering for topic discovery."""
        keyword_graph: dict[str, Counter] = defaultdict(Counter)
        for p in papers:
            keywords = p.topics + self.filters.extract_keywords(
                (p.title + " " + (p.abstract or ""))[:500], top_n=5
            )
            for i, k1 in enumerate(keywords):
                for k2 in keywords[i + 1 :]:
                    keyword_graph[k1][k2] += 1

        clusters: dict[str, list[str]] = {}
        seen: set[str] = set()
        sorted_kw = sorted(
            keyword_graph.items(),
            key=lambda x: sum(x[1].values()),
            reverse=True,
        )
        for kw, neighbors in sorted_kw[:20]:
            if kw in seen or sum(neighbors.values()) < 2:
                continue
            cluster = [kw] + [n for n, c in neighbors.most_common(5) if c >= 2]
            clusters[kw] = cluster
            seen.update(cluster)

        return clusters

    # ------------------------------------------------------------------
    # Phase 2: Systematic Search
    # ------------------------------------------------------------------

    def rerank_corpus(self, state: ReviewState, stage: str = "") -> str:
        """Re-score the *whole* corpus in one scoring context.

        This is the **single** place a ``score_context_id`` is minted. Relevance
        is min-max normalised inside one result set, so scores from different
        batches are not on the same scale: the best paper of a poor batch of
        three can outrank the median paper of a good batch of three hundred, and
        nothing in the number says so. Every corpus change — the initial search,
        snowballing, coupling/co-citation — therefore re-scores everything in
        one context.

        The context is **content-addressed**: it is derived from the query, the
        corpus content and the ranker version, never from a count or a stage
        label.

        ==========  ==========================================
        changes     context
        ----------  ------------------------------------------
        query       changes
        any paper   changes
        ranker      changes
        input order unchanged
        ==========  ==========================================

        ``stage`` is provenance for the run history and the manifest; it is
        deliberately **not** part of the context id. The same corpus reached by
        a different route is still comparable, and including the stage would
        make an identical corpus look incomparable.

        Calibration is invalidated, because a threshold fitted to the old scale
        is meaningless on the new one. A record that *strictly* proves it still
        applies (``CalibrationRecord.is_valid_for``) is kept.

        Returns the new ``score_context_id``.
        """
        papers = self._corpus_for_ranking(state)
        if not papers:
            return ""

        context_id = self.build_score_context(state, papers)
        self.filters.compute_relevance(papers, ranking_query(state))
        for paper in papers:
            paper.score_context_id = context_id

        # The tracker keeps its own copy of the score for queue ordering and for
        # the screening audit trail; leaving it stale would mean two different
        # numbers for the same paper in the same session.
        sync = getattr(state.prisma, "sync_relevance_scores", None)
        if callable(sync):
            sync()

        previous_context = state.score_context_id
        previous_stage = state.score_context_stage
        state.score_context_id = context_id
        state.score_context_stage = stage or state.score_context_stage
        state.score_context_history.append({
            "stage": stage or "corpus",
            "context_id": context_id,
            "previous_context_id": previous_context,
            "previous_stage": previous_stage,
            "papers": len(papers),
        })

        # A new scale invalidates an old threshold — unless the record can prove
        # it still describes this exact query, corpus and ranker version.
        state.calibration = self._keep_calibration_or_none(state, papers, context_id)
        logger.info(
            "Re-ranked %d papers for one score context (%s, stage=%s, %s)",
            len(papers), context_id, stage or "corpus",
            "calibration kept" if state.calibration is not None else "calibration cleared",
        )
        return context_id

    def prune_corpus(self, state: ReviewState) -> int:
        """Drop papers no longer present in ``state.search_papers``.

        A new search replaces the candidate set, but the expansion results and
        the PRISMA tracker still held the *previous* corpus. Leaving them there
        meant the "whole corpus" that gets scored — and screened — silently
        included papers from an abandoned search, which is how a stale score and
        a stale screening decision survive a new query.

        Returns how many records were removed.
        """
        keep = {p.canonical_id for p in state.search_papers}

        def retained(papers: list[Paper]) -> list[Paper]:
            return [p for p in papers if p.canonical_id in keep]

        removed = 0
        if state.snowball_result is not None:
            before = len(state.snowball_result.all_papers)
            state.snowball_result.all_papers = {
                key: paper for key, paper in state.snowball_result.all_papers.items()
                if paper.canonical_id in keep
            }
            removed += before - len(state.snowball_result.all_papers)

        before_similar = len(state.similar_papers)
        state.similar_papers = [row for row in state.similar_papers if row[0].canonical_id in keep]
        removed += before_similar - len(state.similar_papers)

        drop = [key for key, record in state.prisma.records.items()
                if record.paper.canonical_id not in keep]
        for key in drop:
            state.prisma.records.pop(key, None)
        for key, value in list(state.prisma._alt_keys.items()):
            if value in drop:
                state.prisma._alt_keys.pop(key, None)
        removed += len(drop)

        if removed:
            logger.info("Pruned %d stale corpus records after the new search", removed)
        return removed

    @staticmethod
    def build_score_context(state: ReviewState, papers: list[Paper]) -> str:
        """The content-addressed id for scoring ``papers`` in ``state``.

        Extracted so every scoring path derives the id the same way; two
        generators is how a count-based id appeared next to a hash-based one and
        collided for different corpora.
        """
        query = ranking_query(state) or state.topic or ""
        query_digest = hashlib.sha256(query.strip().casefold().encode("utf-8")).hexdigest()[:12]
        # Identity alone is insufficient: provider enrichment may change the
        # title/abstract/topics while canonical IDs stay stable, and those are
        # the ranker's actual inputs.  Keep both hashes: corpus_hash answers
        # "which papers?", ranking_input_hash answers "what was scored?".
        return (
            f"{query_digest}:{corpus_hash(papers)}:{ranking_input_hash(papers)}:"
            f"{ALGORITHM_VERSION}:{SCORE_VERSION}"
        )

    @staticmethod
    def _keep_calibration_or_none(state, papers, context_id):
        """Keep a calibration only when it provably belongs to this context.

        The default is to clear it: a threshold fitted to a different scale is
        not merely stale, it is a number that will silently mis-screen. The test
        is **which context the record was fitted in**, not whether the context id
        happens to be unchanged — two separate searches can legitimately produce
        the same id (same query, same corpus), and a calibration from the earlier
        run must still not be reused there.

        A calibration survives only when

        * it was stamped with this exact context id, or
        * it carries ``is_valid_for`` and that passes for this exact query,
          corpus hash and ranker version.

        A record that cannot answer the question is not kept. "I can't tell
        whether this threshold still applies" has to mean "do not use it" —
        anything else puts an unjustified cut-off in front of a reviewer.
        """
        calibration = getattr(state, "_calibration", None)
        if calibration is None:
            return None
        if state.calibration_context_id and state.calibration_context_id == context_id:
            return calibration
        is_valid_for = getattr(calibration, "is_valid_for", None)
        if not callable(is_valid_for):
            return None
        try:
            still_valid = bool(is_valid_for(
                query=ranking_query(state),
                corpus_hash=corpus_hash(papers),
                algorithm_version=ALGORITHM_VERSION,
                score_version=SCORE_VERSION,
                score_context_id=context_id,
            ))
        except Exception as exc:  # a calibration must never break a run
            logger.warning("calibration validity check failed: %s", exc)
            return None
        return calibration if still_valid else None

    def _corpus_for_ranking(self, state: ReviewState) -> list[Paper]:
        """Every paper that could be compared or screened in this session."""
        return current_scored_corpus(state, self.filters)

    def _retrieve(
        self,
        state: ReviewState,
        plan: dict | None,
        limit: int,
        year_from: int,
        year_to: int,
    ) -> tuple[list[Paper], list, str | None]:
        """Run the provider search and report *how it went*, not just what came back.

        Returns ``(papers, results, failure_reason)``.

        The point of the third element: an empty list used to be the only signal,
        and it meant nine different things (genuine zero, timeout, rate limit,
        cancel, budget, partial page, provider error, ...). Downstream code then
        guessed — and "the provider failed" was routinely read as "there is
        nothing to find". Here the failure is carried explicitly so the session
        can record ``api_failure`` instead of a completed empty search.

        Falls back to the plain ``search_all_sources`` when a caller provides a
        double that does not implement the result API, so the offline test
        doubles and third-party adapters keep working.
        """
        provider_results = getattr(self.sources, "search_all_sources_result", None)
        if not callable(provider_results):
            papers = self.sources.search_all_sources(
                database_topic(state.topic), limit=limit, year_from=year_from, year_to=year_to,
                query_plan=(plan or {}).get("queries"),
            )
            return list(papers or []), [], None

        results = provider_results(
            database_topic(state.topic), limit=limit, year_from=year_from, year_to=year_to,
            query_plan=(plan or {}).get("queries"),
        ) or []
        papers = [p for result in results for p in (result.papers or [])]

        # Only a *genuine* empty answer may pass silently. Anything else is a
        # failure the caller has to reflect in its reported status.
        failures = [
            f"{result.provider}: {result.status.value}"
            + (f" ({result.error})" if result.error else "")
            for result in results
            if not result.ok and not result.genuine_empty
        ]
        reason = "; ".join(failures) if failures else None
        return papers, results, reason

    def systematic_search(
        self,
        state: ReviewState,
        max_papers: int = 200,
        years_back: int = 20,
        min_citations: int = 0,
        use_query_plan: bool = True,
        year_from: int | None = None,
        year_to: int | None = None,
    ) -> ReviewState:
        """Phase 2: Execute multi-database systematic keyword search.

        Searches S2 + OpenAlex + arXiv + Crossref, deduplicates,
        scores relevance, and registers results in the PRISMA tracker.
        """
        year_from = year_from if year_from is not None else year_from_years_back(years_back, floor=1990)
        year_to = year_to if year_to is not None else current_year()
        if not 1990 <= year_from <= year_to <= current_year():
            raise ValueError("Invalid publication year range")
        plan = research_plan(state, year_from, year_to) if use_query_plan else None

        reset_http_budget()
        raw_papers, provider_results, retrieval_failure = self._retrieve(
            state, plan, max_papers, year_from, year_to
        )
        provider_raw = len(raw_papers)

        # A retrieval that did not complete must not be reported as a finished,
        # empty search. This is the whole reason the provider layer now returns a
        # result object: `[]` used to be indistinguishable from a failure.
        if retrieval_failure:
            state.stop_reason = StopReason.API_FAILURE.value
            logger.warning("Retrieval incomplete: %s", retrieval_failure)

        papers = self.filters.deduplicate_by_doi(raw_papers)
        unique_count = len(papers)
        cross_source_duplicates = max(0, provider_raw - unique_count)

        # The ledger's identity is provider_raw - cross_source_duplicates ==
        # unique_records, so the year filter has to be accounted for before the
        # citation filter or the arithmetic would not close.
        before_year_filter = len(papers)
        papers = [p for p in papers if p.year is None or year_from <= p.year <= year_to]
        filtered_year = before_year_filter - len(papers)

        # Score against the whole candidate set. The scoring itself happens in
        # `rerank_corpus` below — the single implementation of the score context
        # — so this method deliberately does not mint an id of its own.
        #
        # v0.9.2 used `search:{topic}:{year_from}-{year_to}:{len(papers)}` here,
        # which collides: two different corpora of the same size, for the same
        # topic and year window, produced the *same* context id and were
        # therefore treated as comparable — and a calibration fitted on one
        # would be reused on the other.
        before_citation_filter = len(papers)
        if min_citations > 0:
            papers = [p for p in papers if p.citation_count >= min_citations]
        filtered_citations = before_citation_filter - len(papers)

        # Sort by a provisional score so truncation keeps the best candidates;
        # `rerank_corpus` then re-scores the retained set in one context.
        self.filters.compute_relevance(papers, ranking_query(state))
        papers.sort(key=lambda p: p.relevance_score, reverse=True)
        truncated = max(0, len(papers) - max_papers)
        papers = papers[:max_papers]
        # A new search is a new corpus; old screening and expansion decisions
        # must never silently carry over to a different candidate set.
        state.prisma = PRISMATracker()
        state.snowball_result = None
        state.similar_papers = []
        state.search_papers = papers
        state.search_manifest = dict(getattr(self.sources, "last_search_manifest", {}))
        state.search_manifest.update({"unique_before_filters": unique_count, "returned_count": len(papers),
                                      "min_citations": min_citations, "research_direction": state.research_direction,
                                      "ranking_query": ranking_query(state),
                                      "ranking": "hybrid_lexical_v1",
                                      "http_budget": http_budget_snapshot()})
        if plan:
            state.search_manifest["research_intent"] = plan
        # Per-provider completeness travels with the manifest so a reader can
        # see which source was short rather than only that the total was small.
        if provider_results:
            state.search_manifest["provider_results"] = [
                result.to_dict() for result in provider_results
            ]
        if retrieval_failure:
            state.search_manifest["retrieval_failure"] = retrieval_failure
        # Anything the new candidate set dropped must leave the corpus before it
        # is scored, or the "whole corpus" ranking would include abandoned work.
        self.prune_corpus(state)

        # One scoring context for the retained corpus, via the same path
        # snowballing and coupling/co-citation use.
        score_context_id = self.rerank_corpus(state, stage="systematic_search")
        state.search_manifest["score_context_id"] = score_context_id
        for p in papers:
            if not p.discovery_traces:
                p.discovery_traces.append(DiscoveryTrace(method="keyword", provider=p.source, query=state.topic))
            for trace in p.discovery_traces:
                if trace.method == "keyword" and trace.score is None:
                    trace.score = p.relevance_score
        state.phase = ReviewPhase.SYSTEMATIC_SEARCH

        state.prisma.add_papers(papers, source="database_search")

        # PRISMA accounting starts at the provider response, not after our own
        # de-duplication and truncation: otherwise "the database only contained
        # this many" silently replaces "we dropped some", and the process the
        # flow diagram is supposed to document disappears. `retrieval_id` is
        # stable per logical search so re-running it replaces its own numbers
        # instead of inflating the counts.
        self._record_ledger(
            state, stage="systematic_search",
            retrieval_id=f"search:{state.topic}:{year_from}-{year_to}",
            query=ranking_query(state),
            provider_raw=provider_raw,
            unique_records=unique_count,
            cross_source_duplicates=cross_source_duplicates,
            filtered_year=filtered_year,
            filtered_citations=filtered_citations,
            truncated=truncated,
            sources=(state.search_manifest.get("provider_counts") or {}),
        )
        state.record_run(
            "systematic_search",
            query=state.topic,
            provider_raw=provider_raw,
            unique_records=unique_count,
            returned=len(papers),
            score_context_id=score_context_id,
            stop_reason=state.stop_reason,
        )

        logger.info(
            "Systematic search: topic='%s', found=%d papers",
            state.topic, len(papers),
        )
        return state

    @staticmethod
    def _record_ledger(state: ReviewState, **fields) -> None:
        """Call the PRISMA ledger when the installed tracker provides it.

        Kept behind a capability check so this module and ``prisma.py`` can be
        repaired independently without a half-landed change breaking search.
        """
        recorder = getattr(state.prisma, "record_retrieval_ledger", None)
        if not callable(recorder):
            logger.debug("PRISMATracker has no retrieval ledger; skipping accounting")
            return
        try:
            recorder(**fields)
        except TypeError as exc:  # signature drift between modules
            logger.warning("retrieval ledger call rejected: %s", exc)

    # ------------------------------------------------------------------
    # Phase 3: Snowballing + Similar Papers
    # ------------------------------------------------------------------

    def run_snowballing(
        self,
        state: ReviewState,
        num_seeds: int = 10,
        max_rounds: int = 3,
        max_per_direction: int = 50,
        min_citations: int = 0,
        year_from: int = 1900,
        year_to: int | None = None,
        on_round=None,
        resume: bool = False,
        seeds: list[Paper] | None = None,
    ) -> ReviewState:
        """Phase 3a: Iterative forward+backward citation tracing (TARCiS-aligned).

        Uses the top-N most relevant papers from systematic search as seeds,
        then iteratively fetches references (backward) and citations (forward),
        stopping at saturation.

        Pass ``seeds`` to trace citations from explicit papers instead — the
        only workable mode when no keyword search has been run.

        Args:
            seeds: Explicit seed papers. When omitted the most relevant papers
                from the current corpus are used. If neither is available the
                call raises rather than reporting a search that never happened.

        `on_round(round_number, max_rounds, discovered)` fires after each
        round so callers can show progress and checkpoint the session.
        """
        if not state.search_papers and not seeds:
            # Auto-run phase 2 if needed
            self.systematic_search(state)

        # Explicit seeds win, and they are what makes this callable when the
        # search returned nothing. v0.9.1 took the seeds solely from
        # `state.search_papers[:num_seeds]`, so an empty (or failed) search made
        # snowballing a silent no-op that still reported "no new results" — a
        # completed-looking outcome for work that never happened.
        if seeds:
            seed_papers = list(seeds)
        else:
            seed_papers = state.search_papers[:num_seeds]
        if not seed_papers:
            raise ValueError(
                "snowballing needs seed papers: none were supplied and the corpus "
                "is empty, so there is nothing to trace citations from"
            )

        year_to = year_to or current_year()

        engine = SnowballEngine(
            sources=self.sources,
            filters=self.filters,
        )

        # Papers are registered with PRISMA as soon as a round discovers them
        # (see `checkpoint`), so "how many were new" is a running total kept at
        # the point of registration. Re-deriving it afterwards is what produced
        # `unique_records = 0` for a run that had just added papers: by then
        # every one of them was already in the tracker, so every probe answered
        # "already known".
        registered = {"new": 0}

        def checkpoint(partial, discovered):
            """Persist progress after every round and register what it found.

            ``discovered`` is what this round actually added, so incremental
            registration can happen here without the post-run counting having
            to guess what was already registered.
            """
            state.snowball_result = partial
            state.phase = ReviewPhase.SNOWBALLING
            for paper in discovered:
                registered["new"] += state.prisma.add_papers([paper], source="snowballing")

        result = engine.run(
            seed_papers=seed_papers,
            research_direction=ranking_query(state),
            max_rounds=max_rounds,
            max_per_direction=max_per_direction,
            min_citations=min_citations,
            year_from=year_from,
            year_to=year_to,
            on_round=on_round,
            on_checkpoint=checkpoint,
            resume=state.snowball_result if resume else None,
            known_papers=state.search_papers,
        )

        state.snowball_result = result

        # Register anything the per-round checkpoints did not already cover.
        # The counter is the same one the checkpoints incremented, so the total
        # reflects genuinely new records regardless of how many rounds ran.
        for paper in result.all_papers.values():
            registered["new"] += state.prisma.add_papers([paper], source="snowballing")
        new_from_snowball = registered["new"]
        state.phase = ReviewPhase.SNOWBALLING

        # The corpus just grew, so every score is now on a stale scale: re-rank
        # the whole corpus into one context before anything can be compared or
        # screened.
        self.rerank_corpus(state, stage="snowball")

        # Ledger for this expansion. `provider_raw` is what the providers
        # returned across all rounds (counting repeats); the distinct papers we
        # did not already hold are the *unique* records, and everything else is
        # a repeat of something the corpus already had.
        #
        # v0.9.1 had these two swapped here: it wrote `new_from_snowball` into
        # `cross_source_duplicates` and `raw - new` into `unique_records`. The
        # identity `raw - duplicates == unique` still held, so a conservation
        # test passed while every reported number meant the opposite of its
        # name — which is why the regression tests now assert field semantics,
        # not just that the arithmetic adds up.
        raw_observations = sum(round_.raw_count for round_ in result.rounds)
        unique_from_expansion = max(0, new_from_snowball)
        duplicates_from_expansion = max(0, raw_observations - unique_from_expansion)
        self._record_ledger(
            state, stage="snowballing",
            retrieval_id=f"snowball:{state.topic}:{max_rounds}",
            query=ranking_query(state),
            provider_raw=raw_observations,
            unique_records=unique_from_expansion,
            cross_source_duplicates=duplicates_from_expansion,
            sources={"rounds": len(result.rounds)},
        )
        state.record_run(
            "snowballing",
            rounds=len(result.rounds),
            discovered=result.total_discovered,
            stop_reason=result.stop_reason,
            saturation_reason=result.saturation_reason,
            failed_seeds=result.failed_seed_count,
            pending_seeds=len(result.next_seed_ids),
        )

        logger.info(
            "Snowballing: rounds=%d, discovered=%d, stop_reason=%s",
            len(result.rounds), result.total_discovered, result.stop_reason,
        )
        return state

    def find_similar(
        self,
        state: ReviewState,
        num_seeds: int = 10,
        top_k: int = 30,
    ) -> ReviewState:
        """Phase 3b: Find similar papers via bibliographic coupling + co-citation."""
        if not state.search_papers:
            self.systematic_search(state)

        seeds = state.search_papers[:num_seeds]

        finder = SimilarPaperFinder(sources=self.sources)
        similar = finder.find_similar(seeds, top_k=top_k)
        self.filters.compute_relevance([p for p, _, _ in similar], ranking_query(state))

        # Read the finder's completeness state defensively. `SimilarPaperFinder`
        # always defines these, but this method is also handed stand-in finders
        # (tests, and any future implementation), and a bare attribute access
        # made a missing attribute raise *after* the expensive work was done.
        # Absence means "did not report anything incomplete", which is the only
        # safe reading: an implementation that cannot report incompleteness must
        # not be assumed to have reported it.
        finder_incomplete = bool(getattr(finder, "incomplete", False))
        finder_stop_reason = getattr(finder, "stop_reason", "") or ""
        finder_results = list(getattr(finder, "retrieval_results", ()) or ())
        if finder_incomplete:
            state.stop_reason = finder_stop_reason or StopReason.API_FAILURE.value

        state.similar_papers = similar

        # Same accounting principle as snowballing: ask the tracker how many of
        # these candidates it had not seen, instead of re-deriving identity here
        # and getting a different answer than the tracker did.
        unique_from_similar = 0
        for paper, _score, _method in similar:
            unique_from_similar += state.prisma.add_papers([paper], source="similar")

        state.phase = ReviewPhase.SNOWBALLING

        # Same reason as after snowballing: candidates were added, so the
        # comparable set changed and everything is re-scored together.
        self.rerank_corpus(state, stage="similar")

        # Coupling/co-citation produce *derived proposals* rather than database
        # records: every candidate here has already been matched against the
        # corpus, so the ones we already held are the duplicates and the rest
        # are genuinely new records. Reporting all of them as duplicates (as
        # v0.9.1 did) made a stage that found 20 new papers look like a stage
        # that found nothing.
        self._record_ledger(
            state, stage="similar",
            retrieval_id=f"similar:{state.topic}:{top_k}",
            query=ranking_query(state),
            provider_raw=len(similar),
            unique_records=unique_from_similar,
            cross_source_duplicates=max(0, len(similar) - unique_from_similar),
            sources={"bibliographic_coupling": sum(1 for *_, m in similar if m == "bibliographic_coupling"),
                     "co_citation": sum(1 for *_, m in similar if m == "co_citation")},
        )
        state.record_run(
            "similar",
            found=len(similar),
            bc=sum(1 for *_, m in similar if m == "bibliographic_coupling"),
            cc=sum(1 for *_, m in similar if m == "co_citation"),
            incomplete=finder_incomplete,
            stop_reason=finder_stop_reason,
            retrieval_results=[r.to_dict() for r in finder_results],
        )

        logger.info(
            "Similar papers: found=%d (bc=%d, cc=%d)",
            len(similar),
            sum(1 for _, _, m in similar if m == "bibliographic_coupling"),
            sum(1 for _, _, m in similar if m == "co_citation"),
        )
        return state

    # ------------------------------------------------------------------
    # Phase 4: Screening + PRISMA
    # ------------------------------------------------------------------

    def get_screening_queue(
        self, state: ReviewState, stage: ScreeningStage = ScreeningStage.TITLE_ABSTRACT
    ) -> list:
        """Get papers pending at a given PRISMA stage, sorted by relevance."""
        return state.prisma.get_screening_queue(stage)

    def screen_paper(
        self,
        state: ReviewState,
        paper_id: str,
        decision: ScreeningDecision,
        reason: str = "",
        stage: ScreeningStage = ScreeningStage.TITLE_ABSTRACT,
    ) -> None:
        """Record a single screening decision at one stage."""
        state.prisma.screen_paper(paper_id, decision, reason, stage=stage)

    def mark_full_text_retrieved(
        self, state: ReviewState, paper_id: str, retrieved: bool = True
    ) -> None:
        """Record whether a full text could be obtained (eligibility stage)."""
        state.prisma.mark_full_text_retrieved(paper_id, retrieved)

    def auto_screen(
        self,
        state: ReviewState,
        relevance_threshold: float | None = None,
        stage: ScreeningStage = ScreeningStage.TITLE_ABSTRACT,
    ) -> ReviewState:
        """Apply a *calibrated* threshold as screening assistance.

        Two rules, both of which v0.9.0 broke:

        1. **Never on FULL_TEXT.** Including a study at the eligibility stage
           requires a human to have read it. There is no score that can stand
           in for that, so the call is refused outright rather than being
           quietly downgraded.
        2. **Never on an uncalibrated score.** Relevance is min-max normalised
           inside one result set, so a fixed 0.15 means something different in
           every search. Without a valid :class:`CalibrationRecord` this method
           is a no-op that leaves everything ``pending`` — it does not reject
           papers on a number it cannot justify.

        ``relevance_threshold`` is an explicit human override; passing it is a
        deliberate act, not a default.
        """
        if stage is ScreeningStage.FULL_TEXT:
            raise ValueError(
                "automatic threshold screening is not allowed on FULL_TEXT; "
                "full-text inclusion requires explicit human assessment"
            )

        calibration = getattr(state, "calibration", None)
        if relevance_threshold is None:
            if calibration is None or not getattr(calibration, "usable", False):
                logger.info(
                    "auto_screen skipped: no usable calibration "
                    "(relevance stays ranking-only, nothing is rejected)"
                )
                return state
            relevance_threshold = calibration.threshold
            if relevance_threshold is None:
                return state

        for record in state.prisma.get_screening_queue(stage):
            decision = (
                ScreeningDecision.ACCEPT
                if record.relevance_score >= relevance_threshold
                else ScreeningDecision.REJECT
            )
            state.prisma.screen_paper(
                record.paper.id,
                decision,
                reason=(
                    f"校准阈值辅助判定 / calibrated-threshold assistance "
                    f"({record.relevance_score:.2f} vs {relevance_threshold:.2f}); "
                    f"可人工复核撤销 / reversible by a human reviewer"
                ),
                stage=stage,
            )
        state.phase = ReviewPhase.SCREENING

        accepted = len(state.prisma.get_accepted_ids())
        logger.info(
            "Auto-screen(%s): accepted=%d by threshold=%.2f (assistance only)",
            stage.value, accepted, relevance_threshold,
        )
        return state

    def generate_prisma_report(self, state: ReviewState) -> PRISMAReport:
        """Generate PRISMA 2020 flow diagram report."""
        report = state.prisma.generate_report()
        unresolved = any(r.screening_decision in (ScreeningDecision.PENDING, ScreeningDecision.MAYBE)
                         or (r.passed_screening and r.full_text_decision in (ScreeningDecision.PENDING, ScreeningDecision.MAYBE))
                         for r in state.prisma.records.values())
        if report.full_text_stage_enabled and not unresolved:
            state.phase = ReviewPhase.COMPLETE
        return report

    def get_included_papers(self, state: ReviewState) -> list[Paper]:
        """Get all papers that passed screening."""
        return state.prisma.get_included_papers()

    # ------------------------------------------------------------------
    # Full pipeline
    # ------------------------------------------------------------------

    def run_full_workflow(
        self,
        topic: str,
        research_direction: str = "",
        years_back: int = 20,
        max_search_papers: int = 200,
        snowball_rounds: int = 3,
        auto_screen: bool = False,
        relevance_threshold: float | None = None,
    ) -> ReviewState:
        """Run the complete 4-phase workflow in one call.

        Automatic screening is **off by default**. It is only defensible once a
        human has calibrated a threshold against labelled papers, so the caller
        must ask for it explicitly; and even then the outcome is recorded as
        reviewer assistance that a human can overturn, never as a final
        inclusion decision.

        Returns the final ReviewState ready for PRISMA reporting and download.
        """
        t0 = time.time()
        direction = research_direction or topic

        state = self.scope_topic(topic, direction, years_back=years_back)
        self.systematic_search(state, max_papers=max_search_papers, years_back=years_back)
        self.run_snowballing(state, max_rounds=snowball_rounds)
        self.find_similar(state)
        if auto_screen:
            self.auto_screen(state, relevance_threshold=relevance_threshold)
        self.generate_prisma_report(state)

        elapsed = time.time() - t0
        logger.info(
            "Full workflow: topic='%s', tracked=%d, included=%d, %.1fs",
            topic, state.total_papers_tracked, state.total_included, elapsed,
        )
        return state

    # ------------------------------------------------------------------
    # Downloads
    # ------------------------------------------------------------------

    def download_included(self, state: ReviewState) -> list[tuple[Paper, str | None]]:
        """Download all papers that passed screening."""
        included = self.get_included_papers(state)
        return self.downloader.batch_download(included)

    # ------------------------------------------------------------------
    # Export helpers
    # ------------------------------------------------------------------

    def export_included_csv(self, state: ReviewState) -> str:
        from litsearch.export import to_csv
        return to_csv(self.get_included_papers(state))

    def export_included_ris(self, state: ReviewState) -> str:
        from litsearch.export import to_ris
        return to_ris(self.get_included_papers(state))
