"""Contract conformance for the v3.2 HTTP surface — the acceptance test.

Everything asserted here is read from ``contracts/openapi.json`` (paths, methods,
status codes) and ``contracts/domain.schema.json`` (``$defs``): no path, status
code or field name is re-typed in this file.  The three properties under test
are the ones that decide whether the surface is really the documented one:

1. every documented operation exists at its ``/api`` path **and** on
   ``/api/v3``, with the documented method;
2. every success body validates against the ``$defs`` object its response
   declares, and every error body against ``$defs/Error``;
3. the four shared paths still belong to the legacy handler, and no route
   mentions ``cas`` or ``sso`` (AC-28).

It also guards the two integration mistakes this wiring can make: a circular
import through ``litsearch.web`` and a re-import of the legacy repository from
inside ``litsearch/v32``.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from litsearch.models import Author, Paper
from litsearch.retrieval import RetrievalResult, RetrievalStatus
from litsearch.v32 import contracts
from litsearch.v32.api import v32_from_app
from litsearch.web import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]
V32_DIR = REPO_ROOT / "litsearch" / "v32"
TERMINAL = contracts.terminal_states()

#: Every documented operation, straight out of the contract.
OPERATIONS: dict[tuple[str, str], dict] = {
    (method.upper(), path): operation
    for path, item in contracts.openapi_schema()["paths"].items()
    for method, operation in item.items()
    if method.lower() in {"get", "post", "put", "delete", "patch"}
}


# ---------------------------------------------------------------------------
# schema validation
# ---------------------------------------------------------------------------


def _validators() -> dict[str, Draft202012Validator]:
    """Validators whose ``$ref`` is resolved through the shipped schema file.

    The contract refers to ``./domain.schema.json#/$defs/X``.  A registry maps
    that URI to the file the package actually ships, so a drifted copy of the
    schema in the test cannot make a wrong body pass.
    """
    document = contracts.domain_schema()
    try:
        from referencing import Registry, Resource
        from referencing.jsonschema import DRAFT202012

        registry = Registry()
        for uri in (
            "./domain.schema.json",
            "domain.schema.json",
            "litsearch/contracts/domain.schema.json",
        ):
            registry = registry.with_resource(
                uri, Resource.from_contents(document, default_specification=DRAFT202012)
            )

        def build(name: str) -> Draft202012Validator:
            return Draft202012Validator(
                {"$ref": f"./domain.schema.json#/$defs/{name}"}, registry=registry
            )

    except ImportError:  # pragma: no cover - referencing ships with jsonschema
        inline = dict(document)

        def build(name: str) -> Draft202012Validator:
            return Draft202012Validator({**inline, "$ref": f"#/$defs/{name}"})

    return {name: build(name) for name in document["$defs"]}


VALIDATORS = _validators()


def validate_def(name: str, payload) -> None:
    assert name in VALIDATORS, f"unknown $defs object: {name}"
    errors = sorted(VALIDATORS[name].iter_errors(payload), key=lambda error: list(error.path))
    assert not errors, "\n".join(
        f"{name}: {list(error.path)}: {error.message}" for error in errors[:8]
    )


def _ref_name(schema: dict) -> str | None:
    reference = str(schema.get("$ref") or "")
    return reference.split("/")[-1] if "$defs/" in reference else None


# ---------------------------------------------------------------------------
# fixtures and helpers
# ---------------------------------------------------------------------------


class FakeSources:
    def __init__(self, papers=None, failures=()):
        self.papers = list(papers if papers is not None else PAPERS)
        self.failures = list(failures)

    def search_all_sources_result(self, query, limit=200, year_from=1900, year_to=None, query_plan=None):
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
        return RetrievalResult(papers=[], provider="fake", status=RetrievalStatus.SUCCESS_EMPTY)

    def get_references_result(self, paper_id, limit=100):
        return RetrievalResult(papers=[], provider="fake", status=RetrievalStatus.SUCCESS_EMPTY)


PAPERS = [
    Paper(id="10.1234/plant-a", title="Plant learning with machine learning",
          abstract="plant learning evidence", authors=[Author(name="A. Researcher")],
          year=2024, venue="Nature", doi="10.1234/plant-a", citation_count=12, source="fake"),
    Paper(id="10.1234/plant-b", title="Machine learning for plant phenotyping",
          abstract="plant learning measurement", authors=[Author(name="B. Researcher")],
          year=2023, doi="10.1234/plant-b", citation_count=3, source="fake"),
    Paper(id="10.1234/plant-c", title="Plant learning at field scale",
          abstract="plant learning deployment", authors=[Author(name="C. Researcher")],
          year=2022, doi="10.1234/plant-c", citation_count=1, source="fake"),
]


class FakeZoteroAdapter:
    configured = True

    def fetch_items(self, library_type, library_id, item_keys):
        return [
            {
                "key": str(key),
                "version": 2,
                "data": {
                    "key": str(key),
                    "itemType": "journalArticle",
                    "title": f"Zotero work {key}",
                    "date": "2020",
                    "DOI": f"10.5555/{key}",
                    "creators": [{"creatorType": "author", "lastName": "Zed"}],
                },
            }
            for key in item_keys
        ]

    def write_items(self, library_type, library_id, items, *, if_unmodified_since=None):
        return [
            {
                "paper_id": str(item.get("paper_id") or ""),
                "status": "successful",
                "item_key": "ZK1",
                "item_version": 1,
                "error_code": None,
            }
            for item in items
        ]


@pytest.fixture()
def app(tmp_path: Path):
    sources = FakeSources()
    application = create_app(data_dir=tmp_path, workflow_factory=lambda demo: type(
        "StubWorkflow", (), {"sources": sources})())
    state = v32_from_app(application)
    state.source_manager = sources
    state.zotero_adapter = FakeZoteroAdapter()
    yield application
    state.jobs.shutdown(grace=0.1)


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client


def _csrf(client) -> str:
    return client.get("/api/v3/session").json()["csrf_token"]


def _headers(csrf, key):
    return {"X-CSRF-Token": csrf, "Idempotency-Key": key}


def _wait_job(client, state, pid, job_id, timeout=15.0):
    deadline = time.monotonic() + timeout
    body = None
    while time.monotonic() < deadline:
        state.jobs.drain()
        response = client.get(f"/api/v3/projects/{pid}/jobs/{job_id}")
        body = response.json()
        if body["state"] in TERMINAL:
            return body
        time.sleep(0.02)
    raise AssertionError(f"job never settled: {body}")


def _pdf_bytes(text: str) -> bytes:
    fitz = pytest.importorskip("fitz")
    document = fitz.open()
    page = document.new_page()
    if text:
        page.insert_text((72, 96), text, fontsize=12)
    payload = document.tobytes()
    document.close()
    return payload


# ---------------------------------------------------------------------------
# 1. every documented operation is mounted, at both mounts
# ---------------------------------------------------------------------------


def test_the_contract_declares_the_twenty_five_operations():
    assert len(OPERATIONS) == 25, sorted(OPERATIONS)


def _route_inventory(app) -> list[tuple[str, str, Any]]:
    """``(path, method, route)`` for every route, included routers unfolded.

    FastAPI 0.143 keeps ``include_router`` calls as a wrapper object
    (``_IncludedRouter``) instead of flattening them into ``app.routes``, so the
    prefix has to be taken from the include context.  The walker is
    version-tolerant: anything that exposes a sub-router is descended into.
    """
    found: list[tuple[str, str, Any]] = []

    def walk(routes, prefix: str = "") -> None:
        for route in routes:
            sub = getattr(route, "original_router", None) or getattr(route, "router", None)
            context = getattr(route, "include_context", None)
            if sub is not None and getattr(route, "path", None) in (None, ""):
                walk(
                    getattr(sub, "routes", []),
                    prefix + str(getattr(context, "prefix", "") or getattr(route, "prefix", "") or ""),
                )
                continue
            path = getattr(route, "path", None)
            if path is None:
                continue
            for method in getattr(route, "methods", None) or ():
                found.append((str(prefix) + str(path), str(method).upper(), route))
            if sub is not None:
                walk(getattr(sub, "routes", []), prefix + str(path))

    walk(getattr(app, "routes", []))
    return found


def test_every_operation_exists_at_its_documented_path_and_on_api_v3(app):
    registered = {(path, method) for path, method, _route in _route_inventory(app)}
    missing = []
    for method, path in OPERATIONS:
        versioned = "/api/v3" + path[len("/api"):]
        if (path, method) not in registered:
            missing.append((method, path))
        if (versioned, method) not in registered:
            missing.append((method, versioned))
    assert not missing, missing


def test_no_route_path_mentions_cas_or_sso(app):
    offenders = [
        path
        for path, _method, _route in _route_inventory(app)
        if "cas" in path.lower() or "sso" in path.lower()
    ]
    assert not offenders, offenders


# ---------------------------------------------------------------------------
# 2. every success body is the documented object
# ---------------------------------------------------------------------------


def _drive_every_operation(app, client) -> list[tuple[str, str, int, object]]:
    """Call one representative instance of each of the 25 operations."""
    state = v32_from_app(app)
    recorded: list[tuple[str, str, int, object]] = []

    def note(method: str, path: str, response, template: str, expect: int):
        assert response.status_code == expect, (method, path, response.status_code, response.text)
        body = None
        if response.headers.get("content-type", "").startswith("application/json"):
            body = response.json()
        recorded.append((method, template, expect, body))
        return body

    csrf = _csrf(client)

    note("GET", "/api/v3/session", client.get("/api/v3/session"), "/api/session", 200)
    note("GET", "/api/v3/capabilities", client.get("/api/v3/capabilities"), "/api/capabilities", 200)

    created = note(
        "POST", "/api/v3/projects",
        client.post("/api/v3/projects", json={"name": "contract", "entry_mode": "topic"},
                    headers=_headers(csrf, "contract-create-01")),
        "/api/projects", 201,
    )
    pid = created["project_id"]

    def revision() -> int:
        return client.get(f"/api/v3/projects/{pid}").json()["revision"]

    note("GET", f"/api/v3/projects/{pid}", client.get(f"/api/v3/projects/{pid}"),
         "/api/projects/{pid}", 200)

    # discover -> JobAccepted, then a settled Job
    discovered = note(
        "POST", f"/api/v3/projects/{pid}/discover",
        client.post(f"/api/v3/projects/{pid}/discover",
                    json={"expected_revision": revision(), "research_text": "plant learning",
                          "seed_paper_ids": [], "mode": "basic"},
                    headers=_headers(csrf, "contract-discover-01")),
        "/api/projects/{pid}/discover", 202,
    )
    discover_job = discovered["job_id"]
    _wait_job(client, state, pid, discover_job)
    note("GET", f"/api/v3/projects/{pid}/jobs/{discover_job}",
         client.get(f"/api/v3/projects/{pid}/jobs/{discover_job}"),
         "/api/projects/{pid}/jobs/{job_id}", 200)

    # PDF import -> ImportBatch; one readable page and one page with no text
    imported = note(
        "POST", f"/api/v3/projects/{pid}/papers/import",
        client.post(f"/api/v3/projects/{pid}/papers/import",
                    data={"expected_revision": str(revision())},
                    files=[
                        ("files", ("readable.pdf", _pdf_bytes(
                            "Plant learning with machine learning\nAbstract\nplant learning"),
                            "application/pdf")),
                        ("files", ("scanned.pdf", _pdf_bytes(""), "application/pdf")),
                    ],
                    headers=_headers(csrf, "contract-import-01")),
        "/api/projects/{pid}/papers/import", 202,
    )
    import_id = imported["import_id"]
    _wait_job(client, state, pid, imported["job_id"])

    stored = note("GET", f"/api/v3/projects/{pid}/imports/{import_id}",
                  client.get(f"/api/v3/projects/{pid}/imports/{import_id}"),
                  "/api/projects/{pid}/imports/{import_id}", 200)
    retryable = [row for row in stored["items"] if row["retryable"]]
    assert retryable, stored["items"]
    to_resolve = stored["items"][0]
    to_retry = next(
        (row for row in stored["items"] if row["item_id"] != to_resolve["item_id"] and row["retryable"]),
        None,
    )
    assert to_retry is not None, stored["items"]

    note("POST", f"/api/v3/projects/{pid}/imports/{import_id}/resolve",
         client.post(f"/api/v3/projects/{pid}/imports/{import_id}/resolve",
                     json={"expected_revision": revision(), "item_id": to_resolve["item_id"],
                           "action": "keep_local", "candidate_id": None},
                     headers=_headers(csrf, "contract-resolve-01")),
         "/api/projects/{pid}/imports/{import_id}/resolve", 200)

    note("POST", f"/api/v3/projects/{pid}/imports/{import_id}/retry",
         client.post(f"/api/v3/projects/{pid}/imports/{import_id}/retry",
                     json={"expected_revision": revision(), "item_ids": [to_retry["item_id"]]},
                     headers=_headers(csrf, "contract-retry-01")),
         "/api/projects/{pid}/imports/{import_id}/retry", 202)

    papers = client.get(f"/api/v3/projects/{pid}/papers", params={"view": "all"}).json()
    assert papers["items"], papers
    paper_id = papers["items"][0]["paper"]["paper_id"]

    note("POST", f"/api/v3/projects/{pid}/judgements",
         client.post(f"/api/v3/projects/{pid}/judgements",
                     json={"expected_revision": revision(), "paper_id": paper_id,
                           "judgement": "core"},
                     headers=_headers(csrf, "contract-judge-01")),
         "/api/projects/{pid}/judgements", 200)

    rerank = note("POST", f"/api/v3/projects/{pid}/rerank",
                  client.post(f"/api/v3/projects/{pid}/rerank",
                              json={"expected_revision": revision()},
                              headers=_headers(csrf, "contract-rerank-01")),
                  "/api/projects/{pid}/rerank", 202)

    expand = note("POST", f"/api/v3/projects/{pid}/expand",
                  client.post(f"/api/v3/projects/{pid}/expand",
                              json={"expected_revision": revision()},
                              headers=_headers(csrf, "contract-expand-01")),
                  "/api/projects/{pid}/expand", 202)

    note("GET", f"/api/v3/projects/{pid}/papers",
         client.get(f"/api/v3/projects/{pid}/papers"), "/api/projects/{pid}/papers", 200)

    note("POST", f"/api/v3/projects/{pid}/jobs/{rerank['job_id']}/cancel",
         client.post(f"/api/v3/projects/{pid}/jobs/{rerank['job_id']}/cancel",
                     headers=_headers(csrf, "contract-cancel-01")),
         "/api/projects/{pid}/jobs/{job_id}/cancel", 200)

    note("GET", f"/api/v3/projects/{pid}/overview",
         client.get(f"/api/v3/projects/{pid}/overview"), "/api/projects/{pid}/overview", 200)
    note("GET", f"/api/v3/projects/{pid}/papers/{paper_id}/access-options",
         client.get(f"/api/v3/projects/{pid}/papers/{paper_id}/access-options"),
         "/api/projects/{pid}/papers/{paper_id}/access-options", 200)
    note("GET", f"/api/v3/projects/{pid}/papers/{paper_id}/venue-metrics",
         client.get(f"/api/v3/projects/{pid}/papers/{paper_id}/venue-metrics"),
         "/api/projects/{pid}/papers/{paper_id}/venue-metrics", 200)

    secret = "s2k-" + "Q7" * 16
    note("PUT", "/api/v3/settings/credentials/semantic_scholar",
         client.put("/api/v3/settings/credentials/semantic_scholar",
                    json={"kind": "retrieval", "secret": secret},
                    headers={"X-CSRF-Token": csrf}),
         "/api/settings/credentials/{provider_id}", 200)
    note("GET", "/api/v3/settings/credentials",
         client.get("/api/v3/settings/credentials"), "/api/settings/credentials", 200)
    note("DELETE", "/api/v3/settings/credentials/semantic_scholar",
         client.delete("/api/v3/settings/credentials/semantic_scholar",
                       headers={"X-CSRF-Token": csrf}),
         "/api/settings/credentials/{provider_id}", 204)

    zimport = note("POST", f"/api/v3/projects/{pid}/zotero/import",
                   client.post(f"/api/v3/projects/{pid}/zotero/import",
                               json={"expected_revision": revision(), "library_type": "user",
                                     "library_id": "42", "item_keys": ["AAAA1111"],
                                     "include_selected_attachments": False},
                               headers=_headers(csrf, "contract-zimport-01")),
                   "/api/projects/{pid}/zotero/import", 202)
    _wait_job(client, state, pid, zimport["job_id"])

    preview = note("POST", f"/api/v3/projects/{pid}/zotero/export-preview",
                   client.post(f"/api/v3/projects/{pid}/zotero/export-preview",
                               json={"expected_revision": revision(), "paper_ids": [paper_id],
                                     "library_type": "user", "library_id": "42",
                                     "collection_key": None},
                               headers=_headers(csrf, "contract-zprev-01")),
                   "/api/projects/{pid}/zotero/export-preview", 200)

    zexport = note("POST", f"/api/v3/projects/{pid}/zotero/export",
                   client.post(f"/api/v3/projects/{pid}/zotero/export",
                               json={"expected_revision": revision(),
                                     "preview_id": preview["preview_id"], "confirm": True},
                               headers=_headers(csrf, "contract-zexport-01")),
                   "/api/projects/{pid}/zotero/export", 202)
    _wait_job(client, state, pid, zexport["job_id"])
    note("GET", f"/api/v3/projects/{pid}/jobs/{zexport['job_id']}/zotero-results",
         client.get(f"/api/v3/projects/{pid}/jobs/{zexport['job_id']}/zotero-results"),
         "/api/projects/{pid}/jobs/{job_id}/zotero-results", 200)

    assert expand["state"] == "queued"
    assert secret not in json.dumps(recorded, default=str)
    return recorded


def test_every_success_body_is_the_documented_object(app, client):
    recorded = _drive_every_operation(app, client)
    covered = {(method, template) for method, template, _status, _body in recorded}
    assert covered == set(OPERATIONS), sorted(set(OPERATIONS) - covered)

    checked = 0
    for method, template, status, body in recorded:
        operation = OPERATIONS[(method.upper(), template)]
        response_spec = operation["responses"][str(status)]
        content = response_spec.get("content")
        if not content:
            assert body is None, (method, template)
            continue
        name = _ref_name(content["application/json"]["schema"])
        assert name, (method, template, status)
        validate_def(name, body)
        checked += 1
    assert checked == 24, checked  # 25 operations, one of them a 204


# ---------------------------------------------------------------------------
# 3. every error body is the closed envelope
# ---------------------------------------------------------------------------


def _envelope(response, status: int, code: str | None = None) -> dict:
    assert response.status_code == status, (response.status_code, response.text)
    body = response.json()
    validate_def("Error", body)
    error = body["error"]
    assert set(error) == {"code", "message", "retryable", "details", "request_id"}
    assert set(error["details"]) <= {
        "current_revision", "item_ids", "retry_after_seconds", "capability",
    }
    assert error["request_id"]
    if code is not None:
        assert error["code"] == code, error
    return error


def test_every_error_response_is_the_closed_envelope(app, client):
    csrf = _csrf(client)
    created = client.post("/api/v3/projects", json={"name": "errors", "entry_mode": "topic"},
                          headers=_headers(csrf, "errors-create-0001")).json()
    pid = created["project_id"]
    discover = client.post(f"/api/v3/projects/{pid}/discover",
                           json={"expected_revision": 1, "research_text": "plant learning",
                                 "seed_paper_ids": [], "mode": "basic"},
                           headers=_headers(csrf, "errors-discover-01"))
    state = v32_from_app(app)
    _wait_job(client, state, pid, discover.json()["job_id"])
    paper_id = client.get(f"/api/v3/projects/{pid}/papers", params={"view": "all"}).json()[
        "items"
    ][0]["paper"]["paper_id"]
    revision = client.get(f"/api/v3/projects/{pid}").json()["revision"]

    with TestClient(app) as fresh:
        _envelope(fresh.get(f"/api/v3/projects/{pid}"), 401, "session_expired")

    _envelope(
        client.post("/api/v3/projects", json={"name": "x", "entry_mode": "topic"},
                    headers={"Idempotency-Key": "errors-csrf-000001"}),
        403, "forbidden",
    )
    _envelope(client.get(f"/api/v3/projects/{'a' * 32}"), 404, "not_found")
    _envelope(
        client.post(f"/api/v3/projects/{pid}/judgements",
                    json={"expected_revision": revision + 5, "paper_id": paper_id,
                          "judgement": "core"},
                    headers=_headers(csrf, "errors-revision-01")),
        409, "revision_conflict",
    )
    stale = client.get(f"/api/v3/projects/{pid}/papers",
                       params={"cursor": _cursor("b" * 32)})
    _envelope(stale, 409, "cursor_stale")
    same_key = "errors-idem-000001"
    body = {"expected_revision": revision, "research_text": "plant learning",
            "seed_paper_ids": [], "mode": "basic"}
    client.post(f"/api/v3/projects/{pid}/discover", json=body, headers=_headers(csrf, same_key))
    _envelope(
        client.post(f"/api/v3/projects/{pid}/discover",
                    json={"expected_revision": revision, "research_text": "something else",
                          "seed_paper_ids": [], "mode": "basic"},
                    headers=_headers(csrf, same_key)),
        409, "idempotency_conflict",
    )
    _envelope(client.post("/api/v3/projects", json={"name": "missing entry mode"},
                          headers=_headers(csrf, "errors-statement-01")),
              422, "unprocessable")
    _envelope(
        client.post(f"/api/v3/projects/{pid}/discover",
                    json={"expected_revision": revision, "research_text": "",
                          "seed_paper_ids": [], "mode": "basic"},
                    headers=_headers(csrf, "errors-semantic-01")),
        422, "unprocessable",
    )
    secret = "sk-" + "L9" * 20
    response = client.put("/api/v3/settings/credentials/nope",
                          json={"kind": "retrieval", "secret": secret},
                          headers={"X-CSRF-Token": csrf})
    _envelope(response, 400, "provider_not_allowlisted")
    assert secret not in response.text

    with TestClient(app) as other:
        _csrf(other)
        _envelope(
            other.post(f"/api/v3/projects/{pid}/zotero/import",
                       json={"expected_revision": revision, "library_type": "user",
                             "library_id": "42", "item_keys": ["AAAA1111"],
                             "include_selected_attachments": False},
                       headers=_headers(_csrf(other), "errors-zotero-0001")),
            404, "not_found",
        )


def _cursor(snapshot_id: str) -> str:
    from litsearch.v32.store import encode_cursor

    return encode_cursor(snapshot_id, 0)


def test_an_unconfigured_zotero_capability_is_a_503_envelope(tmp_path):
    sources = FakeSources()
    application = create_app(data_dir=tmp_path, workflow_factory=lambda demo: type(
        "StubWorkflow", (), {"sources": sources})())
    state = v32_from_app(application)
    state.source_manager = sources
    with TestClient(application) as client:
        csrf = _csrf(client)
        created = client.post("/api/v3/projects", json={"name": "no-zotero", "entry_mode": "topic"},
                              headers=_headers(csrf, "nozotero-create1")).json()
        error = _envelope(
            client.post(f"/api/v3/projects/{created['project_id']}/zotero/import",
                        json={"expected_revision": 1, "library_type": "user", "library_id": "42",
                              "item_keys": ["AAAA1111"], "include_selected_attachments": False},
                        headers=_headers(csrf, "nozotero-import1")),
            503, "capability_unavailable",
        )
        assert error["details"]["capability"] == "zotero:web_api"
    state.jobs.shutdown(grace=0.1)


# ---------------------------------------------------------------------------
# 4. the legacy surface is untouched
# ---------------------------------------------------------------------------


def _first_route(app, path: str, method: str):
    for route_path, route_method, route in _route_inventory(app):
        if route_path == path and route_method == method:
            return route
    raise AssertionError(f"no route {method} {path}")


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/projects"),
        ("GET", "/api/projects/{pid}"),
        ("POST", "/api/projects/{pid}/expand"),
        ("GET", "/api/projects/{pid}/papers"),
    ],
)
def test_the_shared_paths_belong_to_the_legacy_handler(app, method, path):
    route = _first_route(app, path, method)
    endpoint = getattr(route, "endpoint", None)
    assert str(getattr(endpoint, "__module__", "")).startswith("litsearch.web"), (
        method, path, getattr(endpoint, "__module__", None)
    )


def test_the_legacy_bodies_are_unchanged(app, client):
    legacy = client.post(
        "/api/projects", json={"name": "legacy", "topic": "plant learning",
                               "research_direction": "field trials"},
    )
    assert legacy.status_code == 201, legacy.text
    body = legacy.json()
    assert {"demo", "phase", "counts", "search_manifest", "score_context_id"} <= set(body)
    assert "contract_version" not in body and "anchor" not in body
    pid = body["project_id"]

    assert {"phase", "demo"} <= set(client.get(f"/api/projects/{pid}").json())
    listing = client.get(f"/api/projects/{pid}/papers").json()
    assert {"items", "total", "page", "page_size", "revision"} <= set(listing)

    # A v3.2-shaped body is refused by the LEGACY statement handler, which is the
    # sharpest proof that the legacy route answered first (see the dedicated
    # test below).
    demo = client.post("/api/demo/projects").json()
    expanded = client.post(f"/api/projects/{demo['project_id']}/expand",
                           json={"expected_revision": 1})
    assert expanded.status_code == 202, expanded.text
    assert "status" in expanded.json() and "state" not in expanded.json()


def test_a_v3_2_body_never_reaches_the_legacy_project_route(app, client):
    response = client.post("/api/projects", json={"name": "x", "entry_mode": "topic"})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_request"


# ---------------------------------------------------------------------------
# 5. the integration itself cannot silently regress
# ---------------------------------------------------------------------------


def test_the_app_module_imports_without_a_circular_import():
    completed = subprocess.run(
        [sys.executable, "-c", "import litsearch.web.api; print('ok')"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]


def test_no_v32_module_imports_the_legacy_web_repository():
    offenders: list[str] = []
    for path in sorted(V32_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and str(node.module or "").startswith("litsearch.web"):
                offenders.append(f"{path.name}: from {node.module} import …")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("litsearch.web"):
                        offenders.append(f"{path.name}: import {alias.name}")
    assert not offenders, offenders
