"""M5 Zotero adapter: identity, idempotency, per-item outcomes, preview binding.

The network is injected everywhere; nothing in this file contacts Zotero.  The
live Zotero integration is ``external_blocked`` in this environment (no
credential or endpoint to verify against), so no test here claims a real round
trip — they claim the adapter's contract.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from litsearch.v32 import contracts
from litsearch.v32 import zotero as Z
from litsearch.v32.errors import V32Error

FAKE_SECRET = "TEST-NOT-A-REAL-ZOTERO-KEY-abcdef0123456789"
LIBRARY = {"library_type": "user", "library_id": "123456", "collection_key": None}


def _validator(definition: str) -> Draft202012Validator:
    schema = contracts.domain_schema()
    sub = dict(schema["$defs"][definition])
    sub["$defs"] = schema["$defs"]
    return Draft202012Validator(sub)


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 17, 0, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


class FakeTransport:
    """A transport the test drives; records every call."""

    def __init__(self, handler) -> None:
        self.handler = handler
        self.calls: list[dict[str, Any]] = []

    def request(self, method, url, *, headers=None, json_body=None, timeout=None):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers or {}),
                "json": json_body,
            }
        )
        outcome = self.handler(method, url, json_body, len(self.calls))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    @property
    def posts(self) -> list[dict[str, Any]]:
        return [call for call in self.calls if call["method"] == "POST"]


def api_item(
    key: str,
    title: str,
    *,
    item_type: str = "journalArticle",
    version: int = 1,
    doi: str | None = None,
    collection: str | None = None,
    creators: list[dict[str, Any]] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {"key": key, "itemType": item_type, "title": title, "version": version}
    if doi:
        data["DOI"] = doi
    if collection:
        data["collections"] = [collection]
    if creators:
        data["creators"] = creators
    if tags:
        data["tags"] = [{"tag": tag} for tag in tags]
    return {"key": key, "version": version, "data": data, "meta": {"version": version}}


def adapter(handler, *, settings=None) -> tuple[Z.ZoteroWebAdapter, FakeTransport]:
    """A configured adapter whose transport is the test's fake."""
    transport = FakeTransport(handler)
    merged = {"api_key": FAKE_SECRET, "library_type": "user", "library_id": "123456"}
    merged.update(settings or {})
    return Z.ZoteroWebAdapter(merged, transport=transport), transport


class FakeExportAdapter:
    def __init__(self, results=None) -> None:
        self.results = results or {}
        self.calls: list[dict[str, Any]] = []

    def write_items(self, library_type, library_id, items, *, if_unmodified_since=None):
        self.calls.append(
            {
                "library_type": library_type,
                "library_id": library_id,
                "items": [dict(item) for item in items],
                "if_unmodified_since": if_unmodified_since,
            }
        )
        produced = []
        for item in items:
            produced.append(
                self.results.get(
                    item["paper_id"],
                    {
                        "paper_id": item["paper_id"],
                        "status": "successful",
                        "item_key": "KEY-" + item["paper_id"],
                        "item_version": 3,
                        "error_code": None,
                    },
                )
            )
        return produced


# --------------------------------------------------------------------------
# capabilities and the local-API boundary
# --------------------------------------------------------------------------


def test_capabilities_report_an_unconfigured_adapter_honestly():
    caps = Z.create_adapter({}).capabilities()
    zotero = contracts.zotero_defaults()
    discovery = contracts.discovery()

    assert caps["configured"] is False
    assert caps["status"] == "not_configured"
    assert caps["reason_code"] == "api_key_required"
    assert caps["adapter"] == zotero["default_adapter"]
    assert caps["local_desktop_api"] is False
    assert caps["upload_fulltext_by_default"] is False
    assert caps["auto_export"] == zotero["auto_export"]
    assert caps["attachment_download"] == "requires_explicit_selection"
    assert caps["timeout_seconds"] == discovery["provider_total_timeout_seconds"]
    assert caps["retries"] == discovery["provider_retries"]
    assert FAKE_SECRET not in json.dumps(caps, ensure_ascii=False)


def test_a_configured_adapter_still_never_echoes_its_credential():
    instance, _ = adapter(lambda *_: Z.ZoteroResponse(200, "[]"))
    caps = instance.capabilities()
    assert caps["configured"] is True
    assert caps["status"] == "available"
    blob = json.dumps(caps, ensure_ascii=False) + repr(instance)
    assert FAKE_SECRET not in blob


