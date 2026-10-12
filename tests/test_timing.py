"""Stage timing (v0.9.12 P0): cheap, additive, and never fatal.

规范要求：timing 必须存在、必须非负、`partial`/`failed` 也要记录、provider 单独记录、
写入失败不能导致任务失败。本文件固定这些不变量。
"""

from __future__ import annotations

import json

import pytest

from litsearch.models import Paper
from litsearch.search import LiteratureReviewWorkflow, ReviewState
from litsearch.timing import (
    STAGE_NORMALIZATION,
    STAGE_PROVIDER_TOTAL,
    STAGE_QUERY_PLANNING,
    STAGE_RANKING,
    STAGE_TOTAL,
    StageTimer,
    timed_call,
)
from litsearch.web.jobs import _timings

TOPIC = "multi-agent collaboration in computer vision"
DIRECTION = "计算机视觉领域的多智能体合作问题"


def paper(pid: str) -> Paper:
    return Paper(id=pid, title=f"multi-agent study {pid}", doi=pid, year=2024, abstract="agents cooperate")


class LegacyStubSources:
    """Only the legacy list API — no provider_result, no per-provider timings."""

    snowball_delay = 0

    def __init__(self, papers=()):
        self.papers = list(papers)
        self.last_search_manifest = {"query": TOPIC, "provider_counts": {"stub": len(self.papers)}}

    def is_canceled(self):
        return False

    def search_all_sources(self, query, limit=200, year_from=1900, year_to=None, query_plan=None):
        return list(self.papers)


class ResultStubSources(LegacyStubSources):
    """Result API double that also reports per-provider durations."""

    def __init__(self, papers=(), provider_timings=None):
        super().__init__(papers)
        self.last_provider_timings = dict(provider_timings or {})

    def search_all_sources_result(self, query, limit=200, year_from=1900, year_to=None, query_plan=None):
        return []


def workflow(sources) -> LiteratureReviewWorkflow:
    wf = LiteratureReviewWorkflow(sources)
    wf.downloader = None
    return wf


# --------------------------------------------------------------------------- timer


def test_timer_accumulates_and_ignores_unusable_values():
    timer = StageTimer()
    timer.add("ranking", 0.5)
    timer.add("ranking", 0.25)
    timer.add("ranking", None)          # type: ignore[arg-type]
    timer.add("ranking", "not-a-number")  # type: ignore[arg-type]
    timer.add("ranking", -3)            # negative durations are not measurements
    timer.add("", 1.0)                  # unnamed stage is meaningless
    assert timer.snapshot()["ranking"] == 0.75


def test_timer_records_providers_separately_and_extras():
    timer = StageTimer()
    timer.add_provider("openalex", 1.5)
    timer.add_provider("crossref", 0.25)
    timer.add_provider("openalex", 0.5)
    snapshot = timer.snapshot(total=2.5)
    assert snapshot["providers"] == {"openalex": 2.0, "crossref": 0.25}
    assert snapshot["total"] == 2.5
    json.dumps(snapshot)  # must stay JSON-serialisable for job payloads


def test_stage_context_manager_measures_and_survives_exceptions():
    timer = StageTimer()
    with pytest.raises(RuntimeError), timer.stage("ranking"):
        raise RuntimeError("boom")
    assert timer.snapshot()["ranking"] >= 0


def test_timed_call_works_with_and_without_a_timer():
    assert timed_call(None, "ranking", lambda value: value + 1, 1) == 2
    timer = StageTimer()
    assert timed_call(timer, "ranking", lambda: "ok") == "ok"
    assert timer.snapshot()["ranking"] >= 0


# --------------------------------------------------------------------------- search


def test_systematic_search_records_stage_timings():
    wf = workflow(LegacyStubSources([paper("10.1/a"), paper("10.1/b")]))
    state = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(state, max_papers=10)
    timings = state.search_manifest["timings"]
    for stage in (STAGE_QUERY_PLANNING, STAGE_PROVIDER_TOTAL, STAGE_NORMALIZATION, STAGE_RANKING, STAGE_TOTAL):
        assert stage in timings, f"missing stage {stage}"
        assert timings[stage] >= 0
    assert timings[STAGE_TOTAL] >= timings[STAGE_PROVIDER_TOTAL]


def test_provider_timings_are_carried_into_the_manifest():
    wf = workflow(ResultStubSources(provider_timings={"openalex": 1.25, "arxiv": 0.5}))
    state = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(state, max_papers=10)
    assert state.search_manifest["timings"]["providers"] == {"openalex": 1.25, "arxiv": 0.5}


def test_timings_still_recorded_when_the_search_fails():
    class Exploding(LegacyStubSources):
        def search_all_sources(self, *args, **kwargs):
            raise RuntimeError("provider layer exploded")

    wf = workflow(Exploding())
    state = ReviewState(TOPIC, DIRECTION)
    with pytest.raises(RuntimeError):
        wf.systematic_search(state, max_papers=10)
    # 失败路径也必须有耗时（query_planning 与 provider_total 在异常前已记录）
    timings = state.search_manifest.get("timings", {})
    assert STAGE_QUERY_PLANNING in timings and timings[STAGE_QUERY_PLANNING] >= 0
    assert STAGE_PROVIDER_TOTAL in timings


# --------------------------------------------------------------------------- job payload


def test_job_timings_helper_is_fail_safe():
    class State:
        pass

    empty = State()
    assert _timings(empty) == {}

    class Broken:
        @property
        def search_manifest(self):
            raise RuntimeError("no manifest")

    assert _timings(Broken()) == {}

    class WithTimings:
        search_manifest = {"timings": {"total": 1.5}}

    assert _timings(WithTimings()) == {"total": 1.5}
