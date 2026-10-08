"""API clients for Semantic Scholar, OpenAlex, Crossref and arXiv."""

import logging
import re
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone

import requests

from litsearch.cache import Cache
from litsearch.config import current_year, openalex_api_key, semantic_scholar_api_key
from litsearch.diagnostics import (
    ParseError,
    SourceError,
    SourceErrorKind,
    classify_http_error,
    get_diagnostics,
)
from litsearch.identifiers import (
    PaperIdentifiers,
    normalize_arxiv,
    normalize_doi,
    normalize_openalex,
)
from litsearch.models import Author, DiscoveryTrace, Paper
from litsearch.retrieval import (
    RequestStats,
    RetrievalResult,
    RetrievalStatus,
    coerce_status,
)
from litsearch.stop_reasons import (
    CountingSession,
    get_http_budget,
    http_budget_snapshot,
    note_cache_hit,
    note_canceled,
)

logger = logging.getLogger(__name__)


class RetrievalCanceled(RuntimeError):
    """The user cancelled; results so far are partial by definition."""


class RetrievalTimeout(RuntimeError):
    """The total wall-clock budget for one logical retrieval ran out."""


def retrieval_status_for_exception(exc: BaseException) -> RetrievalStatus:
    """Which retrieval status an escaping exception means.

    Rate limiting keeps its own name instead of collapsing into a generic
    ``PROVIDER_ERROR``: a 429 has an actionable user-side fix (add an API key),
    and reporting it as "the provider is broken" hides that.
    """
    if isinstance(exc, RetrievalCanceled):
        return RetrievalStatus.CANCELED
    if isinstance(exc, RetrievalTimeout):
        return RetrievalStatus.TIMEOUT
    if isinstance(exc, SourceError) and exc.kind is SourceErrorKind.RATE_LIMITED:
        return RetrievalStatus.RATE_LIMITED
    if isinstance(exc, (requests.exceptions.Timeout, TimeoutError)):
        # Builtin TimeoutError is what a socket-level timeout surfaces as.
        return RetrievalStatus.TIMEOUT
    return RetrievalStatus.PROVIDER_ERROR


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
    if doi := normalize_doi(pid):
        return f"DOI:{doi}"
    if pid.lower().startswith("s2:"):
        return pid[3:]
    if ax := normalize_arxiv(pid):
        return f"ARXIV:{ax}"
    return pid


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class BaseSource(ABC):
    """Abstract base for all paper data sources."""

    name: str = "base"

    #: Bump when a cached payload's *shape* changes. Cache keys include it, so
    #: raising it retires every older row instead of feeding a new reader a
    #: stale shape (v3 stored bare lists; v4 stores a completeness envelope).
    CACHE_PARSER_VERSION = "parser_v4"

    #: How many times a transient failure is retried before giving up.
    MAX_HTTP_RETRIES = 2

    #: Wall-clock ceiling for one logical paged retrieval, in seconds.
    TOTAL_TIMEOUT_SECONDS = 180.0

    #: Page size for providers that support offset paging. OpenAlex documents
    #: 1-100 and lists 200 as deprecated-but-accepted, so 100 is the supported
    #: maximum (https://help.openalex.org/api/paging/, checked 2026-10-08).
    PAGE_SIZE = 100

    #: Upper bound on the HTTP requests one logical retrieval may issue.
    #: ``None`` (the default) leaves the ceiling to the caller. When set, a
    #: retrieval that would exceed it stops with
    #: :attr:`RetrievalStatus.BUDGET_EXHAUSTED` **and says so**, instead of
    #: returning a short list that reads like a complete answer.
    MAX_REQUESTS_PER_RETRIEVAL: int | None = None

    def __init__(self, cache: Cache | None = None):
        self._cache = cache
        # Counts every call that actually reaches the transport, including
        # retries and page requests, so "HTTP requests" means requests rather
        # than SourceManager method invocations.
        self._session = CountingSession(requests.Session(), self.name)
        self._session.headers.update({"User-Agent": "LEExtractor/0.3"})
        self._cancel_check = None
        self._request_budget = self.MAX_REQUESTS_PER_RETRIEVAL
        #: Result of the most recent logical retrieval, for callers that need
        #: more than the legacy list: status, completeness, statistics.
        self.last_retrieval_result: RetrievalResult | None = None
        #: Legacy bookkeeping view of that same retrieval (page cursor,
        #: ``available``, error text). Kept because existing callers read it;
        #: it is *derived from* the retrieval, never the source of truth.
        self.last_retrieval_meta: dict = {}

    def set_cancel_check(self, predicate) -> None:
        """Install a ``() -> bool`` polled between pages/retries.

        Without this a long pagination could not be stopped, and a cancelled
        run would still be reported as a completed one.
        """
        self._cancel_check = predicate

    def set_request_budget(self, limit: int | None) -> None:
        """Cap how many HTTP requests one logical retrieval may issue.

        ``None`` removes the cap. Hitting it is reported as
        :attr:`RetrievalStatus.BUDGET_EXHAUSTED`, never as a quiet short list.
        """
        self._request_budget = None if limit is None else max(0, int(limit))

    def _budget_exhausted(self, issued: int) -> bool:
        return self._request_budget is not None and issued >= self._request_budget

    def _paging_guard(self, issued: int, deadline: float) -> tuple[str, str] | None:
        """Stop signal before the next page request, or ``None`` to continue.

        Returns ``(reason, error)`` — a :class:`RetrievalStatus` value plus the
        human-readable explanation — so cancel, budget exhaustion and timeout
        are reported identically by every provider loop instead of each one
        collapsing them into "something went wrong".
        """
        if self.is_canceled():
            note_canceled()
            return RetrievalStatus.CANCELED.value, f"{self.name}: canceled by user"
        if self._budget_exhausted(issued):
            return (
                RetrievalStatus.BUDGET_EXHAUSTED.value,
                f"{self.name}: request budget of {self._request_budget} exhausted",
            )
        if time.monotonic() > deadline:
            return (
                RetrievalStatus.TIMEOUT.value,
                f"{self.name}: total timeout of {self.TOTAL_TIMEOUT_SECONDS:.0f}s exceeded",
            )
        return None

    def is_canceled(self) -> bool:
        if self._cancel_check is None:
            return False
        try:
            return bool(self._cancel_check())
        except Exception:  # a broken predicate must not abort retrieval
            return False

    # -- public API (thin wrappers over the result-returning path) -------
    #
    # The list-shaped signatures are kept so every existing caller and test
    # double keeps working, but they carry no information of their own: the
    # retrieval *is* the RetrievalResult below, and completeness, status and
    # request statistics are read from it — never reconstructed from the list.

    def search_papers(
        self, query: str, limit: int = 50, year_from: int = 1900, year_to: int | None = None
    ) -> list[Paper]:
        """Search papers by keyword query."""
        return self._search_papers_result(query, limit, year_from, year_to).papers

    def get_citations(self, paper_id: str, limit: int = 100) -> list[Paper]:
        """Get papers that cite the given paper (forward citations)."""
        return self._get_citations_result(paper_id, limit).papers

    def get_references(self, paper_id: str, limit: int = 100) -> list[Paper]:
        """Get papers cited by the given paper (backward references)."""
        return self._get_references_result(paper_id, limit).papers

    # -- internal API: one RetrievalResult per retrieval -----------------
    #
    # Abstract on purpose. A provider that only implemented the list API
    # would leave the consumer unable to tell "no results" from "it failed",
    # so it cannot be instantiated at all.

    @abstractmethod
    def _search_papers_result(
        self, query: str, limit: int = 50, year_from: int = 1900, year_to: int | None = None
    ) -> RetrievalResult:
        """Search papers, reporting status and completeness."""

    @abstractmethod
    def _get_citations_result(self, paper_id: str, limit: int = 100) -> RetrievalResult:
        """Forward citations, reporting status and completeness."""

    @abstractmethod
    def _get_references_result(self, paper_id: str, limit: int = 100) -> RetrievalResult:
        """Backward references, reporting status and completeness."""

    @abstractmethod
    def get_paper(self, paper_id: str) -> Paper | None:
        """Fetch full metadata for a single paper."""

    @abstractmethod
    def resolve_doi(self, doi: str) -> Paper | None:
        """Resolve a DOI to a Paper."""

    # -- cache ----------------------------------------------------------

    def _cached(self, func_name: str, *args) -> dict | list | None:
        if self._cache is None:
            return None
        row = self._cache.get(
            self.name, self.CACHE_PARSER_VERSION, func_name, *[str(a) for a in args]
        )
        if row is not None:
            note_cache_hit(self.name)
        return row

    def _cache_set(self, value, func_name: str, *args):
        if self._cache is not None:
            self._cache.set(
                value, self.name, self.CACHE_PARSER_VERSION, func_name, *[str(a) for a in args]
            )

    def _cached_record(self, func_name: str, *args) -> dict | None:
        """Read a single-record cache row that is *not* a paged envelope."""
        row = self._cached(func_name, *args)
        return row if isinstance(row, dict) else None

    # -- completeness envelope ------------------------------------------

    @staticmethod
    def _envelope(items: list, *, complete: bool, next_page: int = 1,
                  error: str | None = None, truncated: bool = False) -> dict:
        """Wrap a paged result with what is needed to resume it.

        Storing a bare list cannot express "this is half a pagination", which
        is how v0.9.0 served a truncated citation list forever after one
        failed request.
        """
        return {
            "items": items,
            "complete": bool(complete),
            "next_page": int(next_page),
            "error": error,
            "truncated": bool(truncated),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _unwrap(row) -> tuple[list, bool, int, str | None]:
        """Read either the v4 envelope or a legacy bare list.

        A legacy bare list is treated as *incomplete* with no known resume
        point: we cannot prove it was whole, and assuming it was is the bug.
        """
        if isinstance(row, dict) and "items" in row:
            return (
                list(row.get("items") or []),
                bool(row.get("complete")),
                int(row.get("next_page") or 1),
                row.get("error"),
            )
        if isinstance(row, list):
            return list(row), False, 1, "legacy cache row without completeness metadata"
        return [], False, 1, "unreadable cache row"

    # -- RetrievalResult construction -----------------------------------

    @staticmethod
    def _paging_meta(**overrides) -> dict:
        """Bookkeeping for one paged retrieval: completeness and why it ended.

        ``reason`` carries a :class:`RetrievalStatus` *value* when the run was
        cut short by something other than the caller's limit; ``None`` means
        "nothing went wrong", so completeness alone decides the status.
        """
        meta = {
            "complete": False,
            "next_page": 1,
            "error": None,
            "truncated": False,
            "reason": None,
            "pages": 0,
            "available": None,
        }
        meta.update(overrides)
        return meta

    def _budget_counters(self) -> dict:
        """Counter snapshot for *this* provider, used to diff one retrieval.

        ``cache_hits`` is process-wide in :class:`HttpBudget` (its per-source
        bucket records only that a source was seen), so it is taken from the
        global counter — correct for a single-threaded retrieval.
        """
        snapshot = http_budget_snapshot()
        counters = dict((snapshot.get("by_source") or {}).get(self.name) or {})
        counters["cache_hits"] = int(snapshot.get("cache_hits", 0))
        return counters

    def _request_stats(self, baseline: dict, started: float, pages: int) -> RequestStats:
        after = self._budget_counters()

        def delta(key: str) -> int:
            return max(0, int(after.get(key, 0)) - int(baseline.get(key, 0)))

        return RequestStats(
            requests=delta("requests"),
            retries=delta("retries"),
            cache_hits=delta("cache_hits"),
            rate_limited=delta("rate_limited"),
            errors=delta("errors"),
            elapsed_seconds=round(max(0.0, time.monotonic() - started), 3),
            pages=max(0, int(pages)),
        )

    def _retrieval_result(
        self, papers: list[Paper], meta: dict, *, started: float, baseline: dict, pages: int,
    ) -> RetrievalResult:
        """Turn paging bookkeeping into the single result object.

        The status is decided here, once, from *why* the retrieval ended — not
        afterwards from the paper list, which cannot know the difference.
        """
        complete = bool(meta.get("complete"))
        reason = meta.get("reason")
        if reason:
            status = coerce_status(reason)
        elif complete:
            status = (
                RetrievalStatus.SUCCESS_EMPTY if not papers else RetrievalStatus.SUCCESS
            )
        elif bool(meta.get("truncated")):
            # Normal caller-limit truncation is usable but incomplete.  It is
            # not the same event as a timeout/rate-limit after a partial page.
            status = RetrievalStatus.TRUNCATED
        else:
            status = RetrievalStatus.PARTIAL
        error = meta.get("error")
        if status is RetrievalStatus.TRUNCATED:
            error = None
        elif error is None and reason:
            error = f"{self.name}: {status.value}"
        cursor = meta.get("next_page")
        return RetrievalResult(
            papers=list(papers),
            provider=self.name,
            status=status,
            complete=complete,
            error=error,
            next_cursor=None if complete else (str(cursor) if cursor is not None else None),
            request_stats=self._request_stats(baseline, started, pages),
            truncated=bool(meta.get("truncated")),
        )

    def _record_retrieval(self, result: RetrievalResult, meta: dict | None = None) -> RetrievalResult:
        """Publish the result (and its legacy meta view) on the source."""
        self.last_retrieval_result = result
        if meta is not None:
            self.last_retrieval_meta = dict(meta)
        return result

    def _unsupported_result(self, capability: str) -> RetrievalResult:
        """A capability this provider does not have.

        Deliberately an explicit failure rather than ``SUCCESS_EMPTY``: an
        empty list would claim "the provider answered and this paper has no
        citations", which is false — the endpoint simply does not exist. That
        false claim is the coverage bug this release removes, so a permanent
        capability gap is reported as one and never as a genuine zero.
        """
        return self._record_retrieval(RetrievalResult(
            papers=[],
            provider=self.name,
            status=RetrievalStatus.PROVIDER_ERROR,
            complete=False,
            error=f"{self.name}: {capability} not supported by this provider",
        ))

    # -- retry / cancel / deadline --------------------------------------

    def _retry_delay(self, attempt: int, resp=None) -> float:
        """Backoff for one retry, honouring ``Retry-After`` when present."""
        if resp is not None:
            header = (getattr(resp, "headers", {}) or {}).get("Retry-After")
            if header and str(header).strip().isdigit():
                return min(float(str(header).strip()), 30.0)
        return min(2.0 * (2 ** attempt), 30.0)

    def _transport_request(self, method: str, url: str, **kwargs):
        """Send one request through whichever transport object is in play.

        ``self._session`` is normally the counting wrapper, which deliberately
        defines no verbs so that a replacement of the *inner* session's verbs
        (``source._session.get = Mock(...)``, a pattern the test-suite relies
        on) still wins. Reaching the wrapper directly would bypass that double
        and hit the real network, so the inner session is checked first.

        Accounting happens here rather than inside the wrapper because a
        replaced verb never passes through the wrapper: counting there would
        silently report zero requests for an entire suite of mocked calls.
        An attempt that raises is still an attempt the provider saw.
        """
        session = self._session
        try:
            resp = self._dispatch_request(session, method, url, **kwargs)
        except BaseException:
            get_http_budget().note_error(self.name)
            raise
        get_http_budget().note_request(
            self.name, getattr(resp, "status_code", None)
        )
        return resp

    @staticmethod
    def _dispatch_request(session, method: str, url: str, **kwargs):
        inner = getattr(session, "_session", None)
        if inner is not None:
            verb = getattr(inner, method.lower(), None)
            # A *replaced* verb is a test double and must be honoured. A verb
            # that is still ``requests.Session``'s own implementation is routed
            # through ``Session.request`` instead, so session-level auth
            # handlers and hooks still run.
            if callable(verb) and getattr(verb, "__func__", None) is not getattr(
                requests.Session, method.lower(), None
            ):
                return verb(url, **kwargs)
        request = getattr(session, "request", None)
        if callable(request):
            return request(method, url, **kwargs)
        verb = getattr(session, method.lower(), None)
        if callable(verb):
            return verb(url, **kwargs)
        raise AttributeError(
            f"{type(session).__name__} supports neither .request nor .{method.lower()}"
        )

    def _request_with_retries(self, method: str, url: str, *, deadline: float, **kwargs):
        """Issue one HTTP call with bounded retries, cancellation and deadline.

        Retries only transient outcomes (429 and 5xx). A 404 or a 400 is a
        final answer — retrying it wastes the caller's budget and hides the
        real problem.
        """
        kwargs.setdefault("timeout", 30)
        last_exc = None
        for attempt in range(self.MAX_HTTP_RETRIES + 1):
            if self.is_canceled():
                raise RetrievalCanceled(f"{self.name}: canceled before request")
            if time.monotonic() > deadline:
                raise RetrievalTimeout(
                    f"{self.name}: total timeout of {self.TOTAL_TIMEOUT_SECONDS:.0f}s exceeded"
                )
            try:
                resp = self._transport_request(method, url, **kwargs)
            except Exception as exc:
                last_exc = exc
                if attempt >= self.MAX_HTTP_RETRIES:
                    raise
                self._session.note_retry()
                time.sleep(self._retry_delay(attempt))
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt >= self.MAX_HTTP_RETRIES:
                    return resp
                self._session.note_retry()
                time.sleep(self._retry_delay(attempt, resp))
                continue
            return resp
        if last_exc is not None:  # pragma: no cover - loop always returns or raises
            raise last_exc
        return None

    def _paged_get(
        self,
        url: str,
        build_params,
        extract,
        limit: int,
        *,
        start_page: int = 1,
        page_size: int | None = None,
    ) -> tuple[list, dict]:
        """Fetch ``limit`` records with a CONSTRICTED page size.

        The page size is fixed for the whole sequence and the result is sliced
        at the end. v0.9.0 instead sent ``per_page = limit - len(papers)``, so
        page 2 asked for a different window size than page 1; with offset
        paging that shifts every subsequent window and silently duplicated and
        dropped records (limit=250 returned 250 rows of which only 200 were
        distinct). It also sent ``per_page=200``, above the documented maximum.

        Returns ``(items, meta)`` where meta records completeness so a partial
        result can be cached as partial and resumed rather than mistaken for a
        complete one.
        """
        size = int(page_size or self.PAGE_SIZE)
        deadline = time.monotonic() + self.TOTAL_TIMEOUT_SECONDS
        items: list = []
        page = max(1, int(start_page))
        meta = self._paging_meta(next_page=page)
        total_available: int | None = None

        while len(items) < limit:
            guard = self._paging_guard(meta["pages"], deadline)
            if guard is not None:
                meta["reason"], meta["error"] = guard
                return items, meta
            try:
                meta["pages"] += 1
                resp = self._request_with_retries(
                    "GET", url, deadline=deadline, params=build_params(page, size)
                )
                if resp is None:  # pragma: no cover - defensive
                    meta["error"] = "no response"
                    meta["reason"] = RetrievalStatus.PROVIDER_ERROR.value
                    return items, meta
                raise_for_data(resp, self.name)
                payload = resp.json() or {}
                results = extract(payload) or []
                # Providers that publish a total let us stop as soon as it is
                # reached. Without this the loop kept requesting pages past the
                # end of the result set whenever the caller's limit exceeded
                # what the provider actually holds.
                reported = ((payload.get("meta") or {}).get("count")
                            if isinstance(payload, dict) else None)
                if isinstance(reported, int) and reported >= 0:
                    total_available = reported
            except RetrievalCanceled as exc:
                meta["error"] = str(exc)
                meta["reason"] = RetrievalStatus.CANCELED.value
                note_canceled()
                return items, meta
            except RetrievalTimeout as exc:
                meta["error"] = str(exc)
                meta["reason"] = RetrievalStatus.TIMEOUT.value
                return items, meta
            except Exception as exc:  # transient exhausted, contract, parse
                log_source_failure(exc, self.name, "paged_get")
                meta["error"] = f"{type(exc).__name__}: {exc}"[:200]
                # A 429 gets its own status: it is actionable, not a generic
                # provider fault, and it must never look like an empty answer.
                meta["reason"] = retrieval_status_for_exception(exc).value
                return items, meta

            if not results:
                # A provider answering with an empty page is the natural end of
                # the sequence: everything asked for was returned.
                meta["complete"] = True
                meta["next_page"] = page
                meta["available"] = total_available
                return items[:limit], meta

            items.extend(results)
            page += 1
            meta["next_page"] = page
            if len(results) < size:
                # Short page => last page, unless we stopped exactly on limit.
                meta["complete"] = True
                meta["available"] = total_available
                return items[:limit], meta
            if total_available is not None and len(items) >= total_available:
                meta["complete"] = True
                meta["available"] = total_available
                return items[:limit], meta
            time.sleep(0.3)

        # Reached the requested limit without an empty/short page: the sequence
        # is not proven exhausted, so it is marked truncated rather than
        # complete. A caller that wants "exactly N" is satisfied; a caller that
        # wants "all of them" is told it is not.
        meta["truncated"] = True
        meta["complete"] = False
        meta["next_page"] = page
        meta["available"] = total_available
        return items[:limit], meta

    def _logical_paging(self, url: str, build_params, extract, limit: int,
                        func_name: str, *cache_args):
        """Cache-aware fixed-page pagination shared by search and citations.

        On a cache miss it pages from the start. On a hit for an *incomplete*
        previous run it resumes from the recorded page and merges, so a
        retrieval interrupted by a network failure is completed on the next
        attempt instead of being served truncated forever.
        """
        cap = max(0, int(limit))
        if cap == 0:
            return [], self._paging_meta(complete=True)

        cached = self._cached(func_name, *cache_args)
        items: list = []
        start_page = 1
        if cached is not None:
            cached_items, complete, next_page, error = self._unwrap(cached)
            if complete and len(cached_items) >= min(cap, len(cached_items)):
                return cached_items[:cap], self._paging_meta(
                    complete=True, next_page=next_page, cache_hit=True,
                )
            items = cached_items
            start_page = next_page
            logger.info(
                "%s: resuming incomplete cached pagination at page %d (%d rows held, cache note: %s)",
                func_name, start_page, len(items), error,
            )

        remaining = cap - len(items)
        if remaining <= 0:
            return items[:cap], self._paging_meta(
                next_page=start_page, error=None,
                truncated=True, cache_hit=True,
            )

        fetched, meta = self._paged_get(
            url, build_params, extract, remaining, start_page=start_page
        )
        if start_page > 1:
            seen = {self._dedupe_key(i) for i in items}
            for row in fetched:
                key = self._dedupe_key(row)
                if key not in seen:
                    seen.add(key)
                    items.append(row)
        else:
            items = fetched

        meta["items"] = len(items)
        self._cache_set(
            self._envelope(
                items, complete=meta["complete"], next_page=meta["next_page"],
                error=meta["error"], truncated=meta.get("truncated", False),
            ),
            func_name, *cache_args,
        )
        return items[:cap], meta

    @staticmethod
    def _dedupe_key(row) -> str:
        """Identity of a raw provider record, for merge-time de-duplication."""
        if isinstance(row, dict):
            return str(
                row.get("id") or row.get("doi") or row.get("paperId")
                or row.get("DOI") or row.get("title") or ""
            ).lower()
        return str(row)


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

#: OpenAlex documents ``per_page`` as 1-100. ``200`` is still accepted as
#: deprecated legacy behaviour and "will be removed", so it must not be relied
#: on: https://help.openalex.org/api/paging/ (checked 2026-10-08).
OA_PAGE_SIZE_LIMIT = 100


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
        api_key = semantic_scholar_api_key()
        if api_key:
            self._session.headers["x-api-key"] = api_key
        else:
            self._session.headers.pop("x-api-key", None)
        kwargs.setdefault("timeout", 30)
        last = None
        for attempt in range(self.MAX_RETRIES + 1):
            self._pace()
            _S2_STATS["requests"] += 1
            # Through the shared transport so the HTTP budget, cancellation and
            # failure accounting see S2's requests too; S2 additionally counts
            # its own retry statistics for the 429 guidance in the UI.
            if self.is_canceled():
                raise RetrievalCanceled("semantic_scholar: canceled before request")
            resp = self._transport_request(method, url, **kwargs)
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
            url=data.get("url") or (f"https://doi.org/{doi}" if doi else ""),
            source=self.name,
            identifiers=PaperIdentifiers(doi=normalize_doi(doi), semantic_scholar_id=data.get("paperId") or "", arxiv_id=normalize_arxiv(ext.get("ArXiv")), pmid=str(ext.get("PubMed") or "")),
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

    def _search_papers_result(
        self, query: str, limit: int = 50, year_from: int = 1900, year_to: int | None = None
    ) -> RetrievalResult:
        started = time.monotonic()
        baseline = self._budget_counters()
        year_to = resolve_year_to(year_to)
        cap = max(0, int(limit))
        cache_args = ("s2_search", query, str(limit), str(year_from), str(year_to))
        if cap == 0:
            return self._record_retrieval(
                self._retrieval_result([], self._paging_meta(complete=True),
                                       started=started, baseline=baseline, pages=0)
            )

        records: list[dict] = []
        papers: list[Paper] = []
        offset = 0
        cached = self._cached(*cache_args)
        if isinstance(cached, dict) and "items" in cached:
            # Only the v4 envelope can prove what it holds. A legacy bare list
            # (or an unreadable row) is treated as a miss: it cannot show that
            # it was whole, and serving it as the answer is the reported bug.
            records, complete, next_page, _error = self._unwrap(cached)
            papers = [self._paper_from_s2(d) for d in records]
            if complete:
                return self._record_retrieval(
                    self._retrieval_result(
                        papers[:cap], self._paging_meta(complete=True, next_page=next_page,
                                                        cache_hit=True),
                        started=started, baseline=baseline, pages=0),
                )
            if len(papers) >= cap:
                # The cached prefix already satisfies this call, but it is not
                # proven whole: report it as partial, never as complete.
                return self._record_retrieval(
                    self._retrieval_result(
                        papers[:cap], self._paging_meta(complete=False, next_page=next_page,
                                                        truncated=True, cache_hit=True),
                        started=started, baseline=baseline, pages=0),
                )
            offset = int(next_page)

        page_size = min(100, cap)
        complete = False
        truncated = False
        error = None
        reason = None
        pages = 0
        deadline = time.monotonic() + self.TOTAL_TIMEOUT_SECONDS
        while len(papers) < cap:
            guard = self._paging_guard(pages, deadline)
            if guard is not None:
                reason, error = guard
                break
            try:
                pages += 1
                resp = self._request("GET",
                    f"{S2_BASE}/paper/search",
                    params={
                        "query": query,
                        "limit": page_size,
                        "offset": offset,
                        "year": f"{year_from}-{year_to}",
                        "fields": S2_SEARCH_FIELDS,
                    },
                    timeout=30,
                )
                raise_for_data(resp, "semantic_scholar")
                payload = resp.json() or {}
                data = payload.get("data") or []
            except Exception as exc:
                log_source_failure(exc, "semantic_scholar", "search")
                reason = retrieval_status_for_exception(exc).value
                error = f"{type(exc).__name__}: {exc}"[:200]
                break
            if not data:
                complete = True
                break
            for d in data:
                records.append(d)
                papers.append(self._paper_from_s2(d))
            offset += len(data)
            reported = payload.get("total")
            if len(data) < page_size:
                complete = True
                break
            if isinstance(reported, int) and reported >= 0 and offset >= reported:
                complete = True
                break
            if len(papers) >= cap:
                # A full page came back and only the caller's limit stopped us,
                # so the sequence is not proven exhausted.
                truncated = True
                break
            time.sleep(1.0)

        meta = self._paging_meta(
            complete=complete, next_page=offset, error=error, truncated=truncated,
            reason=reason, pages=pages,
        )
        # The envelope is written even when the retrieval is incomplete, but it
        # records that incompleteness (plus where to resume) instead of letting
        # a truncated prefix pass for the whole answer.
        self._cache_set(
            self._envelope(records, complete=complete, next_page=offset,
                           error=error, truncated=truncated),
            *cache_args,
        )
        return self._record_retrieval(
            self._retrieval_result(papers[:cap], meta, started=started,
                                   baseline=baseline, pages=pages),
            meta,
        )

    def get_paper(self, paper_id: str) -> Paper | None:
        cached = self._cached("s2_paper", paper_id)
        if cached is not None:
            return self._paper_from_s2(cached)

        # Support DOI lookup
        url = f"{S2_BASE}/paper/{s2_paper_id(paper_id)}"
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

    def _s2_relation_result(
        self, paper_id: str, limit: int, *, cache_name: str, endpoint: str,
        relation_key: str, context: str, pause: float,
    ) -> RetrievalResult:
        """Shared paging for S2 citations/references (offset paging).

        Both endpoints answer ``{"data": [{"citingPaper"|"citedPaper": {...}}]}``
        with a raw offset window, so they share one implementation — and one
        definition of what "complete" means for a page sequence.
        """
        started = time.monotonic()
        baseline = self._budget_counters()
        cap = max(0, int(limit))
        cache_args = (cache_name, paper_id, str(limit))
        if cap == 0:
            return self._record_retrieval(
                self._retrieval_result([], self._paging_meta(complete=True),
                                       started=started, baseline=baseline, pages=0)
            )

        def papers_from(rows: list[dict]) -> list[Paper]:
            out = []
            for row in rows:
                related = row.get(relation_key, row)
                if related:
                    out.append(self._paper_from_s2(related))
            return out

        records: list[dict] = []
        papers: list[Paper] = []
        offset = 0
        cached = self._cached(*cache_args)
        if isinstance(cached, dict) and "items" in cached:
            records, complete, next_page, _error = self._unwrap(cached)
            papers = papers_from(records)
            if complete:
                return self._record_retrieval(
                    self._retrieval_result(
                        papers[:cap], self._paging_meta(complete=True, next_page=next_page,
                                                        cache_hit=True),
                        started=started, baseline=baseline, pages=0),
                )
            if len(papers) >= cap:
                return self._record_retrieval(
                    self._retrieval_result(
                        papers[:cap], self._paging_meta(complete=False, next_page=next_page,
                                                        truncated=True, cache_hit=True),
                        started=started, baseline=baseline, pages=0),
                )
            offset = int(next_page)

        page_size = min(500, cap)
        complete = False
        truncated = False
        error = None
        reason = None
        pages = 0
        deadline = time.monotonic() + self.TOTAL_TIMEOUT_SECONDS
        url_base = f"{S2_BASE}/paper/{s2_paper_id(paper_id)}/{endpoint}"
        while len(papers) < cap:
            guard = self._paging_guard(pages, deadline)
            if guard is not None:
                reason, error = guard
                break
            try:
                pages += 1
                resp = self._request("GET",
                    url_base,
                    params={
                        "limit": page_size,
                        "offset": offset,
                        "fields": S2_CITATION_FIELDS,
                    },
                    timeout=30,
                )
                raise_for_data(resp, "semantic_scholar")
                payload = resp.json() or {}
                data = payload.get("data") or []
            except Exception as exc:
                # A 404 lands here as a classified NotFoundError and becomes an
                # explicit provider error: "S2 does not hold this paper" is not
                # the same claim as "this paper has no citations".
                log_source_failure(exc, "semantic_scholar", context)
                reason = retrieval_status_for_exception(exc).value
                error = f"{type(exc).__name__}: {exc}"[:200]
                break
            if not data:
                complete = True
                break
            for d in data:
                related = d.get(relation_key, d)
                if related:
                    records.append(d)
                    papers.append(self._paper_from_s2(related))
            offset += len(data)
            reported = payload.get("total")
            if len(data) < page_size:
                complete = True
                break
            if isinstance(reported, int) and reported >= 0 and offset >= reported:
                complete = True
                break
            if len(papers) >= cap:
                truncated = True
                break
            time.sleep(pause)

        meta = self._paging_meta(
            complete=complete, next_page=offset, error=error, truncated=truncated,
            reason=reason, pages=pages,
        )
        self._cache_set(
            self._envelope(records, complete=complete, next_page=offset,
                           error=error, truncated=truncated),
            *cache_args,
        )
        return self._record_retrieval(
            self._retrieval_result(papers[:cap], meta, started=started,
                                   baseline=baseline, pages=pages),
            meta,
        )

    def _get_citations_result(self, paper_id: str, limit: int = 100) -> RetrievalResult:
        return self._s2_relation_result(
            paper_id, limit, cache_name="s2_citations", endpoint="citations",
            relation_key="citingPaper", context=f"citations({paper_id})", pause=0.5,
        )

    def _get_references_result(self, paper_id: str, limit: int = 100) -> RetrievalResult:
        return self._s2_relation_result(
            paper_id, limit, cache_name="s2_references", endpoint="references",
            relation_key="citedPaper", context=f"references({paper_id})", pause=0.5,
        )

    def resolve_doi(self, doi: str) -> Paper | None:
        return self.get_paper(f"DOI:{doi}")


# ---------------------------------------------------------------------------
# OpenAlex
# ---------------------------------------------------------------------------

OA_BASE = "https://api.openalex.org"


class _OpenAlexAuth(requests.auth.AuthBase):
    def __call__(self, request):
        if key := openalex_api_key():
            request.headers["Authorization"] = f"Bearer {key}"
        else:
            request.headers.pop("Authorization", None)
        return request


class OpenAlexSource(BaseSource):
    """OpenAlex API client — used as fallback for S2 coverage gaps."""

    name = "openalex"

    def __init__(self, cache: Cache | None = None):
        super().__init__(cache)
        # Bearer auth avoids placing the key in URLs and exception messages.
        self._session.auth = _OpenAlexAuth()

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
            url=data.get("doi") or (data.get("primary_location") or {}).get("landing_page_url") or data.get("id") or "",
            source=self.name,
            topics=topics,
            identifiers=PaperIdentifiers(doi=normalize_doi(doi), openalex_id=normalize_openalex(data.get("id"))),
        )

    def _search_papers_result(
        self, query: str, limit: int = 50, year_from: int = 1900, year_to: int | None = None
    ) -> RetrievalResult:
        started = time.monotonic()
        baseline = self._budget_counters()
        year_to = resolve_year_to(year_to)
        cache_args = ("oa_search", query, str(limit), str(year_from), str(year_to))

        def build_params(page: int, size: int) -> dict:
            return {
                "search": query,
                "per_page": size,
                "page": page,
                "filter": f"publication_year:{year_from}-{year_to}",
                "select": OA_SELECT,
            }

        rows, meta = self._logical_paging(
            f"{OA_BASE}/works", build_params,
            lambda payload: payload.get("results") or [],
            limit, *cache_args,
        )
        return self._record_retrieval(
            self._retrieval_result(
                [self._paper_from_oa(d) for d in rows], meta,
                started=started, baseline=baseline, pages=meta.get("pages", 0),
            ),
            meta,
        )

    @staticmethod
    def _oa_to_dict(paper: Paper) -> dict:
        """Minimal serialisable view of a Paper (for the disk cache)."""
        return {
            "id": f"https://openalex.org/{paper.identifiers.openalex_id}" if paper.identifiers.openalex_id else paper.id,
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
        paper_id = normalize_doi(paper_id) or normalize_openalex(paper_id) or paper_id
        cached = self._cached_record("oa_paper", paper_id)
        if cached is not None:
            return self._paper_from_oa(cached)

        url = f"{OA_BASE}/works/{paper_id}"
        if paper_id.startswith("10."):
            url = f"{OA_BASE}/works/https://doi.org/{paper_id}"
        try:
            resp = self._request_with_retries(
                "GET", url, deadline=time.monotonic() + 60
            )
            if resp.status_code == 404:
                return None
            raise_for_data(resp, "openalex")
            data = resp.json()
            self._cache_set(data, "oa_paper", paper_id)
            return self._paper_from_oa(data)
        except Exception as e:
            log_source_failure(e, "openalex", f"get_paper({paper_id})")
            return None

    def _get_citations_result(self, paper_id: str, limit: int = 100) -> RetrievalResult:
        started = time.monotonic()
        baseline = self._budget_counters()
        # OpenAlex' `cites:` filter only accepts an OpenAlex ID (W…), not a
        # DOI — resolve DOIs first, otherwise every lookup returns an error.
        oa_id = self._resolve_oa_id(paper_id)
        if not oa_id:
            # Not a genuine empty: we never got far enough to ask. Reporting []
            # as "this paper has no citations" would be a false claim.
            meta = self._paging_meta(
                error=f"openalex: could not resolve {paper_id} to an OpenAlex id",
                reason=RetrievalStatus.PROVIDER_ERROR.value,
            )
            return self._record_retrieval(
                self._retrieval_result([], meta, started=started, baseline=baseline, pages=0),
                meta,
            )
        cache_args = ("oa_citations", paper_id, str(limit))

        def build_params(page: int, size: int) -> dict:
            return {
                "filter": f"cites:{oa_id}",
                "per_page": size,
                "page": page,
                "select": OA_SELECT_MIN,
            }

        rows, meta = self._logical_paging(
            f"{OA_BASE}/works", build_params,
            lambda payload: payload.get("results") or [],
            limit, *cache_args,
        )
        return self._record_retrieval(
            self._retrieval_result(
                [self._paper_from_oa(d) for d in rows], meta,
                started=started, baseline=baseline, pages=meta.get("pages", 0),
            ),
            meta,
        )

    def _resolve_oa_id(self, paper_id: str) -> str:
        """Return the short OpenAlex ID (W…) for an OpenAlex ID or a DOI."""
        if oa_id := normalize_openalex(paper_id):
            return oa_id
        if doi := normalize_doi(paper_id):
            try:
                resp = self._transport_request(
                    "GET",
                    f"{OA_BASE}/works/https://doi.org/{doi}",
                    params={"select": "id"},
                    timeout=20,
                )
                if resp.status_code == 200:
                    return normalize_openalex(resp.json().get("id"))
                if resp.status_code != 404:
                    raise_for_data(resp, "openalex", "resolve_id")
            except Exception as e:
                log_source_failure(e, "openalex", f"resolve_id({paper_id})")
        return ""

    def _get_references_result(self, paper_id: str, limit: int = 100) -> RetrievalResult:
        """Resolve a paper's referenced works in batched id lookups.

        The batch size is capped at ``OA_PAGE_SIZE_LIMIT`` (100) like every
        other OpenAlex request: asking for 200 was accepted only as deprecated
        legacy behaviour, so relying on it would break silently the day it is
        removed. Chunks are sized to the page limit so a chunk never spans
        more than one page.
        """
        started = time.monotonic()
        baseline = self._budget_counters()
        paper = self.get_paper(paper_id)
        if paper is None:
            # The seed could not be resolved (404 *or* a transport failure), so
            # "no references" cannot be claimed: the question was never asked.
            meta = self._paging_meta(
                error=f"openalex: seed paper {paper_id} could not be resolved",
                reason=RetrievalStatus.PROVIDER_ERROR.value,
            )
            return self._record_retrieval(
                self._retrieval_result([], meta, started=started, baseline=baseline, pages=0),
                meta,
            )
        ref_ids = paper.reference_ids[:limit]
        if not ref_ids:
            meta = self._paging_meta(complete=True)
            return self._record_retrieval(
                self._retrieval_result([], meta, started=started, baseline=baseline, pages=0),
                meta,
            )

        papers: list[Paper] = []
        chunk_size = min(50, OA_PAGE_SIZE_LIMIT)
        chunks = list(range(0, len(ref_ids), chunk_size))
        pages = 0
        failed = 0
        error = None
        reason = None
        deadline = time.monotonic() + self.TOTAL_TIMEOUT_SECONDS
        for i in chunks:
            guard = self._paging_guard(pages, deadline)
            if guard is not None:
                reason, error = guard
                failed += 1
                break
            chunk = ref_ids[i : i + chunk_size]
            try:
                pages += 1
                resp = self._transport_request(
                    "GET",
                    f"{OA_BASE}/works",
                    params={
                        "filter": f"ids.openalex:{'|'.join(chunk)}",
                        "per_page": min(len(chunk), OA_PAGE_SIZE_LIMIT),
                        "select": OA_SELECT_MIN,
                    },
                    timeout=30,
                )
                raise_for_data(resp, "openalex")
                for d in resp.json().get("results") or []:
                    papers.append(self._paper_from_oa(d))
                time.sleep(0.3)
            except Exception as e:
                # Keep the batches that did answer: partial evidence beats none,
                # as long as it is not passed off as the complete set.
                log_source_failure(e, "openalex", "references_batch")
                failed += 1
                if reason is None:
                    reason = retrieval_status_for_exception(e).value
                    error = f"{type(e).__name__}: {e}"[:200]
        complete = failed == 0
        if not complete and error is not None:
            error = f"{failed}/{len(chunks)} reference batches failed: {error}"
        meta = self._paging_meta(
            complete=complete, error=error, reason=reason, pages=pages,
        )
        return self._record_retrieval(
            self._retrieval_result(papers, meta, started=started, baseline=baseline, pages=pages),
            meta,
        )

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
            identifiers=PaperIdentifiers(doi=normalize_doi(doi)),
        )

    def _search_papers_result(self, query, limit=50, year_from=1900, year_to=None) -> RetrievalResult:
        started = time.monotonic()
        baseline = self._budget_counters()
        year_to = resolve_year_to(year_to)
        cap = max(0, int(limit))
        cache_args = ("cr_search", query, str(limit), str(year_from), str(year_to))
        if cap == 0:
            return self._record_retrieval(
                self._retrieval_result([], self._paging_meta(complete=True),
                                       started=started, baseline=baseline, pages=0)
            )

        items: list[dict] = []
        offset = 0
        cached = self._cached(*cache_args)
        if isinstance(cached, dict) and "items" in cached:
            items, complete, next_page, _error = self._unwrap(cached)
            if complete:
                rows = items[:cap]
                return self._record_retrieval(
                    self._retrieval_result(
                        [self._paper_from_cr(d) for d in rows],
                        self._paging_meta(complete=True, next_page=next_page, cache_hit=True),
                        started=started, baseline=baseline, pages=0),
                )
            if len(items) >= cap:
                return self._record_retrieval(
                    self._retrieval_result(
                        [self._paper_from_cr(d) for d in items[:cap]],
                        self._paging_meta(complete=False, next_page=next_page,
                                          truncated=True, cache_hit=True),
                        started=started, baseline=baseline, pages=0),
                )
            offset = int(next_page)

        page_size = min(100, cap)
        complete = False
        truncated = False
        error = None
        reason = None
        pages = 0
        deadline = time.monotonic() + self.TOTAL_TIMEOUT_SECONDS
        while len(items) < cap:
            guard = self._paging_guard(pages, deadline)
            if guard is not None:
                reason, error = guard
                break
            try:
                pages += 1
                resp = self._transport_request(
                    "GET",
                    f"{CR_BASE}/works",
                    params={
                        "query": query,
                        "rows": page_size,
                        "offset": offset,
                        "filter": f"from-pub-date:{year_from}-01-01,until-pub-date:{year_to}-12-31",
                        "select": "DOI,title,abstract,author,issued,container-title,is-referenced-by-count,references-count",
                    },
                    timeout=30,
                )
                raise_for_data(resp, "crossref")
                message = (resp.json() or {}).get("message") or {}
                data = message.get("items") or []
                reported = message.get("total-results")
            except Exception as exc:
                log_source_failure(exc, "crossref", "search")
                reason = retrieval_status_for_exception(exc).value
                error = f"{type(exc).__name__}: {exc}"[:200]
                break
            if not data:
                complete = True
                break
            items.extend(data)
            offset += len(data)
            if len(data) < page_size:
                complete = True
                break
            if isinstance(reported, int) and reported >= 0 and offset >= reported:
                complete = True
                break
            if len(items) >= cap:
                truncated = True
                break

        meta = self._paging_meta(
            complete=complete, next_page=offset, error=error, truncated=truncated,
            reason=reason, pages=pages,
        )
        self._cache_set(
            self._envelope(items, complete=complete, next_page=offset,
                           error=error, truncated=truncated),
            *cache_args,
        )
        return self._record_retrieval(
            self._retrieval_result(
                [self._paper_from_cr(d) for d in items[:cap]], meta,
                started=started, baseline=baseline, pages=pages),
            meta,
        )

    def get_paper(self, paper_id: str) -> Paper | None:
        cached = self._cached("cr_paper", paper_id)
        if cached is not None:
            return self._paper_from_cr(cached)
        try:
            resp = self._transport_request(
                "GET", f"{CR_BASE}/works/{paper_id}", timeout=30
            )
            if resp.status_code == 404:
                return None
            raise_for_data(resp, "crossref")
            data = resp.json()
            self._cache_set(data, "cr_paper", paper_id)
            return self._paper_from_cr(data)
        except Exception as e:
            log_source_failure(e, "crossref", f"get_paper({paper_id})")
            return None

    def _get_citations_result(self, paper_id, limit=100) -> RetrievalResult:
        return self._unsupported_result("citations")

    def _get_references_result(self, paper_id, limit=100) -> RetrievalResult:
        return self._unsupported_result("references")

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
            identifiers=PaperIdentifiers(doi=normalize_doi(doi), arxiv_id=normalize_arxiv(arxiv_id)),
        )

    @staticmethod
    def _total_results(root) -> int | None:
        """``opensearch:totalResults`` from an arXiv Atom feed, if present.

        Without it "we filled the page we asked for" cannot be told apart from
        "the feed holds exactly that many records", and the first must never be
        cached as the second.
        """
        for child in root:
            if str(getattr(child, "tag", "")).endswith("}totalResults"):
                try:
                    return int((child.text or "").strip())
                except (TypeError, ValueError):
                    return None
        return None

    def _search_papers_result(
        self, query: str, limit: int = 50, year_from: int = 1900, year_to: int | None = None
    ) -> RetrievalResult:
        from xml.etree import ElementTree as ET

        started = time.monotonic()
        baseline = self._budget_counters()
        year_to = resolve_year_to(year_to)
        cap = max(0, int(limit))
        if cap == 0 or not query.strip():
            return self._record_retrieval(
                self._retrieval_result([], self._paging_meta(complete=True),
                                       started=started, baseline=baseline, pages=0)
            )
        if year_from > year_to:
            raise ValueError("Start year must not exceed end year")
        cache_args = (query, str(limit), str(year_from), str(year_to))

        def papers_from(rows: list[str]) -> list[Paper]:
            return [self._paper_from_arxiv(ET.fromstring(d)) for d in rows]

        raw_entries: list[str] = []
        start = 0
        cached = self._cached("arxiv_search", *cache_args)
        if isinstance(cached, dict) and "items" in cached:
            raw_entries, complete, next_page, _error = self._unwrap(cached)
            papers = papers_from(raw_entries)
            if complete:
                return self._record_retrieval(
                    self._retrieval_result(
                        papers[:cap], self._paging_meta(complete=True, next_page=next_page,
                                                        cache_hit=True),
                        started=started, baseline=baseline, pages=0),
                )
            if len(papers) >= cap:
                return self._record_retrieval(
                    self._retrieval_result(
                        papers[:cap], self._paging_meta(complete=False, next_page=next_page,
                                                        truncated=True, cache_hit=True),
                        started=started, baseline=baseline, pages=0),
                )
            start = int(next_page)

        expression = query if re.search(r"\b(?:all|ti|au|abs|cat|id):", query) else f"all:({query})"
        expression += f" AND submittedDate:[{year_from}01010000 TO {year_to}12312359]"
        papers = papers_from(raw_entries)
        complete = False
        truncated = False
        error = None
        reason = None
        pages = 0
        deadline = time.monotonic() + self.TOTAL_TIMEOUT_SECONDS
        while len(papers) < cap:
            guard = self._paging_guard(pages, deadline)
            if guard is not None:
                reason, error = guard
                break
            page_size = min(100, cap)
            try:
                if pages:
                    time.sleep(3)
                pages += 1
                resp = self._transport_request(
                    "GET",
                    f"{ARXIV_BASE}/query",
                    params={"search_query": expression, "start": start, "max_results": page_size,
                            "sortBy": "relevance", "sortOrder": "descending"},
                    timeout=30,
                )
                raise_for_data(resp, "arxiv", "search")
                root = ET.fromstring(resp.text)
                entries = root.findall("{http://www.w3.org/2005/Atom}entry")
                reported = self._total_results(root)
            except Exception as exc:
                log_source_failure(exc, "arxiv", "search")
                reason = retrieval_status_for_exception(exc).value
                error = f"{type(exc).__name__}: {exc}"[:200]
                break
            for entry in entries:
                paper = self._paper_from_arxiv(entry)
                if paper.year is not None and year_from <= paper.year <= year_to:
                    raw_entries.append(ET.tostring(entry, encoding="unicode"))
                    papers.append(paper)
            start += len(entries)
            if not entries:
                complete = True
                break
            if len(entries) < page_size:
                complete = True
                break
            if isinstance(reported, int) and reported >= 0 and start >= reported:
                complete = True
                break
            if len(papers) >= cap:
                truncated = True
                break

        meta = self._paging_meta(
            complete=complete, next_page=start, error=error, truncated=truncated,
            reason=reason, pages=pages,
        )
        self._cache_set(
            self._envelope(raw_entries, complete=complete, next_page=start,
                           error=error, truncated=truncated),
            "arxiv_search", *cache_args,
        )
        return self._record_retrieval(
            self._retrieval_result(papers[:cap], meta, started=started,
                                   baseline=baseline, pages=pages),
            meta,
        )

    def get_paper(self, paper_id: str) -> Paper | None:
        import urllib.parse
        from xml.etree import ElementTree as ET

        cached = self._cached("arxiv_paper", paper_id)
        if cached is not None:
            return self._paper_from_arxiv(ET.fromstring(cached))

        encoded_id = urllib.parse.quote(paper_id)
        url = f"{ARXIV_BASE}/query?id_list={encoded_id}&max_results=1"
        try:
            resp = self._transport_request("GET", url, timeout=30)
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

    def _get_citations_result(self, paper_id: str, limit: int = 100) -> RetrievalResult:
        return self._unsupported_result("citations")

    def _get_references_result(self, paper_id: str, limit: int = 100) -> RetrievalResult:
        return self._unsupported_result("references")

    def resolve_doi(self, doi: str) -> Paper | None:
        return None  # arXiv doesn't do DOI resolution


