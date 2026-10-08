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

    @property
    def count(self) -> int:
        return len(self.new_papers)


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

    @property
    def total_discovered(self) -> int:
        return len(set(self.all_papers) - set(self.initial_ids)) if self.initial_ids else sum(r.count for r in self.rounds)


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
            logger.info("Snowball round %d: %d seeds", round_num, len(current_seeds))

            # Fetch citations and references for all current seeds
            new_backward = self._fetch_all(
                current_seeds, direction="backward",
                limit=max_per_direction, year_from=year_from, year_to=year_to,
                round_no=round_num,
            )
            new_forward = self._fetch_all(
                current_seeds, direction="forward",
                limit=max_per_direction, year_from=year_from, year_to=year_to,
                round_no=round_num,
            )

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

            # Check saturation: if < 5 new papers, stop
            if len(all_new) < 5:
                result.saturated = True
                result.saturation_reason = f"Only {len(all_new)} new papers in round {round_num}"
            if on_checkpoint is not None:
                on_checkpoint(result)
            if on_round is not None:
                on_round(round_num, max_rounds, len(all_new))
            if result.saturated:
                break

        result.all_papers = all_papers
        result.completed = True
        return result

    def _fetch_all(
        self, seeds: list[Paper], direction: str, limit: int,
        year_from: int, year_to: int,
        round_no: int = 1,
    ) -> list[Paper]:
        """Fetch citations or references for multiple seeds, deduplicating."""
        all_results: list[Paper] = []

        for seed in seeds:
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
                logger.warning("Snowball %s for %s: %s", direction, seed.id, e)

        return all_results
