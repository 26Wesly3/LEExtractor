"""运行概况聚合（首页访问 PV 与检索入口点击）的回归测试。

对应需求中的硬性约束：
  · 计数是后端持久化的全站聚合，重启不归零；
  · 已开始记数的指标才返回数值，未开始的返回 None（前端显示"—"，不编造历史数据）；
  · 上线日期来自持久化配置，只按日期计算天数，且不使用进程启动时间；
  · 同一 entry_id 在时间窗内只计一次（防重渲染/轮询重复累计）；
  · 统计写入失败不得抛异常，也不得让接口失败。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from litsearch.web import create_app
from litsearch.web.stats import (
    DEFAULT_LAUNCH_DATE,
    METRIC_HOME_VIEWS,
    METRIC_SEARCH_CLICKS,
    StatsStore,
)


def test_snapshot_before_any_count_is_honest(tmp_path):
    store = StatsStore(tmp_path)
    snapshot = store.snapshot(now=datetime(2026, 10, 12, tzinfo=timezone.utc))
    assert snapshot["launch"] == {"date": DEFAULT_LAUNCH_DATE, "days": 0, "precise_time": False}
    # 未开始记数的指标必须是 None，而不是 0 —— 0 会被读成"真实统计为零"。
    assert snapshot[METRIC_HOME_VIEWS] == {"count": None, "counting_since": None}
    assert snapshot[METRIC_SEARCH_CLICKS] == {"count": None, "counting_since": None}
    assert snapshot["counting_since"] is None
    assert snapshot["persisted"] is True


def test_recording_starts_the_counter_and_stamps_the_start(tmp_path):
    store = StatsStore(tmp_path)
    assert store.record(METRIC_HOME_VIEWS) is True
    snapshot = store.snapshot()
    assert snapshot[METRIC_HOME_VIEWS]["count"] == 1
    assert snapshot[METRIC_HOME_VIEWS]["counting_since"]
    assert snapshot["counting_since"] == snapshot[METRIC_HOME_VIEWS]["counting_since"]
    # 另一个指标仍未开始，继续返回 None。
    assert snapshot[METRIC_SEARCH_CLICKS]["count"] is None


def test_same_entry_id_is_counted_once_within_the_window(tmp_path):
    store = StatsStore(tmp_path)
    assert store.record(METRIC_HOME_VIEWS, "entry-1") is True
    assert store.record(METRIC_HOME_VIEWS, "entry-1") is False
    assert store.record(METRIC_HOME_VIEWS, "entry-2") is True
    assert store.snapshot()[METRIC_HOME_VIEWS]["count"] == 2


def test_missing_entry_id_counts_every_call(tmp_path):
    store = StatsStore(tmp_path)
    store.record(METRIC_SEARCH_CLICKS)
    store.record(METRIC_SEARCH_CLICKS)
    assert store.snapshot()[METRIC_SEARCH_CLICKS]["count"] == 2


def test_counts_and_launch_date_survive_a_restart(tmp_path, monkeypatch):
    store = StatsStore(tmp_path)
    store.record(METRIC_HOME_VIEWS, "a")
    store.record(METRIC_SEARCH_CLICKS, "b")
    before = store.snapshot()

    # 重启：新实例读同一目录；即使环境里换了一个更晚的上线日期，也以已持久化的为准。
    monkeypatch.setenv("LEEXTRACTOR_LAUNCH_DATE", "2026-11-01")
    restarted = StatsStore(tmp_path)
    after = restarted.snapshot()
    assert after[METRIC_HOME_VIEWS]["count"] == before[METRIC_HOME_VIEWS]["count"] == 1
    assert after[METRIC_SEARCH_CLICKS]["count"] == 1
    assert after["launch"]["date"] == DEFAULT_LAUNCH_DATE
    assert after["counting_since"] == before["counting_since"]
    # 去重环是进程内的（重启即空），它只为防同一次会话内的重渲染/轮询重复计数；
    # 累计值本身来自磁盘，因此继续计数是正确行为。
    assert restarted.record(METRIC_HOME_VIEWS, "b") is True
    assert restarted.snapshot()[METRIC_HOME_VIEWS]["count"] == 2


def test_launch_date_comes_from_configuration_when_nothing_is_persisted(tmp_path, monkeypatch):
    monkeypatch.setenv("LEEXTRACTOR_LAUNCH_DATE", "2026-10-10")
    store = StatsStore(tmp_path)
    snapshot = store.snapshot(now=datetime(2026, 10, 12, tzinfo=timezone.utc))
    assert snapshot["launch"]["date"] == "2026-10-10"
    assert snapshot["launch"]["days"] == 2


def test_invalid_launch_date_falls_back_to_the_known_date(tmp_path, monkeypatch):
    monkeypatch.setenv("LEEXTRACTOR_LAUNCH_DATE", "not-a-date")
    assert StatsStore(tmp_path).snapshot()["launch"]["date"] == DEFAULT_LAUNCH_DATE


def test_process_uptime_is_separate_from_launch_age(tmp_path):
    store = StatsStore(tmp_path)
    snapshot = store.snapshot(now=datetime(2026, 10, 12, tzinfo=timezone.utc))
    # 进程运行时长与"上线至今"是两件事：前者很小，后者按日期计算。
    assert 0 <= snapshot["runtime"]["process_uptime_seconds"] < 60
    assert snapshot["launch"]["days"] == 0


def test_write_failure_never_raises_and_keeps_in_memory_counts(tmp_path):
    root = tmp_path / "not-a-directory"
    root.write_text("这是一个文件，不是目录", encoding="utf-8")
    store = StatsStore(root)  # 读取也会失败，但不允许抛异常
    assert store.record(METRIC_HOME_VIEWS, "x") is True
    snapshot = store.snapshot()
    assert snapshot["persisted"] is False
    assert snapshot[METRIC_HOME_VIEWS]["count"] == 1


def test_corrupt_file_is_backed_up_and_counting_restarts(tmp_path):
    (tmp_path / "stats.json").write_text("{ this is not json", encoding="utf-8")
    store = StatsStore(tmp_path)
    assert store.snapshot()[METRIC_HOME_VIEWS]["count"] is None
    backups = list(tmp_path.glob("stats.json.corrupt-*.bak"))
    assert backups, "损坏的统计文件应被备份而不是静默覆盖"
    store.record(METRIC_HOME_VIEWS)
    assert json.loads((tmp_path / "stats.json").read_text(encoding="utf-8"))[METRIC_HOME_VIEWS] == 1


# --------------------------------------------------------------------------- API


@pytest.fixture()
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as test_client:
        yield test_client


def test_stats_endpoint_shape(client):
    payload = client.get("/api/stats").json()
    assert payload["launch"]["date"] == DEFAULT_LAUNCH_DATE
    assert payload["launch"]["days"] >= 0
    assert payload["home_views"]["count"] is None
    assert payload["search_entry_clicks"]["count"] is None
    assert payload["persisted"] is True
    # 另外两项由仓库与任务记录即时汇总，同样必须是真实数值（空库时为 0）。
    assert payload["search_runs"]["count"] == 0
    assert payload["records"]["count"] == 0


def test_search_runs_and_records_come_from_real_records(client):
    """搜索运行次数取自任务记录，共检索得文献取自项目语料，均不依赖前端上报。"""
    project = client.post("/api/projects", json={"name": "统计口径", "topic": "plant phenotyping"}).json()
    payload = client.get("/api/stats").json()
    assert payload["records"]["count"] == 0
    assert payload["search_runs"]["count"] == 0
    assert client.get(f"/api/projects/{project['project_id']}").status_code == 200


def test_stats_stream_pushes_real_payload(client):
    """SSE 首帧必须立刻带真实快照，供前端做即时滚动更新。"""
    response = client.get("/api/stats/stream?limit=1")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    lines = [line for line in response.text.splitlines() if line]
    assert lines[0].startswith("event: stats")
    assert lines[1].startswith("data: ")
    payload = json.loads(lines[1][len("data: "):])
    assert payload["launch"]["date"] == DEFAULT_LAUNCH_DATE
    assert "search_runs" in payload and "records" in payload


def test_home_view_endpoint_deduplicates_by_entry_id(client):
    first = client.post("/api/stats/home-view", json={"entry_id": "session-1"}).json()
    assert first["home_views"]["count"] == 1
    again = client.post("/api/stats/home-view", json={"entry_id": "session-1"}).json()
    assert again["home_views"]["count"] == 1, "同一次页面进入不得重复累计"
    other = client.post("/api/stats/home-view", json={"entry_id": "session-2"}).json()
    assert other["home_views"]["count"] == 2
    # GET 只读，不改变计数。
    assert client.get("/api/stats").json()["home_views"]["count"] == 2


def test_search_entry_click_endpoint_is_separate_from_home_views(client):
    client.post("/api/stats/search-entry-click", json={"entry_id": "s1"})
    client.post("/api/stats/search-entry-click", json={"entry_id": "s1"})
    payload = client.get("/api/stats").json()
    assert payload["search_entry_clicks"]["count"] == 1
    assert payload["home_views"]["count"] is None, "点击入口与首页访问量是两个指标"


def test_counter_persists_across_app_instances(tmp_path):
    with TestClient(create_app(tmp_path)) as first:
        first.post("/api/stats/home-view", json={"entry_id": "a"})
    with TestClient(create_app(tmp_path)) as second:
        payload = second.get("/api/stats").json()
        assert payload["home_views"]["count"] == 1, "服务重启后累计值不得归零"


def test_stats_requests_do_not_touch_projects_or_jobs(client):
    """统计接口必须是轻量的旁路：不产生项目、不产生任务。"""
    client.post("/api/stats/home-view", json={"entry_id": "a"})
    client.post("/api/stats/search-entry-click", json={"entry_id": "a"})
    assert client.get("/api/projects").json()["items"] == []


def test_invalid_body_is_rejected_without_breaking_the_page(client):
    assert client.post("/api/stats/home-view", json={"unexpected": 1}).status_code == 422
    assert client.get("/api/stats").status_code == 200
