"""v0.9.12 §5 / §7 语义回归：auth_error、cached、ccf_rank=none。

对应规格要求：
  - 错误语义不得把鉴权失败混进通用 provider error；
  - 「缓存命中」是成功的一种修饰，不是失败态；
  - `ccf_rank = none` 只返回不在 CCF 目录内的记录，且 CCF 与 relevance 分数无耦合。
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from litsearch.demo import DemoSource
from litsearch.diagnostics import AuthError, SourceErrorKind, classify_http_error
from litsearch.downloader import PaperDownloader
from litsearch.models import Paper
from litsearch.retrieval import RequestStats, RetrievalResult, RetrievalStatus
from litsearch.search import LiteratureReviewWorkflow
from litsearch.sources import retrieval_status_for_exception
from litsearch.web import create_app


class VenueSources(DemoSource):
    """一条 CCF-A（NeurIPS）＋ 一条不在目录内的记录。"""

    def search_all_sources(self, query, **kwargs):
        self.last_search_manifest = {"provider_counts": {"fake": 2}, "query": query}
        return [
            Paper("10.1234/ranked", "Ranked venue paper", year=2024, venue="NeurIPS"),
            Paper("10.1234/unranked", "Unranked venue paper", year=2023, venue="Journal of Nothing"),
        ]


def client(tmp_path):
    def factory(demo):
        return LiteratureReviewWorkflow(
            DemoSource() if demo else VenueSources(),
            PaperDownloader(str(tmp_path / "downloads")),
        )

    return TestClient(create_app(tmp_path, workflow_factory=factory))


def searched_project(c):
    project = c.post("/api/projects", json={"topic": "plant learning"}).json()
    started = c.post(f"/api/projects/{project['project_id']}/search", json={
        "expected_revision": project["revision"], "year_from": 2020, "year_to": 2026, "max_papers": 10,
    })
    assert started.status_code == 202, started.text
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = c.get(f"/api/jobs/{started.json()['job_id']}").json()
        if job["status"] in {"completed", "partial", "failed", "canceled"}:
            assert job["status"] in {"completed", "partial"}, job
            break
        time.sleep(0.01)
    return project["project_id"]


def titles(payload):
    return {row["title"] for row in payload["items"]}


# --------------------------------------------------------------------- §5 错误语义


def test_401_and_403_become_auth_errors():
    for status in (401, 403):
        err = classify_http_error("semantic_scholar", status, "unauthorized")
        assert isinstance(err, AuthError), f"HTTP {status} was not an auth error"
        assert err.kind is SourceErrorKind.AUTH
        assert retrieval_status_for_exception(err) is RetrievalStatus.AUTH_ERROR


def test_other_4xx_keeps_its_own_bucket():
    contract = classify_http_error("semantic_scholar", 400, "bad request")
    assert contract.kind is SourceErrorKind.CONTRACT
    assert retrieval_status_for_exception(contract) is RetrievalStatus.PROVIDER_ERROR
    assert classify_http_error("semantic_scholar", 404).kind is SourceErrorKind.NOT_FOUND
    assert classify_http_error("semantic_scholar", 429).kind is SourceErrorKind.RATE_LIMITED


def test_auth_error_status_is_not_treated_as_usable():
    result = RetrievalResult(papers=[Paper("10.1/a", "t")], provider="s2",
                             status=RetrievalStatus.AUTH_ERROR)
    assert result.failed and not result.ok and not result.complete
    assert result.to_dict()["status"] == "auth_error"


def test_cached_is_a_modifier_on_success_not_a_failure():
    cached = RetrievalResult(
        papers=[Paper("10.1/a", "cached paper")], provider="crossref",
        status=RetrievalStatus.SUCCESS,
        request_stats=RequestStats(cache_hits=3, requests=0),
    )
    assert cached.cached is True
    assert cached.ok is True and cached.failed is False
    assert cached.to_dict()["cached"] is True

    live = RetrievalResult(
        papers=[Paper("10.1/b", "live paper")], provider="crossref",
        status=RetrievalStatus.SUCCESS,
        request_stats=RequestStats(cache_hits=1, requests=1),
    )
    assert live.cached is False
    assert live.to_dict()["cached"] is False


# --------------------------------------------------------------------- §7 CCF none


def test_ccf_rank_none_returns_only_records_outside_the_catalogue(tmp_path):
    with client(tmp_path) as c:
        pid = searched_project(c)
        every = c.get(f"/api/projects/{pid}/papers").json()
        outside = c.get(f"/api/projects/{pid}/papers?ccf_rank=none").json()
        ranked_a = c.get(f"/api/projects/{pid}/papers?ccf_rank=A").json()

    assert every["total"] == 2
    assert titles(every) == {"Ranked venue paper", "Unranked venue paper"}
    assert titles(outside) == {"Unranked venue paper"}
    assert titles(ranked_a) == {"Ranked venue paper"}


def test_ccf_filter_does_not_change_relevance_scores(tmp_path):
    with client(tmp_path) as c:
        pid = searched_project(c)
        every = c.get(f"/api/projects/{pid}/papers").json()
        outside = c.get(f"/api/projects/{pid}/papers?ccf_rank=none").json()

    scores = {row["title"]: row["relevance_score"] for row in every["items"]}
    assert outside["items"], "expected the unranked record to survive the filter"
    for row in outside["items"]:
        assert row["relevance_score"] == pytest.approx(scores[row["title"]]), \
            "CCF filtering must not re-score records"


def test_unknown_ccf_rank_is_rejected(tmp_path):
    with client(tmp_path) as c:
        pid = searched_project(c)
        assert c.get(f"/api/projects/{pid}/papers?ccf_rank=D").status_code == 422
