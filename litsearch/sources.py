"""API clients for Semantic Scholar, OpenAlex, Crossref and arXiv."""

import logging
import re
import time
from abc import ABC, abstractmethod

import requests

from litsearch.cache import Cache
from litsearch.config import current_year, semantic_scholar_api_key
from litsearch.diagnostics import (
    ParseError,
    SourceError,
    SourceErrorKind,
    classify_http_error,
    get_diagnostics,
)
from litsearch.models import Author, Paper

logger = logging.getLogger(__name__)


def raise_for_data(resp, source: str, context: str = "") -> None:
    """`raise_for_status()` that classifies the failure first.

    Call this instead of `resp.raise_for_status()` so a malformed query (400)
    and a dropped connection never look the same downstream: both become a
    :class:`SourceError` of the right kind, recorded in the diagnostics log.
    """
    if resp.status_code < 400:
        return
    try:
        body = resp.text[:200]
    except Exception:  # pragma: no cover - body already unreadable
        body = ""
    exc = classify_http_error(source, resp.status_code, body, url=resp.url or "")
    exc.detail = f"{exc.detail} [{context}]" if context else exc.detail
    get_diagnostics().record(
        source, exc.kind, exc.detail, exc.status, context
    )
    raise exc


def as_source_error(exc: BaseException, source: str, context: str = ""):
    """Classify any exception escaping a request call, and record it.

    Call sites become:

        except Exception as e:
            log_source_failure(e, "semantic_scholar", "search")
    """
    diag = get_diagnostics()
    if isinstance(exc, SourceError):  # already classified by raise_for_data
        return exc
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        err = classify_http_error(
            source, exc.response.status_code, "", url=exc.response.url or ""
        )
        err.detail = f"{err.detail} [{context}]" if context else err.detail
        diag.record(source, err.kind, err.detail, err.status, context)
        return err
    if isinstance(
        exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)
    ):
        diag.record(source, SourceErrorKind.TRANSIENT, str(exc)[:200], None, context)
        return exc
    if isinstance(exc, (ValueError, KeyError, TypeError, AttributeError)):
        diag.record(source, SourceErrorKind.PARSE, str(exc)[:200], None, context)
        return ParseError(source, str(exc)[:200])
    diag.record(source, SourceErrorKind.UNKNOWN, str(exc)[:200], None, context)
    return exc


def log_source_failure(exc: BaseException, source: str, context: str = "") -> None:
    """Record and log a classified failure at the right severity."""
    err = as_source_error(exc, source, context)
    if isinstance(err, SourceError) and err.kind in (
        SourceErrorKind.CONTRACT,
        SourceErrorKind.PARSE,
    ):
        # These never fix themselves: our request or our parser is wrong.
        logger.error("%s: %s", context or source, err)
    elif isinstance(err, SourceError) and err.kind == SourceErrorKind.NOT_FOUND:
        logger.info("%s: %s", context or source, err)
    else:
        logger.warning("%s: %s", context or source, err)


def resolve_year_to(year_to: int | None) -> int:
    """Default the end of a publication window to the current year."""
    return int(year_to) if year_to else current_year()


def s2_paper_id(paper_id: str) -> str:
    """Normalise an identifier for Semantic Scholar path segments.

    S2 accepts `DOI:10.xxxx/yyy` but NOT a bare DOI: `/paper/10.1038/nature1`
    returns 404.  Papers built by this package use the DOI as `Paper.id`
    whenever one exists, so without this helper every citation/reference
    lookup silently returned nothing.
    """
    pid = (paper_id or "").strip()
    if pid.startswith("10."):
        return f"DOI:{pid}"
    return pid


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class BaseSource(ABC):
    """Abstract base for all paper data sources."""

    name: str = "base"

    def __init__(self, cache: Cache | None = None):
        self._cache = cache
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "LEExtractor/0.3"})
        api_key = semantic_scholar_api_key()
        if api_key:
            self._session.headers.update({"x-api-key": api_key})

    @abstractmethod
    def search_papers(
        self, query: str, limit: int = 50, year_from: int = 1900, year_to: int | None = None
    ) -> list[Paper]:
        """Search papers by keyword query."""

    @abstractmethod
    def get_paper(self, paper_id: str) -> Paper | None:
        """Fetch full metadata for a single paper."""

    @abstractmethod
    def get_citations(self, paper_id: str, limit: int = 100) -> list[Paper]:
        """Get papers that cite the given paper (forward citations)."""

    @abstractmethod
    def get_references(self, paper_id: str, limit: int = 100) -> list[Paper]:
        """Get papers cited by the given paper (backward references)."""

    @abstractmethod
    def resolve_doi(self, doi: str) -> Paper | None:
        """Resolve a DOI to a Paper."""

    def _cached(self, func_name: str, *args) -> dict | list | None:
        if self._cache is None:
            return None
        return self._cache.get(func_name, *[str(a) for a in args])

    def _cache_set(self, value, func_name: str, *args):
        if self._cache is not None:
            self._cache.set(value, func_name, *[str(a) for a in args])


# ---------------------------------------------------------------------------
# Semantic Scholar
# ---------------------------------------------------------------------------