def test_the_users_local_desktop_service_is_refused():
    for base in ("https://127.0.0.1:23119", "https://localhost:23119", "https://169.254.169.254"):
        with pytest.raises(V32Error) as excinfo:
            Z.create_adapter({"api_key": FAKE_SECRET, "base_url": base})
        assert excinfo.value.code == "zotero_local_api_forbidden"
        assert FAKE_SECRET not in str(excinfo.value)
    with pytest.raises(V32Error) as excinfo:
        Z.create_adapter({"api_key": FAKE_SECRET, "base_url": "http://api.zotero.org"})
    assert excinfo.value.code == "invalid_base_url"


def test_an_unconfigured_adapter_opens_no_connection_at_all():
    instance = Z.create_adapter({})
    with pytest.raises(V32Error) as excinfo:
        instance.list_collections("user", "123456")
    assert excinfo.value.status == 503
    assert excinfo.value.code == "zotero_not_configured"


# --------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------


def test_list_collections_preserves_collection_identity():
    payload = json.dumps(
        [
            {"key": "COLL1", "version": 5, "data": {"key": "COLL1", "name": "Reading list"}},
            {"key": "COLL2", "version": 6, "data": {"key": "COLL2", "name": "Child", "parentCollection": "COLL1"}},
        ]
    )
    instance, transport = adapter(lambda *_: Z.ZoteroResponse(200, payload))
    collections = instance.list_collections("user", "123456")

    assert [entry["collection_key"] for entry in collections] == ["COLL1", "COLL2"]
    assert collections[1]["parent_collection_key"] == "COLL1"
    assert collections[0]["library_id"] == "123456"
    assert transport.calls[0]["url"].startswith("https://api.zotero.org/users/123456/collections")


def test_fetch_items_preserves_external_keys_and_bibliographic_fields():
    payload = json.dumps(
        [
            api_item(
                "ITEM1",
                "A study of studies",
                version=7,
                doi="10.1234/AbC",
                collection="COLL1",
                creators=[{"creatorType": "author", "firstName": "Ada", "lastName": "Lovelace"}],
                tags=["methods"],
            )
        ]
    )
    instance, _ = adapter(lambda *_: Z.ZoteroResponse(200, payload))
    items = instance.fetch_items("user", "123456", ["ITEM1"])

    assert len(items) == 1
    item = items[0]
    assert (item["library_type"], item["library_id"]) == ("user", "123456")
    assert item["item_key"] == "ITEM1"
    assert item["item_version"] == 7
    assert item["collection_key"] == "COLL1"
    assert item["doi"] == "10.1234/abc"
    assert item["authors"] == ["Ada Lovelace"]
    assert item["tags"] == ["methods"]
    assert item["title"] == "A study of studies"


def test_fetch_items_without_keys_makes_no_request():
    instance, transport = adapter(lambda *_: Z.ZoteroResponse(200, "[]"))
    assert instance.fetch_items("user", "123456", []) == []
    assert transport.calls == []


# --------------------------------------------------------------------------
# import
# --------------------------------------------------------------------------


def test_import_keeps_the_users_selection_and_skips_unsupported_types():
    remote = [
        api_item("A", "Alpha", collection="COLL1"),
        api_item("B", "Scan of Alpha", item_type="attachment"),
        api_item("C", "Gamma", item_type="note"),
        api_item("D", "Delta", doi="10.1234/delta"),
    ]
    plan = Z.plan_import(
        [],
        remote,
        library_type="user",
        library_id="123456",
        selected_keys=["A", "B", "D"],
    )
    by_key = {item["item_key"]: item for item in plan["items"]}

    assert by_key["A"]["action"] == "create"
    assert by_key["A"]["external"] == {
        "library_type": "user",
        "library_id": "123456",
        "item_key": "A",
        "item_version": 1,
        "collection_key": "COLL1",
    }
    assert by_key["B"]["action"] == "skipped"
    assert by_key["B"]["reason"] == "unsupported_item_type:attachment"
    assert by_key["C"]["action"] == "skipped"
    assert by_key["C"]["reason"] == "not_selected_by_user"
    assert by_key["D"]["action"] == "create"
    assert plan["counts"] == {"create": 2, "skipped": 2}


