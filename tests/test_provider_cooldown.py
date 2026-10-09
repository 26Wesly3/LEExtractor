"""Rate limits must leave other databases and real request budgets available."""

import json
import threading
import time
from types import SimpleNamespace

import pytest
import requests
from fastapi.testclient import TestClient

from litsearch.cache import Cache
from litsearch.downloader import PaperDownloader
from litsearch.intent import database_topic, plan_payload
from litsearch.search import LiteratureReviewWorkflow, ReviewState, ranking_query
from litsearch.sources import OpenAlexSource, RateLimitedError, SourceManager
from litsearch.stop_reasons import get_http_budget, reset_http_budget
from litsearch.web import create_app
from litsearch.web.dto import public
from litsearch.web.jobs import RequestGuard


def response(status, data=None, retry_after=None):
    result = requests.Response()
    result.status_code = status
    result.url = "https://api.example.org/works"
    result._content = json.dumps(data or {}).encode()
    if retry_after:
        result.headers["Retry-After"] = retry_after
    return result


def test_cooldown_is_shared_and_consumes_no_extra_budget(monkeypatch):
    calls = []
    monkeypatch.setattr(requests.Session, "request", lambda *a, **k: calls.append(a) or response(429))
    reset_http_budget()
    first = OpenAlexSource()
    assert first._transport_request("GET", "https://api.openalex.org/works").status_code == 429
    second = OpenAlexSource()
    guard = RequestGuard(threading.Event(), 1)
    guard.bind(SimpleNamespace(oa=second))
    with pytest.raises(RateLimitedError):
        second._transport_request("GET", "https://api.openalex.org/works")
    assert len(calls) == get_http_budget().snapshot()["requests"] == 1
    assert guard.issued == 0


@pytest.mark.parametrize("header,expected", [(None, 60), ("99999", 300), ("0", 1), ("invalid", 60)])
def test_retry_after_is_bounded_and_expires(monkeypatch, header, expected):
    tick = [10.0]
    monkeypatch.setattr("litsearch.sources.time.monotonic", lambda: tick[0])
    source = OpenAlexSource()
    source._note_rate_limit(response(429, retry_after=header))
    assert source.cooldown_remaining() == expected
    tick[0] += expected
    source.ensure_available()
    assert source.cooldown_remaining() == 0


def test_changed_credentials_do_not_inherit_another_quotas_cooldown(monkeypatch):
    monkeypatch.setattr("litsearch.sources.openalex_api_key", lambda: "test-key-a")
    source = OpenAlexSource()
    source._note_rate_limit(response(429))
    assert source.cooldown_remaining() > 0
    monkeypatch.setattr("litsearch.sources.openalex_api_key", lambda: "test-key-b")
    source.ensure_available()


def test_public_links_survive_disk_path_redaction():
    assert public("https://doi.org/10.1234/example") == "https://doi.org/10.1234/example"
    assert public("http://example.org/paper") == "http://example.org/paper"
    assert public("failed at C:\\Users\\Someone\\paper.pdf") == "failed at [local file]"


@pytest.mark.parametrize("direction", ["深度学习", "计算机视觉方向的多模态情绪识别"])
def test_topic_alias_preserves_original_intent_and_ranking(direction):
    plan = plan_payload("深度学习", direction, 2020, 2026)
    assert plan["topic"] == "深度学习" and plan["direction"] == direction
    assert plan["query_normalization"]["keywords"] == "deep learning"
    assert plan["queries"]["crossref"]["query"] == "deep learning"
    assert ranking_query(ReviewState("深度学习", direction)) == "deep learning"
    assert database_topic("尚未定义的主题句子") == "尚未定义的主题句子"


def test_real_web_workflow_keeps_crossref_results_when_other_sources_limit(tmp_path, monkeypatch):
    calls = []
    def transport(session, method, url, **kwargs):
        calls.append((url, kwargs.get("params", {})))
        if "api.crossref.org" in url:
            return response(200, {"message": {"items": [{
                "DOI": "10.1234/real-contract", "title": ["Deep learning evidence"],
                "published": {"date-parts": [[2024]]}, "URL": "https://doi.org/10.1234/real-contract",
            }], "total-results": 1}})
        return response(429)
    monkeypatch.setattr(requests.Session, "request", transport)
    monkeypatch.setattr("litsearch.sources.SemanticScholarSource._pace", lambda self: None)
    def factory(demo):
        return LiteratureReviewWorkflow(SourceManager(Cache(str(tmp_path / "cache.db"))), PaperDownloader(str(tmp_path / "pdfs")))
    with TestClient(create_app(tmp_path / "projects", workflow_factory=factory)) as client:
        project = client.post("/api/projects", json={"topic": "深度学习", "research_direction": "多模态情绪识别"}).json()
        pid = project["project_id"]
        for providers in ([], ["unknown"], ["crossref", "crossref"]):
            assert client.post(f"/api/projects/{pid}/search", json={"expected_revision": project["revision"], "providers": providers}).status_code == 422
        started = client.post(f"/api/projects/{pid}/search", json={"expected_revision": project["revision"], "max_papers": 5, "year_from": 2020, "year_to": 2026})
        assert started.status_code == 202, started.text
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            job = client.get("/api/jobs/" + started.json()["job_id"]).json()
            if job["status"] in {"completed", "partial", "failed"}:
                break
            time.sleep(.01)
        assert job["status"] == "partial", job
        rows = client.get(f"/api/projects/{pid}/papers").json()
        assert rows["total"] == 1
        assert rows["items"][0]["url"] == "https://doi.org/10.1234/real-contract"
        manifest = client.get(f"/api/projects/{pid}").json()["search_manifest"]
        assert manifest["selected_providers"] == ["semantic_scholar", "openalex", "arxiv", "crossref"]
        assert [r["status"] for r in manifest["provider_results"]][:3] == ["rate_limited"] * 3
        assert len(calls) == 4
        assert manifest["crossref_role"] == "keyword_search_and_metadata_resolution"
        assert calls[-1][1]["query"] == "deep learning"
