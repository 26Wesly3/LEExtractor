"""AC-30 / R19: v3.2 project export and PRISMA counts, plus N1 contract limits.

* ``GET /api/v3/projects/{pid}/export?format=ris|bibtex|csv|evidence_pack|prisma``
  reuses the legacy exporters for the bibliographic formats and carries
  provenance in the evidence pack (doc 16 "导出与工具衔接").
* An ordinary judgement (core / relevant / excluded) never changes the PRISMA
  counts (doc 09 AC-30).
* The evidence pack holds no key, token or file path (doc 10, doc 16).
* ``exclusion_reason = not_relevant`` is accepted and is the only reason that
  feeds negative feedback (doc 02 "人工判断").
* The v3.2 retrieval client honours the contract's concurrency, timeouts and
  retries; the legacy defaults are unchanged (doc 15).

No test reaches the network: the provider client is injected.
"""

from __future__ import annotations

import json
import re

import pytest

from litsearch.v32 import contracts
from litsearch.v32.api import v32_from_app
from litsearch.v32.feedback import query_vector
from litsearch.v32.types import ExclusionReason

from test_v32_api import (  # noqa: F401 - fixtures are used by name
    DEFAULT_PAPERS,
    _csrf,
    _headers,
    _project,
    _ranked_project,
    app,
    client,
)

FORMATS = ("ris", "bibtex", "csv", "evidence_pack", "prisma")


def _setup(client):
    csrf = _csrf(client)
    pid = _project(client, csrf)["project_id"]
    _ranked_project(client, pid, csrf)
    return csrf, pid


def _paper_ids(client, pid):
    listing = client.get(f"/api/v3/projects/{pid}/papers", params={"view": "all"}).json()
    return [row["paper"]["paper_id"] for row in listing["items"]]


def _judge(client, csrf, pid, paper_id, value, key, reason="unspecified"):
    revision = client.get(f"/api/v3/projects/{pid}").json()["revision"]
    response = client.post(
        f"/api/v3/projects/{pid}/judgements",
        json={"expected_revision": revision, "paper_id": paper_id, "judgement": value,
              "exclusion_reason": reason},
        headers=_headers(csrf, key=key.ljust(16, "j")),
    )
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# the route
# ---------------------------------------------------------------------------


def test_every_export_format_is_served_on_both_mounts(client):
    _csrf_token, pid = _setup(client)
    for fmt in FORMATS:
        for prefix in ("/api/v3", "/api"):
            response = client.get(f"{prefix}/projects/{pid}/export", params={"format": fmt})
            assert response.status_code == 200, (prefix, fmt, response.text)
            assert response.headers.get("content-disposition", "").startswith("attachment"), fmt


def test_bibliographic_exports_reuse_the_legacy_exporters_over_v32_records(client):
    _csrf_token, pid = _setup(client)
    ris = client.get(f"/api/v3/projects/{pid}/export", params={"format": "ris"}).text
    assert ris.count("TY  - JOUR") == len(DEFAULT_PAPERS)
    assert "DO  - 10.1234/plant-a" in ris
    assert "TI  - Plant learning with machine learning" in ris

    bib = client.get(f"/api/v3/projects/{pid}/export", params={"format": "bibtex"}).text
    assert bib.count("@article{") == len(DEFAULT_PAPERS)
    assert "doi = {10.1234/plant-a}" in bib

    csv_body = client.get(f"/api/v3/projects/{pid}/export", params={"format": "csv"})
    text = csv_body.content.decode("utf-8-sig")
    assert text.splitlines()[0].startswith("Title,Authors,Year,Venue,DOI")
    assert len([line for line in text.splitlines() if line.strip()]) == len(DEFAULT_PAPERS) + 1


def test_an_unknown_format_is_the_contract_error_envelope(client):
    _csrf_token, pid = _setup(client)
    response = client.get(f"/api/v3/projects/{pid}/export", params={"format": "docx"})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "unprocessable"
    missing = client.get(f"/api/v3/projects/{pid}/export")
    assert missing.status_code == 422, missing.text
    assert missing.json()["error"]["code"] == "unprocessable"


