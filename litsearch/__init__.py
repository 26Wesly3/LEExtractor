"""Systematic Literature Review Toolkit — LEExtractor.

Imports here are **lazy**. Bringing up the engine pulls in scikit-learn, scipy,
pandas and networkx (~3 s on a typical laptop), and the MCP server must answer
``initialize`` before its host's startup timeout — so a tool that only needs
``litsearch.version`` or ``litsearch.models`` must not pay for the whole stack.

``from litsearch import Paper`` and ``litsearch.Paper`` both still work; the
name is resolved on first access via :pep:`562` and cached in the module
namespace, so the import cost is paid once and only by callers that need it.

Use an explicit ``from litsearch.X import Y`` in new code: it says which module
actually owns the name, and it keeps the dependency graph honest.
"""

from __future__ import annotations

import importlib

#: Public name -> defining module. One level of indirection only, so a caller
#: reading this can see exactly where a name comes from.
#:
#: Type checkers and IDEs still resolve the attributes through this mapping at
#: runtime; `dir()` lists them, and an unknown name raises `AttributeError`.
_EXPORTS: dict[str, str] = {
    # Version (kept dependency-free on purpose — see litsearch/version.py)
    "APP_NAME": "litsearch.version",
    "__version__": "litsearch.version",
    "version_tag": "litsearch.version",
    "package_name": "litsearch.version",
    # Models
    "Paper": "litsearch.models",
    "Author": "litsearch.models",
    "CitationNetwork": "litsearch.models",
    "SearchResult": "litsearch.models",
    # Workflow
    "LiteratureReviewWorkflow": "litsearch.search",
    "ReviewState": "litsearch.search",
    "ReviewPhase": "litsearch.search",
    # Infrastructure
    "Cache": "litsearch.cache",
    "SourceManager": "litsearch.sources",
    "ImpactFactorLookup": "litsearch.sources",
    # Snowballing
    "SnowballEngine": "litsearch.snowball",
    "SnowballResult": "litsearch.snowball",
    "SnowballRound": "litsearch.snowball",
    # Similar papers
    "SimilarPaperFinder": "litsearch.similar",
    # PRISMA
    "PRISMATracker": "litsearch.prisma",
    "PRISMARecord": "litsearch.prisma",
    "PRISMAReport": "litsearch.prisma",
    "ScreeningDecision": "litsearch.prisma",
    "ScreeningStage": "litsearch.prisma",
    "FULL_TEXT_EXCLUSION_REASONS": "litsearch.prisma",
    # Download
    "PaperDownloader": "litsearch.downloader",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    """Resolve a public name on first access (PEP 562)."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module_name), name)
    globals()[name] = value  # cache: the next access is a plain dict lookup
    return value


def __dir__():
    return sorted(set(globals()) | set(_EXPORTS))