S2_BASE = "https://api.semanticscholar.org/graph/v1"
S2_PAPER_FIELDS = (
    "paperId,title,abstract,authors,year,citationCount,referenceCount,"
    "externalIds,journal,url,publicationVenue"
)
S2_SEARCH_FIELDS = (
    "paperId,title,abstract,authors,year,citationCount,referenceCount,"
    "externalIds,journal,url,publicationVenue"
)
S2_CITATION_FIELDS = "paperId,title,abstract,authors,year,citationCount,externalIds,journal,url"

# OpenAlex removed the plain `abstract` field from /works; only the inverted
# index can be selected.  Abstracts are reconstructed in _paper_from_oa().
OA_SELECT = (
    "id,doi,title,abstract_inverted_index,authorships,publication_year,"
    "cited_by_count,referenced_works_count,referenced_works,"
    "primary_location,topics,publication_date"
)
OA_SELECT_MIN = (
    "id,doi,title,authorships,publication_year,cited_by_count,"
    "primary_location,topics"
)


_S2_STATS: dict[str, int] = {"requests": 0, "rate_limited": 0, "retries": 0}


def s2_stats() -> dict[str, int]:
    """Request counters for the current process (used by the GUI warnings)."""
    return dict(_S2_STATS)


def reset_s2_stats() -> None:
    _S2_STATS.update({"requests": 0, "rate_limited": 0, "retries": 0})


def _reconstruct_abstract(data: dict) -> str:
    """Rebuild an abstract string from OpenAlex' abstract_inverted_index."""
    inv = data.get("abstract_inverted_index")
    if not inv:
        return ""
    positions: list[tuple[int, str]] = []
    for word, idxs in inv.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort(key=lambda x: x[0])
    return " ".join(w for _, w in positions)