def test_another_owner_cannot_export_a_project(app, client):
    _csrf_token, pid = _setup(client)
    from fastapi.testclient import TestClient

    with TestClient(app) as stranger:
        stranger.get("/api/v3/session")
        response = stranger.get(f"/api/v3/projects/{pid}/export", params={"format": "evidence_pack"})
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"


# ---------------------------------------------------------------------------
# PRISMA (AC-30: an ordinary judgement does not change PRISMA)
# ---------------------------------------------------------------------------


def _prisma(client, pid):
    response = client.get(f"/api/v3/projects/{pid}/export", params={"format": "prisma"})
    assert response.status_code == 200, response.text
    return response.json()


def test_prisma_counts_come_from_v32_records_and_name_what_is_unavailable(client):
    _csrf_token, pid = _setup(client)
    body = _prisma(client, pid)
    assert body["basis"] == "v3.2_project_records"
    assert body["identification"]["unique_records"] == len(DEFAULT_PAPERS)
    assert body["identification"]["records_by_source"] == {"fake": len(DEFAULT_PAPERS)}
    unavailable = {row["count"] for row in body["not_available"]}
    assert {"records_screened", "records_excluded", "reports_assessed", "studies_included"} <= unavailable
    assert all(row["reason"] for row in body["not_available"])
    assert body["judgements_affect_counts"] is False


def test_core_relevant_and_excluded_judgements_leave_prisma_unchanged(client):
    csrf, pid = _setup(client)
    before = _prisma(client, pid)
    ids = _paper_ids(client, pid)
    _judge(client, csrf, pid, ids[0], "core", "prisma-core")
    _judge(client, csrf, pid, ids[1], "relevant", "prisma-relevant")
    _judge(client, csrf, pid, ids[2], "excluded", "prisma-excluded", reason="not_relevant")
    v32_from_app(client.app).jobs.drain()
    after = _prisma(client, pid)
    assert after == before


# ---------------------------------------------------------------------------
# evidence pack
# ---------------------------------------------------------------------------


def test_the_evidence_pack_carries_provenance(client):
    csrf, pid = _setup(client)
    ids = _paper_ids(client, pid)
    _judge(client, csrf, pid, ids[0], "core", "pack-core")
    pack = client.get(f"/api/v3/projects/{pid}/export", params={"format": "evidence_pack"}).json()

    project = client.get(f"/api/v3/projects/{pid}").json()
    assert pack["project_id"] == pid
    assert pack["snapshot_id"] == project["current_snapshot_id"]
    assert pack["exported_at"]
    assert pack["licence_note"]
    assert pack["query"]["original_text"] == "plant learning"
    by_id = {paper["paper_id"]: paper for paper in pack["papers"]}
    assert set(by_id) == set(ids)
    sample = next(paper for paper in pack["papers"] if paper["doi"] == "10.1234/plant-a")
    assert "10.1234/plant-a" in sample["aliases"]
    assert sample["discovery_sources"], "every exported paper says where it came from"
    assert all(row["provider"] and row["retrieved_at"] for row in sample["discovery_sources"])
    assert by_id[ids[0]]["judgement"]["value"] == "core"
    assert pack["judgements"][0]["paper_id"] == ids[0]


_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/])|(?:\\\\)|(?:/(?:Users|home|tmp|var|workspace|e)/)|\.bin\b|\.pdf\b")
_KEY_FIELD_RE = re.compile(
    r"(?:^|[_\-.])(api[_-]?key|apikey|secret|token|password|credential|authorization|path|file)(?:$|[_\-.])",
    re.I,
)


