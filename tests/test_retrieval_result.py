"""Task 4 — every provider retrieval answers with a :class:`RetrievalResult`.

REPRODUCED DEFECT (v0.9.2 tree, before ``litsearch/retrieval.py`` existed)

Every provider answered with ``list[Paper]`` and completeness travelled through
side state (``last_retrieval_meta``, diagnostics, cache envelopes). A consumer
holding ``[]`` therefore could not tell these two apart::

    transport that always raises  -> search_papers() == []
    transport that answers {"results": []} -> search_papers() == []

The first is a failed retrieval that must never be read as "the literature has
nothing"; the second is a genuine zero-result answer.  Both were the same
value, and OpenAlex's paged path already built the completeness envelope
internally only to throw it away at the boundary.

The cases below are the acceptance criteria of contract §1: each failure mode
reaches the consumer as its own status, a partial result keeps its papers and
is never cached as a complete success, and a genuine empty answer stays
distinguishable from a failure.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

from litsearch.cache import Cache
from litsearch.config import current_year
from litsearch.models import Paper
from litsearch.retrieval import (
    STATUS_TO_STOP_REASON,
    RetrievalResult,
    RetrievalStatus,
    stop_reason_for,
)
from litsearch.sources import (
    ArxivSource,
    CrossrefSource,
    OpenAlexSource,
    SemanticScholarSource,
    SourceManager,
)
from litsearch.stop_reasons import (
    COMPLETE_REASONS,
    INCOMPLETE_REASONS,
    CountingSession,
    StopReason,
    reset_http_budget,
)

SOURCES_PY = pathlib.Path(__file__).resolve().parent.parent / "litsearch" / "sources.py"

ALL_PROVIDERS = ["semantic_scholar", "openalex", "arxiv", "crossref"]
PROVIDER_CLASSES = {
    "semantic_scholar": SemanticScholarSource,
    "openalex": OpenAlexSource,
    "arxiv": ArxivSource,
    "crossref": CrossrefSource,
}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """No test may actually sleep through a backoff."""
    monkeypatch.setattr("litsearch.sources.time.sleep", lambda _seconds: None)


# ---------------------------------------------------------------------------
# Fake transport with a per-provider payload shape
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, payload=None, status=200, text="", headers=None, url="https://api.example.invalid/x"):
        self._payload = {} if payload is None else payload
        self._text = text
        self.status_code = status
        self.headers = headers or {}
        self.url = url
        self.closed = False

    def json(self):
        return self._payload

    @property
    def text(self):
        return self._text

    def close(self):
        self.closed = True


class FakeTransport:
    """A session stand-in: records every request and serves one provider."""

    def __init__(self, responder):
        self._responder = responder
        self.headers = {}
        self.calls: list[dict] = []

    def get(self, url, params=None, timeout=None, **kwargs):
        params = dict(params or {})
        self.calls.append(params)
        return self._responder(url, params)


def _offset_window(params, offset_key, size_key, default_size, offset_default=0):
    size = int(params.get(size_key, default_size))
    offset = int(params.get(offset_key, offset_default))
    return offset, size


def records_for(kind: str, count: int) -> list:
    if kind == "semantic_scholar":
        return [
            {"paperId": f"p{i}", "title": f"Paper {i}",
             "externalIds": {"DOI": f"10.9/{i}"}, "year": 2020}
            for i in range(count)
        ]
    if kind == "openalex":
        return [
            {"id": f"https://openalex.org/W{i}", "doi": f"https://doi.org/10.9/{i}",
             "title": f"Paper {i}", "publication_year": 2020, "cited_by_count": 1,
             "referenced_works": [], "authorships": [], "topics": [],
             "primary_location": None}
            for i in range(count)
        ]
    if kind == "crossref":
        return [
            {"DOI": f"10.9/{i}", "title": [f"Paper {i}"],
             "issued": {"date-parts": [[2020]]}, "container-title": ["J"]}
            for i in range(count)
        ]
    raise AssertionError(f"no record shape for {kind}")


def arxiv_feed(start: int, size: int, total: int | None = None) -> str:
    entries = "".join(
        '<entry xmlns="http://www.w3.org/2005/Atom">'
        f"<id>http://arxiv.org/abs/2201.{i:05}</id>"
        "<title>Plant traits</title><published>2022-01-01T00:00:00Z</published>"
        "<summary>Plant images</summary></entry>"
        for i in range(start, start + size)
    )
    count = (
        '<opensearch:totalResults xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'
        f"{total}</opensearch:totalResults>"
        if total is not None else ""
    )
    return f'<feed xmlns="http://www.w3.org/2005/Atom">{count}{entries}</feed>'


def responder_for(kind: str, count: int = 0, total: int | None = None,
                  fail_at_offset: int | None = None, status: int | None = None,
                  fail: bool = False):
    """One provider's paging behaviour, without a network."""

    def respond(url, params):
        if fail:
            raise ConnectionError(f"{kind}: simulated transport failure")
        if status is not None:
            return FakeResponse({}, status=status)
        if kind == "semantic_scholar":
            offset, size = _offset_window(params, "offset", "limit", 25)
            if fail_at_offset is not None and offset >= fail_at_offset:
                raise ConnectionError("semantic_scholar: paged request failed")
            window = records_for(kind, count)[offset:offset + size]
            payload = {"data": window, "offset": offset}
            if total is not None:
                payload["total"] = total
            return FakeResponse(payload)
        if kind == "openalex":
            offset, size = _offset_window(params, "page", "per_page", 25, offset_default=1)
            offset = (offset - 1) * size
            if fail_at_offset is not None and offset >= fail_at_offset:
                raise ConnectionError("openalex: paged request failed")
            window = records_for(kind, count)[offset:offset + size]
            payload = {"results": window, "meta": {"count": count if total is None else total}}
            return FakeResponse(payload)
        if kind == "arxiv":
            start, size = _offset_window(params, "start", "max_results", 100)
            if fail_at_offset is not None and start >= fail_at_offset:
                raise ConnectionError("arxiv: paged request failed")
            window = records_for("semantic_scholar", count)[start:start + size]
            return FakeResponse(text=arxiv_feed(start, len(window), total))
        if kind == "crossref":
            offset, size = _offset_window(params, "offset", "rows", 100)
            if fail_at_offset is not None and offset >= fail_at_offset:
                raise ConnectionError("crossref: paged request failed")
            window = records_for(kind, count)[offset:offset + size]
            message = {"items": window, "total-results": count if total is None else total}
            return FakeResponse({"message": message})
        raise AssertionError(f"no responder for {kind}")

    return respond