class SemanticScholarSource(BaseSource):
    """Semantic Scholar Academic Graph API client.

    Without an API key S2 hands out a very small shared quota, so every
    request is paced and 429s are retried with exponential backoff instead of
    silently degrading the search.
    """

    name = "semantic_scholar"

    # How long to wait between requests, by quota tier.
    PACE_WITH_KEY = 0.2
    PACE_WITHOUT_KEY = 1.2
    MAX_RETRIES = 3

    def __init__(self, cache: Cache | None = None):
        super().__init__(cache)
        self._last_request_at = 0.0

    # -- transport ------------------------------------------------------

    def _pace(self) -> None:
        interval = (
            self.PACE_WITH_KEY if semantic_scholar_api_key() else self.PACE_WITHOUT_KEY
        )
        elapsed = time.time() - self._last_request_at
        if elapsed < interval:
            time.sleep(interval - elapsed)
        self._last_request_at = time.time()

    def _request(self, method: str, url: str, **kwargs):
        """Send a request with pacing and 429 backoff.

        Honours `Retry-After` when present, otherwise backs off
        exponentially (3s → 6s → 12s).  After MAX_RETRIES the last response is
        returned as-is so callers can degrade gracefully.
        """
        kwargs.setdefault("timeout", 30)
        last = None
        for attempt in range(self.MAX_RETRIES + 1):
            self._pace()
            _S2_STATS["requests"] += 1
            resp = self._session.request(method, url, **kwargs)
            if resp.status_code != 429:
                return resp
            last = resp
            _S2_STATS["rate_limited"] += 1
            if attempt == self.MAX_RETRIES:
                break
            retry_after = (resp.headers.get("Retry-After") or "").strip()
            delay = float(retry_after) if retry_after.isdigit() else 3.0 * (2 ** attempt)
            delay = min(delay, 60.0)
            logger.warning(
                "S2 rate limited (429) on %s — retrying in %.1fs (attempt %d/%d). "
                "Set S2_API_KEY for a much larger quota.",
                url, delay, attempt + 1, self.MAX_RETRIES,
            )
            time.sleep(delay)
            _S2_STATS["retries"] += 1
        return last

    def _paper_from_s2(self, data: dict) -> Paper:
        ext = data.get("externalIds") or {}
        doi = ext.get("DOI") or ext.get("doi")
        paper_id = doi or data.get("paperId", "")
        authors = [
            Author(name=a.get("name", ""), author_id=a.get("authorId"))
            for a in (data.get("authors") or [])
        ]
        venue = ""
        journal = data.get("journal") or {}
        if journal and journal.get("name"):
            venue = journal["name"]
        elif data.get("publicationVenue"):
            venue = data["publicationVenue"].get("name", "")
        return Paper(
            id=paper_id,
            title=data.get("title") or "",
            abstract=data.get("abstract") or "",
            authors=authors,
            year=data.get("year"),
            venue=venue,
            doi=doi,
            citation_count=data.get("citationCount") or 0,
            reference_count=data.get("referenceCount") or 0,
            url=data.get("url") or f"https://doi.org/{doi}" if doi else "",
            source=self.name,
        )

    def _batch_get(self, paper_ids: list[str], fields: str = S2_PAPER_FIELDS) -> list[dict]:
        """POST batch endpoint to fetch multiple papers at once."""
        results = []
        # S2 batch endpoint accepts up to 500 IDs
        for i in range(0, len(paper_ids), 500):
            chunk = paper_ids[i : i + 500]
            cached = self._cached("s2_batch", *chunk)
            if cached is not None:
                results.extend(cached)
                continue
            try:
                resp = self._request("POST",
                    f"{S2_BASE}/paper/batch",
                    params={"fields": fields},
                    json={"ids": chunk},
                    timeout=30,
                )
                raise_for_data(resp, "semantic_scholar")
                chunk_results = resp.json()
                self._cache_set(chunk_results, "s2_batch", *chunk)
                results.extend(chunk_results)
            except Exception as e:
                log_source_failure(e, "semantic_scholar", "batch")
            time.sleep(0.5)
        return results

    def search_papers(
        self, query: str, limit: int = 50, year_from: int = 1900, year_to: int | None = None
    ) -> list[Paper]:
        year_to = resolve_year_to(year_to)
        cached = self._cached("s2_search", query, str(limit), str(year_from), str(year_to))
        if cached is not None:
            return [self._paper_from_s2(d) for d in cached]

        papers = []
        offset = 0
        while len(papers) < limit:
            try:
                resp = self._request("GET",
                    f"{S2_BASE}/paper/search",
                    params={
                        "query": query,
                        "limit": min(100, limit - len(papers)),
                        "offset": offset,
                        "year": f"{year_from}-{year_to}",
                        "fields": S2_SEARCH_FIELDS,
                    },
                    timeout=30,
                )
                raise_for_data(resp, "semantic_scholar")
                data = resp.json().get("data") or []
                if not data:
                    break
                for d in data:
                    paper = self._paper_from_s2(d)
                    papers.append(paper)
                offset += len(data)
                time.sleep(1.0)
            except Exception as e:
                log_source_failure(e, "semantic_scholar", "search")
                break

        if papers:
            cache_data = [{
                "paperId": p.id if not p.doi else "",
                "title": p.title,
                "abstract": p.abstract,
                "authors": [{"name": a.name, "authorId": a.author_id} for a in p.authors],
                "year": p.year,
                "citationCount": p.citation_count,
                "referenceCount": p.reference_count,
                "externalIds": {"DOI": p.doi},
                "journal": {"name": p.venue},
                "url": p.url,
                "publicationVenue": {"name": p.venue} if p.venue else None,
            } for p in papers]
            self._cache_set(cache_data, "s2_search", query, str(limit), str(year_from), str(year_to))
        return papers

    def get_paper(self, paper_id: str) -> Paper | None:
        cached = self._cached("s2_paper", paper_id)
        if cached is not None:
            return self._paper_from_s2(cached)

        # Support DOI lookup
        url = f"{S2_BASE}/paper/{paper_id}"
        if paper_id.startswith("10."):
            url = f"{S2_BASE}/paper/DOI:{paper_id}"
        try:
            resp = self._request("GET",
                url,
                params={"fields": S2_PAPER_FIELDS},
                timeout=30,
            )
            if resp.status_code == 404:
                return None
            raise_for_data(resp, "semantic_scholar")
            data = resp.json()
            self._cache_set(data, "s2_paper", paper_id)
            return self._paper_from_s2(data)
        except Exception as e:
            log_source_failure(e, "semantic_scholar", f"get_paper({paper_id})")
            return None

    def get_citations(self, paper_id: str, limit: int = 100) -> list[Paper]:
        cached = self._cached("s2_citations", paper_id, str(limit))
        if cached is not None:
            return [self._paper_from_s2(d.get("citingPaper", d)) for d in cached]

        papers = []
        offset = 0
        url_base = f"{S2_BASE}/paper/{s2_paper_id(paper_id)}/citations"
        while len(papers) < limit:
            try:
                resp = self._request("GET",
                    url_base,
                    params={
                        "limit": min(500, limit - len(papers)),
                        "offset": offset,
                        "fields": S2_CITATION_FIELDS,
                    },
                    timeout=30,
                )
                if resp.status_code == 404:
                    break
                raise_for_data(resp, "semantic_scholar")
                data = resp.json().get("data") or []
                if not data:
                    break
                for d in data:
                    cp = d.get("citingPaper") or d
                    papers.append(self._paper_from_s2(cp))
                offset += len(data)
                time.sleep(0.5)
            except Exception as e:
                log_source_failure(e, "semantic_scholar", f"citations({paper_id})")
                break
        return papers

    def get_references(self, paper_id: str, limit: int = 100) -> list[Paper]:
        cached = self._cached("s2_references", paper_id, str(limit))
        if cached is not None:
            return [self._paper_from_s2(d.get("citedPaper", d)) for d in cached]

        papers = []
        offset = 0
        url_base = f"{S2_BASE}/paper/{s2_paper_id(paper_id)}/references"
        while len(papers) < limit:
            try:
                resp = self._request("GET",
                    url_base,
                    params={
                        "limit": min(500, limit - len(papers)),
                        "offset": offset,
                        "fields": S2_CITATION_FIELDS,
                    },
                    timeout=30,
                )
                if resp.status_code == 404:
                    break
                raise_for_data(resp, "semantic_scholar")
                data = resp.json().get("data") or []
                if not data:
                    break
                for d in data:
                    rp = d.get("citedPaper") or d
                    papers.append(self._paper_from_s2(rp))
                offset += len(data)
                time.sleep(0.5)
            except Exception as e:
                log_source_failure(e, "semantic_scholar", f"references({paper_id})")
                break
        return papers

    def resolve_doi(self, doi: str) -> Paper | None:
        return self.get_paper(f"DOI:{doi}")


# ---------------------------------------------------------------------------
# OpenAlex
# ---------------------------------------------------------------------------

OA_BASE = "https://api.openalex.org"