def test_reimporting_the_same_item_key_is_idempotent_and_creates_no_duplicate():
    remote = [api_item("A", "Alpha"), api_item("D", "Delta")]
    first = Z.plan_import([], remote, library_type="user", library_id="123456")
    applied = Z.apply_import([], first)
    assert applied["created"] == 2
    papers = applied["papers"]

    second = Z.plan_import(papers, remote, library_type="user", library_id="123456")
    assert second["counts"] == {"unchanged": 2}
    again = Z.apply_import(papers, second)

    assert again["created"] == 0
    assert len(again["papers"]) == 2
    keys = [(p["zotero"]["library_id"], p["zotero"]["item_key"]) for p in again["papers"]]
    assert len(keys) == len(set(keys))


def test_the_same_title_under_a_different_key_is_never_overwritten():
    papers = [
        {
            "paper_id": "p1",
            "title": "Same Title",
            "zotero": {
                "library_type": "user",
                "library_id": "123456",
                "item_key": "AAAA",
                "item_version": 1,
                "collection_key": None,
            },
        }
    ]
    remote = [api_item("BBBB", "Same Title")]
    plan = Z.plan_import(papers, remote, library_type="user", library_id="123456")
    decision = plan["items"][0]

    assert decision["action"] == "conflict"
    assert decision["reason"] == "same_title_different_item"
    assert decision["paper_id"] == "p1"

    applied = Z.apply_import(papers, plan)
    assert applied["created"] == 0 and applied["updated"] == 0
    assert applied["papers"][0]["zotero"]["item_key"] == "AAAA"
    assert len(applied["papers"]) == 1


def test_an_attachment_is_only_planned_after_an_explicit_selection():
    remote = [
        {
            "key": "A",
            "version": 1,
            "links": {"attachment": {"href": "https://api.zotero.org/users/1/items/ATCH1", "attachmentType": "application/pdf"}},
            "data": {"key": "A", "itemType": "journalArticle", "title": "Alpha"},
        }
    ]
    without = Z.plan_import([], remote, library_type="user", library_id="123456")
    assert without["items"][0]["attachments"][0]["status"] == "skipped"
    assert without["items"][0]["attachments"][0]["reason"] == "attachment_requires_explicit_selection"

    with_selection = Z.plan_import(
        [],
        remote,
        library_type="user",
        library_id="123456",
        include_attachments=True,
        selected_attachment_keys=["ATCH1"],
    )
    assert with_selection["items"][0]["attachments"][0]["status"] == "selected"

    not_selected = Z.plan_import(
        [],
        remote,
        library_type="user",
        library_id="123456",
        include_attachments=True,
        selected_attachment_keys=["OTHER"],
    )
    assert not_selected["items"][0]["attachments"][0]["status"] == "skipped"
    assert not_selected["items"][0]["attachments"][0]["reason"] == "attachment_not_selected"


# --------------------------------------------------------------------------
# writes
# --------------------------------------------------------------------------


def test_each_item_reports_its_own_outcome_and_a_version_conflict_does_not_overwrite():
    response = json.dumps(
        {
            "successful": {"0": {"key": "NEW1", "version": 1}},
            "unchanged": {"1": {"key": "OLD2", "version": 4}},
            "failed": {"2": {"code": 412, "message": "precondition failed"}},
        }
    )
    instance, transport = adapter(lambda *_: Z.ZoteroResponse(200, response))
    results = instance.write_items(
        "user",
        "123456",
        [
            {"paper_id": "p1", "data": {"itemType": "journalArticle", "title": "Alpha"}},
            {"paper_id": "p2", "item_key": "OLD2", "item_version": 4, "data": {"title": "Beta"}},
            {"paper_id": "p3", "item_key": "OLD3", "item_version": 1, "data": {"title": "Gamma"}},
        ],
    )

    assert [result["status"] for result in results] == ["successful", "unchanged", "conflict"]
    assert results[2]["error_code"] == "zotero_412"
    assert results[0]["item_key"] == "NEW1"
    for result in results:
        _validator("ZoteroItemResult").validate(result)

    body = transport.posts[0]["json"]
    # The official version precondition travels with the update.
    assert body[1]["key"] == "OLD2" and body[1]["version"] == 4
    assert "key" not in body[0]
    assert "If-Unmodified-Since-Version" not in transport.posts[0]["headers"]


