"""Unified data models for literature search results."""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from litsearch.identifiers import PaperIdentifiers, fallback_key, identifier_key, normalize_doi


@dataclass
class DiscoveryTrace:
    method: str
    provider: str = ""
    query: str = ""
    seed_id: str = ""
    round_no: int | None = None
    score: float | None = None
    evidence_ids: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class Author:
    name: str
    author_id: str | None = None


@dataclass
class Paper:
    """Unified paper representation across all data sources."""

    id: str  # Canonical ID: DOI if available, else S2 ID or OpenAlex ID
    title: str
    abstract: str | None = None
    authors: list[Author] = field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    citation_count: int = 0
    reference_count: int = 0
    citation_ids: list[str] = field(default_factory=list)
    reference_ids: list[str] = field(default_factory=list)
    source: str = ""  # "semantic_scholar" | "openalex" | "crossref"
    url: str | None = None
    relevance_score: float = 0.0
    topics: list[str] = field(default_factory=list)
    identifiers: PaperIdentifiers = field(default_factory=PaperIdentifiers)
    discovery_traces: list[DiscoveryTrace] = field(default_factory=list)
    score_breakdown: dict[str, float] = field(default_factory=dict)

    @property
    def canonical_id(self) -> str:
        if doi := normalize_doi(self.doi or self.identifiers.doi or self.id):
            return doi
        for value, provider in (
            (self.identifiers.openalex_id, "openalex"),
            (self.identifiers.semantic_scholar_id, "semantic_scholar"),
            (self.identifiers.arxiv_id, "arxiv"),
        ):
            if value:
                return identifier_key(value, provider)
        return identifier_key(self.id, self.source) if self.id else fallback_key(self.title, self.year)


@dataclass
class CitationNetwork:
    """Wraps a NetworkX DiGraph with metadata about the citation network."""

    seed_paper_id: str
    papers: dict[str, "Paper"] = field(default_factory=dict)
    hop_depth: int = 0
    central_papers: list[str] = field(default_factory=list)
    clusters: dict[int, list[str]] = field(default_factory=dict)
    graph_data: dict | None = None  # node-link JSON for serialization

    @property
    def node_count(self) -> int:
        return len(self.papers)

    @property
    def edge_count(self) -> int:
        if self.graph_data is None:
            return 0
        return len(self.graph_data.get("links", []))


@dataclass
class SearchResult:
    """Result of a literature search operation."""

    papers: list[Paper] = field(default_factory=list)
    network: CitationNetwork | None = None
    trend_metrics: dict = field(default_factory=dict)
    total_available: int = 0
    elapsed_seconds: float = 0.0
