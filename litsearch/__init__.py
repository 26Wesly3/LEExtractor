"""Systematic Literature Review Toolkit — LEExtractor."""

# The version lives in litsearch/version.py so that packaging and the UI can
# read it without importing the runtime dependency tree (see that module).
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
from litsearch.version import APP_NAME, __version__, package_name, version_tag

__all__ = [
    # Version
    "APP_NAME",
    "__version__",
    "version_tag",
    "package_name",
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