class OpenAlexSource(BaseSource):
    """OpenAlex API client — used as fallback for S2 coverage gaps."""

    name = "openalex"

    def _paper_from_oa(self, data: dict) -> Paper:
        doi = (data.get("doi") or "").replace("https://doi.org/", "")
        paper_id = doi or data.get("id", "")
        authors = [
            Author(
                name=a.get("author", {}).get("display_name", ""),
                author_id=a.get("author", {}).get("id"),
            )
            for a in (data.get("authorships") or [])
        ]
        venue = ""
        if data.get("primary_location") and data["primary_location"].get("source"):
            venue = data["primary_location"]["source"].get("display_name", "")
        # OpenAlex no longer exposes a plain `abstract` field (selecting it
        # makes the whole request fail) — rebuild it from the inverted index.
        abstract = data.get("abstract") or _reconstruct_abstract(data)
        topics = [
            t.get("display_name", "")
            for t in (data.get("topics") or [])
            if t.get("display_name")
        ]
        # OpenAlex stores referenced_works as full URLs; extract IDs
        ref_ids = [r.split("/")[-1] for r in (data.get("referenced_works") or [])]
        return Paper(
            id=paper_id,
            title=data.get("title") or data.get("display_name") or "",
            abstract=abstract,
            authors=authors,
            year=data.get("publication_year"),
            venue=venue,
            doi=doi,
            citation_count=data.get("cited_by_count") or 0,
            reference_count=data.get("referenced_works_count") or 0,
            reference_ids=ref_ids,
            url=data.get("doi") or "",
            source=self.name,
            topics=topics,
        )

    def search_papers(
        self, query: str, limit: int = 50, year_from: int = 1900, year_to: int | None = None
    ) -> list[Paper]:
        year_to = resolve_year_to(year_to)
        cached = self._cached("oa_search", query, str(limit), str(year_from), str(year_to))
        if cached is not None:
            return [self._paper_from_oa(d) for d in cached]

        papers = []
        page = 1
        while len(papers) < limit:
            try:
                resp = self._session.get(
                    f"{OA_BASE}/works",
                    params={
                        "search": query,
                        "per_page": min(200, limit - len(papers)),
                        "page": page,
                        "filter": f"publication_year:{year_from}-{year_to}",
                        "sort": "cited_by_count:desc",
                        "select": OA_SELECT,
                    },
                    timeout=30,
                )
                raise_for_data(resp, "openalex")
                data = resp.json()
                results = data.get("results") or []
                if not results:
                    break
                for d in results:
                    papers.append(self._paper_from_oa(d))
                page += 1
                time.sleep(0.3)
            except Exception as e:
                log_source_failure(e, "openalex", "search")
                break
        if papers:
            self._cache_set(
                [self._oa_to_dict(p) for p in papers],
                "oa_search", query, str(limit), str(year_from), str(year_to),
            )
        return papers

    @staticmethod
    def _oa_to_dict(paper: Paper) -> dict:
        """Minimal serialisable view of a Paper (for the disk cache)."""
        return {
            "doi": f"https://doi.org/{paper.doi}" if paper.doi else None,
            "title": paper.title,
            "abstract": paper.abstract,
            "authorships": [{"author": {"display_name": a.name, "id": a.author_id}}
                            for a in paper.authors],
            "publication_year": paper.year,
            "cited_by_count": paper.citation_count,
            "referenced_works_count": paper.reference_count,
            "referenced_works": [
                f"https://openalex.org/{r}" if not r.startswith("http") else r
                for r in paper.reference_ids
            ],
            "primary_location": {"source": {"display_name": paper.venue}} if paper.venue else None,
            "topics": [{"display_name": t} for t in paper.topics],
        }

    def get_paper(self, paper_id: str) -> Paper | None:
        cached = self._cached("oa_paper", paper_id)
        if cached is not None:
            return self._paper_from_oa(cached)

        url = f"{OA_BASE}/works/{paper_id}"
        if paper_id.startswith("10."):
            url = f"{OA_BASE}/works/https://doi.org/{paper_id}"
        try:
            resp = self._session.get(url, timeout=30)
            if resp.status_code == 404:
                return None
            raise_for_data(resp, "openalex")
            data = resp.json()
            self._cache_set(data, "oa_paper", paper_id)
            return self._paper_from_oa(data)
        except Exception as e:
            log_source_failure(e, "openalex", f"get_paper({paper_id})")
            return None

    def get_citations(self, paper_id: str, limit: int = 100) -> list[Paper]:
        cached = self._cached("oa_citations", paper_id, str(limit))
        if cached is not None:
            return [self._paper_from_oa(d) for d in cached]

        papers = []
        page = 1
        # OpenAlex' `cites:` filter only accepts an OpenAlex ID (W…), not a
        # DOI — resolve DOIs first, otherwise every lookup returns an error.
        oa_id = self._resolve_oa_id(paper_id)
        if not oa_id:
            return []
        while len(papers) < limit:
            try:
                resp = self._session.get(
                    f"{OA_BASE}/works",
                    params={
                        "filter": f"cites:{oa_id}",
                        "per_page": min(200, limit - len(papers)),
                        "page": page,
                        "select": OA_SELECT_MIN,
                    },
                    timeout=30,
                )
                raise_for_data(resp, "openalex")
                data = resp.json()
                results = data.get("results") or []
                if not results:
                    break
                for d in results:
                    papers.append(self._paper_from_oa(d))
                page += 1
                time.sleep(0.3)
            except Exception as e:
                log_source_failure(e, "openalex", f"citations({paper_id})")
                break
        if papers:
            self._cache_set(
                [self._oa_to_dict(p) for p in papers], "oa_citations", paper_id, str(limit)
            )
        return papers

    def _resolve_oa_id(self, paper_id: str) -> str:
        """Return the short OpenAlex ID (W…) for an OpenAlex ID or a DOI."""
        if paper_id.startswith("W") and "/" not in paper_id:
            return paper_id
        m = re.search(r"(W\d{5,})", paper_id or "")
        if m:
            return m.group(1)
        if paper_id.startswith("10."):
            try:
                resp = self._session.get(
                    f"{OA_BASE}/works/https://doi.org/{paper_id}",
                    params={"select": "id"},
                    timeout=20,
                )
                if resp.status_code == 200:
                    m = re.search(r"(W\d{5,})", resp.json().get("id", ""))
                    if m:
                        return m.group(1)
            except Exception as e:
                log_source_failure(e, "openalex", f"resolve_id({paper_id})")
        return ""

    def get_references(self, paper_id: str, limit: int = 100) -> list[Paper]:
        paper = self.get_paper(paper_id)
        if paper is None:
            return []
        ref_ids = paper.reference_ids[:limit]
        papers = []
        for i in range(0, len(ref_ids), 50):
            chunk = ref_ids[i : i + 50]
            try:
                resp = self._session.get(
                    f"{OA_BASE}/works",
                    params={
                        "filter": f"ids.openalex:{'|'.join(chunk)}",
                        "per_page": 200,
                        "select": OA_SELECT_MIN,
                    },
                    timeout=30,
                )
                raise_for_data(resp, "openalex")
                for d in resp.json().get("results") or []:
                    papers.append(self._paper_from_oa(d))
                time.sleep(0.3)
            except Exception as e:
                log_source_failure(e, "openalex", "references_batch")
        return papers

    def resolve_doi(self, doi: str) -> Paper | None:
        return self.get_paper(doi)


