"""Regression tests for v0.9.0 spec B: OpenAlex paging, cache completeness, budget.

Baseline evidence (reproduced on v0.8.0 before the fix, with a fake transport
serving 250 fixed records):

* ``limit=201`` returned 201 rows containing only **200 distinct** ids;
  ``limit=250`` returned 250 rows of which only 200 were distinct. Cause: the
  page size was recomputed per page as ``min(200, limit - len(papers))``, so
  page 2 asked for a different window size than page 1 and every later offset
  shifted. ``per_page=200`` was also above the documented maximum of 100
  (https://help.openalex.org/api/paging/, checked 2026-10-08).
* a citation pagination that failed on page 2 cached its 200-row prefix as a
  complete success, and every later call was served those 200 rows from cache
  without issuing a single request.
"""

import pytest

from litsearch.cache import Cache
from litsearch.sources import (
    OA_PAGE_SIZE_LIMIT,
    OpenAlexSource,
    RetrievalCanceled,
)
from litsearch.stop_reasons import (
    CountingSession,
    HttpBudget,
    http_budget_snapshot,
    reset_http_budget,
)

RECORD_COUNT = 250


def record(i):
    return {
        "id": f"https://openalex.org/W{i}",
        "doi": f"https://doi.org/10.9/{i}",
        "title": f"Paper {i}",
        "publication_year": 2020,
        "cited_by_count": 1,
        "referenced_works": [],
        "primary_location": None,
        "topics": [],
        "authorships": [],
    }


RECORDS = [record(i) for i in range(RECORD_COUNT)]


class FakeResponse:
    def __init__(self, payload, status=200, headers=None):
        self._payload = payload
        self.status_code = status
        self.headers = headers or {}
        self.url = "https://api.openalex.org/works"
        self.closed = False

    def json(self):
        return self._payload

    @property
    def text(self):
        return ""

    def close(self):
        self.closed = True


class PagedTransport:
    """Offset-paging OpenAlex stand-in that records every request it serves."""

    def __init__(self, total=RECORD_COUNT, fail_on_pages=(), status_on_pages=None):
        self.total = total
        self.fail_on_pages = set(fail_on_pages)
        self.status_on_pages = status_on_pages or {}
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, timeout=None, **kwargs):
        params = dict(params or {})
        self.calls.append(params)
        page = int(params.get("page", 1))
        size = int(params.get("per_page", 25))
        if page in self.fail_on_pages:
            raise RuntimeError(f"simulated transport failure on page {page}")
        if page in self.status_on_pages:
            return FakeResponse({}, status=self.status_on_pages[page])
        available = RECORDS[:self.total]
        start = (page - 1) * size
        return FakeResponse({
            "results": available[start:start + size],
            "meta": {"count": self.total, "per_page": size},
        })

    @property
    def pages(self):
        return [int(call.get("page", 1)) for call in self.calls]

    @property
    def page_sizes(self):
        return [int(call.get("per_page", 25)) for call in self.calls]


def make_source(tmp_path, transport, name="cache.db"):
    source = OpenAlexSource(cache=Cache(str(tmp_path / name)))
    source._session = CountingSession(transport, "openalex")
    return source


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("litsearch.sources.time.sleep", lambda _seconds: None)


# ---------------------------------------------------------------------------
# B1/B2/B3 — fixed page size, correct total, no duplicates, respects the cap
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("limit", [101, 199, 200, 201, 250, 500])
def test_pagination_returns_unique_records_without_gaps(tmp_path, limit):
    transport = PagedTransport()
    source = make_source(tmp_path, transport)
    papers = source.search_papers("wheat", limit=limit)

    expected = min(limit, RECORD_COUNT)
    ids = [p.canonical_id for p in papers]
    assert len(papers) == expected
    assert len(set(ids)) == expected, f"duplicate records served for limit={limit}"
    # No record is skipped either: the ids are exactly the first N records.
    assert set(ids) == {r["doi"].replace("https://doi.org/", "") for r in RECORDS[:expected]}


