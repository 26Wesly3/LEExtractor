"""Systematic Literature Review Toolkit — LEExtractor."""

__version__ = "0.6.0"

from litsearch.cache import Cache
from litsearch.downloader import PaperDownloader
from litsearch.models import Author, CitationNetwork, Paper, SearchResult
from litsearch.prisma import (
    FULL_TEXT_EXCLUSION_REASONS,
    PRISMARecord,
    PRISMAReport,
    PRISMATracker,
    ScreeningDecision,
    ScreeningStage,
)
from litsearch.search import (
    LiteratureReviewWorkflow,
    ReviewPhase,
    ReviewState,
)
from litsearch.similar import SimilarPaperFinder
from litsearch.snowball import SnowballEngine, SnowballResult, SnowballRound
from litsearch.sources import ImpactFactorLookup, SourceManager

__all__ = [
    # Models
    "Paper",
    "Author",
    "CitationNetwork",
    "SearchResult",
    # Workflow
    "LiteratureReviewWorkflow",
    "ReviewState",
    "ReviewPhase",
    # Infrastructure
    "Cache",
    "SourceManager",
    "ImpactFactorLookup",
    # Snowballing
    "SnowballEngine",
    "SnowballResult",
    "SnowballRound",
    # Similar papers
    "SimilarPaperFinder",
    # PRISMA
    "PRISMATracker",
    "PRISMARecord",
    "PRISMAReport",
    "ScreeningDecision",
    "ScreeningStage",
    "FULL_TEXT_EXCLUSION_REASONS",
    # Download
    "PaperDownloader",
]
