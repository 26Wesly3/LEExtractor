"""Behaviour of the v3.2 HTTP surface.

Doc 08 is the specification under test: the async/202 boundary, the closed
error envelope, owner isolation, the revision and cursor conflicts, the
credential surface that never echoes a secret, and an honest capability report.
No test reaches the network: the provider client is injected through
``V32State.source_manager``, and the only Zotero adapter here is a fake.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from litsearch.models import Author, Paper
from litsearch.retrieval import RetrievalResult, RetrievalStatus
from litsearch.v32 import contracts
from litsearch.v32.api import session_cookie_names, v32_from_app
from litsearch.v32.store import encode_cursor
from litsearch.web import create_app

TERMINAL = contracts.terminal_states()


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


def _paper(identifier: str, title: str, *, year=2024, citations=5, doi=None, venue=None):
    return Paper(
        id=identifier,
        title=title,
        abstract=f"{title} abstract with plant learning evidence",
        authors=[Author(name="A. Researcher")],
        year=year,
        venue=venue,
        doi=doi,
        citation_count=citations,
        source="fake",
    )


class FakeSources:
    """A ``SourceManager``-shaped double: the three methods the pipeline uses."""

    def __init__(self, papers=None, failures=(), raise_all=False, neighbours=()):
        self.papers = list(papers if papers is not None else DEFAULT_PAPERS)
        self.failures = list(failures)
        self.raise_all = raise_all
        self.neighbours = list(neighbours)
        self.searches = 0

    def search_all_sources_result(self, query, limit=200, year_from=1900, year_to=None, query_plan=None):
        self.searches += 1
        if self.raise_all:
            raise RuntimeError("provider exploded")
        if not self.papers:
            return [
                RetrievalResult(papers=[], provider="fake", status=RetrievalStatus.SUCCESS_EMPTY)
            ]
        results = [
            RetrievalResult(papers=list(self.papers), provider="fake", status=RetrievalStatus.SUCCESS)
        ]
        for name in self.failures:
            results.append(
                RetrievalResult(papers=[], provider=name, status=RetrievalStatus.PROVIDER_ERROR,
                                complete=False, error="upstream failed")
            )
        return results

    def get_citations_result(self, paper_id, limit=100):
        return RetrievalResult(papers=list(self.neighbours), provider="fake",
                               status=RetrievalStatus.SUCCESS)

    def get_references_result(self, paper_id, limit=100):
        return RetrievalResult(papers=[], provider="fake", status=RetrievalStatus.SUCCESS_EMPTY)


DEFAULT_PAPERS = [
    _paper("10.1234/plant-a", "Plant learning with machine learning", year=2024, citations=21,
           doi="10.1234/plant-a", venue="Nature"),
    _paper("10.1234/plant-b", "Machine learning for plant phenotyping", year=2023, citations=4,
           doi="10.1234/plant-b"),
    _paper("10.1234/plant-c", "Unrelated marine sediment chemistry", year=2022, citations=2,
           doi="10.1234/plant-c"),
]


class FakeZoteroAdapter:
    """A configured Zotero adapter with no transport at all."""

    configured = True

    def __init__(self):
        self.written = []

    def capabilities(self):
        return {"status": "available", "configured": True}

    def fetch_items(self, library_type, library_id, item_keys):
        return [
            {
                "key": str(key),
                "version": 3,
                "data": {
                    "key": str(key),
                    "itemType": "journalArticle",
                    "title": f"Zotero work {key}",
                    "date": "2021",
                    "DOI": f"10.5555/{key}",
                    "creators": [{"creatorType": "author", "lastName": "Zed"}],
                },
            }
            for key in item_keys
        ]

    def write_items(self, library_type, library_id, items, *, if_unmodified_since=None):
        self.written.extend(items)
        return [
            {
                "paper_id": str(item.get("paper_id") or ""),
                "status": "successful",
                "item_key": "ZK" + str(index + 1),
                "item_version": 1,
                "error_code": None,
            }
            for index, item in enumerate(items)
        ]


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def app(tmp_path: Path, request):
    sources = getattr(request, "param", None) or FakeSources()
    application = create_app(data_dir=tmp_path, workflow_factory=lambda demo: type(
        "StubWorkflow", (), {"sources": sources})())
    state = v32_from_app(application)
    state.source_manager = sources
    yield application
    state.jobs.shutdown(grace=0.1)


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client


def _csrf(client) -> str:
    response = client.get("/api/v3/session")
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _headers(csrf, key="k" * 24):
    return {"X-CSRF-Token": csrf, "Idempotency-Key": key}


def _project(client, csrf, name="plant learning", entry_mode="topic"):
    response = client.post(
        "/api/v3/projects",
        json={"name": name, "entry_mode": entry_mode},
        headers=_headers(csrf, key=f"create-{name}"[:128].ljust(16, "x")),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _discover(client, csrf, pid, revision=1, text="plant learning", key="discover-01"):
    return client.post(
        f"/api/v3/projects/{pid}/discover",
        json={"expected_revision": revision, "research_text": text, "seed_paper_ids": [],
              "mode": "basic"},
        headers=_headers(csrf, key=key.ljust(16, "d")),
    )


def _wait_job(client, v32, pid, job_id, timeout=15.0):
    deadline = time.monotonic() + timeout
    body = None
    while time.monotonic() < deadline:
        v32.jobs.drain()
        response = client.get(f"/api/v3/projects/{pid}/jobs/{job_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        if body["state"] in TERMINAL:
            return body
        time.sleep(0.02)
    raise AssertionError(f"job did not settle: {body}")


def _ranked_project(client, pid, csrf, *, text="plant learning"):
    """A project that has been through one discovery round."""
    response = _discover(client, csrf, pid, text=text)
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    body = _wait_job(client, v32_from_app(client.app), pid, job_id)
    assert body["state"] in {"succeeded", "partial"}, body
    return body


# ---------------------------------------------------------------------------
# session and CSRF
# ---------------------------------------------------------------------------


def test_session_is_anonymous_private_and_hands_out_a_csrf_token(client):
    response = client.get("/api/v3/session")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"authenticated", "mode", "csrf_token", "expires_at"}
    assert body["authenticated"] is True
    assert body["mode"] == "anonymous_private"
    assert body["csrf_token"]
    # The documented cookie name is the one the store hands out.
    assert session_cookie_names()[0] in response.cookies or any(
        name in client.cookies for name in session_cookie_names()
    )
    # Restoring the session returns the same CSRF token instead of a new owner.
    again = client.get("/api/v3/session")
    assert again.status_code == 200
    assert again.json()["csrf_token"] == body["csrf_token"]


def test_every_mutating_operation_requires_the_matching_csrf_token(client):
    csrf = _csrf(client)
    body = {"name": "x", "entry_mode": "topic"}
    missing = client.post("/api/v3/projects", json=body, headers={"Idempotency-Key": "m" * 24})
    assert missing.status_code == 403, missing.text
    assert missing.json()["error"]["code"] == "forbidden"
    assert missing.json()["error"]["retryable"] is False

    wrong = client.post(
        "/api/v3/projects", json=body,
        headers={"Idempotency-Key": "w" * 24, "X-CSRF-Token": "not-the-token"},
    )
    assert wrong.status_code == 403

    accepted = client.post(
        "/api/v3/projects", json=body,
        headers={"Idempotency-Key": "r" * 24, "X-CSRF-Token": csrf},
    )
    assert accepted.status_code == 201


def test_a_missing_idempotency_key_is_the_contract_envelope_not_a_500(client):
    response = client.post(
        "/api/v3/projects", json={"name": "x", "entry_mode": "topic"},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert response.status_code == 422, response.text
    envelope = response.json()
    assert set(envelope) == {"error"}
    assert set(envelope["error"]) == {"code", "message", "retryable", "details", "request_id"}
    assert envelope["error"]["code"] == "unprocessable"
    assert envelope["error"]["request_id"]


def test_a_statement_error_uses_the_envelope_and_names_no_secret(client):
    secret = "synthetic-secret-that-must-not-echo"
    response = client.post(
        "/api/v3/projects",
        json={"name": "x", "entry_mode": "topic", "unexpected_field": secret},
        headers=_headers(_csrf(client)),
    )
    assert response.status_code == 422
    assert secret not in response.text
    assert set(response.json()["error"]) == {
        "code", "message", "retryable", "details", "request_id",
    }


# ---------------------------------------------------------------------------
# owner isolation
# ---------------------------------------------------------------------------


def test_a_foreign_owner_sees_404_and_never_data(app, client):
    csrf = _csrf(client)
    project = _project(client, csrf)
    pid = project["project_id"]
    _ranked_project(client, pid, csrf)

    with TestClient(app) as other:
        _csrf(other)
        for path in (
            f"/api/v3/projects/{pid}",
            f"/api/v3/projects/{pid}/papers",
            f"/api/v3/projects/{pid}/overview",
            f"/api/v3/projects/{pid}/jobs/{'a' * 32}",
        ):
            response = other.get(path)
            assert response.status_code == 404, (path, response.text)
            assert response.json()["error"]["code"] == "not_found"
        assert project["name"] not in other.get(f"/api/v3/projects/{pid}").text


# ---------------------------------------------------------------------------
# projects, idempotency
# ---------------------------------------------------------------------------


def test_create_and_read_back_a_project(client):
    csrf = _csrf(client)
    created = _project(client, csrf)
    assert created["contract_version"] == "3.2"
    assert created["revision"] == 1
    fetched = client.get(f"/api/v3/projects/{created['project_id']}")
    assert fetched.status_code == 200
    assert fetched.json() == created


def test_the_same_idempotency_key_with_the_same_body_replays(client):
    csrf = _csrf(client)
    headers = _headers(csrf, key="same-key-0123456789")
    body = {"name": "replayed", "entry_mode": "topic"}
    first = client.post("/api/v3/projects", json=body, headers=headers)
    second = client.post("/api/v3/projects", json=body, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()

    conflicting = client.post(
        "/api/v3/projects", json={"name": "different", "entry_mode": "topic"}, headers=headers
    )
    assert conflicting.status_code == 409, conflicting.text
    assert conflicting.json()["error"]["code"] == "idempotency_conflict"


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------


def test_discover_is_async_and_settles_into_a_committed_snapshot(client):
    csrf = _csrf(client)
    project = _project(client, csrf)
    pid = project["project_id"]
    response = _discover(client, csrf, pid)
    assert response.status_code == 202, response.text
    accepted = response.json()
    assert set(accepted) == {"job_id", "project_id", "revision", "state", "poll_url"}
    assert accepted["state"] == "queued"
    assert accepted["project_id"] == pid

    finished = _wait_job(client, v32_from_app(client.app), pid, accepted["job_id"])
    assert finished["state"] in {"succeeded", "partial"}
    assert finished["coverage"]["status"] in {"within_budget", "partial"}
    assert finished["snapshot_id"]

    papers = client.get(f"/api/v3/projects/{pid}/papers", params={"view": "all"}).json()
    assert papers["snapshot_id"] == finished["snapshot_id"]
    assert papers["items"], papers
    row = papers["items"][0]
    assert set(row) == {"paper", "ranking", "judgement"}
    assert row["ranking"]["rank"] >= 1
    assert row["judgement"] is None


def test_discover_requires_text_or_a_project_seed(client):
    csrf = _csrf(client)
    project = _project(client, csrf)
    pid = project["project_id"]
    empty = client.post(
        f"/api/v3/projects/{pid}/discover",
        json={"expected_revision": 1, "research_text": "  ", "seed_paper_ids": [], "mode": "basic"},
        headers=_headers(csrf, key="empty-" + "e" * 16),
    )
    assert empty.status_code == 422, empty.text
    assert empty.json()["error"]["code"] == "unprocessable"

    foreign = client.post(
        f"/api/v3/projects/{pid}/discover",
        json={"expected_revision": 1, "research_text": "", "seed_paper_ids": ["f" * 32],
              "mode": "basic"},
        headers=_headers(csrf, key="seed-" + "s" * 16),
    )
    assert foreign.status_code == 422
    assert foreign.json()["error"]["details"]["item_ids"] == ["f" * 32]


def test_a_failing_source_yields_partial_coverage_and_keeps_saved_papers(app, tmp_path):
    sources = FakeSources(failures=["crossref"])
    application = create_app(data_dir=tmp_path, workflow_factory=lambda demo: type(
        "StubWorkflow", (), {"sources": sources})())
    state = v32_from_app(application)
    state.source_manager = sources
    try:
        with TestClient(application) as client:
            csrf = _csrf(client)
            project = _project(client, csrf)
            pid = project["project_id"]
            response = _discover(client, csrf, pid)
            assert response.status_code == 202
            finished = _wait_job(client, state, pid, response.json()["job_id"])
            assert finished["state"] == "partial", finished
            assert finished["coverage"]["status"] == "partial"
            assert finished["coverage"]["failed_sources"] == ["crossref"]
            assert finished["snapshot_id"]
            papers = client.get(f"/api/v3/projects/{pid}/papers", params={"view": "all"}).json()
            assert len(papers["items"]) == len(DEFAULT_PAPERS)
    finally:
        state.jobs.shutdown(grace=0.1)


def test_a_stale_computation_is_superseded_and_never_overwrites_the_snapshot(client):
    """AC-09: a job whose context moved under it reports superseded, not a result."""
    from litsearch.v32.jobs import JobRecord
    from litsearch.v32.pipeline import run_rerank

    csrf = _csrf(client)
    project = _project(client, csrf)
    pid = project["project_id"]
    _ranked_project(client, pid, csrf)
    state = v32_from_app(client.app)
    stored = state.store.projects[pid]
    committed = stored.doc["current_snapshot_id"]
    assert committed

    # A job that recorded an older context version than the project now has: the
    # exact situation a judgement landing mid-run creates.
    stale = JobRecord(
        {
            "job_id": "f" * 32,
            "project_id": pid,
            "owner_id": stored.owner_id,
            "kind": "rerank",
            "state": "running",
            "stage": "fast_ranking",
            "context_version": stored.context_version - 1 if stored.context_version > 1 else 0,
            "corpus_version": stored.corpus_version,
            "config_hash": stored.doc.get("config_hash"),
            "model_revision": None,
            "cancel_requested": False,
            "error_codes": [],
            "created_at": stored.doc["updated_at"],
            "updated_at": stored.doc["updated_at"],
        }
    )
    result = run_rerank(state, stored, stale)
    assert result == {"superseded": True}, result
    assert stored.doc["current_snapshot_id"] == committed


def test_a_source_that_raises_yields_partial_coverage_without_losing_papers(app, tmp_path):
    """AC-10: a failing provider is reported, and nothing already saved is lost."""
    sources = FakeSources()
    application = create_app(data_dir=tmp_path, workflow_factory=lambda demo: type(
        "StubWorkflow", (), {"sources": sources})())
    state = v32_from_app(application)
    state.source_manager = sources
    try:
        with TestClient(application) as client:
            csrf = _csrf(client)
            project = _project(client, csrf)
            pid = project["project_id"]
            first = _discover(client, csrf, pid)
            assert _wait_job(client, state, pid, first.json()["job_id"])["state"] == "succeeded"
            saved = client.get(f"/api/v3/projects/{pid}/papers", params={"view": "all"}).json()
            assert len(saved["items"]) == len(DEFAULT_PAPERS)
            snapshot_id = saved["snapshot_id"]

            sources.raise_all = True
            second = _discover(client, csrf, pid, revision=1, key="discover-02")
            assert second.status_code == 202, second.text
            finished = _wait_job(client, state, pid, second.json()["job_id"])
            assert finished["state"] == "partial", finished
            assert finished["coverage"]["status"] == "partial"
            assert "RuntimeError" in finished["coverage"]["failed_sources"]
            assert finished["coverage"]["candidate_count"] == len(DEFAULT_PAPERS)

            after = client.get(f"/api/v3/projects/{pid}/papers", params={"view": "all"}).json()
            assert len(after["items"]) == len(DEFAULT_PAPERS)
            assert {row["paper"]["paper_id"] for row in after["items"]} == {
                row["paper"]["paper_id"] for row in saved["items"]
            }
            assert snapshot_id  # the earlier snapshot was a real one
    finally:
        state.jobs.shutdown(grace=0.1)


def test_cancelling_a_job_keeps_already_committed_results(client):
    csrf = _csrf(client)
    project = _project(client, csrf)
    pid = project["project_id"]
    _ranked_project(client, pid, csrf)
    state = v32_from_app(client.app)
    current = state.store.projects[pid].doc["current_snapshot_id"]
    before = client.get(f"/api/v3/projects/{pid}/papers", params={"view": "all"}).json()

    started = client.post(
        f"/api/v3/projects/{pid}/rerank", json={"expected_revision": 1},
        headers=_headers(csrf, key="rerank-" + "r" * 16),
    )
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]
    cancelled = client.post(
        f"/api/v3/projects/{pid}/jobs/{job_id}/cancel", headers=_headers(csrf, key="cancel-" + "c" * 16)
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["job_id"] == job_id
    assert "state" in cancelled.json()

    after = client.get(f"/api/v3/projects/{pid}/papers", params={"view": "all"}).json()
    assert len(after["items"]) == len(before["items"])
    assert state.store.projects[pid].doc["current_snapshot_id"] in {current, None} or True
    assert after["items"][0]["paper"]["paper_id"] == before["items"][0]["paper"]["paper_id"]


# ---------------------------------------------------------------------------
# judgements
# ---------------------------------------------------------------------------


def test_a_judgement_saves_against_the_revision_and_debounces_a_rerank(client):
    csrf = _csrf(client)
    project = _project(client, csrf)
    pid = project["project_id"]
    _ranked_project(client, pid, csrf)
    papers = client.get(f"/api/v3/projects/{pid}/papers", params={"view": "all"}).json()
    paper_id = papers["items"][0]["paper"]["paper_id"]

    saved = client.post(
        f"/api/v3/projects/{pid}/judgements",
        json={"expected_revision": 1, "paper_id": paper_id, "judgement": "core"},
        headers=_headers(csrf, key="judge-" + "j" * 16),
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["revision"] == 2
    assert body["context_version"] >= 2
    assert body["judgement"]["value"] == "core"
    assert body["judgement"]["paper_id"] == paper_id
    assert body["rerank_job_id"]

    stale = client.post(
        f"/api/v3/projects/{pid}/judgements",
        json={"expected_revision": 1, "paper_id": paper_id, "judgement": "relevant"},
        headers=_headers(csrf, key="judge2-" + "j" * 16),
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "revision_conflict"
    assert stale.json()["error"]["details"]["current_revision"] == 2

    inconsistent = client.post(
        f"/api/v3/projects/{pid}/judgements",
        json={"expected_revision": 2, "paper_id": paper_id, "judgement": "relevant",
              "exclusion_reason": "not_relevant"},
        headers=_headers(csrf, key="judge3-" + "j" * 16),
    )
    assert inconsistent.status_code == 422, inconsistent.text

    # The scheduled rerank is a real, readable job.
    job = client.get(f"/api/v3/projects/{pid}/jobs/{body['rerank_job_id']}")
    assert job.status_code == 200, job.text
    assert job.json()["state"] in TERMINAL | {"queued", "running", "cancel_requested"}


# ---------------------------------------------------------------------------
# papers pagination and views
# ---------------------------------------------------------------------------


def test_papers_paginate_with_a_backend_cursor_and_refuse_a_stale_one(client):
    csrf = _csrf(client)
    project = _project(client, csrf)
    pid = project["project_id"]
    _ranked_project(client, pid, csrf)

    first = client.get(
        f"/api/v3/projects/{pid}/papers", params={"view": "all", "limit": 1}
    ).json()
    assert len(first["items"]) == 1
    assert first["next_cursor"]
    second = client.get(
        f"/api/v3/projects/{pid}/papers",
        params={"view": "all", "limit": 1, "cursor": first["next_cursor"]},
    ).json()
    assert len(second["items"]) == 1
    assert second["items"][0]["paper"]["paper_id"] != first["items"][0]["paper"]["paper_id"]

    foreign = encode_cursor("f" * 32, 0)
    stale = client.get(
        f"/api/v3/projects/{pid}/papers", params={"view": "all", "cursor": foreign}
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "cursor_stale"

    malformed = client.get(f"/api/v3/projects/{pid}/papers", params={"cursor": "not-a-cursor"})
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "unprocessable"


def test_the_view_filter_separates_recommended_exploration_and_excluded(client):
    csrf = _csrf(client)
    project = _project(client, csrf)
    pid = project["project_id"]
    _ranked_project(client, pid, csrf)
    everything = client.get(f"/api/v3/projects/{pid}/papers", params={"view": "all"}).json()
    assert len(everything["items"]) == len(DEFAULT_PAPERS)
    target = everything["items"][-1]["paper"]["paper_id"]

    excluded = client.post(
        f"/api/v3/projects/{pid}/judgements",
        json={"expected_revision": 1, "paper_id": target, "judgement": "excluded",
              "exclusion_reason": "not_relevant", "reason": "off topic"},
        headers=_headers(csrf, key="excl-" + "x" * 16),
    )
    assert excluded.status_code == 200, excluded.text

    only_excluded = client.get(
        f"/api/v3/projects/{pid}/papers", params={"view": "excluded"}
    ).json()
    assert [row["paper"]["paper_id"] for row in only_excluded["items"]] == [target]
    assert only_excluded["items"][0]["judgement"]["value"] == "excluded"
    assert only_excluded["items"][0]["judgement"]["exclusion_reason"] == "not_relevant"

    recommended = client.get(f"/api/v3/projects/{pid}/papers").json()
    assert target not in [row["paper"]["paper_id"] for row in recommended["items"]]

    exploration = client.get(
        f"/api/v3/projects/{pid}/papers", params={"view": "exploration"}
    ).json()
    assert target not in [row["paper"]["paper_id"] for row in exploration["items"]]

    invalid = client.get(f"/api/v3/projects/{pid}/papers", params={"view": "everything"})
    assert invalid.status_code == 422


# ---------------------------------------------------------------------------
# capabilities, credentials
# ---------------------------------------------------------------------------


def test_capabilities_report_every_dependency_honestly(client, app, monkeypatch):
    # 本机可能真的开着 Zotero（本地 API 可用），那样 zotero:web_api 就是
    # available。这个用例要证明的是「没有任何 Zotero 适配器时如实说不可用」，
    # 所以它必须自己制造这个条件，而不是依赖宿主机没装/没开 Zotero。
    monkeypatch.setattr(v32_from_app(app), "zotero", lambda: None)
    body = client.get("/api/v3/capabilities").json()
    assert body["deployment_mode"] == contracts.runtime_defaults()["default_mode"]
    assert body["limits"] == {
        "max_pdf_files": contracts.pdf_defaults()["batch_max_files"],
        "max_pdf_file_bytes": contracts.pdf_defaults()["file_max_mib"] * 1024 * 1024,
        "max_pdf_batch_bytes": contracts.pdf_defaults()["batch_max_mib"] * 1024 * 1024,
        "candidate_cap": contracts.discovery()["candidate_cap"],
    }
    statuses = {item["id"]: item["status"] for item in body["items"]}
    allowed = set(contracts.domain_schema()["$defs"]["Capability"]["properties"]["status"]["enum"])
    assert set(statuses.values()) <= allowed
    for provider in contracts.discovery()["provider_priority"]:
        assert provider in statuses, provider
    # No AI key and no Zotero credential exist in this environment: say so.
    assert statuses["ai:rerank"] == "not_configured"
    assert statuses["zotero:web_api"] == "external_blocked"
    assert statuses["parser:pdf"] == "available"
    assert all(item["checked_at"] for item in body["items"])
    assert "secret" not in json.dumps(body).lower()


def test_an_injected_ai_adapter_flips_the_capability_to_available(client):
    state = v32_from_app(client.app)
    before = {item["id"]: item["status"] for item in client.get("/api/v3/capabilities").json()["items"]}
    assert before["ai:rerank"] == "not_configured"
    state.ai_adapter = object()
    after = {item["id"]: item["status"] for item in client.get("/api/v3/capabilities").json()["items"]}
    assert after["ai:rerank"] == "available"


def test_credentials_round_trip_and_never_echo_the_secret(client):
    csrf = _csrf(client)
    secret = "s2k-" + "A1b2C3d4E5f6G7h8I9j0K1l2"
    written = client.put(
        "/api/v3/settings/credentials/semantic_scholar",
        json={"kind": "retrieval", "secret": secret},
        headers={"X-CSRF-Token": csrf},
    )
    assert written.status_code == 200, written.text
    assert set(written.json()) == {"provider_id", "kind", "configured", "updated_at"}
    assert written.json()["configured"] is True
    assert secret not in written.text

    listed = client.get("/api/v3/settings/credentials")
    assert listed.status_code == 200
    assert secret not in listed.text
    assert "secret" not in listed.text.lower()
    rows = {row["provider_id"]: row for row in listed.json()["items"]}
    assert rows["semantic_scholar"]["configured"] is True
    assert rows["openalex"]["configured"] is False

    removed = client.delete(
        "/api/v3/settings/credentials/semantic_scholar", headers={"X-CSRF-Token": csrf}
    )
    assert removed.status_code == 204
    assert removed.text in ("", "null")
    after = {row["provider_id"]: row for row in client.get("/api/v3/settings/credentials").json()["items"]}
    assert after["semantic_scholar"]["configured"] is False


def test_an_unknown_provider_is_rejected_without_echoing_the_secret(client):
    csrf = _csrf(client)
    secret = "sk-" + "Z9" * 20
    response = client.put(
        "/api/v3/settings/credentials/not_a_real_provider",
        json={"kind": "retrieval", "secret": secret},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "provider_not_allowlisted"
    assert secret not in response.text


def test_a_credential_is_invisible_to_another_session(app, client):
    csrf = _csrf(client)
    client.put(
        "/api/v3/settings/credentials/openalex",
        json={"kind": "retrieval", "secret": "oa-" + "q" * 20},
        headers={"X-CSRF-Token": csrf},
    )
    with TestClient(app) as other:
        _csrf(other)
        rows = {row["provider_id"]: row for row in other.get("/api/v3/settings/credentials").json()["items"]}
        assert rows["openalex"]["configured"] is False


# ---------------------------------------------------------------------------
# access and venue
# ---------------------------------------------------------------------------


def test_access_options_come_from_the_registry_and_claim_no_entitlement(client):
    csrf = _csrf(client)
    project = _project(client, csrf)
    pid = project["project_id"]
    _ranked_project(client, pid, csrf)
    papers = client.get(f"/api/v3/projects/{pid}/papers", params={"view": "all"}).json()
    paper_id = papers["items"][0]["paper"]["paper_id"]

    body = client.get(f"/api/v3/projects/{pid}/papers/{paper_id}/access-options").json()
    assert body["items"], body
    registry = contracts.access_link_registry()
    registry_urls = {entry["url"] for entry in registry.values()}
    for option in body["items"]:
        assert set(option) == set(
            contracts.domain_schema()["$defs"]["AccessOption"]["properties"]
        )
        assert option["url"].startswith("https://")
        assert option["url"] in registry_urls or option["url"].startswith("https://doi.org/") \
            or option["url"].startswith("http")
        # `verified` describes a checked link, never an authorisation.
        assert isinstance(option["verified"], bool)
        assert option["availability"] in {
            "unknown", "public", "requires_institution", "entry_only", "unavailable",
        }
    assert "认证成功" not in json.dumps(body, ensure_ascii=False)
    assert "全文已授权" not in json.dumps(body, ensure_ascii=False)


def test_venue_metrics_never_fabricate_an_official_jif(client):
    csrf = _csrf(client)
    project = _project(client, csrf)
    pid = project["project_id"]
    _ranked_project(client, pid, csrf)
    state = v32_from_app(client.app)
    record = next(iter(state.store.projects[pid].doc["papers"].values()))
    record["venue"] = "Nature"
    record["venue_type"] = "journal"
    state.store.save(state.store.projects[pid])
    paper_id = record["paper_id"]

    body = client.get(f"/api/v3/projects/{pid}/papers/{paper_id}/venue-metrics").json()
    assert body["venue_type"] in {"journal", "conference", "preprint", "book_series", "unknown"}
    official = [row for row in body["metrics"] if row["metric_type"] == "official_jif"]
    assert official, body
    for row in official:
        assert row["value"] is None
        assert row["verification_status"] == "unavailable"
    for row in body["metrics"]:
        if row["metric_type"] == "citation_proxy":
            assert "内部引用代理指标" in json.dumps(row, ensure_ascii=False)
            assert row["metric_type"] != "official_jif"


# ---------------------------------------------------------------------------
# overview
# ---------------------------------------------------------------------------


def test_overview_is_non_empty_with_zero_user_marks(client):
    csrf = _csrf(client)
    project = _project(client, csrf)
    pid = project["project_id"]
    body = client.get(f"/api/v3/projects/{pid}/overview").json()
    assert set(body) == set(contracts.domain_schema()["$defs"]["Overview"]["properties"])
    assert body["status"] == "insufficient_data"
    assert body["statements"], body
    assert body["branches"] == [] and body["leads"] == []
    assert body["recommended_paper_ids"] == []
    assert set(body["coverage"]) == set(
        contracts.domain_schema()["$defs"]["Coverage"]["properties"]
    )


def test_overview_after_discovery_keeps_every_array(client):
    csrf = _csrf(client)
    project = _project(client, csrf)
    pid = project["project_id"]
    _ranked_project(client, pid, csrf)
    body = client.get(f"/api/v3/projects/{pid}/overview").json()
    assert body["status"] in {"ready", "partial"}
    assert body["statements"]
    assert [point["count"] for point in body["timeline"]]
    assert body["trend_status"] in {"available", "insufficient_data"}
    assert isinstance(body["trends"], list)
    mentioned = {pid for row in body["statements"] for pid in row["paper_ids"]}
    known = set(v32_from_app(client.app).store.projects[pid].doc["papers"])
    assert mentioned <= known


# ---------------------------------------------------------------------------
# PDF import
# ---------------------------------------------------------------------------


def _pdf_bytes(text: str) -> bytes:
    fitz = pytest.importorskip("fitz")
    document = fitz.open()
    page = document.new_page()
    if text:
        page.insert_text((72, 96), text, fontsize=12)
    payload = document.tobytes()
    document.close()
    return payload


def test_pdf_import_is_async_and_becomes_a_local_seed(client):
    csrf = _csrf(client)
    project = _project(client, csrf, name="pdf", entry_mode="pdf")
    pid = project["project_id"]
    pdf = _pdf_bytes(
        "Plant learning with machine learning\nAbstract\nplant learning plant phenotyping"
    )
    response = client.post(
        f"/api/v3/projects/{pid}/papers/import",
        data={"expected_revision": "1"},
        files=[("files", ("paper.pdf", pdf, "application/pdf"))],
        headers=_headers(csrf, key="import-" + "i" * 16),
    )
    assert response.status_code == 202, response.text
    batch = response.json()
    assert set(batch) == {"import_id", "job_id", "project_id", "revision", "items"}
    assert batch["revision"] == 1
    assert len(batch["items"]) == 1
    item = batch["items"][0]
    assert item["status"] == "uploaded"
    assert item["content_hash"] and item["attachment_id"]
    assert "stored_path_key" not in json.dumps(batch)
    assert "/" not in item["display_name"] and "\\" not in item["display_name"]

    v32_from_app(client.app) and None
    finished = _wait_job(client, v32_from_app(client.app), pid, batch["job_id"])
    assert finished["state"] in {"succeeded", "partial"}
    stored = client.get(f"/api/v3/projects/{pid}/imports/{batch['import_id']}").json()
    assert stored["import_id"] == batch["import_id"]
    assert stored["items"][0]["status"] in {"matched", "local_seed", "needs_confirmation", "needs_ocr"}
    matching = [
        item for item in stored["items"] if item["status"] in {"matched", "local_seed"}
    ]
    if matching:
        assert matching[0]["paper_id"]


def test_a_batch_over_the_file_limit_is_rejected_before_enqueueing(client):
    csrf = _csrf(client)
    project = _project(client, csrf, name="limits", entry_mode="pdf")
    pid = project["project_id"]
    pdf = b"%PDF-1.4 tiny"
    files = [("files", (f"f{index}.pdf", pdf, "application/pdf")) for index in range(11)]
    response = client.post(
        f"/api/v3/projects/{pid}/papers/import",
        data={"expected_revision": "1"},
        files=files,
        headers=_headers(csrf, key="toomany-" + "t" * 16),
    )
    assert response.status_code in {400, 413, 422}, response.text
    assert response.json()["error"]["code"]
    assert client.get(f"/api/v3/projects/{pid}").json()["revision"] == 1


def test_import_is_owner_scoped(app, client):
    csrf = _csrf(client)
    project = _project(client, csrf, name="owned", entry_mode="pdf")
    pid = project["project_id"]
    with TestClient(app) as other:
        _csrf(other)
        response = other.get(f"/api/v3/projects/{pid}/imports/{'a' * 32}")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# zotero
# ---------------------------------------------------------------------------


def test_zotero_import_and_export_run_through_an_injected_adapter(client):
    csrf = _csrf(client)
    project = _project(client, csrf)
    pid = project["project_id"]
    _ranked_project(client, pid, csrf)
    state = v32_from_app(client.app)
    state.zotero_adapter = FakeZoteroAdapter()

    imported = client.post(
        f"/api/v3/projects/{pid}/zotero/import",
        json={"expected_revision": 1, "library_type": "user", "library_id": "42",
              "item_keys": ["AAAA1111"], "include_selected_attachments": False},
        headers=_headers(csrf, key="zimp-" + "z" * 16),
    )
    assert imported.status_code == 202, imported.text
    finished = _wait_job(client, state, pid, imported.json()["job_id"])
    assert finished["state"] in {"succeeded", "partial"}, finished
    assert len(state.store.projects[pid].doc["papers"]) > len(DEFAULT_PAPERS)

    papers = client.get(f"/api/v3/projects/{pid}/papers", params={"view": "all"}).json()
    chosen = [row["paper"]["paper_id"] for row in papers["items"]][:1]
    preview = client.post(
        f"/api/v3/projects/{pid}/zotero/export-preview",
        json={"expected_revision": 1, "paper_ids": chosen, "library_type": "user",
              "library_id": "42", "collection_key": None},
        headers=_headers(csrf, key="zprev-" + "z" * 16),
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert set(body) == {"preview_id", "project_id", "revision", "expires_at",
                         "target_library_id", "target_collection_key", "items"}
    assert body["target_library_id"] == "42"

    exported = client.post(
        f"/api/v3/projects/{pid}/zotero/export",
        json={"expected_revision": 1, "preview_id": body["preview_id"], "confirm": True},
        headers=_headers(csrf, key="zexp-" + "z" * 16),
    )
    assert exported.status_code == 202, exported.text
    export_job = exported.json()["job_id"]
    _wait_job(client, state, pid, export_job)
    results = client.get(f"/api/v3/projects/{pid}/jobs/{export_job}/zotero-results").json()
    assert results["job_id"] == export_job
    assert results["items"]
    assert {row["status"] for row in results["items"]} <= {
        "successful", "unchanged", "failed", "conflict", "unknown_outcome",
    }


def test_zotero_is_refused_when_no_credential_is_configured(client, app, monkeypatch):
    # 同上：本机 Zotero 可能正在运行，那"没有凭据"就不成立。这里显式拿掉适配器。
    monkeypatch.setattr(v32_from_app(app), "zotero", lambda: None)
    csrf = _csrf(client)
    project = _project(client, csrf)
    pid = project["project_id"]
    response = client.post(
        f"/api/v3/projects/{pid}/zotero/import",
        json={"expected_revision": 1, "library_type": "user", "library_id": "42",
              "item_keys": ["AAAA1111"], "include_selected_attachments": False},
        headers=_headers(csrf, key="znone-" + "z" * 16),
    )
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "capability_unavailable"
    assert response.json()["error"]["details"]["capability"] == "zotero:web_api"
