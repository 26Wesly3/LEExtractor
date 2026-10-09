"""Offline acceptance tests for real HTTP contracts and isolated project jobs."""

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from litsearch.demo import DemoSource
from litsearch.downloader import PaperDownloader
from litsearch.models import Paper
from litsearch.persistence import state_to_dict
from litsearch.search import LiteratureReviewWorkflow, ReviewState
from litsearch.web import create_app
from litsearch.web.jobs import RequestGuard


class FakeSources(DemoSource):
    def __init__(self, entered=None, release=None):
        super().__init__()
        self.entered, self.release, self.cancel_check = entered, release, lambda: False

    def set_cancel_check(self, predicate):
        self.cancel_check = predicate

    def is_canceled(self):
        return self.cancel_check()

    def search_all_sources(self, query, **kwargs):
        if self.entered:
            self.entered.set()
        if self.release:
            while not self.release.wait(0.01):
                if self.is_canceled():
                    return []
        self.last_search_manifest = {"provider_counts": {"fake": 2}, "query": query}
        return [Paper("10.1234/a", "Machine learning plants", year=2024, source="fake"),
                Paper("10.1234/b", "Plant measurement", year=2023, source="fake")]


def client(tmp_path, sources=None, **kwargs):
    def factory(demo):
        return LiteratureReviewWorkflow(DemoSource() if demo else sources or FakeSources(),
                                        PaperDownloader(str(tmp_path / "downloads")))

    return TestClient(create_app(tmp_path, workflow_factory=factory, **kwargs))


def create(c, name="Research"):
    response = c.post("/api/projects", json={"name": name, "topic": "plant learning"})
    assert response.status_code == 201, response.text
    return response.json()


def wait(c, jid):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        row = c.get(f"/api/jobs/{jid}").json()
        if row["status"] in {"completed", "partial", "failed", "canceled"}:
            return row
        time.sleep(0.01)
    raise AssertionError("Local fake-source job did not stop")


def search(c, project):
    response = c.post(f"/api/projects/{project['project_id']}/search", json={
        "expected_revision": project["revision"], "year_from": 2020, "year_to": 2025,
        "max_papers": 10})
    assert response.status_code == 202, response.text
    return wait(c, response.json()["job_id"])


def test_project_search_isolation_pagination_and_score_context(tmp_path):
    with client(tmp_path) as c:
        first, second = create(c), create(c, "Untouched")
        job = search(c, first)
        assert job["status"] == "completed", job
        pid = first["project_id"]
        rows = c.get(f"/api/projects/{pid}/papers?page_size=1").json()
        assert rows["total"] == 2 and len(rows["items"]) == 1
        paper = rows["items"][0]
        assert len(paper["paper_key"]) == 64 and "/" not in paper["paper_key"]
        assert rows["score_context_id"] == paper["score_context_id"] != ""
        assert c.get(f"/api/projects/{pid}/papers/{paper['paper_key']}").json()["abstract"] is None
        assert c.get(f"/api/projects/{second['project_id']}/papers").json()["total"] == 0
        assert c.get(f"/api/projects/{pid}/facets").json()["providers"] == [{"value": "fake", "count": 2}]


def test_revision_conflict_prevents_overwrite(tmp_path):
    with client(tmp_path) as c:
        project = create(c)
        url = f"/api/projects/{project['project_id']}/save"
        assert c.post(url, json={"expected_revision": 1, "name": "Updated"}).status_code == 200
        response = c.post(url, json={"expected_revision": 1, "name": "Lost update"})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "revision_conflict"
        assert c.get(url.removesuffix("/save")).json()["name"] == "Updated"


def test_real_cancel_hook_busy_project_and_global_queue(tmp_path):
    entered, release = threading.Event(), threading.Event()
    with client(tmp_path, FakeSources(entered, release)) as c:
        first, second = create(c), create(c)
        body = {"expected_revision": 1}
        initial = c.post(f"/api/projects/{first['project_id']}/search", json=body).json()
        assert entered.wait(3)
        assert c.post(f"/api/projects/{first['project_id']}/search", json=body).status_code == 409
        queued = c.post(f"/api/projects/{second['project_id']}/search", json=body).json()
        assert queued["status"] == "queued"
        c.post(f"/api/jobs/{queued['job_id']}/cancel")
        assert wait(c, queued["job_id"])["status"] == "canceled"
        c.post(f"/api/jobs/{initial['job_id']}/cancel")
        canceled = wait(c, initial["job_id"])
        assert canceled["status"] == "canceled" and canceled["stop_reason"] == "canceled"
        release.set()


def test_restart_marks_unfinished_job_interrupted(tmp_path):
    tmp_path.joinpath("jobs").mkdir()
    jid = "a" * 32
    tmp_path.joinpath("jobs", jid + ".json").write_text(json.dumps({"job_id": jid, "status": "running",
        "project_id": "b" * 32, "kind": "search"}), encoding="utf-8")
    with client(tmp_path) as c:
        job = c.get(f"/api/jobs/{jid}").json()
        assert job["status"] == "partial" and job["stop_reason"] == "interrupted"
        assert json.loads(tmp_path.joinpath("jobs", jid + ".json").read_text(encoding="utf-8"))["status"] == "partial"