def test_page_size_is_fixed_across_the_whole_sequence(tmp_path):
    transport = PagedTransport()
    source = make_source(tmp_path, transport)
    source.search_papers("wheat", limit=250)

    assert len(set(transport.page_sizes)) == 1, (
        f"page size changed mid-sequence: {transport.page_sizes}"
    )
    assert transport.page_sizes[0] <= 100, (
        "per_page must stay within the documented 1-100 range"
    )
    assert transport.pages == sorted(transport.pages)


def test_page_size_never_exceeds_the_documented_cap(tmp_path):
    transport = PagedTransport()
    source = make_source(tmp_path, transport)
    source.search_papers("wheat", limit=500)
    assert max(transport.page_sizes) <= OA_PAGE_SIZE_LIMIT == 100


def test_asking_for_more_than_the_provider_has_stops_at_the_reported_count(tmp_path):
    """The provider's ``meta.count`` ends the sequence; no endless empty pages."""
    transport = PagedTransport(total=120)
    source = make_source(tmp_path, transport)
    papers = source.search_papers("wheat", limit=500)
    assert len(papers) == 120
    assert len({p.canonical_id for p in papers}) == 120
    # Two pages of 100 (the second short at 20) - never a third, empty page.
    assert transport.pages == [1, 2]
    assert source.last_retrieval_meta["available"] == 120
    assert source.last_retrieval_meta["complete"] is True


def test_zero_limit_issues_no_request(tmp_path):
    transport = PagedTransport()
    source = make_source(tmp_path, transport)
    assert source.search_papers("wheat", limit=0) == []
    assert transport.calls == []


# ---------------------------------------------------------------------------
# B4 — an incomplete pagination is cached as incomplete and resumed
# ---------------------------------------------------------------------------


def test_partial_citation_pagination_is_not_cached_as_complete(tmp_path):
    source = make_source(tmp_path, PagedTransport(fail_on_pages=(3,)))
    source._resolve_oa_id = lambda _paper_id: "W1"

    first = source.get_citations("10.9/root", limit=250)
    assert len(first) == 200  # two good pages, the third failed

    row = source._cache.get(
        "openalex", source.CACHE_PARSER_VERSION, "oa_citations", "10.9/root", "250"
    )
    assert isinstance(row, dict), "pagination must be cached in a completeness envelope"
    assert row["complete"] is False
    assert row["next_page"] == 3
    assert row["error"], "the failure must be recorded, not swallowed"


def test_failed_citation_pagination_resumes_and_completes(tmp_path):
    broken = make_source(tmp_path, PagedTransport(fail_on_pages=(3,)))
    broken._resolve_oa_id = lambda _paper_id: "W1"
    assert len(broken.get_citations("10.9/root", limit=250)) == 200

    healthy_transport = PagedTransport()
    healthy = make_source(tmp_path, healthy_transport)
    healthy._resolve_oa_id = lambda _paper_id: "W1"
    papers = healthy.get_citations("10.9/root", limit=250)

    assert len(papers) == 250, "the missing records were never re-fetched"
    assert len({p.canonical_id for p in papers}) == 250
    assert healthy_transport.pages[:1] == [3], (
        "resume must continue from the recorded page, not restart the sequence"
    )


def test_resume_matches_a_single_successful_run(tmp_path):
    broken = make_source(tmp_path, PagedTransport(fail_on_pages=(3,)), name="a.db")
    broken._resolve_oa_id = lambda _paper_id: "W1"
    broken.get_citations("10.9/root", limit=250)
    resumed = make_source(tmp_path, PagedTransport(), name="a.db")
    resumed._resolve_oa_id = lambda _paper_id: "W1"
    after_resume = [p.canonical_id for p in resumed.get_citations("10.9/root", limit=250)]

    clean = make_source(tmp_path, PagedTransport(), name="b.db")
    clean._resolve_oa_id = lambda _paper_id: "W1"
    reference = [p.canonical_id for p in clean.get_citations("10.9/root", limit=250)]

    assert after_resume == reference


