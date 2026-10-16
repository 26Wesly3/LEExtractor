"""The process-wide retrieval cap must not depend on start-up or test order.

``docs/v3.2/15_PERFORMANCE_DEPLOYMENT.md`` budgets "默认单个重计算任务" on the
reference 2 vCPU / 2 GB host, and ADR-006 implements it as one process-wide
semaphore that every JobManager shares.

The original implementation *replaced* that semaphore whenever
``LEEXTRACTOR_MAX_ACTIVE_JOBS`` changed.  A JobManager captures the semaphore once
at construction, so an app built before the change kept one semaphore while an app
built after it took another, and the first stayed held by whoever had acquired it:
three live semaphores, the cap enforced only within whichever app you held, and
which limit an app obeyed decided by test order.  This was found while
investigating transient failures in the v3.2 suite (the v3.2 layer has its own
JobManager and never used this helper — the flakiness had a different cause — but
the hazard itself was real and is pinned here).
"""

from __future__ import annotations

import threading

import pytest

from litsearch.web import jobs as web_jobs


@pytest.fixture(autouse=True)
def _clean_slots():
    web_jobs.reset_retrieval_slots()
    yield
    web_jobs.reset_retrieval_slots()


def test_a_fixed_environment_always_returns_the_same_semaphore(monkeypatch):
    monkeypatch.setenv("LEEXTRACTOR_MAX_ACTIVE_JOBS", "2")
    first = web_jobs.retrieval_slots()
    assert first is web_jobs.retrieval_slots()


def test_changing_and_restoring_the_capacity_returns_the_original_semaphore(monkeypatch):
    """The regression: restoring the env must not orphan the first semaphore."""
    monkeypatch.setenv("LEEXTRACTOR_MAX_ACTIVE_JOBS", "1")
    original = web_jobs.retrieval_slots()

    monkeypatch.setenv("LEEXTRACTOR_MAX_ACTIVE_JOBS", "2")
    other = web_jobs.retrieval_slots()
    assert other is not original

    monkeypatch.setenv("LEEXTRACTOR_MAX_ACTIVE_JOBS", "1")
    assert web_jobs.retrieval_slots() is original, (
        "replacing the global semaphore made an app built before the change and "
        "one built after it obey different limits"
    )


def test_a_manager_built_before_a_capacity_change_keeps_a_live_bounded_semaphore(monkeypatch):
    """Two managers must never end up honouring two different live caps.

    With per-capacity caching, a manager built under capacity 1 and one built
    under capacity 2 each hold a semaphore whose capacity matches the setting
    they were built with, and neither is silently superseded.
    """
    monkeypatch.setenv("LEEXTRACTOR_MAX_ACTIVE_JOBS", "1")
    first = web_jobs.retrieval_slots()
    assert first.acquire(timeout=0)
    try:
        monkeypatch.setenv("LEEXTRACTOR_MAX_ACTIVE_JOBS", "2")
        second = web_jobs.retrieval_slots()
        assert second is not first
        # The capacity-1 slot is still held, so a second capacity-1 app cannot
        # start a retrieval while the first is running.
        assert first.acquire(timeout=0) is False
        # And the capacity-2 semaphore is genuinely a separate budget.
        assert second.acquire(timeout=0)
        second.release()
    finally:
        first.release()

    monkeypatch.setenv("LEEXTRACTOR_MAX_ACTIVE_JOBS", "1")
    assert web_jobs.retrieval_slots() is first


def test_reset_retrieval_slots_gives_a_fresh_semaphore(monkeypatch):
    monkeypatch.setenv("LEEXTRACTOR_MAX_ACTIVE_JOBS", "1")
    original = web_jobs.retrieval_slots()
    web_jobs.reset_retrieval_slots()
    assert web_jobs.retrieval_slots() is not original


def test_the_cached_semaphores_are_never_none_and_are_thread_safe(monkeypatch):
    monkeypatch.setenv("LEEXTRACTOR_MAX_ACTIVE_JOBS", "3")
    seen: list[object] = []
    lock = threading.Lock()

    def grab():
        value = web_jobs.retrieval_slots()
        with lock:
            seen.append(value)

    threads = [threading.Thread(target=grab) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(seen) == 16
    assert len({id(value) for value in seen}) == 1