def build(kind: str, tmp_path, responder, name: str = "h.db"):
    source = PROVIDER_CLASSES[kind](cache=Cache(str(tmp_path / f"{kind}_{name}")))
    source._session = CountingSession(FakeTransport(responder), kind)
    return source


# ---------------------------------------------------------------------------
# REPRODUCTION — the empty list that meant two different things
# ---------------------------------------------------------------------------


def test_reproduced_empty_list_cannot_separate_failure_from_a_genuine_zero(tmp_path):
    """The defect: ``[]`` was the same value for "failed" and "no results"."""
    failing = build("openalex", tmp_path, responder_for("openalex", fail=True), "f.db")
    empty = build("openalex", tmp_path, responder_for("openalex", count=0), "e.db")

    # Legacy public API — identical values, no way to tell them apart.
    assert failing.search_papers("wheat", limit=5) == []
    assert empty.search_papers("wheat", limit=5) == []

    # Result API — the two are now different objects with different verdicts.
    bad = failing._search_papers_result("wheat", limit=5)
    good = empty._search_papers_result("wheat", limit=5)
    assert bad.papers == good.papers == []
    assert bad.status is RetrievalStatus.PROVIDER_ERROR
    assert bad.failed is True and bad.ok is False and bad.genuine_empty is False
    assert good.status is RetrievalStatus.SUCCESS_EMPTY
    assert good.ok is True and good.failed is False and good.genuine_empty is True


# ---------------------------------------------------------------------------
# §1 invariants
# ---------------------------------------------------------------------------


def test_genuine_empty_invariant():
    result = RetrievalResult(papers=[], complete=True, error=None)
    assert result.status is RetrievalStatus.SUCCESS_EMPTY
    assert result.genuine_empty is True
    assert result.failed is False and result.ok is True
    assert stop_reason_for(result.status) is StopReason.NO_NEW_RESULTS