def test_a_library_level_precondition_is_sent_when_the_caller_has_one():
    instance, transport = adapter(
        lambda *_: Z.ZoteroResponse(200, json.dumps({"successful": {"0": {"key": "K", "version": 2}}}))
    )
    instance.write_items(
        "user", "123456", [{"paper_id": "p1", "data": {"title": "Alpha"}}], if_unmodified_since=11
    )
    assert transport.posts[0]["headers"]["If-Unmodified-Since-Version"] == "11"
    assert transport.posts[0]["headers"]["Zotero-API-Key"] == FAKE_SECRET


def test_a_whole_request_version_conflict_never_overwrites():
    instance, transport = adapter(
        lambda *_: Z.ZoteroResponse(412, json.dumps({"error": "precondition failed"}))
    )
    results = instance.write_items(
        "user", "123456", [{"paper_id": "p1", "item_key": "OLD1", "item_version": 2, "data": {"title": "Alpha"}}]
    )
    assert results[0]["status"] == "conflict"
    assert results[0]["error_code"] == "zotero_version_conflict"
    assert len(transport.posts) == 1


def test_an_http_200_without_a_batch_document_is_not_a_success():
    instance, _ = adapter(lambda *_: Z.ZoteroResponse(200, "[]"))
    results = instance.write_items(
        "user", "123456", [{"paper_id": "p1", "data": {"title": "Alpha"}}]
    )
    assert results[0]["status"] == "unknown_outcome"
    assert results[0]["error_code"] == "unparsable_response"


def test_a_timeout_is_reconciled_before_anything_is_written_again():
    created = api_item("NEW1", "Alpha", doi="10.1234/alpha", version=1)

    def handler(method, url, body, count):
        if method == "POST":
            return TimeoutError("simulated timeout")
        return Z.ZoteroResponse(200, json.dumps([created]))

    instance, transport = adapter(handler, settings={"retries": 1})
    results = instance.write_items(
        "user",
        "123456",
        [{"paper_id": "p1", "data": {"title": "Alpha", "DOI": "10.1234/alpha"}}],
    )

    # The item is found by the reconciliation read, so the create is not repeated.
    assert results[0]["status"] == "successful"
    assert results[0]["item_key"] == "NEW1"
    assert len(transport.posts) == 1
    assert any("q=" in call["url"] for call in transport.calls if call["method"] == "GET")


def test_an_uncertain_outcome_is_never_repeated_without_a_retry_budget():
    instance, transport = adapter(
        lambda method, url, body, count: TimeoutError("simulated timeout")
        if method == "POST"
        else Z.ZoteroResponse(200, "[]"),
        settings={"retries": 0},
    )
    results = instance.write_items(
        "user", "123456", [{"paper_id": "p1", "data": {"title": "Alpha"}}]
    )
    assert results[0]["status"] == "unknown_outcome"
    assert results[0]["error_code"] == "zotero_timeout"
    assert len(transport.posts) == 1


def test_a_repeated_failure_stops_at_the_retry_budget():
    instance, transport = adapter(
        lambda method, url, body, count: TimeoutError("simulated timeout")
        if method == "POST"
        else Z.ZoteroResponse(200, "[]"),
        settings={"retries": 1},
    )
    results = instance.write_items(
        "user", "123456", [{"paper_id": "p1", "data": {"title": "Alpha"}}]
    )
    assert results[0]["status"] == "unknown_outcome"
    assert len(transport.posts) == 2


def test_a_proven_absence_allows_one_reconciled_retry():
    attempts = {"posts": 0}

    def handler(method, url, body, count):
        if method == "POST":
            attempts["posts"] += 1
            if attempts["posts"] == 1:
                return TimeoutError("simulated timeout")
            return Z.ZoteroResponse(200, json.dumps({"successful": {"0": {"key": "NEW1", "version": 1}}}))
        return Z.ZoteroResponse(200, "[]")

    instance, transport = adapter(handler, settings={"retries": 1})
    results = instance.write_items(
        "user", "123456", [{"paper_id": "p1", "data": {"title": "Alpha"}}]
    )
    assert results[0]["status"] == "successful"
    assert results[0]["item_key"] == "NEW1"
    assert len(transport.posts) == 2