def test_synthetic_demo_expand_and_graph_canonical_relations(tmp_path):
    with client(tmp_path) as c:
        project = c.post("/api/demo/projects").json()
        assert project["demo"] and project["counts"]["records_in_corpus"] == 8
        pid = project["project_id"]
        assert c.post(f"/api/projects/{pid}/search", json={"expected_revision": 1}).status_code == 409
        job = c.post(f"/api/projects/{pid}/expand", json={"expected_revision": 1, "num_seeds": 1,
            "max_rounds": 1, "year_from": 2010, "year_to": 2026}).json()
        outcome = wait(c, job["job_id"])
        assert outcome["status"] in {"completed", "partial"}, outcome
        graph = c.get(f"/api/projects/{pid}/landscape").json()["graph"]
        assert "semantic" not in graph["edge_type_counts"]
        assert sum(graph["edge_type_counts"].values()) == len(graph["edges"])
        assert all("score_context_id" in node for node in graph["nodes"])
        assert c.get(f"/api/projects/{pid}/questions").status_code == 200


def test_review_maybe_actionable_fulltext_read_required_and_history(tmp_path):
    with client(tmp_path) as c:
        project = c.post("/api/demo/projects").json()
        pid = project["project_id"]
        paper = c.get(f"/api/projects/{pid}/papers").json()["items"][0]
        url = f"/api/projects/{pid}/screening-decisions"
        payload = {"expected_revision": 1, "paper_key": paper["paper_key"], "decision": "maybe"}
        assert c.post(url, json=payload).status_code == 200
        assert len(c.get(f"/api/projects/{pid}/review").json()["queue"]) == 8
        payload.update(expected_revision=2, decision="accept")
        assert c.post(url, json=payload).status_code == 200
        payload.update(expected_revision=3, stage="full_text")
        assert c.post(url, json=payload).status_code == 422
        payload["read_confirmed"] = True
        response = c.post(url, json=payload)
        assert response.status_code == 200, response.text
        detail = response.json()["paper"]
        assert detail["screening"]["full_text_retrieved"] and detail["screening"]["status"] == "final_included"
        assert len(detail["history"]) >= 3


def test_exports_controlled_download_and_session_roundtrip(tmp_path):
    with client(tmp_path) as c:
        project = c.post("/api/demo/projects").json()
        pid = project["project_id"]
        export = c.post(f"/api/projects/{pid}/exports", json={"format": "session"}).json()
        assert "path" not in export and export["download_url"].startswith("/api/files/")
        session = c.get(export["download_url"]).json()
        restored = c.post("/api/projects/import", json={"session": session}).json()
        assert restored["project_id"] != pid and restored["demo"]
        assert restored["counts"] == project["counts"]
        assert c.post(f"/api/projects/{pid}/exports", json={"format": "evidence_pack", "scope": "included"}).status_code == 422
        assert c.get("/api/files/%2E%2E%2F%2E%2E%2FREADME.md").status_code == 404


def test_secrets_never_echo_settings_errors_or_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("S2_API_KEY", "initial-secret-1234")
    with client(tmp_path) as c:
        response = c.post("/api/settings", json={"s2_api_key": "new-secret-5678"})
        assert response.status_code == 200 and "new-secret" not in response.text
        assert response.json()["providers"]["semantic_scholar"]["configured"]
        invalid = c.post("/api/settings", json={"s2_api_key": "new-secret-5678", "unknown": "secret"})
        assert invalid.status_code == 422 and "new-secret" not in invalid.text
        state = ReviewState("plants", "plants")
        state.search_manifest = {"api_key": "new-secret-5678", "error": "C:/private/home/file.txt new-secret-5678"}
        project = c.post("/api/projects/import", json={"session": state_to_dict(state)}).json()
        assert "new-secret" not in json.dumps(project)
        assert "C:/private" not in json.dumps(project)


def test_same_origin_access_and_invalid_years(tmp_path):
    with client(tmp_path) as c:
        project = create(c)
        assert c.get("/api/settings", headers={"Origin": "https://evil.example"}).status_code == 403
        assert c.get("/api/health", headers={"Host": "evil.example"}).status_code == 403
        assert c.get("/api/settings", headers={"Origin": "http://testserver"}).status_code == 200
        response = c.post(f"/api/projects/{project['project_id']}/search", json={"expected_revision": 1,
            "year_from": 2025, "year_to": 2020})
        assert response.status_code == 422


def test_invalid_import_does_not_create_project(tmp_path):
    with client(tmp_path) as c:
        response = c.post("/api/projects/import", json={"session": {"version": 999}})
        assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_session"
        assert c.get("/api/projects").json()["total"] == 0


def test_spa_history_fallback_and_missing_asset(tmp_path):
    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text("<h1>LEExtractor</h1>")
    with client(tmp_path, static_dir=static) as c:
        assert "LEExtractor" in c.get("/projects/any/results").text
        assert c.get("/assets/missing.js").status_code == 404
        assert c.get("/api/unknown").status_code == 404