def _keys(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from _keys(value)
    elif isinstance(node, list):
        for value in node:
            yield from _keys(value)


def test_the_evidence_pack_contains_no_key_token_or_path(app, client, tmp_path):
    csrf, pid = _setup(client)
    secret = "s2k-" + "Q" * 32
    written = client.put(
        "/api/v3/settings/credentials/semantic_scholar",
        json={"kind": "retrieval", "secret": secret},
        headers={"X-CSRF-Token": csrf},
    )
    assert written.status_code == 200, written.text

    state = v32_from_app(app)
    project = state.store.projects[pid]
    attachment = state.store.add_attachment(project, b"%PDF-1.4 fake", "C:\\Users\\me\\private.pdf")
    # A field the pack does not whitelist must never reach it, whatever it holds.
    paper_id = next(iter(project.doc["papers"]))
    project.doc["papers"][paper_id]["local_pdf_path"] = str(tmp_path / "hidden.pdf")

    response = client.get(f"/api/v3/projects/{pid}/export", params={"format": "evidence_pack"})
    assert response.status_code == 200, response.text
    text = response.text
    pack = response.json()

    assert secret not in text
    assert str(tmp_path) not in text and str(tmp_path).replace("\\", "\\\\") not in text
    assert str(state.data_dir) not in text
    assert attachment["attachment_id"] not in text
    assert "private.pdf" not in text and "hidden.pdf" not in text
    assert not _PATH_RE.search(text), _PATH_RE.search(text)
    offenders = [key for key in _keys(pack) if _KEY_FIELD_RE.search(key)]
    assert not offenders, offenders


# ---------------------------------------------------------------------------
# N1d: not_relevant is accepted and is the only negative-feedback reason
# ---------------------------------------------------------------------------


def test_every_exclusion_reason_is_accepted_and_only_not_relevant_is_negative(client):
    csrf, pid = _setup(client)
    target = _paper_ids(client, pid)[0]
    q_initial = [1.0, 0.0]
    vectors = {target: [0.0, 1.0]}
    state = v32_from_app(client.app)

    shifted = {}
    for index, reason in enumerate(sorted(str(r) for r in ExclusionReason)):
        saved = _judge(client, csrf, pid, target, "excluded", f"reason-{index}", reason=reason)
        assert saved["judgement"]["exclusion_reason"] == reason
        stored = state.store.projects[pid].doc["judgements"]
        assert stored[target]["exclusion_reason"] == reason
        shifted[reason] = query_vector(q_initial, stored, vectors) != q_initial

    assert shifted.pop("not_relevant") is True
    assert not any(shifted.values()), shifted


# ---------------------------------------------------------------------------
# N1a/b: the v3.2 retrieval client honours the contract limits
# ---------------------------------------------------------------------------


def test_the_v32_retrieval_client_uses_the_contract_limits(tmp_path):
    from litsearch.cache import Cache
    from litsearch.sources import (
        DEFAULT_CONNECT_TIMEOUT,
        DEFAULT_PROVIDER_CONCURRENCY,
        DEFAULT_READ_TIMEOUT,
        BaseSource,
        SourceManager,
    )
    from litsearch.v32.pipeline import V32State
    from litsearch.v32.store import V32Store

    discovery = contracts.discovery()
    state = V32State(
        store=V32Store(tmp_path / "v32"), jobs=None, sessions=None, credentials=None, data_dir=tmp_path
    )
    client = state.retrieval()
    assert isinstance(client, SourceManager)
    connect = float(discovery["provider_connect_timeout_seconds"])
    total = float(discovery["provider_total_timeout_seconds"])
    assert client.effective_provider_concurrency() == discovery["provider_concurrency"] == 3
    for source in client.all_sources():
        assert source._timeouts() == (connect, total - connect)
        assert source.MAX_HTTP_RETRIES == discovery["provider_retries"] == 1

    # A session-scoped copy keeps the same limits.
    scoped = client.with_api_keys({"semantic_scholar": "s2k-" + "A" * 24})
    assert scoped.effective_provider_concurrency() == 3
    assert all(source._timeouts() == (connect, total - connect) for source in scoped.all_sources())

    # The legacy path is untouched.
    legacy = SourceManager(Cache(str(tmp_path / "legacy.db")))
    assert legacy.effective_provider_concurrency() == DEFAULT_PROVIDER_CONCURRENCY
    for source in legacy.all_sources():
        assert source._timeouts() == (DEFAULT_CONNECT_TIMEOUT, DEFAULT_READ_TIMEOUT)
        assert source.MAX_HTTP_RETRIES == BaseSource.MAX_HTTP_RETRIES


@pytest.mark.parametrize("fmt", FORMATS)
def test_export_bodies_are_utf8(client, fmt):
    _csrf_token, pid = _setup(client)
    response = client.get(f"/api/v3/projects/{pid}/export", params={"format": fmt})
    assert response.status_code == 200
    response.content.decode("utf-8-sig")
    if fmt in {"evidence_pack", "prisma"}:
        json.loads(response.content.decode("utf-8"))