# ---------------------------------------------------------------------------
# Crossref
# ---------------------------------------------------------------------------

CR_BASE = "https://api.crossref.org"


class CrossrefSource(BaseSource):
    """Crossref API — used primarily for DOI validation and journal metadata."""

    name = "crossref"

    def _paper_from_cr(self, data: dict) -> Paper:
        msg = data.get("message") or data
        doi = msg.get("DOI", "")
        authors = [
            Author(
                name=f"{a.get('given', '')} {a.get('family', '')}".strip(),
            )
            for a in (msg.get("author") or [])
        ]
        year = None
        issued = msg.get("issued", {}).get("date-parts", [[None]])[0]
        if issued and issued[0]:
            year = issued[0]
        venue = ""
        if msg.get("container-title"):
            venue = msg["container-title"][0] if isinstance(msg["container-title"], list) else msg["container-title"]
        return Paper(
            id=doi,
            title=msg.get("title", [""])[0] if msg.get("title") else "",
            abstract=msg.get("abstract") or "",
            authors=authors,
            year=year,
            venue=venue,
            doi=doi,
            citation_count=msg.get("is-referenced-by-count") or 0,
            reference_count=msg.get("references-count") or 0,
            url=f"https://doi.org/{doi}",
            source=self.name,
        )

    def search_papers(self, query, limit=50, year_from=1900, year_to=None):
        year_to = resolve_year_to(year_to)
        cached = self._cached("cr_search", query, str(limit), str(year_from), str(year_to))
        if cached is not None:
            return [self._paper_from_cr(d) for d in cached]

        papers = []
        rows = min(limit, 100)
        try:
            resp = self._session.get(
                f"{CR_BASE}/works",
                params={
                    "query": query,
                    "rows": rows,
                    "filter": f"from-pub-date:{year_from}-01-01,until-pub-date:{year_to}-12-31",
                    "select": "DOI,title,abstract,author,issued,container-title,is-referenced-by-count,references-count",
                },
                timeout=30,
            )
            raise_for_data(resp, "crossref")
            items = resp.json().get("message", {}).get("items") or []
            papers = [self._paper_from_cr(item) for item in items[:limit]]
        except Exception as e:
            log_source_failure(e, "crossref", "search")
        return papers

    def get_paper(self, paper_id: str) -> Paper | None:
        cached = self._cached("cr_paper", paper_id)
        if cached is not None:
            return self._paper_from_cr(cached)
        try:
            resp = self._session.get(f"{CR_BASE}/works/{paper_id}", timeout=30)
            if resp.status_code == 404:
                return None
            raise_for_data(resp, "crossref")
            data = resp.json()
            self._cache_set(data, "cr_paper", paper_id)
            return self._paper_from_cr(data)
        except Exception as e:
            log_source_failure(e, "crossref", f"get_paper({paper_id})")
            return None

    def get_citations(self, paper_id, limit=100):
        return []  # Crossref doesn't have a direct citations endpoint

    def get_references(self, paper_id, limit=100):
        return []  # Crossref doesn't provide structured references easily

    def resolve_doi(self, doi: str) -> Paper | None:
        return self.get_paper(doi)


# ---------------------------------------------------------------------------
# Source Manager (facade with fallback)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# arXiv
# ---------------------------------------------------------------------------

