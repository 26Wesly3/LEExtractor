"""Iterative citation tracing engine (forward + backward snowballing).

Implements the TARCiS-statement-aligned snowballing methodology:
- Backward: trace references of seed papers (older foundations)
- Forward: trace papers citing seed papers (newer developments)
- Iterative rounds until saturation (no new relevant papers)
"""

import logging
import math
import time
from dataclasses import dataclass, field

from litsearch.config import current_year
from litsearch.filters import RelevanceFilter
from litsearch.models import DiscoveryTrace, Paper
from litsearch.sources import SourceManager
from litsearch.stop_reasons import (
    LOW_YIELD_THRESHOLD,
    StopReason,
    is_complete,
    reason_label,
)

logger = logging.getLogger(__name__)


def paper_key(paper: Paper) -> str:
    """Canonical, case-insensitive identity for deduplication.

    DOIs and IDs were previously mixed with mixed casing, which silently
    defeated every `key not in all_papers` check in this module.
    """
    return paper.canonical_id


@dataclass
class SnowballRound:
    """Results from one round of snowballing."""

    round_number: int
    source_papers: list[str]  # Paper IDs used as seeds for this round
    new_papers: list[Paper]   # New papers discovered this round
    direction: str            # "backward" | "forward" | "both"
    raw_count: int = 0
    unique_count: int = 0
    relevant_count: int = 0
    cumulative_unique: int = 0
    #: Seeds whose citation/reference lookup raised. A round with failures is
    #: an incomplete round, not evidence that citation searching is saturated.
    failed_seeds: int = 0
    #: Canonical ids still owed a lookup, so an interrupted round can resume
    #: instead of silently treating the lost seeds as exhausted.
    pending_seeds: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.new_papers)

    @property
    def had_failures(self) -> bool:
        return self.failed_seeds > 0


@dataclass
class SnowballResult:
    """Complete snowballing result across all rounds."""

    rounds: list[SnowballRound] = field(default_factory=list)
    all_papers: dict[str, Paper] = field(default_factory=dict)
    saturated: bool = False
    saturation_reason: str = ""
    initial_ids: list[str] = field(default_factory=list)
    next_seed_ids: list[str] = field(default_factory=list)
    parameters: dict = field(default_factory=dict)
    completed: bool = False
    #: Why the run ended — see :class:`litsearch.stop_reasons.StopReason`.
    #: Only "saturated" / "no_new_results" mean the search really finished.
    stop_reason: str = ""

    @property
    def total_discovered(self) -> int:
        return len(set(self.all_papers) - set(self.initial_ids)) if self.initial_ids else sum(r.count for r in self.rounds)

    @property
    def failed_seed_count(self) -> int:
        return sum(r.failed_seeds for r in self.rounds)

    @property
    def is_complete(self) -> bool:
        """True only when the stop reason permits a coverage claim."""
        return self.completed and is_complete(self.stop_reason)

    @property
    def stop_reason_label(self) -> str:
        return reason_label(self.stop_reason) if self.stop_reason else ""