def test_a_third_party_error_body_never_reaches_the_exception_or_the_log(caplog):
    caplog.set_level(logging.DEBUG)
    body = json.dumps({"error": "forbidden", "echoed": FAKE_SECRET})
    instance, _ = adapter(lambda *_: Z.ZoteroResponse(403, body))
    with pytest.raises(V32Error) as excinfo:
        instance.list_collections("user", "123456")

    assert excinfo.value.code == "zotero_forbidden"
    assert FAKE_SECRET not in str(excinfo.value)
    assert body not in str(excinfo.value)
    assert FAKE_SECRET not in caplog.text


# --------------------------------------------------------------------------
# export preview and run
# --------------------------------------------------------------------------


def _preview_store(clock: FakeClock | None = None) -> Z.PreviewStore:
    return Z.PreviewStore(clock=clock or FakeClock())


def test_preview_ttl_comes_from_the_contract():
    store = _preview_store()
    assert store.ttl_seconds == contracts.zotero_defaults()["export_preview_ttl_seconds"]


def test_preview_document_is_schema_valid_and_bound_to_owner_and_revision():
    store = _preview_store()
    document = store.create(
        "owner-1",
        "project-1",
        4,
        LIBRARY,
        [{"paper_id": "p1", "action": "create", "existing_item_key": None, "title": "Alpha", "reason": "new"}],
    )
    _validator("ExportPreview").validate(document)
    assert document["project_id"] == "project-1"
    assert document["revision"] == 4
    assert document["target_library_id"] == "123456"

    handoff = store.consume("owner-1", "project-1", document["preview_id"], revision=4)
    _validator("ExportPreview").validate(handoff["preview"])
    assert handoff["preview_id"] == document["preview_id"]
    assert handoff["records"][0]["paper_id"] == "p1"

    with pytest.raises(V32Error) as excinfo:
        store.consume("owner-1", "project-1", document["preview_id"], revision=4)
    assert excinfo.value.code == "preview_already_used"


def test_an_expired_preview_is_rejected():
    clock = FakeClock()
    store = _preview_store(clock)
    document = store.create(
        "owner-1", "project-1", 1, LIBRARY, [{"paper_id": "p1", "title": "Alpha"}]
    )
    clock.advance(seconds=store.ttl_seconds + 1)
    with pytest.raises(V32Error) as excinfo:
        store.consume("owner-1", "project-1", document["preview_id"], revision=1)
    assert excinfo.value.status == 422
    assert excinfo.value.code == "preview_expired"


def test_a_foreign_or_unknown_preview_is_reported_as_absent():
    store = _preview_store()
    document = store.create(
        "owner-1", "project-1", 1, LIBRARY, [{"paper_id": "p1", "title": "Alpha"}]
    )
    for owner, project, preview_id in (
        ("owner-2", "project-1", document["preview_id"]),
        ("owner-1", "project-2", document["preview_id"]),
        ("owner-1", "project-1", "deadbeef"),
    ):
        with pytest.raises(V32Error) as excinfo:
            store.consume(owner, project, preview_id, revision=1)
        assert excinfo.value.status == 404
        assert excinfo.value.code == "preview_not_found"


def test_a_preview_from_another_revision_is_rejected_with_the_current_revision():
    store = _preview_store()
    document = store.create(
        "owner-1", "project-1", 7, LIBRARY, [{"paper_id": "p1", "title": "Alpha"}]
    )
    with pytest.raises(V32Error) as excinfo:
        store.consume("owner-1", "project-1", document["preview_id"], revision=8)
    assert excinfo.value.status == 409
    assert excinfo.value.code == "revision_conflict"
    assert excinfo.value.details["current_revision"] == 7


def test_build_export_preview_classifies_every_item():
    target = Z._normalise_target(LIBRARY)
    beta = {"paper_id": "p2", "title": "Beta", "authors": ["Ada Lovelace"], "year": 2024}
    recorded_for_beta = {
        "paper_id": "p2",
        "title": "Beta",
        "authors": ["Ada Lovelace"],
        "year": 2024,
        "zotero": {
            "library_type": "user",
            "library_id": "123456",
            "item_key": "BETAKEY",
            "item_version": 3,
            "collection_key": None,
            "content_hash": Z._hash_json(Z._export_payload(beta, target)),
        },
    }
    papers = [
        {"paper_id": "p1", "title": "Alpha", "year": 2023},
        recorded_for_beta,
        {
            "paper_id": "p3",
            "title": "Gamma",
            "zotero": {
                "library_type": "group",
                "library_id": "999",
                "item_key": "GAMMAKEY",
                "item_version": 1,
                "collection_key": None,
            },
        },
        {"paper_id": "p4", "title": "Alpha", "year": 2023},
    ]
    store = _preview_store()
    document = Z.build_export_preview(
        papers, LIBRARY, preview_store=store, owner_id="owner-1", project_id="project-1", revision=2
    )
    _validator("ExportPreview").validate(document)

    actions = {item["paper_id"]: item["action"] for item in document["items"]}
    assert actions == {"p1": "create", "p2": "unchanged", "p3": "conflict", "p4": "conflict"}
    by_id = {item["paper_id"]: item for item in document["items"]}
    assert by_id["p2"]["existing_item_key"] == "BETAKEY"
    assert set(by_id["p1"]) == {"paper_id", "action", "existing_item_key", "title", "reason"}
    assert "其他条目题名重复" in by_id["p4"]["reason"]


