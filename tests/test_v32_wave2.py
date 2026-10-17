"""Wave-2 gaps: PDF seeds start discovery, per-session model and retrieval keys,
uncertain PDF matches as local seeds, DOI conflicts, and the intent job.

* G3 (AC-02/AC-03, doc 02 「从已有文献开始」): a PDF-only project goes
  PDF -> discover -> snapshot with candidates beyond its own PDFs;
* G4 (doc 05 §设置, doc 10): a session's model key serves only that session's
  jobs, and basic mode never calls a model;
* G6 (doc 02/16): a needs_confirmation item is held as a local seed without
  adopting the doubtful entity; an all-failed batch with no seed is ``failed``;
* G11 (doc 08/10): a session's retrieval key is used for that session's job
  only; a provider 429 shows in the job coverage;
* G12 (AC-27, doc 16): only the parsed DOI is an alias, several front-matter
  DOIs need confirmation, and DOI + title >= 0.80 auto-matches;
* intent reading runs in its own job after the local commit (doc 17).

No test reaches the network: providers, parser and models are injected fakes.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from litsearch.models import Author, Paper
from litsearch.retrieval import RetrievalResult, RetrievalStatus
from litsearch.v32 import ai_providers, contracts, intent_ai
from litsearch.v32 import pdf_import as P
from litsearch.v32.api import v32_from_app
from litsearch.web import create_app

TERMINAL = contracts.terminal_states()


def _paper(identifier, title, *, doi=None, abstract=None, citations=5):
    return Paper(
        id=identifier,
        title=title,
        abstract=abstract if abstract is not None else f"{title} abstract mint drought evidence",
        authors=[Author(name="A. Researcher")],
        year=2023,
        doi=doi,
        citation_count=citations,
        source="fake",
    )


EXTERNAL = [
    _paper("10.5555/mint-a", "Mint drought stress and leaf phenotyping", doi="10.5555/mint-a", citations=30),
    _paper("10.5555/mint-b", "Drought response of Mentha essential oil", doi="10.5555/mint-b", citations=12),
    _paper("10.5555/mint-c", "Imaging mint leaves under water deficit", doi="10.5555/mint-c", citations=7),
]


class Sources:
    """Search fake; records the query and the keys each call was scoped with."""

    def __init__(self, papers=None, keys=None, log=None, statuses=None):
        self.papers = list(EXTERNAL if papers is None else papers)
        self.keys = dict(keys or {})
        self.log = log if log is not None else []
        self.statuses = dict(statuses or {})

    def with_api_keys(self, keys):
        return Sources(self.papers, keys=keys, log=self.log, statuses=self.statuses)

    def search_all_sources_result(self, query, limit=200, year_from=1900, year_to=None, query_plan=None):
        self.log.append({"query": query, "keys": dict(self.keys)})
        results = [RetrievalResult(papers=list(self.papers), provider="openalex", status=RetrievalStatus.SUCCESS)]
        for provider, status in self.statuses.items():
            results.append(RetrievalResult(papers=[], provider=provider, status=status))
        return results

    def get_citations_result(self, paper_id, limit=100):
        return RetrievalResult(papers=[], provider="openalex", status=RetrievalStatus.SUCCESS_EMPTY)

    def get_references_result(self, paper_id, limit=100):
        return RetrievalResult(papers=[], provider="openalex", status=RetrievalStatus.SUCCESS_EMPTY)


@pytest.fixture()
def env(tmp_path: Path):
    apps = []

    def _build(sources):
        application = create_app(data_dir=tmp_path / f"app{len(apps)}", workflow_factory=lambda demo: type(
            "StubWorkflow", (), {"sources": sources})())
        state = v32_from_app(application)
        state.source_manager = sources
        apps.append(application)
        return application, state

    yield _build
    for application in apps:
        state = v32_from_app(application)
        state.jobs.shutdown(grace=1.0)


def _csrf(client):
    return client.get("/api/v3/session").json()["csrf_token"]


def _headers(csrf, key):
    return {"X-CSRF-Token": csrf, "Idempotency-Key": key.ljust(16, "x")}


def _project(client, csrf, name, entry_mode="topic"):
    response = client.post("/api/v3/projects", json={"name": name, "entry_mode": entry_mode},
                           headers=_headers(csrf, f"create-{name}"))
    assert response.status_code == 201, response.text
    return response.json()["project_id"]


def _wait(client, state, pid, job_id, timeout=15.0):
    deadline = time.monotonic() + timeout
    body = None
    while time.monotonic() < deadline:
        for job in list(state.jobs.jobs.values()):
            if job.doc.get("debounce_until") and job.state == "queued":
                job.doc["debounce_until"] = time.monotonic() - 1
        state.jobs.drain()
        body = client.get(f"/api/v3/projects/{pid}/jobs/{job_id}").json()
        if body["state"] in TERMINAL:
            return body
        time.sleep(0.02)
    raise AssertionError(f"job never settled: {body}")


def _result(state, body):
    return state.jobs.jobs[body["job_id"]].doc["result"]


def _discover(client, state, csrf, pid, mode, key):
    revision = client.get(f"/api/v3/projects/{pid}").json()["revision"]
    response = client.post(
        f"/api/v3/projects/{pid}/discover",
        json={"expected_revision": revision, "research_text": "mint drought phenotyping",
              "seed_paper_ids": [], "mode": mode},
        headers=_headers(csrf, key),
    )
    assert response.status_code == 202, response.text
    return _wait(client, state, pid, response.json()["job_id"])


def _rerank(client, state, csrf, pid, key):
    revision = client.get(f"/api/v3/projects/{pid}").json()["revision"]
    response = client.post(f"/api/v3/projects/{pid}/rerank", json={"expected_revision": revision},
                           headers=_headers(csrf, key))
    assert response.status_code == 202, response.text
    return _wait(client, state, pid, response.json()["job_id"])


def _parsed(title, *, doi=None, doi_candidates=None, abstract="Mint plants under drought stress."):
    return P.ParsedPaper(
        title=title,
        authors=["A. Researcher"],
        year=2022,
        abstract=abstract,
        doi=doi,
        doi_candidates=list(doi_candidates if doi_candidates is not None else ([doi] if doi else [])),
        text_chars=800,
        page_count=3,
    )


def _fake_parser(monkeypatch, parsed_by_name):
    def fake(data, display_name, *args, **kwargs):
        parsed = parsed_by_name.get(display_name)
        if parsed is None:
            return P.ParseOutcome(status=P.ImportStatus.FAILED, parsed=None,
                                  error_code="malformed_pdf", retryable=False)
        return P.ParseOutcome(status=P.ImportStatus.MATCHING, parsed=parsed, error_code=None, retryable=False)

    monkeypatch.setattr(P, "parse_pdf_bytes", fake)


def _import(client, csrf, pid, names, key):
    files = [("files", (name, b"%PDF-1.4 " + name.encode("utf-8"), "application/pdf")) for name in names]
    revision = client.get(f"/api/v3/projects/{pid}").json()["revision"]
    response = client.post(f"/api/v3/projects/{pid}/papers/import", data={"expected_revision": str(revision)},
                           files=files, headers=_headers(csrf, key))
    assert response.status_code == 202, response.text
    return response.json()


# ---------------------------------------------------------------------------
# G3: PDF -> discover -> snapshot
# ---------------------------------------------------------------------------


def test_a_pdf_only_project_seeds_discovery_and_gets_candidates_beyond_its_pdfs(env, monkeypatch):
    sources = Sources()
    application, state = env(sources)
    _fake_parser(monkeypatch, {"seed.pdf": _parsed("Phenotyping mint drought tolerance with imaging")})
    with TestClient(application) as client:
        csrf = _csrf(client)
        pid = _project(client, csrf, "pdf only", entry_mode="pdf")
        batch = _import(client, csrf, pid, ["seed.pdf"], "import-g3")
        finished = _wait(client, state, pid, batch["job_id"])
        assert finished["state"] == "succeeded", finished
        result = _result(state, finished)
        project = state.store.projects[pid]
        seeds = result["seed_paper_ids"]
        assert len(seeds) == 1
        seed = seeds[0]
        # The seed is recorded through the store with its import origin.
        assert project.doc["anchor"]["seed_paper_ids"] == [seed]
        assert project.doc["project_papers"][seed]["seed_origin"] == "upload"
        # The user's own anchor text is not overwritten by the derived text.
        assert not project.doc["anchor"].get("original_text")

        assert result["discover_job_id"]
        discover = _wait(client, state, pid, result["discover_job_id"])
        assert discover["state"] in {"succeeded", "partial"}, (state.jobs.jobs[discover["job_id"]].doc.get("error_codes"), state.jobs.jobs[discover["job_id"]].doc.get("error_message"))
        # Discovery searched with the seed's title/abstract.
        assert sources.log and "Phenotyping mint drought tolerance" in sources.log[0]["query"]
        snapshot = state.store.load_snapshot(project, discover["snapshot_id"])
        ranked = {item["paper_id"] for item in snapshot["items"]}
        assert seed in ranked
        assert len(ranked - {seed}) >= len(EXTERNAL)


# ---------------------------------------------------------------------------
# G6 / G12: uncertain matches, failed batches, DOI conflicts
# ---------------------------------------------------------------------------


def test_a_needs_confirmation_item_continues_as_a_local_seed_without_adopting_the_candidate(env, monkeypatch):
    sources = Sources()
    application, state = env(sources)
    title = "Phenotyping mint drought tolerance with imaging"
    # Two distinct DOIs on the first pages: the identity must be confirmed.
    _fake_parser(monkeypatch, {
        "two-dois.pdf": _parsed(title, doi="10.7777/first", doi_candidates=["10.7777/first", "10.7777/second"]),
    })
    with TestClient(application) as client:
        csrf = _csrf(client)
        pid = _project(client, csrf, "uncertain", entry_mode="pdf")
        project = state.store.projects[pid]
        existing = state.store.upsert_paper(project, {"paper_id": None, "title": title,
                                                      "doi": "10.7777/first", "aliases": ["10.7777/first"],
                                                      "authors": ["A. Researcher"], "year": 2022})
        state.store.save(project, touch_corpus=True)
        batch = _import(client, csrf, pid, ["two-dois.pdf"], "import-g6")
        finished = _wait(client, state, pid, batch["job_id"])
        assert finished["state"] == "partial", finished
        item = client.get(f"/api/v3/projects/{pid}/imports/{batch['import_id']}").json()["items"][0]
        assert item["status"] == "needs_confirmation"
        assert item["error_code"] == "identifier_conflict"
        assert [row["candidate_id"] for row in item["match_candidates"]] == [existing]
        held = item["paper_id"]
        assert held and held != existing
        # Not auto-adopted: no DOI alias points at the held record, the existing
        # paper is untouched and still separate.
        assert project.doc["papers"][held]["doi"] is None
        assert project.doc["aliases"]["10.7777/first"] == existing
        assert "10.7777/second" not in project.doc["aliases"]
        # But it is a seed and discovery continued from it.
        assert held in project.doc["anchor"]["seed_paper_ids"]
        assert _result(state, finished)["discover_job_id"]


def test_a_batch_with_no_success_and_no_seed_is_failed_not_partial(env, monkeypatch):
    application, state = env(Sources())
    _fake_parser(monkeypatch, {})
    with TestClient(application) as client:
        csrf = _csrf(client)
        pid = _project(client, csrf, "all failed", entry_mode="pdf")
        batch = _import(client, csrf, pid, ["broken.pdf", "broken2.pdf"], "import-failed")
        finished = _wait(client, state, pid, batch["job_id"])
        assert finished["state"] == "failed", finished
        assert finished["coverage"]["status"] == "failed"
        assert _result(state, finished)["discover_job_id"] is None


def test_only_the_parsed_doi_becomes_an_alias():
    from litsearch.v32.pipeline import _parsed_record

    record = _parsed_record(_parsed("A title", doi="10.1/a", doi_candidates=["10.1/a", "10.1/cited"]))
    assert record["aliases"] == ["10.1/a"]
    assert record["doi"] == "10.1/a"


def test_several_front_matter_dois_need_confirmation_even_with_a_perfect_candidate():
    parsed = _parsed("Mint drought stress and leaf phenotyping", doi="10.5555/mint-a",
                     doi_candidates=["10.5555/mint-a", "10.5555/other"])
    candidate = {"paper_id": "p1", "title": "Mint drought stress and leaf phenotyping",
                 "doi": "10.5555/mint-a", "authors": ["A. Researcher"], "year": 2022}
    outcome = P.match_parsed(parsed, [candidate])
    assert outcome.status == P.ImportStatus.NEEDS_CONFIRMATION
    assert outcome.paper_id is None
    assert outcome.conflicts == [P.ERROR_IDENTIFIER_CONFLICT]


def test_doi_agreement_with_title_similarity_of_at_least_0_80_auto_matches():
    parsed = P.ParsedPaper(title="drought stress response of mint leaves under field conditions",
                           doi="10.5555/mint-x", doi_candidates=["10.5555/mint-x"], text_chars=500)
    candidate = {"paper_id": "p9", "title": "drought stress response of mint leaves under field",
                 "doi": "10.5555/mint-x"}
    similarity = P.title_similarity(parsed.title, candidate["title"])
    score = P.match_score(parsed, candidate)
    limits = P.pdf_limits()
    # The case doc 16 names: DOI + title >= 0.80, but an overall score below 0.70.
    assert similarity >= float(limits["doi_title_similarity"])
    assert score < float(limits["candidate_score_min"])
    outcome = P.match_parsed(parsed, [candidate])
    assert outcome.status == P.ImportStatus.MATCHED
    assert outcome.paper_id == "p9"


# ---------------------------------------------------------------------------
# G4: model credentials are per session and per job
# ---------------------------------------------------------------------------


class ModelAdapter:
    model_revision = "fake-model"

    def __init__(self, secret, calls):
        self.secret = secret
        self.calls = calls

    def rerank(self, request):
        self.calls.append(("rerank", self.secret))
        raise RuntimeError("model unavailable in tests")

    def intent(self, request):
        self.calls.append(("intent", self.secret))
        raise RuntimeError("model unavailable in tests")


@pytest.fixture()
def model_calls(monkeypatch):
    calls = []
    built = []

    def create_adapter(provider_id, api_key, *, model=None, transport=None, timeout=20.0):
        built.append(api_key)
        return ModelAdapter(api_key, calls)

    monkeypatch.setattr(ai_providers, "create_adapter", create_adapter)
    return calls, built


def _put_model_key(client, csrf, secret):
    response = client.put("/api/v3/settings/credentials/ai_model",
                          json={"kind": "model", "secret": secret, "model": "m", "endpoint_profile_id": "openai"},
                          headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.text


def test_session_b_never_uses_session_a_model_key(env, model_calls):
    calls, built = model_calls
    application, state = env(Sources())
    with TestClient(application) as a, TestClient(application) as b:
        csrf_a, csrf_b = _csrf(a), _csrf(b)
        _put_model_key(a, csrf_a, "sk-session-a-0000000000")
        pid_a = _project(a, csrf_a, "session a")
        pid_b = _project(b, csrf_b, "session b")
        _discover(a, state, csrf_a, pid_a, "ai_enhanced", "disc-a")
        _discover(b, state, csrf_b, pid_b, "ai_enhanced", "disc-b")

        local_a = _rerank(a, state, csrf_a, pid_a, "rerank-a")
        assert state.ai_adapter is None, "a session adapter was stored on the shared state"
        ai_a = _result(state, local_a)["ai_job_id"]
        assert ai_a
        _wait(a, state, pid_a, ai_a)
        assert ("rerank", "sk-session-a-0000000000") in calls

        calls.clear()
        local_b = _rerank(b, state, csrf_b, pid_b, "rerank-b")
        result_b = _result(state, local_b)
        assert result_b["ai_status"] == "skipped"
        assert result_b["ai_job_id"] is None
        for job in list(state.jobs.jobs.values()):
            if job.project_id == pid_b and job.state not in TERMINAL:
                _wait(b, state, pid_b, job.job_id)
        assert calls == []
        assert state.ai_adapter is None
        # The job file never carries the session reference or a key.
        for job in state.jobs.jobs.values():
            text = str(job.doc)
            assert "sk-session-a" not in text


def test_a_basic_mode_session_never_calls_a_model(env, model_calls, monkeypatch):
    calls, built = model_calls
    monkeypatch.setenv(intent_ai.ENV_SWITCH, "1")
    intent_ai._CACHE.clear()
    application, state = env(Sources())
    with TestClient(application) as client:
        csrf = _csrf(client)
        _put_model_key(client, csrf, "sk-basic-session-000000")
        pid = _project(client, csrf, "basic")
        discover = _discover(client, state, csrf, pid, "basic", "disc-basic")
        local = _rerank(client, state, csrf, pid, "rerank-basic")
        assert _result(state, local)["ai_status"] == "skipped"
        assert _result(state, local)["ai_job_id"] is None
        assert client.get(f"/api/v3/projects/{pid}/overview").status_code == 200
        assert "ai_job_id" not in _result(state, discover)
        assert calls == [] and built == []


# ---------------------------------------------------------------------------
# intent reading: separate job, after the local commit
# ---------------------------------------------------------------------------


class IntentAdapter:
    model_revision = "fake-intent"

    def __init__(self, state):
        self.state = state
        self.snapshot_at_call = []

    def intent(self, request):
        pid = next(iter(self.state.store.projects))
        self.snapshot_at_call.append(self.state.store.projects[pid].doc.get("current_snapshot_id"))
        return {"english_query": "Mentha drought stress phenotyping", "object": "Mentha",
                "task": "drought stress", "methods": ["imaging"], "synonyms": []}

    def rerank(self, request):
        raise RuntimeError("no rerank in this test")


def test_the_intent_reading_runs_in_its_own_job_after_the_local_snapshot(env, monkeypatch):
    from litsearch.v32 import api as api_mod
    from litsearch.v32 import pipeline

    monkeypatch.setenv(intent_ai.ENV_SWITCH, "1")
    intent_ai._CACHE.clear()
    application, state = env(Sources())
    adapter = IntentAdapter(state)
    monkeypatch.setattr(api_mod, "_resolve_model_adapter", lambda state, session: adapter)
    # Local ranking must never produce a reading itself.
    real_interpret = intent_ai.interpret
    in_rank = {"value": False}
    real_rank = pipeline.rank_project

    def guarded_rank(*args, **kwargs):
        in_rank["value"] = True
        try:
            return real_rank(*args, **kwargs)
        finally:
            in_rank["value"] = False

    def guarded_interpret(text, model):
        assert not in_rank["value"], "local ranking called the model"
        return real_interpret(text, model)

    monkeypatch.setattr(pipeline, "rank_project", guarded_rank)
    monkeypatch.setattr(intent_ai, "interpret", guarded_interpret)
    with TestClient(application) as client:
        csrf = _csrf(client)
        pid = _project(client, csrf, "intent")
        discover = _discover(client, state, csrf, pid, "ai_enhanced", "disc-intent")
        result = _result(state, discover)
        assert result["ai_status"] == "queued"
        intent_job = state.jobs.jobs[result["ai_job_id"]]
        assert intent_job.doc["kind"] == "intent"
        finished = _wait(client, state, pid, intent_job.job_id)
        assert finished["state"] == "succeeded", finished
        # The model was asked only after the discover snapshot was committed.
        assert adapter.snapshot_at_call == [discover["snapshot_id"]]
        assert intent_ai.cached("mint drought phenotyping") is not None
        rerank_id = _result(state, finished)["rerank_job_id"]
        assert rerank_id
        assert _wait(client, state, pid, rerank_id)["state"] == "succeeded"


# ---------------------------------------------------------------------------
# G11: per-session retrieval keys, and 429 in coverage
# ---------------------------------------------------------------------------


def test_a_session_retrieval_key_is_used_for_that_session_only(env):
    sources = Sources()
    application, state = env(sources)
    with TestClient(application) as a, TestClient(application) as b:
        csrf_a, csrf_b = _csrf(a), _csrf(b)
        response = a.put("/api/v3/settings/credentials/semantic_scholar",
                         json={"kind": "retrieval", "secret": "s2k-session-a-000000000000"},
                         headers={"X-CSRF-Token": csrf_a})
        assert response.status_code == 200, response.text
        pid_a = _project(a, csrf_a, "keys a")
        pid_b = _project(b, csrf_b, "keys b")
        _discover(a, state, csrf_a, pid_a, "basic", "keys-disc-a")
        _discover(b, state, csrf_b, pid_b, "basic", "keys-disc-b")
    assert sources.log[0]["keys"] == {"semantic_scholar": "s2k-session-a-000000000000"}
    assert sources.log[1]["keys"] == {}
    # The shared client was never re-keyed.
    assert state.source_manager is sources and sources.keys == {}


def test_a_provider_429_is_visible_in_the_job_coverage(env):
    application, state = env(Sources(statuses={"semantic_scholar": RetrievalStatus.RATE_LIMITED}))
    with TestClient(application) as client:
        csrf = _csrf(client)
        pid = _project(client, csrf, "quota")
        finished = _discover(client, state, csrf, pid, "basic", "quota-disc")
        assert "rate_limited:semantic_scholar" in finished["coverage"]["stop_reasons"]
        assert "semantic_scholar" in finished["coverage"]["failed_sources"]


def test_source_manager_scopes_session_keys_without_touching_the_shared_one(monkeypatch, tmp_path):
    from litsearch.cache import Cache
    from litsearch.sources import SourceManager

    monkeypatch.delenv("S2_API_KEY", raising=False)
    monkeypatch.setenv("OPENALEX_API_KEY", "deployment-openalex")
    shared = SourceManager(Cache(str(tmp_path / "cache.db")))
    scoped = shared.with_api_keys({"semantic_scholar": "s2k-session-0000000000000"})
    assert scoped is not shared
    assert scoped.s2.api_key() == "s2k-session-0000000000000"
    assert shared.s2.api_key() == ""
    # A provider without a session key keeps the deployment's configuration.
    assert scoped.oa.api_key() == "deployment-openalex"
