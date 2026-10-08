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
from litsearch.models import Paper
from litsearch.sources import SourceManager

logger = logging.getLogger(__name__)


def paper_key(paper: Paper) -> str:
    """Canonical, case-insensitive identity for deduplication.

    DOIs and IDs were previously mixed with mixed casing, which silently
    defeated every `key not in all_papers` check in this module.
    """
    return (paper.doi or paper.id or "").strip().lower()


@dataclass
class SnowballRound:
    """Results from one round of snowballing."""

    round_number: int
    source_papers: list[str]  # Paper IDs used as seeds for this round
    new_papers: list[Paper]   # New papers discovered this round
    direction: str            # "backward" | "forward" | "both"

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

    @property
    def total_discovered(self) -> int:
        return len(self.all_papers)


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
        result = SnowballResult()
        year_to = year_to or current_year()

        # Initialize with seed papers
        current_seeds: list[Paper] = list(seed_papers)
        all_papers: dict[str, Paper] = {}
        for sp in seed_papers:
            all_papers[paper_key(sp)] = sp

        for round_num in range(1, max_rounds + 1):
            logger.info("Snowball round %d: %d seeds", round_num, len(current_seeds))

            # Fetch citations and references for all current seeds
            new_backward = self._fetch_all(
                current_seeds, direction="backward",
                limit=max_per_direction, year_from=year_from, year_to=year_to,
            )
            new_forward = self._fetch_all(
                current_seeds, direction="forward",
                limit=max_per_direction, year_from=year_from, year_to=year_to,
            )

            # Merge and deduplicate against known papers
            all_new = []
            seen_in_round = set()
            for p in new_backward + new_forward:
                key = paper_key(p)
                if key not in all_papers and key not in seen_in_round:
                    seen_in_round.add(key)
                    all_new.append(p)

            if not all_new:
                result.saturated = True
                result.saturation_reason = f"No new papers found in round {round_num}"
                break

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
            )
            result.rounds.append(round_result)

            if on_round is not None:
                try:
                    on_round(round_num, max_rounds, len(all_papers))
                except Exception as e:  # progress callbacks must never break a run
                    logger.warning("on_round callback failed: %s", e)

            # Add to master collection
            for p in all_new:
                all_papers[paper_key(p)] = p

            # Next round's seeds: new papers with highest relevance
            all_new.sort(
                key=lambda p: p.relevance_score * math.log(p.citation_count + 2),
                reverse=True,
            )
            current_seeds = all_new[:20]  # Top 20 become next seeds

            # Check saturation: if < 5 new papers, stop
            if len(all_new) < 5:
                result.saturated = True
                result.saturation_reason = f"Only {len(all_new)} new papers in round {round_num}"
                break

        result.all_papers = all_papers
        return result

    def _fetch_all(
        self, seeds: list[Paper], direction: str, limit: int,
        year_from: int, year_to: int,
    ) -> list[Paper]:
        """Fetch citations or references for multiple seeds, deduplicating."""
        all_results: list[Paper] = []
        seen: set[str] = set()

        for seed in seeds:
            try:
                if direction == "backward":
                    papers = self.sources.get_references(seed.id, limit=limit)
                else:
                    papers = self.sources.get_citations(seed.id, limit=limit)

                for p in papers:
                    key = (p.doi or p.id).lower()
                    if key not in seen:
                        seen.add(key)
                        # Year filter
                        if p.year and (p.year < year_from or p.year > year_to):
                            continue
                        all_results.append(p)

                time.sleep(0.5)  # Rate limit
            except Exception as e:
                logger.warning("Snowball %s for %s: %s", direction, seed.id, e)

        return all_results