def test_incomplete_empty_answer_with_an_error_is_never_success_empty():
    result = RetrievalResult(papers=[], complete=False, error="boom")
    assert result.status is not RetrievalStatus.SUCCESS_EMPTY
    assert result.genuine_empty is False
    assert result.failed is True and result.ok is False
    assert stop_reason_for(result.status) in INCOMPLETE_REASONS


def test_partial_result_keeps_its_papers_and_marks_itself_incomplete():
    papers = [Paper(id=f"10.9/{i}", title="plant") for i in range(3)]
    result = RetrievalResult(papers=papers, complete=False)
    assert result.status is RetrievalStatus.PARTIAL
    assert result.complete is False
    assert [p.id for p in result.papers] == ["10.9/0", "10.9/1", "10.9/2"]
    assert result.genuine_empty is False
    assert stop_reason_for(result.status) in INCOMPLETE_REASONS


@pytest.mark.parametrize("status", [
    RetrievalStatus.PARTIAL,
    RetrievalStatus.RATE_LIMITED,
    RetrievalStatus.TIMEOUT,
    RetrievalStatus.CANCELED,
    RetrievalStatus.BUDGET_EXHAUSTED,
    RetrievalStatus.PROVIDER_ERROR,
])
def test_incomplete_statuses_never_claim_no_new_results(status):
    result = RetrievalResult(status=status, complete=False, error="why")
    assert result.genuine_empty is False
    assert stop_reason_for(result.status) in INCOMPLETE_REASONS
    assert result.complete is False


def test_to_dict_is_json_serialisable_and_carries_the_verdict():
    result = RetrievalResult(
        papers=[Paper(id="10.9/a", title="plant")],
        provider="openalex",
        status=RetrievalStatus.RATE_LIMITED,
        complete=False,
        error="429 Too Many Requests",
        next_cursor="3",
    )
    payload = json.loads(json.dumps(result.to_dict()))
    assert payload["provider"] == "openalex"
    assert payload["status"] == "rate_limited"
    assert payload["ok"] is False and payload["failed"] is True
    assert payload["genuine_empty"] is False and payload["complete"] is False
    assert payload["paper_count"] == 1 and payload["paper_ids"] == ["10.9/a"]
    assert payload["error"] == "429 Too Many Requests"
    assert payload["next_cursor"] == "3"
    assert payload["stop_reason"] == StopReason.API_FAILURE.value
    assert payload["request_stats"]["requests"] == 0


def test_every_status_maps_onto_the_frozen_stop_reason_enum():
    assert set(STATUS_TO_STOP_REASON) == set(RetrievalStatus)
    for status in RetrievalStatus:
        reason = stop_reason_for(status)
        assert isinstance(reason, StopReason)
        if status in (RetrievalStatus.SUCCESS, RetrievalStatus.SUCCESS_EMPTY):
            assert reason in COMPLETE_REASONS, status
        else:
            assert reason in INCOMPLETE_REASONS, status
    # The mapping is also reachable from the wire form (a manifest round-trip).
    assert stop_reason_for("canceled") is StopReason.CANCELED
    assert stop_reason_for("not-a-status") in INCOMPLETE_REASONS


# ---------------------------------------------------------------------------
# Provider coverage: every failure mode arrives as its own status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ALL_PROVIDERS)
def test_a_failed_request_is_a_provider_error_not_an_empty_success(kind, tmp_path):
    source = build(kind, tmp_path, responder_for(kind, fail=True))
    result = source._search_papers_result("plant", limit=5)
    assert result.papers == []
    assert result.status is RetrievalStatus.PROVIDER_ERROR
    assert result.complete is False
    assert result.error, "a failure must carry its reason"
    assert result.failed is True and result.genuine_empty is False
    assert source.last_retrieval_result is result


@pytest.mark.parametrize("kind", ALL_PROVIDERS)
def test_a_normal_zero_result_answer_is_a_genuine_empty(kind, tmp_path):
    source = build(kind, tmp_path, responder_for(kind, count=0))
    result = source._search_papers_result("plant", limit=5)
    assert result.papers == []
    assert result.status is RetrievalStatus.SUCCESS_EMPTY
    assert result.complete is True and result.error is None
    assert result.genuine_empty is True and result.failed is False
    assert result.next_cursor is None