def test_completed_pagination_is_served_from_cache_without_new_requests(tmp_path):
    transport = PagedTransport()
    source = make_source(tmp_path, transport)
    source._resolve_oa_id = lambda _paper_id: "W1"
    first = source.get_citations("10.9/root", limit=250)

    second_transport = PagedTransport()
    second = make_source(tmp_path, second_transport)
    second._resolve_oa_id = lambda _paper_id: "W1"
    again = second.get_citations("10.9/root", limit=250)

    assert [p.canonical_id for p in again] == [p.canonical_id for p in first]
    assert second_transport.calls == [], "a complete cached run must not re-request"


def test_complete_flag_is_set_only_on_a_natural_end(tmp_path):
    transport = PagedTransport(total=120)
    source = make_source(tmp_path, transport)
    source.search_papers("wheat", limit=500)  # provider ends before the limit
    row = source._cache.get(
        "openalex", source.CACHE_PARSER_VERSION, "oa_search", "wheat", "500", "1900", "2026"
    ) or source._cache.get(
        "openalex", source.CACHE_PARSER_VERSION, "oa_search", "wheat", "500", "1900",
        str(__import__("litsearch.config", fromlist=["current_year"]).current_year()),
    )
    assert row is not None and row["complete"] is True


# ---------------------------------------------------------------------------
# B5 — retry, cancel, timeout and honest HTTP accounting
# ---------------------------------------------------------------------------


def test_transient_failure_is_retried_then_succeeds(tmp_path):
    class FlakyOnce(PagedTransport):
        def __init__(self):
            super().__init__(total=5)
            self.failed = False

        def get(self, url, params=None, timeout=None, **kwargs):
            if not self.failed:
                self.failed = True
                self.calls.append(dict(params or {}))
                raise ConnectionError("temporary blip")
            return super().get(url, params=params, timeout=timeout, **kwargs)

    transport = FlakyOnce()
    source = make_source(tmp_path, transport)
    papers = source.search_papers("wheat", limit=5)
    assert len(papers) == 5, "a transient failure was not retried"
    assert len(transport.calls) == 2


def test_cancellation_stops_paging_and_is_reported(tmp_path):
    transport = PagedTransport()
    source = make_source(tmp_path, transport)
    calls = {"n": 0}

    def cancel_after_first_page():
        calls["n"] += 1
        return calls["n"] > 1

    source.set_cancel_check(cancel_after_first_page)
    papers = source.search_papers("wheat", limit=250)
    assert len(papers) < 250
    assert len(transport.pages) <= 2
    meta = source.last_retrieval_meta
    assert meta["complete"] is False
    assert "cancel" in (meta["error"] or "").lower()


def test_canceled_pagination_is_cached_as_incomplete(tmp_path):
    transport = PagedTransport()
    source = make_source(tmp_path, transport)
    calls = {"n": 0}

    def cancel_after_first_page():
        calls["n"] += 1
        return calls["n"] > 1

    source.set_cancel_check(cancel_after_first_page)
    source.search_papers("wheat", limit=250)
    row = source._cache.get(
        "openalex", source.CACHE_PARSER_VERSION, "oa_search", "wheat", "250", "1900",
        str(__import__("litsearch.config", fromlist=["current_year"]).current_year()),
    )
    assert row is not None and row["complete"] is False


def test_http_budget_counts_real_requests_not_method_calls(tmp_path):
    reset_http_budget()
    transport = PagedTransport()
    source = make_source(tmp_path, transport)
    source.search_papers("wheat", limit=250)  # one method call, three pages

    snapshot = http_budget_snapshot()
    assert len(transport.calls) == 3
    assert snapshot["requests"] == 3, (
        "budget must count HTTP requests; one SourceManager call is not one request"
    )
    assert snapshot["by_source"]["openalex"]["requests"] == 3