ARXIV_BASE = "https://export.arxiv.org/api"


class ArxivSource(BaseSource):
    """arXiv API client for preprint search.

    Uses the official arXiv API (no key needed, rate limit ~1 req/3s).
    """

    name = "arxiv"

    def _paper_from_arxiv(self, entry) -> Paper:
        """Parse an arXiv Atom XML entry into a Paper."""

        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "arxiv": "http://arxiv.org/schemas/atom",
        }

        def get_text(tag: str) -> str:
            el = entry.find(f"atom:{tag}", ns)
            return el.text.strip() if el is not None and el.text else ""

        def get_arxiv(tag: str) -> str:
            el = entry.find(f"arxiv:{tag}", ns)
            return el.text.strip() if el is not None and el.text else ""

        title = get_text("title")
        abstract = get_text("summary")
        arxiv_id_full = get_text("id")
        arxiv_id = arxiv_id_full.split("/abs/")[-1] if "/abs/" in arxiv_id_full else arxiv_id_full

        authors = []
        for auth_el in entry.findall("atom:author", ns):
            name_el = auth_el.find("atom:name", ns)
            if name_el is not None and name_el.text:
                authors.append(Author(name=name_el.text.strip()))

        year = None
        published = get_text("published")
        if published:
            year = int(published[:4])

        categories = [
            cat.get("term", "")
            for cat in entry.findall("atom:category", ns)
            if cat.get("term")
        ]

        # arXiv papers don't have DOIs natively, but newer ones might
        doi = get_arxiv("doi") or ""

        return Paper(
            id=arxiv_id,
            title=title,
            abstract=abstract,
            authors=authors,
            year=year,
            venue="arXiv",
            doi=doi,
            citation_count=0,
            url=f"https://arxiv.org/pdf/{arxiv_id}.pdf",
            source=self.name,
            topics=categories,
        )

    def search_papers(
        self, query: str, limit: int = 50, year_from: int = 1900, year_to: int | None = None
    ) -> list[Paper]:
        import urllib.parse
        from xml.etree import ElementTree as ET

        year_to = resolve_year_to(year_to)
        cached = self._cached("arxiv_search", query, str(limit))
        if cached is not None:
            return [self._paper_from_arxiv(ET.fromstring(d)) for d in cached]

        papers = []
        max_results = min(limit, 100)
        encoded_query = urllib.parse.quote(query)
        url = (
            f"{ARXIV_BASE}/query?"
            f"search_query=all:{encoded_query}&"
            f"start=0&"
            f"max_results={max_results}&"
            f"sortBy=submittedDate&"
            f"sortOrder=descending"
        )

        try:
            resp = self._session.get(url, timeout=30)
            raise_for_data(resp, "arxiv")
            root = ET.fromstring(resp.text)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            entries = root.findall("atom:entry", ns)

            # Cache raw XML strings
            raw_entries = []
            for entry in entries:
                raw_entries.append(ET.tostring(entry, encoding="unicode"))
                papers.append(self._paper_from_arxiv(entry))
            if raw_entries:
                self._cache_set(raw_entries, "arxiv_search", query, str(limit))
        except Exception as e:
            log_source_failure(e, "arxiv", "search")

        return papers

    def get_paper(self, paper_id: str) -> Paper | None:
        import urllib.parse
        from xml.etree import ElementTree as ET

        cached = self._cached("arxiv_paper", paper_id)
        if cached is not None:
            return self._paper_from_arxiv(ET.fromstring(cached))

        encoded_id = urllib.parse.quote(paper_id)
        url = f"{ARXIV_BASE}/query?id_list={encoded_id}&max_results=1"
        try:
            resp = self._session.get(url, timeout=30)
            raise_for_data(resp, "arxiv")
            root = ET.fromstring(resp.text)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            entry = root.find("atom:entry", ns)
            if entry is not None:
                raw = ET.tostring(entry, encoding="unicode")
                self._cache_set(raw, "arxiv_paper", paper_id)
                return self._paper_from_arxiv(entry)
        except Exception as e:
            log_source_failure(e, "arxiv", f"get_paper({paper_id})")
        return None

    def get_citations(self, paper_id: str, limit: int = 100) -> list[Paper]:
        return []  # arXiv API doesn't provide citation data

    def get_references(self, paper_id: str, limit: int = 100) -> list[Paper]:
        return []  # arXiv API doesn't provide reference data

    def resolve_doi(self, doi: str) -> Paper | None:
        return None  # arXiv doesn't do DOI resolution


# ---------------------------------------------------------------------------
# Impact Factor Lookup
# ---------------------------------------------------------------------------


