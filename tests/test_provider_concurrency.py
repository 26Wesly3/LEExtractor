"""Provider-level concurrency (v0.9.12 P0#2).

规范要求：provider 之间并发、同一 provider 内仍守自己的 pacing/rate limit、
429 冷却必须 provider-local、一个 provider 失败/超时不得让其它 provider 结果消失、
取消仍有效、HTTP 预算不因并发失控、并发数有明确上限。

这些用例直接打在 ``SourceManager.search_all_sources_result`` 上，用假 provider 记录
并发峰值与顺序。
"""

from __future__ import annotations

import threading
import time

from litsearch.cache import Cache
from litsearch.diagnostics import RateLimitedError
from litsearch.models import Paper
from litsearch.retrieval import RetrievalResult, RetrievalStatus
from litsearch.sources import (
    MAX_PROVIDER_CONCURRENCY,
    RetrievalCanceled,
    SourceManager,
    provider_concurrency,
)


class FakeProvider:
    """Records concurrency so a test can prove providers overlapped."""

    def __init__(self, name, *, papers=(), delay=0.0, error=None, tracker=None):
        self.name = name
        self._papers = list(papers)
        self._delay = delay
        self._error = error
        self._tracker = tracker

    def _search_papers_result(self, query, limit=50, year_from=1900, year_to=None):
        if self._tracker is not None:
            with self._tracker["lock"]:
                self._tracker["active"] += 1
                self._tracker["max"] = max(self._tracker["max"], self._tracker["active"])
        try:
            time.sleep(self._delay)
            if self._error is not None:
                raise self._error
            return RetrievalResult(papers=list(self._papers), provider=self.name,
                                   status=RetrievalStatus.SUCCESS, complete=True)
        finally:
            if self._tracker is not None:
                with self._tracker["lock"]:
                    self._tracker["active"] -= 1


def tracker():
    return {"lock": threading.Lock(), "active": 0, "max": 0}


def manager(tmp_path, monkeypatch, rows):
    sources = SourceManager(Cache(str(tmp_path / "cache.db")))
    monkeypatch.setattr(sources, "_provider_plan", lambda query, limit, plan: iter(rows))
    return sources


def paper(pid: str) -> Paper:
    return Paper(id=pid, title=f"record {pid}", doi=pid, year=2024)


def test_provider_concurrency_env_is_bounded(monkeypatch):
    monkeypatch.setenv("LEEXTRACTOR_PROVIDER_CONCURRENCY", "3")
    assert provider_concurrency() == 3
    monkeypatch.setenv("LEEXTRACTOR_PROVIDER_CONCURRENCY", "9999")
    assert provider_concurrency() == MAX_PROVIDER_CONCURRENCY
    monkeypatch.setenv("LEEXTRACTOR_PROVIDER_CONCURRENCY", "0")
    assert provider_concurrency() == 1
    monkeypatch.setenv("LEEXTRACTOR_PROVIDER_CONCURRENCY", "not-a-number")
    assert provider_concurrency() == 5
    monkeypatch.delenv("LEEXTRACTOR_PROVIDER_CONCURRENCY", raising=False)
    assert provider_concurrency() == 5


def test_slow_providers_overlap_instead_of_serialising(tmp_path, monkeypatch):
    monkeypatch.setenv("LEEXTRACTOR_PROVIDER_CONCURRENCY", "3")
    seen = tracker()
    rows = [
        (FakeProvider(f"p{index}", papers=[paper(f"10.1/{index}")], delay=0.3, tracker=seen), 10, f"q{index}")
        for index in range(3)
    ]
    sources = manager(tmp_path, monkeypatch, rows)
    started = time.perf_counter()
    results = sources.search_all_sources_result("query", limit=10)
    elapsed = time.perf_counter() - started
    assert len(results) == 3
    assert seen["max"] == 3, "providers did not overlap"
    assert elapsed < 0.75, f"expected overlap, took {elapsed:.2f}s (serial would be ~0.9s)"


def test_concurrency_never_exceeds_the_configured_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("LEEXTRACTOR_PROVIDER_CONCURRENCY", "2")
    seen = tracker()
    rows = [(FakeProvider(f"p{index}", delay=0.1, tracker=seen), 10, "q") for index in range(5)]
    sources = manager(tmp_path, monkeypatch, rows)
    sources.search_all_sources_result("query", limit=10)
    assert seen["max"] <= 2, f"fan-out exceeded the limit: {seen['max']}"