# ---------------------------------------------------------------------------
# Impact Factor Lookup
# ---------------------------------------------------------------------------


class ImpactFactorLookup:
    """Journal metric lookup built on the OpenAlex ``/sources`` endpoint.

    Despite the historical name, this does **not** retrieve Journal Impact
    Factors: OpenAlex publishes no such metric. What it returns is an internal
    *Citation Proxy*, and :meth:`citation_proxy` is the API to use when the
    value is shown to a reader because it carries the source, year, formula and
    verification state alongside the number. The class name is kept so existing
    imports do not break; the label a user sees is "Citation Proxy".

    Caches results locally to minimise API calls.
    """

    def __init__(self, cache: Cache | None = None):
        self._cache = cache or Cache()
        # Wrapped like every other provider client so this lookup is accounted
        # for in the shared HTTP budget instead of being invisible to it.
        self._session = CountingSession(requests.Session(), "openalex_metric")
        self._session.headers.update(
            {"User-Agent": "LEExtractor/0.2"}
        )
        self._if_cache: dict[str, float | None] = {}

    def get_if(self, venue: str, issn: str = "") -> float | None:
        """Return a **Citation Proxy** for a journal, or ``None``.

        This is *not* a Journal Impact Factor and must never be labelled as
        one. Two different quantities live behind this number:

        * a value from the vendored table in this module, which has no
          machine-checkable provenance and is therefore reported as
          ``verified=False`` — treat it as an unverified number until someone
          checks it against the publisher's own metric page;
        * a value derived on the fly from OpenAlex ``cited_by_count`` /
          ``works_count`` / ``h_index``, whose formula is stated in
          :meth:`citation_proxy`.

        Use :meth:`citation_proxy` when the value is shown to a reader: it
        carries the source, year, formula and verification state. ``get_if``
        is kept as the numeric accessor for callers that only rank or sort.
        """
        key = venue.lower().strip() if venue else issn
        if not key:
            return None
        if key in self._if_cache:
            return self._if_cache[key]

        # Vendored table of commonly seen journals. NOT an official metric
        # feed: the values predate this release and have not been re-verified.
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
                    src = results[0]                    # OpenAlex does not expose a Journal Impact Factor. The
                    # formula below is an internal proxy, documented in
                    # citation_proxy() so a reader can judge it.
                    cited = src.get("cited_by_count", 0)
                    works = src.get("works_count", 1)
                    h_index = src.get("summary_stats", {}).get("h_index", 0)
                    est = round(cited / max(works, 1) * 0.15, 2) if works else None
                    if h_index and est:
                        est = round(est * 0.7 + (h_index / 100) * 0.3, 2)
                    if self._cache is not None:
                        self._cache.set(est, "if_lookup", key)
                    self._if_cache[key] = est
                    return est
        except Exception as e:
            # Impact factor is a display-only nicety, so a failure here must
            # not pollute diagnostics with noise that hides real problems.
            # It is logged at debug rather than silently dropped.
            logger.debug("Citation proxy lookup failed for %s: %s", key, e)

        self._if_cache[key] = None
        return None

    #: Formula strings shown next to the number, so the value is auditable.
    PROXY_FORMULA_OPENALEX = (
        "0.15 * cited_by_count / works_count, blended 0.7/0.3 with "
        "h_index / 100 (OpenAlex /sources)"
    )
    PROXY_FORMULA_TABLE = "vendored table in litsearch/sources.py; not re-verified"

    def citation_proxy(self, venue: str, issn: str = "") -> dict:
        """Structured Citation Proxy with source, year, formula and verification.

        Callers that display a journal metric must use this and render
        ``display`` — never a bare number presented as an Impact Factor.
        """
        key = (venue or "").lower().strip()
        value = self.get_if(venue, issn)
        from_table = key in _HARDCODED_IF
        if value is None:
            return {
                "value": None,
                "kind": "citation_proxy",
                "source": "not available",
                "year": None,
                "formula": None,
                "verified": False,
                "needs_source_verification": False,
                "display": "Citation Proxy: N/A",
                "note": (
                    "No proxy is available for this venue. This is not evidence "
                    "that the journal is unimportant or low quality."
                ),
            }
        source = "litsearch vendored journal table" if from_table else "OpenAlex /sources"
        formula = self.PROXY_FORMULA_TABLE if from_table else self.PROXY_FORMULA_OPENALEX
        return {
            "value": value,
            "kind": "citation_proxy",
            "source": source,
            "year": _HARDCODED_IF_YEAR if from_table else None,
            "formula": formula,
            # Neither branch is an official metric, so nothing here may be
            # presented as a current Journal Impact Factor.
            "verified": False,
            "needs_source_verification": from_table,
            "display": (
                f"Citation Proxy: {value:.1f}"
                + (" (unverified table value)" if from_table else " (internal proxy)")
            ),
            "note": (
                "内部 Citation Proxy，不是官方 JCR Impact Factor，也不得作为"
                "官方指标展示；如需官方数值请核对来源年份与公式。 "
                "Internal citation proxy, NOT an official Journal Impact Factor."
            ),
        }

    def get_if_display(self, venue: str, issn: str = "") -> str:
        """Display string for the Citation Proxy.

        Renamed output: v0.9.0 printed ``IF: 50.5`` for a vendored table value
        and ``IF: ~8.3 (est.)`` for a derived one, which invited readers to
        treat both as Journal Impact Factors. Neither is one, so the label now
        says what the number actually is.
        """
        return self.citation_proxy(venue, issn)["display"]


