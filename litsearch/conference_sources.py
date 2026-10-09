"""Public OpenReview / Google Scholar sources and legacy DBLP ID compatibility."""

import hashlib
import re
import time
from datetime import datetime, timezone
from itertools import zip_longest

import requests

from litsearch.config import current_year, serpapi_api_key
from litsearch.diagnostics import PermanentSourceError
from litsearch.identifiers import PaperIdentifiers, normalize_doi
from litsearch.models import Author, Paper
from litsearch.retrieval import RetrievalResult, RetrievalStatus
from litsearch.sources import BaseSource


def value(content, key, default=""):
    item = content.get(key, default)
    return item.get("value", default) if isinstance(item, dict) else item


class ConferenceSource(BaseSource):
    PAGE_SIZE = 100

    def _request_with_retries(self, method, url, *, deadline, **kwargs):
        response = super()._request_with_retries(method, url, deadline=deadline, **kwargs)
        if response is not None and response.status_code == 200 and "text/html" in response.headers.get("Content-Type", "").lower():
            from litsearch.diagnostics import ParseError
            raise ParseError(self.name, "服务返回 HTML 访问校验／反爬页面，未取得文献元数据；请稍后重试。")
        return response

    def _get_citations_result(self, paper_id, limit=100):
        return self._unsupported_result("citations")

    def _get_references_result(self, paper_id, limit=100):
        return self._unsupported_result("references")

    def resolve_doi(self, doi):
        return None

    def get_paper(self, paper_id):
        return None

    def _search_papers_result(self, query, limit=50, year_from=1900, year_to=None):
        year_to = year_to or current_year()
        baseline, started = self._budget_counters(), time.monotonic()
        args = (self.endpoint, query, limit, year_from, year_to)
        cached = self._cached("search", *args)
        if cached:
            raw, meta = cached["items"], cached["meta"]
            meta = dict(meta, pages=0)
        else:
            # Year filtering is local for these APIs. Bound overfetch explicitly;
            # a short filtered set must never imply the provider has no more hits.
            raw, meta = self._paged_get(self.endpoint, lambda page, size: self.params(query, page, size), self.extract,
                                        max(limit * 3, 100), page_size=self.PAGE_SIZE)
            if raw or meta.get("complete"):
                self._cache_set({"items": raw, "meta": meta}, "search", *args)
        papers = [self.to_paper(row) for row in raw if self.is_paper(row)]
        papers = [p for p in papers if p.title and p.year is not None and year_from <= p.year <= year_to]
        if len(papers) > limit:
            meta = dict(meta, complete=False, truncated=True)
        result = self._retrieval_result(papers[:limit], meta, baseline=baseline, started=started, pages=meta["pages"])
        return self._record_retrieval(result, meta)

    def is_paper(self, row):
        return True