def test_run_export_requires_an_explicit_confirmation():
    store = _preview_store()
    document = Z.build_export_preview(
        [{"paper_id": "p1", "title": "Alpha"}],
        LIBRARY,
        preview_store=store,
        owner_id="owner-1",
        project_id="project-1",
        revision=1,
    )
    handoff = store.consume("owner-1", "project-1", document["preview_id"], revision=1)
    with pytest.raises(V32Error) as excinfo:
        Z.run_export(handoff, FakeExportAdapter(), confirm=False)
    assert excinfo.value.status == 422
    assert excinfo.value.code == "export_not_confirmed"


def test_run_export_writes_only_the_needed_items_and_reports_per_item():
    papers = [
        {"paper_id": "p1", "title": "Alpha", "year": 2023},
        {
            "paper_id": "p2",
            "title": "Beta",
            "zotero": {
                "library_type": "user",
                "library_id": "123456",
                "item_key": "BETAKEY",
                "item_version": 3,
                "collection_key": None,
            },
        },
        {"paper_id": "p3", "title": "Alpha", "year": 2023},
    ]
    store = _preview_store()
    document = Z.build_export_preview(
        papers,
        {"library_type": "user", "library_id": "123456", "collection_key": "COLL1"},
        preview_store=store,
        owner_id="owner-1",
        project_id="project-1",
        revision=1,
    )
    handoff = store.consume("owner-1", "project-1", document["preview_id"], revision=1)
    fake = FakeExportAdapter(
        results={
            "p1": {"paper_id": "p1", "status": "successful", "item_key": "NEWP1", "item_version": 1, "error_code": None}
        }
    )
    results = Z.run_export(handoff, fake, confirm=True)

    assert [result["paper_id"] for result in results] == ["p1", "p2", "p3"]
    assert [result["status"] for result in results] == ["successful", "successful", "conflict"]
    assert results[2]["error_code"] == "preview_conflict"
    for result in results:
        _validator("ZoteroItemResult").validate(result)

    # Only the create and the update were sent; the conflicting duplicate was not.
    assert len(fake.calls) == 1
    written = fake.calls[0]["items"]
    assert [item["paper_id"] for item in written] == ["p1", "p2"]
    assert fake.calls[0]["library_id"] == "123456"
    assert written[1]["item_key"] == "BETAKEY" and written[1]["item_version"] == 3

    # Metadata only: no full text and no attachment content is ever uploaded.
    blob = json.dumps(written, ensure_ascii=False).lower()
    for forbidden in ("fulltext", "full_text", "pdf", "attachment", "content"):
        assert forbidden not in blob
    assert written[0]["data"]["title"] == "Alpha"
    assert written[0]["data"]["collections"] == ["COLL1"]


def test_run_export_updates_through_the_recorded_item_key():
    papers = [
        {
            "paper_id": "p2",
            "title": "Beta",
            "zotero": {
                "library_type": "user",
                "library_id": "123456",
                "item_key": "BETAKEY",
                "item_version": 3,
                "collection_key": None,
                "content_hash": "not-the-current-hash",
            },
        }
    ]
    store = _preview_store()
    document = Z.build_export_preview(
        papers, LIBRARY, preview_store=store, owner_id="owner-1", project_id="project-1", revision=1
    )
    handoff = store.consume("owner-1", "project-1", document["preview_id"], revision=1)
    fake = FakeExportAdapter()
    results = Z.run_export(handoff, fake, confirm=True)
    assert results[0]["status"] == "successful"
    assert fake.calls[0]["items"][0]["item_key"] == "BETAKEY"