@pytest.mark.parametrize("kind", ALL_PROVIDERS)
def test_rate_limiting_is_its_own_status(kind, tmp_path):
    source = build(kind, tmp_path, responder_for(kind, status=429))
    result = source._search_papers_result("plant", limit=5)
    assert result.status is RetrievalStatus.RATE_LIMITED
    assert result.status is not RetrievalStatus.PROVIDER_ERROR
    assert result.complete is False and result.error
    assert stop_reason_for(result.status) in INCOMPLETE_REASONS


@pytest.mark.parametrize("kind", ALL_PROVIDERS)
def test_a_timeout_is_its_own_status(kind, tmp_path):
    source = build(kind, tmp_path, responder_for(kind, count=250, total=250))
    source.TOTAL_TIMEOUT_SECONDS = 0  # the wall-clock budget is already gone
    result = source._search_papers_result("plant", limit=5)
    assert result.status is RetrievalStatus.TIMEOUT
    assert result.complete is False and result.error


@pytest.mark.parametrize("kind", ALL_PROVIDERS)
def test_a_socket_timeout_exception_is_reported_as_a_timeout(kind, tmp_path):
    def timeout_responder(url, params):
        raise TimeoutError("socket timed out")

    source = build(kind, tmp_path, timeout_responder)
    result = source._search_papers_result("plant", limit=5)
    assert result.status is RetrievalStatus.TIMEOUT
    assert result.status is not RetrievalStatus.PROVIDER_ERROR


@pytest.mark.parametrize("kind", ALL_PROVIDERS)
def test_cancellation_is_its_own_status(kind, tmp_path):
    source = build(kind, tmp_path, responder_for(kind, count=250, total=250))
    source.set_cancel_check(lambda: True)
    result = source._search_papers_result("plant", limit=5)
    assert result.status is RetrievalStatus.CANCELED
    assert result.complete is False
    assert "cancel" in (result.error or "").lower()


@pytest.mark.parametrize("kind", ALL_PROVIDERS)
def test_budget_exhaustion_is_its_own_status(kind, tmp_path):
    source = build(kind, tmp_path, responder_for(kind, count=250, total=250))
    source.set_request_budget(1)
    result = source._search_papers_result("plant", limit=250)
    assert result.status is RetrievalStatus.BUDGET_EXHAUSTED
    assert result.status is not RetrievalStatus.PROVIDER_ERROR
    assert result.complete is False and result.error
    assert len(source._session._session.calls) == 1, "the budget must stop further requests"


@pytest.mark.parametrize("kind", ALL_PROVIDERS)
def test_a_truncated_pagination_is_not_a_provider_failure(kind, tmp_path):
    source = build(kind, tmp_path, responder_for(kind, count=250, total=250))
    result = source._search_papers_result("plant", limit=150)
    assert len(result.papers) == 150
    assert len({p.canonical_id for p in result.papers}) == 150
    assert result.status is RetrievalStatus.TRUNCATED
    assert result.complete is False and result.truncated is True
    assert result.ok is True and result.failed is False
    assert result.genuine_empty is False
    assert stop_reason_for(result.status) is StopReason.TRUNCATED
    assert result.next_cursor, "a truncated prefix must publish where to resume"


@pytest.mark.parametrize("kind", ALL_PROVIDERS)
def test_the_public_list_api_is_still_a_working_thin_wrapper(kind, tmp_path):
    source = build(kind, tmp_path, responder_for(kind, count=3, total=3))
    papers = source.search_papers("plant", limit=3)
    assert isinstance(papers, list) and len(papers) == 3
    assert all(isinstance(p, Paper) for p in papers)
    assert source.last_retrieval_result.papers == papers
    # citations / references keep their public signature too
    assert isinstance(source.get_citations("10.9/0", limit=3), list)
    assert isinstance(source.get_references("10.9/0", limit=3), list)