# Vendored journal table behind the internal Citation Proxy.
#
# These numbers are NOT an official Journal Impact Factor feed and they carry no
# source citation, so every value taken from here is reported as
# ``verified=False`` / ``needs_source_verification=True``. They were compiled by
# hand for display convenience and have not been re-checked against the
# publisher's own metric pages since. Do not present them as current official
# metrics, and do not use them to rank venues in a report.
_HARDCODED_IF_YEAR = 2023
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
        #: Per-provider RetrievalResults of the most recent
        #: :meth:`search_all_sources_result` call, in provider order.
        self.last_retrieval_results: list[RetrievalResult] = []

    @property
    def cache(self) -> Cache:
        return self._cache

    # -- cancellation ---------------------------------------------------

    def set_cancel_check(self, predicate) -> None:
        """Install a ``() -> bool`` cancel hook on every provider."""
        for source in (self.s2, self.oa, self.cr, self.arxiv):
            source.set_cancel_check(predicate)

    def is_canceled(self) -> bool:
        """True if any provider reports cancellation."""
        return any(
            source.is_canceled() for source in (self.s2, self.oa, self.cr, self.arxiv)
        )

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
                arxiv_papers = self.arxiv.search_papers(query, limit=max(1, limit // 3), year_from=year_from, year_to=year_to)
                papers.extend(arxiv_papers)
            except Exception as e:
                log_source_failure(e, "arxiv", "manager.search")
        return papers

    def _provider_plan(self, query: str, limit: int, query_plan: dict | None):
        """``(source, size, query)`` triples shared by both search entry points.

        ``query_plan`` (from :mod:`litsearch.intent`) supplies a query written in
        each provider's own syntax. Without one every provider receives the same
        bare string, which is what most reviews do and also why they under-retrieve
        on arXiv. The strings actually sent are recorded in the manifest, because
        PRISMA 2020 asks for the exact search per database.
        """
        plan = []
        for source, size in ((self.s2, limit), (self.oa, max(1, limit // 2)),
                             (self.arxiv, max(1, limit // 4))):
            source_query = query
            entry = (query_plan or {}).get(source.name) or {}
            candidate = str(entry.get("query") or "").strip()
            if candidate:
                source_query = candidate
            plan.append((source, size, source_query))
        return plan

    def _search_manifest(self, query, year_from, year_to, limit, provider_counts,
                         per_source_queries, query_plan, raw_count,
                         results: list[RetrievalResult] | None = None) -> dict:
        manifest = {
            "query": query, "year_from": year_from, "year_to": year_to,
            "requested_limit": limit, "provider_counts": provider_counts,
            "raw_count": raw_count, "crossref_role": "metadata_resolution",
            "per_source_queries": per_source_queries,
            "query_plan": query_plan,
            "diagnostics": [{"source": e.source, "kind": e.kind.value, "message": e.message} for e in get_diagnostics().events],
        }
        if results is not None:
            # Additive: the legacy list-only path has no results to describe,
            # and consumers that read only the old keys keep working.
            manifest["provider_results"] = [r.to_dict() for r in results]
        return manifest

    def search_all_sources(
        self, query: str, limit: int = 200, year_from: int = 1900, year_to: int | None = None,
        query_plan: dict | None = None,
    ) -> list[Paper]:
        """Return observations from each provider; merge metadata downstream.

        Legacy list-shaped entry point, kept for callers that do not need
        completeness. Prefer :meth:`search_all_sources_result`: an empty list
        here cannot say whether a provider failed or genuinely had nothing.
        """
        year_to = resolve_year_to(year_to)
        if limit <= 0 or not query.strip():
            return []
        all_papers: list[Paper] = []
        provider_counts = {}
        per_source_queries: dict[str, str] = {}
        for source, size, source_query in self._provider_plan(query, limit, query_plan):
            per_source_queries[source.name] = source_query
            try:
                papers = source.search_papers(source_query, limit=size, year_from=year_from, year_to=year_to)
            except Exception as e:
                log_source_failure(e, source.name, "manager.search_all")
                papers = []
            provider_counts[source.name] = len(papers)
            for paper in papers:
                paper.discovery_traces.append(DiscoveryTrace(method="keyword", provider=source.name, query=source_query))
            all_papers.extend(papers)
        self.last_search_manifest = self._search_manifest(
            query, year_from, year_to, limit, provider_counts, per_source_queries,
            query_plan, len(all_papers),
        )
        return all_papers

    def search_all_sources_result(
        self, query: str, limit: int = 200, year_from: int = 1900, year_to: int | None = None,
        query_plan: dict | None = None,
    ) -> list[RetrievalResult]:
        """One :class:`RetrievalResult` per provider, in provider order.

        This is the entry point a workflow should use: ``[r for r in results
        for r in r.papers]`` replaces the old list, and ``r.complete`` /
        ``r.status`` / ``r.failed`` say whether that list may be read as
        coverage. A provider that raises is converted into an explicit
        ``PROVIDER_ERROR`` result rather than being flattened into "no papers".

        Returns ``[]`` only when nothing was asked of the providers (a
        non-positive limit or a blank query); those calls return one
        ``SUCCESS_EMPTY`` result per provider instead, so an empty return value
        never has to be guessed at.
        """
        year_to = resolve_year_to(year_to)
        provider_counts: dict[str, int] = {}
        per_source_queries: dict[str, str] = {}
        if limit <= 0 or not query.strip():
            results = []
            for source, _size, source_query in self._provider_plan(query, 1, query_plan):
                provider_counts[source.name] = 0
                per_source_queries[source.name] = source_query
                results.append(RetrievalResult(
                    papers=[], provider=source.name,
                    status=RetrievalStatus.SUCCESS_EMPTY, complete=True,
                ))
            self.last_retrieval_results = results
            self.last_search_manifest = self._search_manifest(
                query, year_from, year_to, limit, provider_counts, per_source_queries,
                query_plan, 0, results=results,
            )
            return results

        results: list[RetrievalResult] = []
        all_papers: list[Paper] = []
        for source, size, source_query in self._provider_plan(query, limit, query_plan):
            per_source_queries[source.name] = source_query
            try:
                result = source._search_papers_result(
                    source_query, limit=size, year_from=year_from, year_to=year_to
                )
            except Exception as e:
                log_source_failure(e, source.name, "manager.search_all")
                result = RetrievalResult(
                    papers=[], provider=source.name,
                    status=retrieval_status_for_exception(e), complete=False,
                    error=f"{type(e).__name__}: {e}"[:200],
                )
            for paper in result.papers:
                paper.discovery_traces.append(
                    DiscoveryTrace(method="keyword", provider=source.name, query=source_query)
                )
            provider_counts[source.name] = len(result.papers)
            all_papers.extend(result.papers)
            results.append(result)
        self.last_retrieval_results = results
        self.last_search_manifest = self._search_manifest(
            query, year_from, year_to, limit, provider_counts, per_source_queries,
            query_plan, len(all_papers), results=results,
        )
        return results

    def _routes(self, paper: str | Paper):
        """Only send an ID to providers whose identifier contract accepts it."""
        value = paper.id if isinstance(paper, Paper) else paper
        ids = paper.identifiers if isinstance(paper, Paper) else PaperIdentifiers()
        doi = normalize_doi((paper.doi if isinstance(paper, Paper) else "") or ids.doi or value)
        oa = normalize_openalex(ids.openalex_id or value.removeprefix("openalex:"))
        ax = normalize_arxiv(ids.arxiv_id or value)
        s2 = ids.semantic_scholar_id
        if value.lower().startswith("s2:"):
            s2 = value[3:]
        if not s2 and not (doi or oa or ax):
            s2 = value
        routes = []
        if s2 or doi or ax:
            routes.append((self.s2, s2 or doi or f"ARXIV:{ax}"))
        if oa or doi:
            routes.append((self.oa, oa or doi))
        if doi:
            routes.append((self.cr, doi))
        if ax:
            routes.append((self.arxiv, ax))
        return routes

    def get_paper(self, paper_id: str | Paper) -> Paper | None:
        for source, pid in self._routes(paper_id):
            try:
                paper = source.get_paper(pid)
                if paper is not None:
                    return paper
            except Exception as e:
                log_source_failure(e, source.name, "manager.get_paper")
        return None

    @staticmethod
    def _merge_request_stats(results: list[RetrievalResult]) -> RequestStats:
        stats = RequestStats()
        for result in results:
            current = result.request_stats
            stats.requests += current.requests
            stats.retries += current.retries
            stats.cache_hits += current.cache_hits
            stats.rate_limited += current.rate_limited
            stats.errors += current.errors
            stats.elapsed_seconds += current.elapsed_seconds
            stats.pages += current.pages
        stats.elapsed_seconds = round(stats.elapsed_seconds, 3)
        return stats

    @staticmethod
    def _dedupe_result_papers(results: list[RetrievalResult]) -> list[Paper]:
        seen: set[str] = set()
        papers: list[Paper] = []
        for result in results:
            for paper in result.papers:
                key = paper.canonical_id
                if key in seen:
                    continue
                seen.add(key)
                papers.append(paper)
        return papers

    def _get_related_result(
        self, paper: str | Paper, method: str, limit: int
    ) -> RetrievalResult:
        """Resolve one citations/references lookup without collapsing failures to ``[]``.

        S2/OpenAlex are alternative routes for the same logical relation. A
        complete non-empty result wins immediately. Genuine empties are only
        trusted when no attempted route failed. Partial evidence is preserved,
        but the aggregate remains ``PARTIAL`` so downstream expansion can queue
        the seed for retry instead of declaring saturation.
        """
        attempted: list[RetrievalResult] = []
        result_method = f"_{method}_result"
        for source, pid in self._routes(paper):
            if source not in (self.s2, self.oa):
                continue
            try:
                result = getattr(source, result_method)(pid, limit)
            except Exception as e:
                log_source_failure(e, source.name, f"manager.{method}")
                result = RetrievalResult(
                    papers=[], provider=source.name,
                    status=retrieval_status_for_exception(e), complete=False,
                    error=f"{type(e).__name__}: {e}"[:200],
                )
            attempted.append(result)
            if result.status in {RetrievalStatus.SUCCESS, RetrievalStatus.TRUNCATED} and result.papers:
                return result

        if not attempted:
            return RetrievalResult(
                papers=[], provider="source_manager",
                status=RetrievalStatus.PROVIDER_ERROR, complete=False,
                error=f"no provider route supports {method}",
            )

        partial_papers = self._dedupe_result_papers(attempted)
        incomplete = [r for r in attempted if not r.ok]
        if incomplete:
            # Preserve every paper we did receive. Prefer the most actionable
            # ending when several routes failed.
            priority = [
                RetrievalStatus.CANCELED,
                RetrievalStatus.BUDGET_EXHAUSTED,
                RetrievalStatus.RATE_LIMITED,
                RetrievalStatus.TIMEOUT,
                RetrievalStatus.PARTIAL,
                RetrievalStatus.PROVIDER_ERROR,
            ]
            status = next(
                (candidate for candidate in priority
                 if any(r.status is candidate for r in incomplete)),
                RetrievalStatus.PROVIDER_ERROR,
            )
            errors = [r.error or f"{r.provider}: {r.status.value}" for r in incomplete]
            return RetrievalResult(
                papers=partial_papers, provider="source_manager",
                status=status if not partial_papers else RetrievalStatus.PARTIAL,
                complete=False, error="; ".join(errors)[:500],
                request_stats=self._merge_request_stats(attempted),
            )

        # Every attempted route answered normally and none returned papers.
        if all(r.genuine_empty for r in attempted):
            return RetrievalResult(
                papers=[], provider="source_manager",
                status=RetrievalStatus.SUCCESS_EMPTY, complete=True,
                request_stats=self._merge_request_stats(attempted),
            )

        # Only intentional truncations with zero rows remain. They are usable
        # prefixes, but zero rows at a limit is not proof of provider exhaustion.
        return RetrievalResult(
            papers=partial_papers, provider="source_manager",
            status=RetrievalStatus.TRUNCATED, complete=False,
            request_stats=self._merge_request_stats(attempted), truncated=True,
        )

    def get_citations_result(self, paper_id: str | Paper, limit: int = 100) -> RetrievalResult:
        return self._get_related_result(paper_id, "get_citations", limit)

    def get_references_result(self, paper_id: str | Paper, limit: int = 100) -> RetrievalResult:
        return self._get_related_result(paper_id, "get_references", limit)

    def get_citations(self, paper_id: str | Paper, limit: int = 100) -> list[Paper]:
        return self.get_citations_result(paper_id, limit).papers

    def get_references(self, paper_id: str | Paper, limit: int = 100) -> list[Paper]:
        return self.get_references_result(paper_id, limit).papers

    def resolve_doi(self, doi: str) -> Paper | None:
        return self.get_paper(normalize_doi(doi)) if normalize_doi(doi) else None
