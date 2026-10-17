"""Pipeline behaviour the v3.2 spec requires and earlier code missed.

* doc 02 / AC-01 / AC-32: discovery runs exactly one automatic neighbour round
  over system-chosen seeds, and never a second;
* expansion sends an external identifier (DOI first), never the internal uuid;
* a round never exceeds the 300-candidate cap and truncates round-robin over
  ``provider_priority``;
* doc 08: retrying one failed import item leaves every other row untouched;
* doc 17: the AI window is the recommended top-N only, and the local snapshot
  is committed before the model is called.

No test reaches the network: every provider and model is an injected fake.
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
from litsearch.v32.api import v32_from_app
from litsearch.web import create_app

TERMINAL = contracts.terminal_states()


def _paper(identifier, title, *, doi=None, year=2024, citations=5, source="fake", abstract=None):
    return Paper(
        id=identifier,
        title=title,
        abstract=abstract if abstract is not None else f"{title} abstract with plant learning evidence",
        authors=[Author(name="A. Researcher")],
        year=year,
        doi=doi,
        citation_count=citations,
        source=source,
    )


PAPERS = [
    _paper("10.1234/plant-a", "Plant learning with machine learning", doi="10.1234/plant-a", citations=21),
    _paper("10.1234/plant-b", "Machine learning for plant phenotyping", doi="10.1234/plant-b", citations=4),
    _paper("10.1234/plant-d", "Plant learning for leaf imaging", doi="10.1234/plant-d", citations=9),
    _paper("10.1234/plant-c", "Unrelated marine sediment chemistry", doi="10.1234/plant-c", citations=2,
           abstract="marine sediment chemistry"),
]


class RecordingSources:
    """Records every neighbour call; returns per-provider search results."""

    def __init__(self, per_provider=None, neighbours_per_call=0):
        self.per_provider = per_provider if per_provider is not None else {"openalex": list(PAPERS)}
        self.neighbours_per_call = neighbours_per_call
        self.citation_calls = []
        self.reference_calls = []
        self.search_limits = []

    def search_all_sources_result(self, query, limit=200, year_from=1900, year_to=None, query_plan=None):
        self.search_limits.append(limit)
        return [
            RetrievalResult(papers=list(papers), provider=name, status=RetrievalStatus.SUCCESS)
            for name, papers in self.per_provider.items()
        ]

    def _neighbours(self, tag, paper_id, limit):
        count = min(self.neighbours_per_call, limit)
        return [
            _paper(f"10.9999/{tag}-{len(self.citation_calls)}-{len(self.reference_calls)}-{index}",
                   f"Plant learning neighbour {tag} {paper_id} {index}",
                   doi=f"10.9999/{tag}-{len(self.citation_calls)}-{len(self.reference_calls)}-{index}")
            for index in range(count)
        ]

    def get_citations_result(self, paper_id, limit=100):
        self.citation_calls.append(paper_id)
        papers = self._neighbours("cit", paper_id, limit)
        status = RetrievalStatus.SUCCESS if papers else RetrievalStatus.SUCCESS_EMPTY
        return RetrievalResult(papers=papers, provider="openalex", status=status)

    def get_references_result(self, paper_id, limit=100):
        self.reference_calls.append(paper_id)
        papers = self._neighbours("ref", paper_id, limit)
        status = RetrievalStatus.SUCCESS if papers else RetrievalStatus.SUCCESS_EMPTY
        return RetrievalResult(papers=papers, provider="openalex", status=status)


def _make_app(tmp_path: Path, sources):
    application = create_app(data_dir=tmp_path, workflow_factory=lambda demo: type(
        "StubWorkflow", (), {"sources": sources})())
    state = v32_from_app(application)
    state.source_manager = sources
    return application, state


def _csrf(client):
    return client.get("/api/v3/session").json()["csrf_token"]


def _headers(csrf, key):
    return {"X-CSRF-Token": csrf, "Idempotency-Key": key.ljust(16, "x")}


def _project(client, csrf, name="plant learning"):
    response = client.post("/api/v3/projects", json={"name": name, "entry_mode": "topic"},
                           headers=_headers(csrf, f"create-{name}"))
    assert response.status_code == 201, response.text
    return response.json()["project_id"]


def _wait(client, state, pid, job_id, timeout=15.0):
    deadline = time.monotonic() + timeout
    body = None
    while time.monotonic() < deadline:
        state.jobs.drain()
        body = client.get(f"/api/v3/projects/{pid}/jobs/{job_id}").json()
        if body["state"] in TERMINAL:
            return body
        time.sleep(0.02)
    raise AssertionError(f"job never settled: {body}")


def _result(state, body):
    """The handler result the job recorded (the Job DTO itself carries no result)."""
    return state.jobs.jobs[body["job_id"]].doc["result"]


def _discover(client, state, csrf, pid, key="discover-01"):
    response = client.post(
        f"/api/v3/projects/{pid}/discover",
        json={"expected_revision": 1, "research_text": "plant learning", "seed_paper_ids": [],
              "mode": "basic"},
        headers=_headers(csrf, key),
    )
    assert response.status_code == 202, response.text
    return _wait(client, state, pid, response.json()["job_id"])


@pytest.fixture()
def env(tmp_path):
    created = []

    def _build(sources):
        application, state = _make_app(tmp_path / f"app{len(created)}", sources)
        client = TestClient(application)
        client.__enter__()
        created.append((client, state))
        return client, state

    yield _build
    for client, state in created:
        client.__exit__(None, None, None)
        state.jobs.shutdown(grace=0.1)


# ---------------------------------------------------------------------------
# G1 / G2: one automatic neighbour round, external ids only
# ---------------------------------------------------------------------------


def test_discover_performs_exactly_one_automatic_neighbour_round_and_never_a_second(env):
    sources = RecordingSources(neighbours_per_call=2)
    client, state = env(sources)
    csrf = _csrf(client)
    pid = _project(client, csrf)
    finished = _discover(client, state, csrf, pid)
    assert finished["state"] in {"succeeded", "partial"}, finished

    per_round = int(contracts.discovery()["seeds_per_round"])
    first_round_dois = {paper.doi for paper in PAPERS}
    assert sources.citation_calls, "the automatic round never ran"
    assert len(sources.citation_calls) <= per_round
    assert sources.citation_calls == sources.reference_calls
    # Every seed is a first-round paper: the neighbours were never expanded again.
    assert set(sources.citation_calls) <= first_round_dois
    assert len(set(sources.citation_calls)) == len(sources.citation_calls)

    report = _result(state, finished)["auto_expansion"]
    assert report["rounds"] == 1
    assert "auto_round_limit_reached" in report["stop_reasons"]
    assert all(row["origin"] == "system" for row in report["seeds"])
    assert "auto_round_limit_reached" in finished["coverage"]["stop_reasons"]

    project = state.store.projects[pid]
    # A system choice is never recorded as a user anchor.
    assert not (project.doc.get("anchor") or {}).get("seed_paper_ids")
    assert not project.doc.get("seeds")
    assert len(project.doc["papers"]) > len(PAPERS)
    assert project.doc["current_snapshot_id"] == finished["snapshot_id"]

    # Draining again runs nothing more: no second round.
    calls = len(sources.citation_calls)
    state.jobs.drain()
    assert len(sources.citation_calls) == calls


def test_expansion_sends_the_doi_for_a_seed_whose_internal_id_is_a_uuid(env):
    from litsearch.v32.jobs import JobRecord
    from litsearch.v32.pipeline import retrieve_neighbours

    sources = RecordingSources(neighbours_per_call=0)
    client, state = env(sources)
    csrf = _csrf(client)
    pid = _project(client, csrf)
    project = state.store.projects[pid]
    with_doi = state.store.upsert_paper(project, {
        "paper_id": None, "title": "Seed with doi", "doi": "10.5555/seed-doi",
        "aliases": ["10.5555/seed-doi"],
    })
    with_s2 = state.store.upsert_paper(project, {
        "paper_id": None, "title": "Seed with s2", "doi": None, "aliases": ["a" * 40],
        "source_records": [{"provider": "semantic_scholar", "external_id": "a" * 40}],
    })
    bare = state.store.upsert_paper(project, {"paper_id": None, "title": "No identifier", "aliases": []})
    assert len(with_doi) == 32 and "/" not in with_doi  # an internal uuid

    job = JobRecord({"job_id": "e" * 32, "project_id": pid, "kind": "expand", "state": "running",
                     "error_codes": []})
    outcome = retrieve_neighbours(state, project, job, seeds=[with_doi, with_s2, bare])
    assert sources.citation_calls == ["10.5555/seed-doi", "a" * 40]
    assert sources.reference_calls == ["10.5555/seed-doi", "a" * 40]
    assert with_doi not in sources.citation_calls
    assert outcome.skipped_seeds == [bare]
    assert "seed_without_external_id" in outcome.stop_reasons


# ---------------------------------------------------------------------------
# G5: candidate cap and round-robin truncation
# ---------------------------------------------------------------------------


def _many(provider, count):
    return [
        _paper(f"10.7/{provider}-{index}", f"Plant learning {provider} {index}", doi=f"10.7/{provider}-{index}",
               source=provider)
        for index in range(count)
    ]


def test_a_round_never_exceeds_the_cap_and_truncates_round_robin(env):
    from litsearch.v32.jobs import JobRecord
    from litsearch.v32.pipeline import query_variants, retrieve_candidates

    cap = int(contracts.discovery()["candidate_cap"])
    assert cap == 300
    # semantic_scholar is listed first by the provider but second in priority.
    sources = RecordingSources(
        per_provider={"semantic_scholar": _many("semantic_scholar", 100), "openalex": _many("openalex", 250)},
        neighbours_per_call=20,
    )
    client, state = env(sources)
    csrf = _csrf(client)
    pid = _project(client, csrf)
    project = state.store.projects[pid]

    job = JobRecord({"job_id": "c" * 32, "project_id": pid, "kind": "discover", "state": "running",
                     "error_codes": []})
    outcome = retrieve_candidates(state, project, job, text="plant learning",
                                  variants=query_variants("plant learning"))
    assert len(outcome.papers) == cap
    assert "candidate_cap_reached" in outcome.stop_reasons
    head = [record["doi"] for record in outcome.papers[:4]]
    assert head == ["10.7/openalex-0", "10.7/semantic_scholar-0", "10.7/openalex-1", "10.7/semantic_scholar-1"]
    assert outcome.provider_stats["semantic_scholar"]["kept"] == 100
    assert outcome.provider_stats["openalex"]["kept"] == 200
    assert outcome.provider_stats["openalex"]["returned"] == 250

    finished = _discover(client, state, csrf, pid)
    assert len(state.store.projects[pid].doc["papers"]) == cap
    assert _result(state, finished)["provider_counts"]["openalex"] == {"returned": 250, "kept": 200}
    assert "openalex" in _result(state, finished)["provider_cursors"]
    # The cap left no room, so the automatic round asked for nothing.
    assert sources.citation_calls == []
    assert "candidate_cap_reached" in _result(state, finished)["auto_expansion"]["stop_reasons"]


def test_the_neighbour_round_only_gets_what_is_left_of_the_cap(env):
    from litsearch.v32.jobs import JobRecord
    from litsearch.v32.pipeline import retrieve_neighbours

    sources = RecordingSources(neighbours_per_call=20)
    client, state = env(sources)
    csrf = _csrf(client)
    pid = _project(client, csrf)
    project = state.store.projects[pid]
    seeds = [
        state.store.upsert_paper(project, {"paper_id": None, "title": f"Seed {i}", "doi": f"10.1/s{i}",
                                           "aliases": [f"10.1/s{i}"]})
        for i in range(5)
    ]
    job = JobRecord({"job_id": "d" * 32, "project_id": pid, "kind": "expand", "state": "running",
                     "error_codes": []})
    outcome = retrieve_neighbours(state, project, job, seeds=seeds, cap=7)
    assert outcome.new_count == 7
    assert len(outcome.papers) == 7
    assert "candidate_cap_reached" in outcome.stop_reasons


def test_the_round_budget_actually_stops_a_stalled_provider(env, monkeypatch):
    from litsearch.v32 import pipeline
    from litsearch.v32.jobs import JobRecord

    class Stalled(RecordingSources):
        def get_citations_result(self, paper_id, limit=100):
            self.citation_calls.append(paper_id)
            time.sleep(2.0)
            return RetrievalResult(papers=[], provider="openalex", status=RetrievalStatus.SUCCESS_EMPTY)

    sources = Stalled()
    client, state = env(sources)
    csrf = _csrf(client)
    pid = _project(client, csrf)
    project = state.store.projects[pid]
    seed = state.store.upsert_paper(project, {"paper_id": None, "title": "Seed", "doi": "10.1/s",
                                              "aliases": ["10.1/s"]})
    job = JobRecord({"job_id": "b" * 32, "project_id": pid, "kind": "expand", "state": "running",
                     "error_codes": []})
    started = time.monotonic()
    outcome = pipeline.retrieve_neighbours(state, project, job, seeds=[seed, seed],
                                           deadline=time.monotonic() + 0.2)
    assert time.monotonic() - started < 1.5
    assert "round_budget_exceeded" in outcome.stop_reasons
    assert sources.citation_calls == ["10.1/s"]
    assert sources.reference_calls == []


# ---------------------------------------------------------------------------
# G7: retry only the named items
# ---------------------------------------------------------------------------


def test_retrying_one_failed_item_leaves_a_matched_item_byte_identical(env):
    sources = RecordingSources()
    client, state = env(sources)
    csrf = _csrf(client)
    pid = _project(client, csrf)
    project = state.store.projects[pid]
    matched_paper = state.store.upsert_paper(project, {"paper_id": None, "title": "Matched work",
                                                       "doi": "10.1/m", "aliases": ["10.1/m"]})
    matched = {
        "item_id": "a" * 32, "attachment_id": "a" * 32, "display_name": "matched.pdf",
        "content_hash": "a" * 64, "status": "matched", "paper_id": matched_paper,
        "match_candidates": [], "match_score": 0.97, "error_code": None, "retryable": False,
        "size_bytes": 10, "reused": False,
    }
    failed = {
        "item_id": "b" * 32, "attachment_id": "b" * 32, "display_name": "failed.pdf",
        "content_hash": "b" * 64, "status": "failed", "paper_id": None,
        "match_candidates": [], "match_score": None, "error_code": "attachment_missing", "retryable": True,
        "size_bytes": 10, "reused": False,
    }
    import_id = "f" * 32
    project.doc.setdefault("imports", {})[import_id] = {
        "import_id": import_id, "project_id": pid, "expected_revision": 1,
        "item_ids": [matched["item_id"], failed["item_id"]],
        "items": {matched["item_id"]: dict(matched), failed["item_id"]: dict(failed)},
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z", "job_id": "",
    }
    state.store.save(project)
    before = json.dumps(project.doc["imports"][import_id]["items"][matched["item_id"]], sort_keys=True)

    response = client.post(
        f"/api/v3/projects/{pid}/imports/{import_id}/retry",
        json={"expected_revision": project.revision, "item_ids": [failed["item_id"]]},
        headers=_headers(csrf, "retry-one-item"),
    )
    assert response.status_code == 202, response.text
    finished = _wait(client, state, pid, project.doc["imports"][import_id]["job_id"])
    assert finished["state"] in {"succeeded", "partial"}, finished

    items = state.store.projects[pid].doc["imports"][import_id]["items"]
    assert json.dumps(items[matched["item_id"]], sort_keys=True) == before
    # The named item was actually reprocessed (its attachment is still missing).
    assert items[failed["item_id"]]["status"] == "failed"
    assert items[failed["item_id"]]["error_code"] == "attachment_missing"
    assert _result(state, finished)["items_failed"] == 1
    assert _result(state, finished)["items_ok"] == 0


# ---------------------------------------------------------------------------
# G8: AI window and staging
# ---------------------------------------------------------------------------


class RecordingAdapter:
    model_revision = "fake-model-1"

    def __init__(self, state, pid):
        self.state = state
        self.pid = pid
        self.requests = []
        self.snapshot_at_call = []

    def rerank(self, request):
        project = self.state.store.projects[self.pid]
        self.snapshot_at_call.append(project.doc.get("current_snapshot_id"))
        self.requests.append(dict(request))
        raise RuntimeError("model unavailable in tests")


def test_the_ai_window_is_recommended_only_and_runs_after_the_local_commit(env, monkeypatch):
    from litsearch.v32 import api as api_mod
    from litsearch.v32.pipeline import ai_window_ids

    sources = RecordingSources()
    client, state = env(sources)
    csrf = _csrf(client)
    pid = _project(client, csrf)
    _discover(client, state, csrf, pid)
    adapter = RecordingAdapter(state, pid)
    # The rerank route resolves the session's model adapter; inject the fake there.
    monkeypatch.setattr(api_mod, "_resolve_model_adapter", lambda state, session: adapter)
    project = state.store.projects[pid]

    started = client.post(f"/api/v3/projects/{pid}/rerank", json={"expected_revision": project.revision},
                          headers=_headers(csrf, "rerank-ai-staging"))
    assert started.status_code == 202, started.text
    local = _wait(client, state, pid, started.json()["job_id"])
    assert local["state"] == "succeeded", local
    # The judgement-path rerank finished without any model call.
    assert adapter.requests == []
    local_snapshot = local["snapshot_id"]
    assert project.doc["current_snapshot_id"] == local_snapshot
    assert _result(state, local)["ai_status"] == "queued"

    ai_job = state.jobs.jobs[_result(state, local)["ai_job_id"]]
    assert ai_job.doc["kind"] == "ai_rerank"
    assert ai_job.doc["parent_snapshot_id"] == local_snapshot
    delay = int(contracts.job_defaults()["ai_idle_delay_ms"]) / 1000.0
    remaining = float(ai_job.doc["debounce_until"]) - time.monotonic()
    assert 0 < remaining <= delay
    state.jobs.drain()
    assert adapter.requests == [], "the AI pass ran before the idle delay"

    ai_job.doc["debounce_until"] = time.monotonic() - 1  # skip the wait in the test
    finished = _wait(client, state, pid, ai_job.job_id)
    assert finished["state"] in {"succeeded", "partial"}, finished
    assert adapter.snapshot_at_call == [local_snapshot]

    snapshot = state.store.load_snapshot(project, local_snapshot)
    recommended = {
        item["paper_id"] for item in snapshot["items"] if item["eligibility"] == "recommended"
    }
    assert any(item["eligibility"] != "recommended" for item in snapshot["items"]), snapshot["items"]
    window = adapter.requests[0]["allowed_paper_ids"]
    assert window and set(window) <= recommended
    assert window == ai_window_ids(snapshot["items"])
    assert adapter.requests[0]["parent_snapshot_id"] == local_snapshot
    # A failed model call keeps the local snapshot as the current one.
    assert project.doc["current_snapshot_id"] == local_snapshot


def test_the_ai_window_takes_the_recommended_top_n_only():
    from litsearch.v32.pipeline import ai_window_ids

    top_n = int(contracts.ai_defaults()["top_n"])
    items = []
    for rank in range(1, 80):
        eligibility = "recommended" if rank % 2 else "exploration"
        items.append({"paper_id": f"p{rank:03d}", "rank": rank, "eligibility": eligibility})
    window = ai_window_ids(items)
    assert len(window) == top_n
    assert all(int(pid[1:]) % 2 for pid in window)
    assert window == [f"p{rank:03d}" for rank in range(1, 80, 2)][:top_n]