def test_s2_citations_and_references_report_their_own_result(tmp_path):
    source = build(
        "semantic_scholar", tmp_path,
        lambda url, params: FakeResponse({
            "data": [{"citingPaper": {"paperId": "c1", "title": "citer"}}],
            "offset": 0,
        }),
    )
    citations = source._get_citations_result("10.9/seed", limit=10)
    assert [p.id for p in citations.papers] == ["c1"]
    assert citations.complete is True and citations.status is RetrievalStatus.SUCCESS
    assert citations.provider == "semantic_scholar"


def test_openalex_citations_and_references_report_their_own_result(tmp_path):
    source = build("openalex", tmp_path, responder_for("openalex", count=2, total=2))
    source._resolve_oa_id = lambda _pid: "W1"
    citations = source._get_citations_result("10.9/seed", limit=10)
    assert len(citations.papers) == 2
    assert citations.complete is True and citations.status is RetrievalStatus.SUCCESS

    source.get_paper = lambda _pid: Paper(id="W1", title="seed", reference_ids=["W2", "W3"])
    references = source._get_references_result("W1", limit=10)
    assert len(references.papers) == 2
    assert references.complete is True


@pytest.mark.parametrize("kind", ["arxiv", "crossref"])
def test_an_unsupported_capability_is_not_a_false_genuine_empty(kind, tmp_path):
    """arXiv/Crossref have no citation endpoints: that is not "zero citations"."""
    source = build(kind, tmp_path, responder_for(kind, count=0))
    for result in (source._get_citations_result("10.9/seed", limit=10),
                   source._get_references_result("10.9/seed", limit=10)):
        assert result.papers == []
        assert result.status is RetrievalStatus.PROVIDER_ERROR
        assert result.genuine_empty is False
        assert "not supported" in (result.error or "")


def test_request_stats_count_real_requests_and_pages(tmp_path):
    reset_http_budget()
    source = build("openalex", tmp_path, responder_for("openalex", count=250, total=250))
    result = source._search_papers_result("plant", limit=150)
    assert result.request_stats.requests == 2, "two pages were fetched"
    assert result.request_stats.pages == 2
    assert result.request_stats.cache_hits == 0
    assert result.request_stats.elapsed_seconds >= 0


def test_arxiv_reported_total_proves_the_page_sequence_is_complete(tmp_path):
    """A filled page plus ``totalResults`` is a finished search, not a partial."""
    source = build("arxiv", tmp_path, responder_for("arxiv", count=2, total=2))
    result = source._search_papers_result("plant", limit=2, year_from=2020, year_to=2024)
    assert len(result.papers) == 2
    assert result.complete is True and result.status is RetrievalStatus.SUCCESS


def test_arxiv_full_page_without_a_reported_total_is_not_called_complete(tmp_path):
    """Without ``totalResults`` a full page cannot prove there is no next one."""
    source = build("arxiv", tmp_path, responder_for("arxiv", count=2))
    result = source._search_papers_result("plant", limit=2, year_from=2020, year_to=2024)
    assert len(result.papers) == 2
    assert result.status is RetrievalStatus.TRUNCATED
    assert result.complete is False and result.truncated is True
    assert result.ok is True and result.failed is False
    assert result.next_cursor == "2"


# ---------------------------------------------------------------------------
# Cache completeness
# ---------------------------------------------------------------------------


def test_s2_partial_pagination_is_cached_incomplete_and_resumed(tmp_path):
    broken = build("semantic_scholar", tmp_path,
                   responder_for("semantic_scholar", count=250, total=250, fail_at_offset=100),
                   name="s2.db")
    first = broken._search_papers_result("plant", limit=200)
    assert len(first.papers) == 100
    assert first.complete is False and first.status is RetrievalStatus.PROVIDER_ERROR

    row = broken._cache.get(
        "semantic_scholar", SemanticScholarSource.CACHE_PARSER_VERSION,
        "s2_search", "plant", "200", "1900", str(current_year()),
    )
    assert isinstance(row, dict), "a paged S2 result must be cached in an envelope"
    assert row["complete"] is False
    assert row["error"], "the failure must be recorded, not swallowed"

    healthy_transport = FakeTransport(
        responder_for("semantic_scholar", count=250, total=250))
    healthy = SemanticScholarSource(cache=Cache(str(tmp_path / "semantic_scholar_s2.db")))
    healthy._session = CountingSession(healthy_transport, "semantic_scholar")
    resumed = healthy._search_papers_result("plant", limit=200)
    assert len(resumed.papers) == 200
    assert len({p.canonical_id for p in resumed.papers}) == 200
    assert healthy_transport.calls[0]["offset"] == 100, "resume must not restart the sequence"