class ImpactFactorLookup:
    """Lookup journal impact factors via OpenAlex sources endpoint.

    Caches results locally to minimize API calls.
    """

    def __init__(self, cache: Cache | None = None):
        self._cache = cache or Cache()
        self._session = requests.Session()
        self._session.headers.update(
            {"User-Agent": "LEExtractor/0.2"}
        )
        # In-memory cache for this session
        self._if_cache: dict[str, float | None] = {}

    def get_if(self, venue: str, issn: str = "") -> float | None:
        """Get impact factor for a journal by name or ISSN.

        Uses OpenAlex's cited_by_percentile and h-index as proxy metrics,
        plus a hardcoded lookup table for common high-IF journals.
        """
        key = venue.lower().strip() if venue else issn
        if not key:
            return None
        if key in self._if_cache:
            return self._if_cache[key]

        # Check hardcoded lookup first (2023 JCR data for common journals)
        if_val = _HARDCODED_IF.get(key)
        if if_val is not None:
            self._if_cache[key] = if_val
            return if_val

        # Try OpenAlex source lookup (from cache)
        if self._cache is not None:
            cached = self._cache.get("if_lookup", key)
            if cached is not None:
                self._if_cache[key] = cached
                return cached

        try:
            params = {"search": venue, "per_page": 1}
            if issn:
                params["filter"] = f"issn:{issn}"
            resp = self._session.get(
                f"{OA_BASE}/sources",
                params=params,
                timeout=10,
            )
            if resp.status_code == 200:
                results = resp.json().get("results") or []
                if results:
                    src = results[0]
                    # OpenAlex doesn't have JCR IF directly.
                    # Use cited_by_count / works_count * 0.1 as rough proxy.
                    cited = src.get("cited_by_count", 0)
                    works = src.get("works_count", 1)
                    h_index = src.get("summary_stats", {}).get("h_index", 0)
                    # Rough IF estimate: citations per paper over last 2 years
                    est_if = round(cited / max(works, 1) * 0.15, 2) if works else None
                    # Blend with h-index for better estimate
                    if h_index and est_if:
                        est_if = round(est_if * 0.7 + (h_index / 100) * 0.3, 2)
                    if self._cache is not None:
                        self._cache.set(est_if, "if_lookup", key)
                    self._if_cache[key] = est_if
                    return est_if
        except Exception as e:
            # Impact factor is a display-only nicety, so a failure here must
            # not pollute diagnostics with noise that hides real problems.
            # It is logged at debug rather than silently dropped.
            logger.debug("IF lookup failed for %s: %s", key, e)

        self._if_cache[key] = None
        return None

    def get_if_display(self, venue: str, issn: str = "") -> str:
        """Get IF as a display string, e.g. 'IF: 8.3' or 'IF: N/A'.

        Values not present in the JCR table are *estimates* derived from
        OpenAlex citation/h-index stats and are prefixed with '~' so they are
        never mistaken for real JCR impact factors.
        """
        val = self.get_if(venue, issn)
        if val is None:
            return "IF: N/A"
        if (venue or "").lower().strip() in _HARDCODED_IF:
            return f"IF: {val:.1f}"
        return f"IF: ~{val:.1f} (est.)"


# Hardcoded IF lookup for common journals (JCR 2023)
_HARDCODED_IF: dict[str, float] = {
    "nature": 50.5,
    "science": 44.8,
    "cell": 45.6,
    "nature communications": 14.7,
    "nature genetics": 31.7,
    "nature methods": 36.1,
    "nature biotechnology": 33.1,
    "nature plants": 15.8,
    "nature machine intelligence": 23.8,
    "nature reviews molecular cell biology": 81.3,
    "proceedings of the national academy of sciences": 9.4,
    "pnas": 9.4,
    "science advances": 11.7,
    "neuron": 14.7,
    "immunity": 22.1,
    "cancer cell": 22.8,
    "molecular cell": 12.6,
    "developmental cell": 9.2,
    "cell reports": 7.5,
    "cell metabolism": 21.3,
    "cell stem cell": 19.8,
    "the lancet": 98.4,
    "lancet": 98.4,
    "the bmj": 93.7,
    "bmj": 93.7,
    "jama": 63.1,
    "new england journal of medicine": 96.3,
    "nejm": 96.3,
    "nature medicine": 58.7,
    "nature neuroscience": 21.2,
    "nature immunology": 21.0,
    "nature materials": 37.2,
    "nature chemistry": 15.2,
    "nature physics": 15.6,
    "nature climate change": 21.3,
    "nature energy": 32.5,
    "nature sustainability": 19.3,
    "nature food": 16.3,
    "plant cell": 10.0,
    "plant physiology": 6.5,
    "new phytologist": 8.3,
    "journal of experimental botany": 5.6,
    "plant journal": 6.2,
    "frontiers in plant science": 4.1,
    "horticulture research": 6.1,
    "computers and electronics in agriculture": 7.7,
    "biosystems engineering": 4.2,
    "trends in plant science": 14.3,
    "annual review of plant biology": 17.8,
    "current biology": 7.5,
    "elife": 6.9,
    "plos biology": 6.5,
    "plos genetics": 4.3,
    "plos computational biology": 3.8,
    "plos one": 2.9,
    "bmc genomics": 3.5,
    "bmc plant biology": 3.7,
    "plant methods": 3.6,
    "gigascience": 5.5,
    "scientific data": 5.4,
    "scientific reports": 3.8,
    "ieee transactions on pattern analysis and machine intelligence": 20.8,
    "ieee transactions on neural networks and learning systems": 10.2,
    "ieee transactions on image processing": 8.9,
    "ieee access": 3.4,
    "cvpr": 15.2,
    "iccv": 13.8,
    "neurips": 20.4,
    "neurips proceedings": 20.4,
    "icml": 18.7,
    "iclr": 16.9,
    "acl": 10.5,
    "emnlp": 10.2,
    "aaai": 7.8,
    "ijcai": 6.2,
    "international conference on machine learning": 18.7,
    "international conference on learning representations": 16.9,
    "journal of machine learning research": 3.8,
    "jmlr": 3.8,
    "machine learning": 5.1,
    "artificial intelligence": 8.7,
    "pattern recognition": 7.5,
    "neural networks": 7.8,
    "neurocomputing": 5.2,
    "information fusion": 14.8,
    "knowledge-based systems": 6.8,
    "expert systems with applications": 6.3,
    "engineering applications of artificial intelligence": 6.2,
    "applied soft computing": 6.4,
    "ieee transactions on cybernetics": 9.9,
    "ieee transactions on fuzzy systems": 9.4,
    "ieee transactions on evolutionary computation": 11.2,
    "swarm and evolutionary computation": 6.6,
    "evolutionary computation": 3.8,
    "genetic programming and evolvable machines": 3.0,
    "complex & intelligent systems": 4.3,
    "advanced engineering informatics": 7.0,
    "computers in industry": 6.3,
    "robotics and computer-integrated manufacturing": 8.3,
    "ieee transactions on industrial informatics": 9.0,
    "ieee transactions on systems man and cybernetics systems": 7.2,
    "annual review of biochemistry": 15.6,
    "annual review of genetics": 6.1,
    "trends in genetics": 9.5,
    "trends in biotechnology": 10.5,
    "nucleic acids research": 12.1,
    "genome biology": 11.4,
    "genome research": 6.2,
    "molecular biology and evolution": 9.2,
    "bioinformatics": 5.8,
    "briefings in bioinformatics": 7.2,
    "plant biotechnology journal": 10.5,
    "biotechnology advances": 10.3,
    "metabolic engineering": 8.9,
    "acs synthetic biology": 4.1,
    "journal of agricultural and food chemistry": 5.7,
    "food chemistry": 7.4,
    "industrial crops and products": 5.6,
    "phytochemistry": 3.8,
    "phytochemistry reviews": 5.7,
    "journal of natural products": 4.5,
    "journal of ethnopharmacology": 4.1,
}


