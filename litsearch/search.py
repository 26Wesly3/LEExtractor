"""Systematic literature review workflow: Scoping → Search → Snowballing → PRISMA.

Gold-standard review methodology:
- Gusenbauer (2024): multi-database systematic search
- TARCiS (2024): formal citation searching guidance
- PRISMA 2020: standard flow diagram for reporting
"""

import logging
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import Enum

from litsearch.config import current_year, year_from_years_back
from litsearch.downloader import PaperDownloader
from litsearch.filters import RelevanceFilter
from litsearch.intent import plan_payload
from litsearch.models import DiscoveryTrace, Paper
from litsearch.prisma import (
    PRISMAReport,
    PRISMATracker,
    ScreeningDecision,
    ScreeningStage,
)
from litsearch.similar import SimilarPaperFinder
from litsearch.snowball import SnowballEngine, SnowballResult, paper_key
from litsearch.sources import SourceManager
from litsearch.stop_reasons import (
    http_budget_snapshot,
    reset_http_budget,
)

logger = logging.getLogger(__name__)


def ranking_query(state: "ReviewState") -> str:
    """Keep the research goal verbatim; use English keywords if a Chinese-only
    goal is paired with an English database query. This is not translation."""
    direction = state.research_direction
    return direction if re.search(r"[a-zA-Z]{3,}", direction) else state.topic


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
    calibration: "object | None" = None

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

    def __init__(self, source_manager: SourceManager | None = None):
        self.sources = source_manager or SourceManager()
        self.filters = RelevanceFilter()
        self.downloader = PaperDownloader()

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

        year_from = year_from_years_back(years_back, floor=1990)
        year_to = current_year()
        plan = research_plan(state, year_from, year_to) if use_query_plan else None

        papers = self.sources.search_all_sources(
            topic, limit=initial_limit, year_from=year_from, year_to=year_to,
            query_plan=(plan or {}).get("queries"),
        )
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

    def systematic_search(
        self,
        state: ReviewState,
        max_papers: int = 200,
        years_back: int = 20,
        min_citations: int = 0,
        use_query_plan: bool = True,
    ) -> ReviewState:
        """Phase 2: Execute multi-database systematic keyword search.

        Searches S2 + OpenAlex + arXiv + Crossref, deduplicates,
        scores relevance, and registers results in the PRISMA tracker.
        """
        year_from = year_from_years_back(years_back, floor=1990)
        year_to = current_year()
        plan = research_plan(state, year_from, year_to) if use_query_plan else None

        reset_http_budget()
        raw_papers = self.sources.search_all_sources(
            state.topic, limit=max_papers, year_from=year_from, year_to=year_to,
            query_plan=(plan or {}).get("queries"),
        )
        provider_raw = len(raw_papers)

        papers = self.filters.deduplicate_by_doi(raw_papers)
        unique_count = len(papers)
        cross_source_duplicates = max(0, provider_raw - unique_count)

        # The ledger's identity is provider_raw - cross_source_duplicates ==
        # unique_records, so the year filter has to be accounted for before the
        # citation filter or the arithmetic would not close.
        before_year_filter = len(papers)
        papers = [p for p in papers if p.year is None or year_from <= p.year <= year_to]
        filtered_year = before_year_filter - len(papers)

        # Score against the *whole* deduplicated candidate set, and label the
        # batch: scores are relative within one scoring context, so a later
        # expansion must not be compared against these numbers directly.
        score_context_id = f"search:{state.topic}:{year_from}-{year_to}:{len(papers)}"
        self.filters.compute_relevance(papers, ranking_query(state))
        for p in papers:
            p.score_context_id = score_context_id

        before_citation_filter = len(papers)
        if min_citations > 0:
            papers = [p for p in papers if p.citation_count >= min_citations]
        filtered_citations = before_citation_filter - len(papers)

        papers.sort(key=lambda p: p.relevance_score, reverse=True)
        truncated = max(0, len(papers) - max_papers)
        papers = papers[:max_papers]
        # A new search is a new corpus; old screening and expansion decisions
        # must never silently carry over to a different candidate set.
        state.prisma = PRISMATracker()
        state.snowball_result = None
        state.similar_papers = []
        state.calibration = None  # a new query invalidates any old calibration
        state.search_papers = papers
        state.search_manifest = dict(getattr(self.sources, "last_search_manifest", {}))
        state.search_manifest.update({"unique_before_filters": unique_count, "returned_count": len(papers),
                                      "min_citations": min_citations, "research_direction": state.research_direction,
                                      "ranking_query": ranking_query(state),
                                      "ranking": "hybrid_lexical_v1",
                                      "score_context_id": score_context_id,
                                      "http_budget": http_budget_snapshot()})
        if plan:
            state.search_manifest["research_intent"] = plan
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
    ) -> ReviewState:
        """Phase 3a: Iterative forward+backward citation tracing (TARCiS-aligned).

        Uses the top-N most relevant papers from systematic search as seeds,
        then iteratively fetches references (backward) and citations (forward),
        stopping at saturation.

        `on_round(round_number, max_rounds, discovered)` fires after each
        round so callers can show progress and checkpoint the session.
        """
        if not state.search_papers:
            # Auto-run phase 2 if needed
            self.systematic_search(state)

        seeds = state.search_papers[:num_seeds]

        year_to = year_to or current_year()

        engine = SnowballEngine(
            sources=self.sources,
            filters=self.filters,
        )

        def checkpoint(partial):
            state.snowball_result = partial
            state.phase = ReviewPhase.SNOWBALLING
            for paper in partial.all_papers.values():
                if state.prisma._find_key(paper.id) is None:
                    state.prisma.add_papers([paper], source="snowballing")

        result = engine.run(
            seed_papers=seeds,
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

        # Add newly discovered papers to PRISMA (skip those already in search results)
        existing_ids = {
            paper_key(p) for p in state.search_papers
        }
        new_from_snowball = 0
        for paper in result.all_papers.values():
            key = paper_key(paper)
            if key not in existing_ids and state.prisma._find_key(paper.id) is None:
                state.prisma.add_papers([paper], source="snowballing")
                new_from_snowball += 1
        state.phase = ReviewPhase.SNOWBALLING

        # Ledger for this expansion: the raw count is what the providers
        # returned across all rounds, which is larger than what survived
        # de-duplication against the existing corpus.
        raw_observations = sum(round_.raw_count for round_ in result.rounds)
        self._record_ledger(
            state, stage="snowballing",
            retrieval_id=f"snowball:{state.topic}:{max_rounds}",
            query=ranking_query(state),
            provider_raw=raw_observations,
            unique_records=max(0, raw_observations - new_from_snowball),
            cross_source_duplicates=new_from_snowball,
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

        state.similar_papers = similar

        for paper, _score, _method in similar:
            state.prisma.add_papers([paper], source="similar")

        state.phase = ReviewPhase.SNOWBALLING

        # Coupling/co-citation candidates are *derived* proposals, not database
        # records: they are already past de-duplication when they arrive, so the
        # ledger records them as recovered (duplicate) rather than raw hits.
        self._record_ledger(
            state, stage="similar",
            retrieval_id=f"similar:{state.topic}:{top_k}",
            query=ranking_query(state),
            provider_raw=len(similar),
            unique_records=0,
            cross_source_duplicates=len(similar),
            sources={"bibliographic_coupling": sum(1 for *_, m in similar if m == "bibliographic_coupling"),
                     "co_citation": sum(1 for *_, m in similar if m == "co_citation")},
        )
        state.record_run(
            "similar",
            found=len(similar),
            bc=sum(1 for *_, m in similar if m == "bibliographic_coupling"),
            cc=sum(1 for *_, m in similar if m == "co_citation"),
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

        Two rules, both of which v0.8.0 broke:

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