def test_s2_complete_cache_hit_is_reported_as_complete(tmp_path):
    first = build("semantic_scholar", tmp_path,
                  responder_for("semantic_scholar", count=5, total=5), name="hit.db")
    original = first._search_papers_result("plant", limit=5)
    assert original.complete is True and original.status is RetrievalStatus.SUCCESS

    transport = FakeTransport(responder_for("semantic_scholar", count=5, total=5))
    second = SemanticScholarSource(cache=Cache(str(tmp_path / "semantic_scholar_hit.db")))
    second._session = CountingSession(transport, "semantic_scholar")
    cached = second._search_papers_result("plant", limit=5)
    assert transport.calls == [], "a complete cache hit must not re-request"
    assert cached.complete is True
    assert cached.status is RetrievalStatus.SUCCESS
    assert [p.id for p in cached.papers] == [p.id for p in original.papers]
    assert cached.request_stats.requests == 0
    assert cached.request_stats.cache_hits >= 1


def test_s2_legacy_bare_list_cache_row_is_not_trusted_as_complete(tmp_path):
    cache = Cache(str(tmp_path / "legacy.db"))
    cache.set(
        records_for("semantic_scholar", 2),
        "semantic_scholar", SemanticScholarSource.CACHE_PARSER_VERSION,
        "s2_search", "plant", "5", "1900", str(current_year()),
    )
    source = SemanticScholarSource(cache=cache)
    transport = FakeTransport(responder_for("semantic_scholar", count=250, total=250))
    source._session = CountingSession(transport, "semantic_scholar")
    result = source._search_papers_result("plant", limit=5)
    assert transport.calls, "a bare-list row cannot prove completeness, so refetch"
    assert result.complete is False
    assert len(result.papers) == 5, "the untrusted prefix must not be served as the answer"


@pytest.mark.parametrize("kind", ["openalex", "arxiv", "crossref"])
def test_a_partial_paged_answer_is_never_cached_as_a_complete_success(kind, tmp_path):
    source = build(kind, tmp_path,
                   responder_for(kind, count=250, total=250, fail_at_offset=100))
    result = source._search_papers_result("plant", limit=250)
    assert result.complete is False
    assert result.status is RetrievalStatus.PROVIDER_ERROR

    key = {"openalex": ("oa_search", "plant", "250", "1900", str(current_year())),
           "arxiv": ("arxiv_search", "plant", "250", "1900", str(current_year())),
           "crossref": ("cr_search", "plant", "250", "1900", str(current_year()))}[kind]
    row = source._cache.get(source.name, source.CACHE_PARSER_VERSION, *key)
    assert isinstance(row, dict), "paged results must be cached in a completeness envelope"
    assert row["complete"] is False
    assert row["error"], "the failure must be recorded, not swallowed"


def test_openalex_complete_cache_hit_is_reported_as_complete(tmp_path):
    first = build("openalex", tmp_path, responder_for("openalex", count=120), name="oa.db")
    original = first._search_papers_result("plant", limit=500)
    assert original.complete is True and len(original.papers) == 120

    transport = FakeTransport(responder_for("openalex", count=120))
    second = OpenAlexSource(cache=Cache(str(tmp_path / "openalex_oa.db")))
    second._session = CountingSession(transport, "openalex")
    cached = second._search_papers_result("plant", limit=500)
    assert transport.calls == []
    assert cached.complete is True and cached.status is RetrievalStatus.SUCCESS
    assert cached.request_stats.cache_hits >= 1
    assert len(cached.papers) == 120


# ---------------------------------------------------------------------------
# SourceManager
# ---------------------------------------------------------------------------


class _StubProvider:
    def __init__(self, name, result=None, error=None):
        self.name = name
        self._result = result
        self._error = error
        self.calls: list[tuple] = []

    def _search_papers_result(self, query, limit=50, year_from=1900, year_to=None):
        self.calls.append((query, limit, year_from, year_to))
        if self._error is not None:
            raise self._error
        return self._result


