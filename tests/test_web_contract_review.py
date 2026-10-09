"""Independent regressions for the local Web/backend boundary."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest
import requests
from fastapi.testclient import TestClient

from litsearch.filters import RelevanceFilter, corpus_hash
from litsearch.models import Paper
from litsearch.persistence import state_to_dict
from litsearch.prisma import ScreeningDecision
from litsearch.search import ReviewState
from litsearch.snowball import SnowballResult
from litsearch.stop_reasons import get_http_budget
from litsearch.web.api import create_app
from litsearch.web.dto import paper_key
from litsearch.web.jobs import JobManager
from litsearch.web.repository import ProjectRepository


def seed_state(topic="plants", pid="10.1000/fixture"):
    state = ReviewState(topic, topic)
    paper = Paper(id=pid, doi=pid, title="Independent fixture", year=2024)
    state.search_papers = [paper]
    state.prisma.add_papers([paper])
    return state


def noop_factory(_demo):
    return SimpleNamespace(sources=SimpleNamespace())


def join_job(job):
    job.thread.join(timeout=5)
    assert not job.thread.is_alive(), "fixture task did not finish"
    assert job.payload["status"] in {"completed", "partial", "canceled", "failed"}


def test_rejected_full_text_request_cannot_change_live_state_without_revision(tmp_path):
    app = create_app(data_dir=tmp_path, workflow_factory=noop_factory)
    state = seed_state()
    paper = state.search_papers[0]
    state.prisma.screen_paper(paper.id, ScreeningDecision.ACCEPT)
    project = app.state.repository.create("review", state)
    before = state_to_dict(project.state)
    before.pop("saved_at")
    with TestClient(app) as client:
        response = client.post(f"/api/projects/{project.project_id}/screening-decisions", json={
            "expected_revision": project.revision, "paper_key": paper_key(paper),
            "stage": "full_text", "decision": "reject", "read_confirmed": True, "reason": "",
        })
        assert response.status_code == 422
    after = state_to_dict(project.state)
    after.pop("saved_at")
    assert after == before, "an invalid request applied a retrieval fact before validation"
    assert project.revision == 1


def test_invalid_project_envelope_is_preserved_without_blocking_startup(tmp_path):
    projects = tmp_path / "projects"
    projects.mkdir()
    corrupt = projects / "corrupt.json"
    raw = json.dumps({"project_id": "../outside", "state": state_to_dict(seed_state())})
    corrupt.write_text(raw, encoding="utf-8")
    app = create_app(data_dir=tmp_path, workflow_factory=noop_factory)
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["unreadable_projects"] == 1
        assert client.get("/api/projects").json()["total"] == 0
    assert corrupt.read_text(encoding="utf-8") == raw


@pytest.mark.parametrize("payload", [{"job_id": "../outside"}, ["not an envelope"]])
def test_invalid_job_envelope_is_preserved_without_blocking_startup(tmp_path, payload):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    corrupt = jobs / "corrupt.json"
    raw = json.dumps(payload)
    corrupt.write_text(raw, encoding="utf-8")
    app = create_app(data_dir=tmp_path, workflow_factory=noop_factory)
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert app.state.jobs.jobs == {}
    assert corrupt.read_text(encoding="utf-8") == raw


def test_download_counts_every_transport_in_http_budget(tmp_path, monkeypatch):
    body = b"%PDF-1.4\nfixture\n%%EOF\n"

    class Response:
        status_code = 200
        headers = {"content-length": str(len(body))}

        def iter_content(self, chunk_size):
            yield body

        def close(self):
            pass

    calls = []

    def request(_session, method, url, **kwargs):
        calls.append(url)
        return Response()

    monkeypatch.setattr(requests.Session, "request", request)
    monkeypatch.setattr("litsearch.downloader.check_url_safety", lambda _url: "")
    monkeypatch.setattr("litsearch.downloader.PaperDownloader._candidate_urls",
                        lambda _self, _paper: iter(["https://fixture.example/paper.pdf"]))
    app = create_app(data_dir=tmp_path, workflow_factory=noop_factory)
    state = seed_state()
    paper = state.search_papers[0]
    project = app.state.repository.create("download", state)
    with TestClient(app) as client:
        response = client.post(f"/api/projects/{project.project_id}/downloads", json={
            "expected_revision": project.revision, "paper_keys": [paper_key(paper)], "http_budget": 5,
        })
        assert response.status_code == 202
        job = app.state.jobs.get(response.json()["job_id"])
        join_job(job)
        payload = client.get(f"/api/jobs/{job.payload['job_id']}").json()
        assert payload["status"] == "completed"
        assert len(calls) == 1
        assert payload["http_budget"]["requests"] == len(calls)
        assert payload["http_budget"]["transport_attempts"] == len(calls)
        assert payload["http_budget"]["by_source"]["fulltext"]["requests"] == 1


def test_successful_new_task_does_not_inherit_old_snowball_failure(tmp_path):
    repo = ProjectRepository(tmp_path)
    state = seed_state()
    state.snowball_result = SnowballResult(stop_reason="api_failure", completed=False)
    project = repo.create("separate task", state)
    jobs = JobManager(repo, noop_factory)

    def operation(_workflow, current, _guard, _checkpoint):
        current.stop_reason = ""
        return {"downloaded": 1}, None

    job = jobs.submit(project, "downloads", project.revision, operation)
    join_job(job)
    assert job.payload["status"] == "completed"
    assert job.payload["stop_reason"] == ""
    assert project.state.snowball_result.stop_reason == "api_failure"


def test_global_http_lock_keeps_projects_separate_and_queued_cancel_never_executes(tmp_path):
    first_repo, second_repo = ProjectRepository(tmp_path / "first"), ProjectRepository(tmp_path / "second")
    first = first_repo.create("first", seed_state())
    canceled = second_repo.create("canceled", seed_state(pid="10.1000/canceled"))
    third = second_repo.create("third", seed_state(pid="10.1000/third"))
    started, release, third_started = threading.Event(), threading.Event(), threading.Event()
    executed_canceled = []
    first_jobs, second_jobs = JobManager(first_repo, noop_factory), JobManager(second_repo, noop_factory)

    def run_first(_workflow, _state, _guard, _checkpoint):
        for _ in range(3):
            get_http_budget().note_request("fixture_first")
        started.set()
        assert release.wait(timeout=5)
        return {}, None

    def run_canceled(*_args):
        executed_canceled.append(True)
        return {}, None

    def run_third(_workflow, _state, _guard, _checkpoint):
        third_started.set()
        get_http_budget().note_request("fixture_third")
        return {}, None

    first_job = first_jobs.submit(first, "search", first.revision, run_first)
    try:
        assert started.wait(timeout=5)
        canceled_job = second_jobs.submit(canceled, "search", canceled.revision, run_canceled)
        third_job = second_jobs.submit(third, "search", third.revision, run_third)
        assert not third_started.wait(timeout=0.1), "another app bypassed the global retrieval lock"
        second_jobs.cancel(canceled_job)
        join_job(canceled_job)
        assert canceled_job.payload["status"] == "canceled"
        assert canceled_job.payload["http_budget"]["requests"] == 0
        assert executed_canceled == []
    finally:
        release.set()
    join_job(first_job)
    join_job(third_job)
    assert first_job.payload["http_budget"]["requests"] == 3
    assert first_job.payload["http_budget"]["by_source"] == {
        "fixture_first": {"requests": 3, "retries": 0, "rate_limited": 0, "errors": 0}}
    assert third_job.payload["http_budget"]["requests"] == 1
    assert set(third_job.payload["http_budget"]["by_source"]) == {"fixture_third"}


def test_cross_project_keys_and_stale_tab_revision_are_rejected(tmp_path):
    app = create_app(data_dir=tmp_path, workflow_factory=noop_factory)
    first = app.state.repository.create("first", seed_state(pid="10.1000/first/a"))
    second = app.state.repository.create("second", seed_state(pid="10.1000/second/a"))
    first_paper = first.state.search_papers[0]
    with TestClient(app) as client:
        route = f"/api/projects/{second.project_id}/papers/{paper_key(first_paper)}"
        assert client.get(route).status_code == 404
        assert client.post(f"/api/projects/{second.project_id}/screening-decisions", json={
            "expected_revision": second.revision, "paper_key": paper_key(first_paper), "decision": "accept",
        }).status_code == 404
        updated = client.post(f"/api/projects/{first.project_id}/save", json={
            "expected_revision": 1, "name": "first tab updated",
        })
        assert updated.status_code == 200
        stale = client.post(f"/api/projects/{first.project_id}/save", json={
            "expected_revision": 1, "name": "stale overwrite",
        })
        assert stale.status_code == 409
        assert client.get(f"/api/projects/{first.project_id}").json()["name"] == "first tab updated"
        key = paper_key(first_paper)
        assert len(key) == 64 and "/" not in key
        assert client.get(f"/api/projects/{first.project_id}/papers/{key}").status_code == 200


def test_import_refuses_future_schema_and_leaves_existing_project_unchanged(tmp_path):
    app = create_app(data_dir=tmp_path, workflow_factory=noop_factory)
    existing = app.state.repository.create("existing", seed_state())
    payload = state_to_dict(existing.state)
    payload["version"] += 1
    with TestClient(app) as client:
        response = client.post("/api/projects/import", json={"session": payload})
        assert response.status_code == 422
        assert response.json()["error"]["detail"][0]["code"] == "future_version"
        assert client.get("/api/projects").json()["total"] == 1
        assert client.get(f"/api/projects/{existing.project_id}").json()["revision"] == 1


def test_import_legacy_no_stop_reason_stays_unknown(tmp_path):
    app = create_app(data_dir=tmp_path, workflow_factory=noop_factory)
    with TestClient(app) as client:
        response = client.post("/api/projects/import", json={
            "session": {"version": 3, "topic": "legacy", "phase": "scoping"},
        })
        assert response.status_code == 201
        assert response.json()["stop_reason"] == ""
        assert response.json()["complete_for_retrieval"] is False
        assert response.json()["http_budget"] == {}


def test_screening_facts_redact_configured_secret_in_legacy_calibration(tmp_path, monkeypatch):
    secret = "synthetic-key-for-redaction-test"
    monkeypatch.setenv("S2_API_KEY", secret)
    state = seed_state(topic=secret)
    paper = state.search_papers[0]
    other = Paper(id="10.1000/negative", doi="10.1000/negative", title="Negative fixture")
    state.search_papers.append(other)
    state.prisma.add_papers([other])
    paper.relevance_score, other.relevance_score = 0.9, 0.1
    state.score_context_id = "ctx"
    for row in state.search_papers:
        row.score_context_id = "ctx"
    state.calibration = RelevanceFilter.calibrate_threshold(
        [paper], [other], query=secret, corpus_hash=corpus_hash(state.search_papers), score_context_id="ctx",
    )
    app = create_app(data_dir=tmp_path, workflow_factory=noop_factory)
    project = app.state.repository.create("legacy imported calibration", state)
    with TestClient(app) as client:
        response = client.post(f"/api/projects/{project.project_id}/screening-decisions", json={
            "expected_revision": 1, "paper_key": paper_key(paper), "decision": "accept",
        })
        assert response.status_code == 200
        assert secret not in response.text


def test_validation_details_redact_secret_even_if_used_as_an_unknown_field(tmp_path, monkeypatch):
    secret = "synthetic-key-for-validation-test"
    monkeypatch.setenv("OPENALEX_API_KEY", secret)
    app = create_app(data_dir=tmp_path, workflow_factory=noop_factory)
    with TestClient(app) as client:
        response = client.post("/api/settings", json={secret: "wrong field"})
        assert response.status_code == 422
        assert secret not in response.text


@pytest.mark.parametrize("pid", ["..", "../outside", "0" * 31, "G" * 32])
def test_project_paths_cannot_escape_repository(tmp_path, pid):
    app = create_app(data_dir=tmp_path, workflow_factory=noop_factory)
    with TestClient(app) as client:
        assert client.get(f"/api/projects/{pid}").status_code == 404