class SourceManager:
    """Facade that tries Semantic Scholar first, falls back to OpenAlex."""

    def __init__(self, cache: Cache | None = None):
        self._cache = cache or Cache()
        self.s2 = SemanticScholarSource(self._cache)
        self.oa = OpenAlexSource(self._cache)
        self.cr = CrossrefSource(self._cache)
        self.arxiv = ArxivSource(self._cache)
        self.if_lookup = ImpactFactorLookup(self._cache)

    @property
    def cache(self) -> Cache:
        return self._cache

    def search_papers(
        self, query: str, limit: int = 50, year_from: int = 1900, year_to: int | None = None,
        include_preprints: bool = True,
    ) -> list[Paper]:
        year_to = resolve_year_to(year_to)
        try:
            papers = self.s2.search_papers(query, limit, year_from, year_to)
        except Exception as e:
            log_source_failure(e, "semantic_scholar", "manager.search")
            papers = []
        if not papers:
            # Falling through to OpenAlex is not a silent convenience: the S2
            # failure (if any) is already in diagnostics, so the user can see
            # that these results came from a fallback rather than a primary.
            try:
                papers = self.oa.search_papers(query, limit, year_from, year_to)
            except Exception as e:
                log_source_failure(e, "openalex", "manager.search")
                papers = []
        if include_preprints:
            try:
                arxiv_papers = self.arxiv.search_papers(query, limit=limit // 3)
                papers.extend(arxiv_papers)
            except Exception as e:
                log_source_failure(e, "arxiv", "manager.search")
        return papers

    def search_all_sources(
        self, query: str, limit: int = 200, year_from: int = 1900, year_to: int | None = None,
    ) -> list[Paper]:
        """Search across ALL sources and merge results. For comprehensive mode."""
        year_to = resolve_year_to(year_to)
        all_papers: list[Paper] = []
        seen: set[str] = set()

        def add(papers):
            for p in papers:
                key = (p.doi or p.id).lower()
                if key not in seen:
                    seen.add(key)
                    all_papers.append(p)

        # S2 (primary, broadest coverage)
        add(self.s2.search_papers(query, limit=limit, year_from=year_from, year_to=year_to))
        # OpenAlex (deeper, different ranking)
        add(self.oa.search_papers(query, limit=limit // 2, year_from=year_from, year_to=year_to))
        # arXiv (preprints)
        add(self.arxiv.search_papers(query, limit=limit // 4, year_from=year_from, year_to=year_to))

        return all_papers

    def get_paper(self, paper_id: str) -> Paper | None:
        paper = self.s2.get_paper(paper_id)
        if paper is None:
            paper = self.oa.get_paper(paper_id)
        if paper is None and paper_id.startswith("10."):
            paper = self.cr.get_paper(paper_id)
        return paper

    def get_citations(self, paper_id: str, limit: int = 100) -> list[Paper]:
        papers = self.s2.get_citations(paper_id, limit)
        if not papers:
            papers = self.oa.get_citations(paper_id, limit)
        return papers

    def get_references(self, paper_id: str, limit: int = 100) -> list[Paper]:
        papers = self.s2.get_references(paper_id, limit)
        if not papers:
            papers = self.oa.get_references(paper_id, limit)
        return papers

    def resolve_doi(self, doi: str) -> Paper | None:
        paper = self.s2.resolve_doi(doi)
        if paper is None:
            paper = self.oa.resolve_doi(doi)
        if paper is None:
            paper = self.cr.resolve_doi(doi)
        return paper