class SnowballEngine:
    """Iterative forward + backward citation tracing.

    Starts from a set of seed papers, then iteratively:
    1. Fetches references (backward) and citations (forward) for each seed
    2. Filters by relevance and deduplication
    3. Uses newly discovered papers as seeds for the next round
    4. Stops when saturation is reached (no new papers)

    This implements the TARCiS-statement methodology for citation searching
    in systematic literature reviews.
    """

    def __init__(self, sources: SourceManager, filters: RelevanceFilter):
        self.sources = sources
        self.filters = filters

    def _canceled(self) -> bool:
        """Poll the source manager's cancel hook, tolerating light test doubles."""
        check = getattr(self.sources, "is_canceled", None)
        if not callable(check):
            return False
        try:
            return bool(check())
        except Exception:  # a broken hook must not abort the run
            return False

    def run(
        self,
        seed_papers: list[Paper],
        research_direction: str,
        max_rounds: int = 3,
        max_per_direction: int = 50,
        min_citations: int = 0,
        year_from: int = 1900,
        year_to: int | None = None,
        on_round=None,
        on_checkpoint=None,
        resume: SnowballResult | None = None,
        known_papers: list[Paper] | None = None,
    ) -> SnowballResult:
        """Execute iterative snowballing from seed papers.

        Args:
            seed_papers: Initial set of papers to snowball from.
            research_direction: Used for relevance scoring new papers.
            max_rounds: Maximum snowball iterations.
            max_per_direction: Max papers to fetch per seed per direction.
            min_citations: Filter out papers below this citation count.
            year_from, year_to: Year filter range.

        Returns:
            SnowballResult with all rounds and discovered papers.
        """
        year_to = year_to or current_year()
        parameters = {"seed_ids": [paper_key(p) for p in seed_papers], "research_direction": research_direction,
                      "max_per_direction": max_per_direction, "min_citations": min_citations,
                      "year_from": year_from, "year_to": year_to}
        if resume and resume.parameters != parameters:
            raise ValueError("追溯参数已改变，请开始新的追溯 / checkpoint parameters differ")
        result = resume or SnowballResult(parameters=parameters)
        if result.saturated:
            return result
        result.completed = False

        # Initialize with seed papers
        if resume:
            all_papers = result.all_papers
            current_seeds = [all_papers[k] for k in result.next_seed_ids if k in all_papers]
        else:
            current_seeds = list(seed_papers)
            initial = self.filters.deduplicate_by_doi([*(known_papers or []), *seed_papers])
            all_papers = {paper_key(p): p for p in initial}
            result.initial_ids = list(all_papers)
        result.all_papers = all_papers

        for round_num in range(len(result.rounds) + 1, max_rounds + 1):
            if self._canceled():
                result.stop_reason = StopReason.CANCELED.value
                result.saturation_reason = reason_label(StopReason.CANCELED)
                result.completed = False
                return result
            logger.info("Snowball round %d: %d seeds", round_num, len(current_seeds))

            # Fetch citations and references for all current seeds
            new_backward, failed_backward = self._fetch_all(
                current_seeds, direction="backward",
                limit=max_per_direction, year_from=year_from, year_to=year_to,
                round_no=round_num,
            )
            new_forward, failed_forward = self._fetch_all(
                current_seeds, direction="forward",
                limit=max_per_direction, year_from=year_from, year_to=year_to,
                round_no=round_num,
            )
            failed_seeds = failed_backward + failed_forward
            # Merge and deduplicate against known papers
            observations = new_backward + new_forward
            initial_objects = {id(all_papers[key]) for key in result.initial_ids if key in all_papers}
            merged = self.filters.deduplicate_by_doi([*all_papers.values(), *observations])
            result.initial_ids = [paper_key(p) for p in merged if id(p) in initial_objects]
            known_objects = {id(p) for p in all_papers.values()}
            all_new = [p for p in merged if id(p) not in known_objects]
            unique_count = len(all_new)
            all_papers = {paper_key(p): p for p in merged if id(p) in known_objects}
            result.all_papers = all_papers

            # Score relevance
            self.filters.compute_relevance(all_new, research_direction)

            # Filter by citation threshold
            if min_citations > 0:
                all_new = [p for p in all_new if p.citation_count >= min_citations]

            # Record round
            round_result = SnowballRound(
                round_number=round_num,
                source_papers=[p.id for p in current_seeds],
                new_papers=all_new,
                direction="both",
                raw_count=len(observations),
                unique_count=unique_count,
                relevant_count=sum(p.relevance_score >= 0.15 for p in all_new),
                cumulative_unique=len(all_papers) - len(result.initial_ids) + len(all_new),
                failed_seeds=len(failed_seeds),
                pending_seeds=failed_seeds,
            )
            result.rounds.append(round_result)

            # Add to master collection
            for p in all_new:
                all_papers[paper_key(p)] = p

            # Next round's seeds: new papers with highest relevance
            all_new.sort(
                key=lambda p: p.relevance_score * math.log(p.citation_count + 2),
                reverse=True,
            )
            current_seeds = all_new[:20]  # Top 20 become next seeds
            result.next_seed_ids = [paper_key(p) for p in current_seeds]

            # ----------------------------------------------------------
            # Decide why (or whether) to stop. The ordering matters: a round
            # whose lookups failed produced a small `all_new` *because it
            # failed*, so it must be classified as a failure before any
            # yield-based reading of the same number.
            # ----------------------------------------------------------
            last_round = round_num >= max_rounds
            if failed_seeds:
                result.stop_reason = StopReason.API_FAILURE.value
                result.saturation_reason = (
                    f"第 {round_num} 轮有 {len(failed_seeds)} 个种子的引文/参考文献请求失败，"
                    f"结果不完整，可用相同参数断点续跑。 "
                    f"Round {round_num} lost {len(failed_seeds)} seed lookups to source failures; "
                    f"this is NOT evidence of saturation."
                )
                # The seeds that were never answered stay queued so a resumed
                # run repeats exactly the lookups that were lost. Without this
                # the queue would be the *newly found* papers only, and the
                # interrupted work would be silently abandoned.
                queued = [*failed_seeds, *result.next_seed_ids]
                result.next_seed_ids = list(dict.fromkeys(queued))
                result.completed = False
                if on_checkpoint is not None:
                    on_checkpoint(result)
                if on_round is not None:
                    on_round(round_num, max_rounds, len(all_new))
                return result

            if len(all_new) == 0:
                # Every source answered normally with nothing new: the only
                # case that may claim real coverage.
                result.stop_reason = (
                    StopReason.NO_NEW_RESULTS.value
                    if not result.rounds[:-1]
                    else StopReason.SATURATED.value
                )
                result.saturated = True
                result.saturation_reason = reason_label(StopReason(result.stop_reason))
                result.completed = True
                if on_checkpoint is not None:
                    on_checkpoint(result)
                if on_round is not None:
                    on_round(round_num, max_rounds, len(all_new))
                return result

            if len(all_new) < LOW_YIELD_THRESHOLD:
                # A product heuristic, explicitly not proof of coverage.
                result.stop_reason = StopReason.LOW_YIELD.value
                result.saturated = False
                result.saturation_reason = (
                    f"第 {round_num} 轮仅新增 {len(all_new)} 篇（< {LOW_YIELD_THRESHOLD}），"
                    f"按产品启发式停止；这不代表该主题已被检索穷尽。 "
                    f"Only {len(all_new)} new papers in round {round_num}: a heuristic stop, "
                    f"not proof of coverage."
                )
                result.completed = True
                if on_checkpoint is not None:
                    on_checkpoint(result)
                if on_round is not None:
                    on_round(round_num, max_rounds, len(all_new))
                return result

            if on_checkpoint is not None:
                on_checkpoint(result)
            if on_round is not None:
                on_round(round_num, max_rounds, len(all_new))

            if last_round:
                result.stop_reason = StopReason.MAX_ROUNDS.value
                result.saturation_reason = reason_label(StopReason.MAX_ROUNDS)
                result.completed = True
                return result

        # Loop finished without breaking: the round cap was the binding limit.
        if not result.stop_reason:
            result.stop_reason = StopReason.MAX_ROUNDS.value
            result.saturation_reason = reason_label(StopReason.MAX_ROUNDS)
        result.all_papers = all_papers
        result.completed = True
        return result

    def _fetch_all(
        self, seeds: list[Paper], direction: str, limit: int,
        year_from: int, year_to: int,
        round_no: int = 1,
    ) -> tuple[list[Paper], list[str]]:
        """Fetch citations or references for multiple seeds, deduplicating.

        Returns ``(papers, failed_seed_ids)``. The failures are returned as
        identities, not just counted: a round that lost half its lookups must
        neither be read as a round that found nothing (which is how a failing
        API used to turn into "the literature is saturated") nor lose track of
        which lookups still need to happen when the run resumes.
        """
        all_results: list[Paper] = []
        failed: list[str] = []

        for seed in seeds:
            if self._canceled():
                failed.append(seed.canonical_id)
                break
            try:
                if direction == "backward":
                    papers = self.sources.get_references(seed, limit=limit)
                else:
                    papers = self.sources.get_citations(seed, limit=limit)

                for p in papers:
                    if p.year and (p.year < year_from or p.year > year_to):
                        continue
                    p.discovery_traces.append(DiscoveryTrace(
                        method="backward_citation" if direction == "backward" else "forward_citation",
                        provider=p.source, seed_id=seed.canonical_id, round_no=round_no,
                    ))
                    all_results.append(p)

                time.sleep(getattr(self.sources, "snowball_delay", 0.5))
            except Exception as e:
                failed.append(seed.canonical_id)
                logger.warning("Snowball %s for %s: %s", direction, seed.id, e)

        return all_results, failed