def test_a_failing_provider_does_not_lose_the_others(tmp_path, monkeypatch):
    monkeypatch.setenv("LEEXTRACTOR_PROVIDER_CONCURRENCY", "3")
    rows = [
        (FakeProvider("broken", error=RuntimeError("boom")), 10, "q"),
        (FakeProvider("slow", papers=[paper("10.1/slow")], delay=0.2), 10, "q"),
        (FakeProvider("fine", papers=[paper("10.1/fine")], delay=0.05), 10, "q"),
    ]
    sources = manager(tmp_path, monkeypatch, rows)
    results = sources.search_all_sources_result("query", limit=10)
    by_name = {result.provider: result for result in results}
    assert by_name["broken"].status is RetrievalStatus.PROVIDER_ERROR
    assert "boom" in (by_name["broken"].error or "")
    assert [p.id for p in by_name["slow"].papers] == ["10.1/slow"]
    assert [p.id for p in by_name["fine"].papers] == ["10.1/fine"]


def test_rate_limit_stays_provider_local(tmp_path, monkeypatch):
    monkeypatch.setenv("LEEXTRACTOR_PROVIDER_CONCURRENCY", "3")
    rows = [
        (FakeProvider("limited", error=RateLimitedError("limited", "429", 429)), 10, "q"),
        (FakeProvider("ok", papers=[paper("10.1/ok")], delay=0.05), 10, "q"),
    ]
    sources = manager(tmp_path, monkeypatch, rows)
    results = sources.search_all_sources_result("query", limit=10)
    by_name = {result.provider: result for result in results}
    assert by_name["limited"].status is RetrievalStatus.RATE_LIMITED
    assert [p.id for p in by_name["ok"].papers] == ["10.1/ok"]


def test_cancellation_is_still_reported_as_canceled(tmp_path, monkeypatch):
    monkeypatch.setenv("LEEXTRACTOR_PROVIDER_CONCURRENCY", "3")
    rows = [
        (FakeProvider("canceled", error=RetrievalCanceled("canceled: user canceled")), 10, "q"),
        (FakeProvider("ok", papers=[paper("10.1/ok")]), 10, "q"),
    ]
    sources = manager(tmp_path, monkeypatch, rows)
    results = sources.search_all_sources_result("query", limit=10)
    by_name = {result.provider: result for result in results}
    assert by_name["canceled"].status is RetrievalStatus.CANCELED


def test_result_order_follows_the_provider_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("LEEXTRACTOR_PROVIDER_CONCURRENCY", "4")
    # Slowest first: with `as_completed` semantics the order would flip.
    rows = [
        (FakeProvider("first", papers=[paper("10.1/1")], delay=0.25), 10, "q"),
        (FakeProvider("second", papers=[paper("10.1/2")], delay=0.15), 10, "q"),
        (FakeProvider("third", papers=[paper("10.1/3")], delay=0.02), 10, "q"),
    ]
    sources = manager(tmp_path, monkeypatch, rows)
    results = sources.search_all_sources_result("query", limit=10)
    assert [result.provider for result in results] == ["first", "second", "third"]


def test_provider_timings_are_recorded_per_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("LEEXTRACTOR_PROVIDER_CONCURRENCY", "3")
    rows = [
        (FakeProvider("quick", delay=0.01), 10, "q"),
        (FakeProvider("slow", delay=0.2), 10, "q"),
    ]
    sources = manager(tmp_path, monkeypatch, rows)
    sources.search_all_sources_result("query", limit=10)
    timings = sources.last_provider_timings
    assert set(timings) == {"quick", "slow"}
    assert timings["slow"] >= 0.15 > timings["quick"]


def test_concurrency_one_keeps_the_sequential_path(tmp_path, monkeypatch):
    monkeypatch.setenv("LEEXTRACTOR_PROVIDER_CONCURRENCY", "1")
    seen = tracker()
    rows = [(FakeProvider(f"p{index}", papers=[paper(f"10.1/{index}")], delay=0.05, tracker=seen), 10, "q")
            for index in range(3)]
    sources = manager(tmp_path, monkeypatch, rows)
    results = sources.search_all_sources_result("query", limit=10)
    assert seen["max"] == 1
    assert [result.provider for result in results] == ["p0", "p1", "p2"]


def test_blank_query_short_circuit_still_reports_no_provider_timings(tmp_path, monkeypatch):
    rows = [(FakeProvider("p0"), 10, "q")]
    sources = manager(tmp_path, monkeypatch, rows)
    results = sources.search_all_sources_result("   ", limit=10)
    assert [result.status for result in results] == [RetrievalStatus.SUCCESS_EMPTY]
    assert sources.last_provider_timings == {}