class OpenReviewSource(ConferenceSource):
    name = "openreview"
    endpoint = "https://api2.openreview.net/notes/search"

    def _search_papers_result(self, query, limit=50, year_from=1900, year_to=None):
        modern = super()._search_papers_result(query, limit, year_from, year_to)
        self.api_coverage = [{"version": "v2", "status": modern.status.value, "records": len(modern.papers)}]
        if year_from >= 2024 or modern.failed:
            self.api_coverage.append({"version": "v1", "status": "not_attempted"})
            return modern
        self.endpoint = "https://api.openreview.net/notes/search"
        try:
            legacy = super()._search_papers_result(query, limit, year_from, year_to)
        finally:
            self.endpoint = "https://api2.openreview.net/notes/search"
        self.api_coverage.append({"version": "v1", "status": legacy.status.value, "records": len(legacy.papers)})
        # Interleave the two API generations before applying the source cap;
        # otherwise a full v2 page silently discards every older v1 paper.
        combined = [p for pair in zip_longest(modern.papers, legacy.papers) for p in pair if p is not None]
        papers = list({p.id: p for p in combined}.values())
        stats = modern.request_stats
        for name in ("requests", "retries", "cache_hits", "rate_limited", "errors", "pages", "elapsed_seconds"):
            setattr(stats, name, getattr(stats, name) + getattr(legacy.request_stats, name))
        complete = modern.complete and legacy.complete and len(papers) <= limit
        status = RetrievalStatus.SUCCESS if complete else RetrievalStatus.PARTIAL if legacy.failed else RetrievalStatus.TRUNCATED
        result = RetrievalResult(papers=papers[:limit], provider=self.name, status=status,
                                 complete=complete, error=legacy.error if legacy.failed else None,
                                 request_stats=stats, truncated=len(papers) > limit)
        return self._record_retrieval(result)

    def params(self, query, page, size):
        return {"term": query, "type": "terms", "content": "all", "source": "forum", "limit": size, "offset": (page - 1) * size}

    @staticmethod
    def extract(payload):
        if not isinstance(payload.get("notes"), list):
            raise ValueError("OpenReview response has no notes list")
        return payload["notes"]

    def is_paper(self, row):
        return bool(row.get("id")) and row.get("id") == row.get("forum") and not row.get("ddate")

    def to_paper(self, note):
        content = note.get("content") or {}
        venue = value(content, "venue")
        venueid = value(content, "venueid")
        invitations = note.get("invitations") or [note.get("invitation", "")]
        dates = re.findall(r"(?:19|20)\d{2}", str(venueid) + " " + str(venue) + " " + " ".join(invitations))
        year = int(dates[0]) if dates else (datetime.fromtimestamp(note["tcdate"] / 1000, timezone.utc).year if note.get("tcdate") else None)
        status = "submission"
        if re.search(r"reject|withdraw", str(venue), re.I):
            status = "rejected_or_withdrawn"
        elif re.search(r"\b(accepted|poster|oral|spotlight)\b", str(venue), re.I):
            status = "accepted"
        return Paper(id="openreview:" + note["id"], title=value(content, "title"), abstract=value(content, "abstract") or None,
                     authors=[Author(str(a)) for a in (value(content, "authors", []) or [])], year=year,
                     venue=venue or venueid or None, doi=normalize_doi(value(content, "doi")) or None,
                     source=self.name, url="https://openreview.net/forum?id=" + note["id"],
                     topics=keywords if isinstance(keywords := value(content, "keywords", []), list) else [str(keywords)], identifiers=PaperIdentifiers(openreview_id=note["id"]),
                     publication_status=status)


class DBLPSource(ConferenceSource):
    name = "dblp"
    endpoint = "https://dblp.org/search/publ/api"

    def params(self, query, page, size):
        return {"q": query, "format": "json", "h": size, "f": (page - 1) * size, "c": 0}

    @staticmethod
    def extract(payload):
        hits = payload.get("result", {}).get("hits")
        if not isinstance(hits, dict):
            raise ValueError("DBLP response has no result.hits object; an HTML challenge is not an empty search")
        rows = hits.get("hit", [])
        return [rows] if isinstance(rows, dict) else rows

    def to_paper(self, row):
        data = row.get("info") or {}
        authors = (data.get("authors") or {}).get("author") or []
        if not isinstance(authors, list):
            authors = [authors]
        year = int(data["year"]) if str(data.get("year", "")).isdigit() else None
        raw_doi = data.get("doi")
        doi = normalize_doi(raw_doi if isinstance(raw_doi, str) else "") or None
        venue = data.get("venue")
        if isinstance(venue, list):
            venue = " / ".join(str(v) for v in venue)
        return Paper(id="dblp:" + data.get("key", ""), title=data.get("title", ""),
                     authors=[Author(a.get("text", "") if isinstance(a, dict) else str(a)) for a in authors],
                     year=year, venue=venue, doi=doi, source=self.name,
                     url=data.get("ee") if isinstance(data.get("ee"), str) else data.get("url"),
                     identifiers=PaperIdentifiers(doi=doi or "", dblp_key=data.get("key", "")),
                     publication_status="published_metadata")