def _manager_with(providers):
    manager = SourceManager.__new__(SourceManager)
    manager.set_search_providers(["semantic_scholar", "openalex", "arxiv"])
    manager.s2, manager.oa, manager.arxiv, manager.cr = providers
    return manager


def test_search_all_sources_result_returns_one_result_per_provider():
    ok = _StubProvider("semantic_scholar", RetrievalResult(
        papers=[Paper(id="10.9/a", title="plant")], provider="semantic_scholar"))
    empty = _StubProvider("openalex", RetrievalResult(
        papers=[], provider="openalex", status=RetrievalStatus.SUCCESS_EMPTY))
    manager = _manager_with([ok, empty, _StubProvider("arxiv", RetrievalResult(
        papers=[Paper(id="10.9/b", title="plant")], provider="arxiv")), None])

    results = manager.search_all_sources_result("plant", limit=8, year_from=2018, year_to=2025)
    assert [r.provider for r in results] == ["semantic_scholar", "openalex", "arxiv"]
    assert [len(r.papers) for r in results] == [1, 0, 1]
    assert results[1].genuine_empty is True
    assert manager.last_search_manifest["provider_counts"] == {
        "semantic_scholar": 1, "openalex": 0, "arxiv": 1}
    assert manager.last_search_manifest["provider_results"][0]["status"] == "success"
    # The discovery trace is still attached, exactly as the legacy path did.
    assert results[0].papers[0].discovery_traces[0].provider == "semantic_scholar"


def test_search_all_sources_result_isolates_a_provider_that_raises():
    boom = _StubProvider("semantic_scholar", error=RuntimeError("provider down"))
    healthy = _StubProvider("openalex", RetrievalResult(
        papers=[Paper(id="10.9/a", title="plant")], provider="openalex"))
    manager = _manager_with([boom, healthy, _StubProvider("arxiv", RetrievalResult(
        papers=[], provider="arxiv", status=RetrievalStatus.SUCCESS_EMPTY)), None])

    results = manager.search_all_sources_result("plant", limit=4, year_from=2020, year_to=2024)
    assert results[0].status is RetrievalStatus.PROVIDER_ERROR
    assert results[0].failed is True
    assert len(results[1].papers) == 1
    assert manager.last_search_manifest["provider_counts"]["semantic_scholar"] == 0


# ---------------------------------------------------------------------------
# Architecture: the information must flow *through* RetrievalResult
# ---------------------------------------------------------------------------


def _provider_class(name: str) -> ast.ClassDef:
    tree = ast.parse(SOURCES_PY.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in sources.py")


def test_providers_implement_the_result_api_and_do_not_redefine_the_list_api():
    """Forbids "internal still returns a list, outer wraps a RetrievalResult"."""
    internal = {"_search_papers_result", "_get_citations_result", "_get_references_result"}
    public = {"search_papers", "get_citations", "get_references"}
    for name in ("SemanticScholarSource", "OpenAlexSource", "ArxivSource", "CrossrefSource"):
        methods = {n.name for n in _provider_class(name).body if isinstance(n, ast.FunctionDef)}
        assert internal <= methods, f"{name} must build the result internally: {internal - methods}"
        assert not (public & methods), (
            f"{name} redefines the list-shaped API ({public & methods}); the public "
            "signature must stay a thin wrapper over the result API"
        )


def test_the_public_base_methods_are_thin_wrappers_over_the_result_api():
    base = _provider_class("BaseSource")
    methods = {n.name: n for n in base.body if isinstance(n, ast.FunctionDef)}
    for public, internal_name in (("search_papers", "_search_papers_result"),
                                  ("get_citations", "_get_citations_result"),
                                  ("get_references", "_get_references_result")):
        node = methods[public]
        returns = [n for n in ast.walk(node) if isinstance(n, ast.Return)]
        assert len(returns) == 1, f"BaseSource.{public} must be a one-line wrapper"
        value = returns[0].value
        assert isinstance(value, ast.Attribute) and value.attr == "papers", (
            f"BaseSource.{public} must return the result's papers, not rebuild them"
        )
        called = {
            n.func.attr for n in ast.walk(value)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        assert internal_name in called, f"BaseSource.{public} must call {internal_name}()"
        assert internal_name in methods, "the internal entry point must be declared"