def test_http_guard_caps_actual_transport_and_cancellation():
    from litsearch.web.jobs import BudgetExceeded

    event = threading.Event()
    guard = RequestGuard(event, 2)
    guard.check()
    guard.check()
    try:
        guard.check()
        raise AssertionError("budget guard allowed a third transport attempt")
    except BudgetExceeded:
        assert guard.exhausted and guard.issued == 2
    event.set()
    from litsearch.sources import RetrievalCanceled

    try:
        guard.check()
        raise AssertionError("cancel guard allowed a transport attempt")
    except RetrievalCanceled:
        pass


def test_similar_uses_explicit_seed_and_preserves_search_corpus(tmp_path):
    class ObservedDemo(DemoSource):
        def __init__(self):
            super().__init__()
            self.citation_calls = []

        def get_citations(self, paper, limit=100):
            self.citation_calls.append(self._key(paper))
            return super().get_citations(paper, limit)

    sources = ObservedDemo()

    def factory(_demo):
        return LiteratureReviewWorkflow(sources, PaperDownloader(str(tmp_path / "downloads")))

    with TestClient(create_app(tmp_path, workflow_factory=factory)) as c:
        project = c.post("/api/demo/projects").json()
        pid = project["project_id"]
        papers = c.get(f"/api/projects/{pid}/papers").json()["items"]
        selected = next(p for p in papers if p["canonical_id"] == "demo:6")
        response = c.post(f"/api/projects/{pid}/similar", json={"expected_revision": 1,
            "seed_keys": [selected["paper_key"]], "num_seeds": 1, "top_k": 2})
        assert response.status_code == 202, response.text
        assert wait(c, response.json()["job_id"])["status"] == "completed"
        assert sources.citation_calls[0] == "demo:6"
        assert c.get(f"/api/projects/{pid}/papers").json()["total"] >= 8
        assert len(c.app.state.repository.get(pid).state.search_papers) == 8


def test_job_total_http_limit_stops_transport_and_saves_partial_result(tmp_path):
    from litsearch.stop_reasons import get_http_budget
    from litsearch.web.jobs import BudgetExceeded

    class Provider:
        def __init__(self):
            self.calls = 0

        def set_request_budget(self, _limit):
            pass

        def _transport_request(self, _method, _url):
            self.calls += 1
            get_http_budget().note_request("fixture", 200)

    class BudgetSources(FakeSources):
        def __init__(self):
            super().__init__()
            self.oa = Provider()

        def search_all_sources(self, query, **kwargs):
            for _ in range(10):
                try:
                    self.oa._transport_request("GET", "https://fixture.invalid/papers")
                except BudgetExceeded:
                    break
            return super().search_all_sources(query, **kwargs)

    sources = BudgetSources()
    with client(tmp_path, sources) as c:
        project = create(c)
        pid = project["project_id"]
        response = c.post(f"/api/projects/{pid}/search", json={"expected_revision": 1, "http_budget": 2})
        result = wait(c, response.json()["job_id"])
        assert result["status"] == "partial" and result["stop_reason"] == "budget_exhausted"
        assert sources.oa.calls == result["http_budget"]["transport_attempts"] == 2
        assert result["http_budget"]["requests"] == 2
        persisted = c.get(f"/api/projects/{pid}").json()
        assert persisted["stop_reason"] == "budget_exhausted" and persisted["counts"]["records_in_corpus"] == 2


def test_settings_non_object_json_does_not_block_startup(tmp_path):
    (tmp_path / "settings.json").write_text("null", encoding="utf-8")
    with client(tmp_path) as c:
        assert c.get("/api/health").json()["status"] == "ok"


@pytest.mark.parametrize("signal", ["budget", "canceled", "timeout"])
def test_control_signals_do_not_fake_retries_or_sleep(signal, monkeypatch):
    from litsearch.sources import (
        OpenAlexSource,
        RetrievalBudgetExceeded,
        RetrievalCanceled,
        RetrievalTimeout,
    )
    from litsearch.stop_reasons import http_budget_snapshot, reset_http_budget
    from litsearch.web.jobs import BudgetExceeded

    reset_http_budget()
    provider = OpenAlexSource(cache=None)
    control = {"budget": BudgetExceeded, "canceled": RetrievalCanceled, "timeout": RetrievalTimeout}[signal]
    assert issubclass(BudgetExceeded, RetrievalBudgetExceeded)
    calls = []

    def interrupted(_method, _url, **_kwargs):
        calls.append(signal)
        raise control("Local control signal; no HTTP transport was issued")

    def unexpected_sleep(_seconds):
        raise AssertionError("A local control signal must not sleep or retry")

    monkeypatch.setattr(provider, "_transport_request", interrupted)
    monkeypatch.setattr("litsearch.sources.time.sleep", unexpected_sleep)
    with pytest.raises(control):
        provider._request_with_retries("GET", "https://fixture.invalid", deadline=time.monotonic() + 30)
    snapshot = http_budget_snapshot()
    assert calls == [signal]
    assert snapshot["requests"] == snapshot["retries"] == snapshot["errors"] == 0