class GoogleScholarSource(ConferenceSource):
    """Google Scholar results through SerpApi, not Google's own API.

    Search snippets are kept separately from full abstracts. No API key is
    cached, logged, or exposed in result metadata.
    """

    name = "google_scholar"
    endpoint = "https://serpapi.com/search.json"
    PAGE_SIZE = 20

    def _quota_identity(self):
        return self.name, hashlib.sha256(serpapi_api_key().encode()).hexdigest()

    def _transport_request(self, method, url, **kwargs):
        key = serpapi_api_key()
        try:
            response = super()._transport_request(method, url, **kwargs)
        except requests.RequestException as exc:
            error_type = requests.Timeout if isinstance(exc, requests.Timeout) else requests.RequestException
            raise error_type(str(exc).replace(key, "[redacted]") if key else str(exc)) from None
        if key:
            response.url = (response.url or "").replace(key, "[redacted]")
            response._content = response.content.replace(key.encode(), b"[redacted]")
        return response

    def _search_papers_result(self, query, limit=50, year_from=1900, year_to=None):
        year_to = year_to or current_year()
        baseline, started = self._budget_counters(), time.monotonic()
        if not serpapi_api_key():
            return self._record_retrieval(RetrievalResult(provider=self.name, status=RetrievalStatus.PROVIDER_ERROR,
                                         complete=False, error="Google Scholar 自动检索需在设置中配置 SerpApi API Key；也可使用检索计划中的谷歌学术链接手动检索。"))
        raw, meta = self._logical_paging(self.endpoint,
            lambda page, size: {"engine": "google_scholar", "q": query, "hl": "en", "as_ylo": year_from,
                                "as_yhi": year_to, "num": size, "start": (page - 1) * size, "api_key": serpapi_api_key()},
            self.extract, limit, "search", query, limit, year_from, year_to)
        papers = [self.to_paper(row) for row in raw if row.get("title")]
        papers = [p for p in papers if p.year is not None and year_from <= p.year <= year_to]
        result = self._retrieval_result(papers, meta, baseline=baseline, started=started, pages=meta["pages"])
        return self._record_retrieval(result, meta)

    @staticmethod
    def extract(payload):
        if payload.get("error"):
            raise PermanentSourceError("google_scholar", str(payload["error"])[:200])
        rows = payload.get("organic_results")
        if isinstance(rows, list):
            return rows
        if (payload.get("search_information") or {}).get("organic_results_state") == "Fully empty":
            return []
        raise ValueError("SerpApi response has no Scholar results or explicit empty status")

    def to_paper(self, row):
        info = row.get("publication_info") or {}
        summary = str(info.get("summary") or "")
        dates = re.findall(r"\b(?:19|20)\d{2}\b", summary)
        year = int(dates[-1]) if dates else None
        # A truncated publication summary cannot establish a CCF venue rank.
        parts = re.split(r"\s+-\s+", summary)
        venue = None
        if len(parts) >= 3:
            candidate = re.sub(r",?\s*(?:19|20)\d{2}\s*$", "", " - ".join(parts[1:-1])).strip()
            if candidate and not re.search(r"\.\.\.|…", candidate):
                venue = candidate
        native = str(row.get("result_id") or "")
        link = row.get("link")
        doi = normalize_doi(link if isinstance(link, str) else "") or None
        return Paper(id="google_scholar:" + native if native else "", title=row["title"],
                     authors=[Author(str(a.get("name", ""))) for a in info.get("authors", []) if isinstance(a, dict)],
                     year=year, venue=venue, doi=doi, source=self.name, url=link,
                     citation_count=int(((row.get("inline_links") or {}).get("cited_by") or {}).get("total") or 0),
                     identifiers=PaperIdentifiers(doi=doi or "", google_scholar_id=native),
                     search_snippet=row.get("snippet") or "", publication_status="unknown")