def test_http_budget_counts_retries_separately(tmp_path):
    class AlwaysFails(PagedTransport):
        def get(self, url, params=None, timeout=None, **kwargs):
            self.calls.append(dict(params or {}))
            raise ConnectionError("down")

    reset_http_budget()
    transport = AlwaysFails()
    source = make_source(tmp_path, transport)
    source.search_papers("wheat", limit=5)

    snapshot = http_budget_snapshot()
    attempted = len(transport.calls)
    assert attempted == source.MAX_HTTP_RETRIES + 1
    # `requests` counts attempts, failures included; `errors` is the subset that raised.
    assert snapshot["requests"] == attempted
    assert snapshot["errors"] == attempted
    assert snapshot["retries"] == source.MAX_HTTP_RETRIES


def test_http_budget_counts_cache_hits(tmp_path):
    reset_http_budget()
    transport = PagedTransport(total=5)
    source = make_source(tmp_path, transport)
    source.search_papers("wheat", limit=5)
    requests_after_first = http_budget_snapshot()["requests"]

    second = make_source(tmp_path, PagedTransport(total=5))
    second.search_papers("wheat", limit=5)
    snapshot = http_budget_snapshot()
    assert snapshot["requests"] == requests_after_first
    assert snapshot["cache_hits"] >= 1


def test_budget_snapshot_is_json_serialisable():
    import json

    reset_http_budget()
    json.dumps(http_budget_snapshot())


def test_budget_reset_clears_previous_run():
    budget = HttpBudget()
    budget.note_request("openalex")
    assert budget.snapshot()["requests"] == 1
    budget.reset()
    assert budget.snapshot()["requests"] == 0
    assert budget.snapshot()["by_source"] == {}


def test_legacy_bare_list_cache_rows_are_not_trusted_as_complete(tmp_path):
    """A bare-list row cannot prove it was whole, so it must not be reused as one.

    Rows written by v0.8.0 stored a plain list with no completeness metadata.
    They are now unreachable through the normal key path because the parser
    version was bumped, so this seeds one *under the current key* to prove the
    reader itself refuses to treat a bare list as a complete pagination.
    """
    cache = Cache(str(tmp_path / "legacy.db"))
    cache.set(
        [record(0), record(1)],
        "openalex", OpenAlexSource.CACHE_PARSER_VERSION, "oa_citations", "10.9/x", "250",
    )
    source = OpenAlexSource(cache=cache)
    source._session = CountingSession(PagedTransport(total=3), "openalex")
    source._resolve_oa_id = lambda _paper_id: "W1"
    papers = source.get_citations("10.9/x", limit=250)
    assert len(papers) == 3, "a bare-list cache row was trusted as a complete result"
    assert {p.canonical_id for p in papers} == {"10.9/0", "10.9/1", "10.9/2"}


def test_unversioned_v080_cache_rows_are_ignored_entirely(tmp_path):
    """v0.8.0 wrote rows under ``parser_v3``; the bump must retire them."""
    cache = Cache(str(tmp_path / "old.db"))
    cache.set([record(0)], "openalex", "parser_v3", "oa_citations", "10.9/x", "250")
    source = OpenAlexSource(cache=cache)
    source._session = CountingSession(PagedTransport(total=3), "openalex")
    source._resolve_oa_id = lambda _paper_id: "W1"
    assert len(source.get_citations("10.9/x", limit=250)) == 3


def test_cache_key_is_versioned_so_shapes_do_not_collide(tmp_path):
    assert OpenAlexSource.CACHE_PARSER_VERSION == "parser_v4"
    assert OpenAlexSource.PAGE_SIZE == 100


def test_retrieval_canceled_exception_is_exported():
    from litsearch import sources

    assert issubclass(sources.RetrievalCanceled, RuntimeError)
    assert issuable(RetrievalCanceled)


def issuable(exc_type):
    try:
        raise exc_type("x")
    except exc_type:
        return True
