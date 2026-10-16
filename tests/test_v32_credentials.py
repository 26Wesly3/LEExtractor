"""M5 credential store: scope, redaction and the memory-only boundary.

Every sample secret here is obviously fake and was invented for this test file;
no real key, token or password is used anywhere (doc 10 "测试要求").
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

from litsearch.v32 import contracts
from litsearch.v32.credentials import (
    PROVIDER_ALLOWLIST,
    CredentialStatus,
    CredentialStore,
    credential_kinds,
    redact,
    redact_text,
    scrub_error_body,
)
from litsearch.v32.errors import V32Error

FAKE_KEY = "TEST-NOT-A-REAL-KEY-0123456789abcdef"
FAKE_TOKEN = "TEST-NOT-A-REAL-TOKEN-abcdef0123456789"


class FakeClock:
    """A clock the test moves by hand, so a one-hour TTL needs no sleep."""

    def __init__(self) -> None:
        self.now = datetime(2026, 10, 17, 0, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


def make_store(**kwargs) -> tuple[CredentialStore, FakeClock]:
    clock = FakeClock()
    return CredentialStore(allowlist=PROVIDER_ALLOWLIST, clock=clock, **kwargs), clock


# --------------------------------------------------------------------------
# allowlist and contract wiring
# --------------------------------------------------------------------------


def test_allowlist_covers_the_required_providers_with_contract_kinds():
    kinds = set(credential_kinds())
    assert kinds == {"retrieval", "model", "zotero"}
    for provider in ("openalex", "semantic_scholar", "crossref", "arxiv", "openreview"):
        assert PROVIDER_ALLOWLIST[provider] == "retrieval"
    assert PROVIDER_ALLOWLIST["zotero"] == "zotero"
    assert "ai_model" in PROVIDER_ALLOWLIST
    assert PROVIDER_ALLOWLIST["ai_model"] == "model"
    assert set(PROVIDER_ALLOWLIST.values()) <= kinds


def test_idle_ttl_and_secret_cap_come_from_the_contract_not_local_literals():
    credentials = contracts.credentials_defaults()
    store, _ = make_store()
    assert store.ttl_seconds == credentials["idle_ttl_seconds"]
    assert credentials["return_secret"] is False
    assert credentials["public_persistence"] == "memory_only"
    assert store.persistent is False


# --------------------------------------------------------------------------
# round trip and status surface
# --------------------------------------------------------------------------


def test_put_get_delete_round_trip():
    store, _ = make_store()
    status = store.put("session-a", "semantic_scholar", "retrieval", FAKE_KEY)
    assert isinstance(status, CredentialStatus)
    assert status.configured is True
    assert status.kind == "retrieval"
    assert status.updated_at is not None

    assert store.get("session-a", "semantic_scholar") == FAKE_KEY
    assert store.provider_kind("semantic_scholar") == "retrieval"

    store.delete("session-a", "semantic_scholar")
    assert store.get("session-a", "semantic_scholar") is None
    assert store.status("session-a", "semantic_scholar").configured is False
    # Deleting again is a no-op, not an error.
    store.delete("session-a", "semantic_scholar")


def test_status_surface_never_contains_the_secret():
    store, _ = make_store()
    store.put("session-a", "zotero", "zotero", FAKE_TOKEN)
    status = store.status("session-a", "zotero")
    payload = store.payload("session-a")

    assert set(status.as_dict()) == {"provider_id", "kind", "configured", "updated_at"}
    blob = json.dumps(payload, ensure_ascii=False)
    assert FAKE_TOKEN not in blob
    assert FAKE_KEY not in blob
    assert FAKE_TOKEN not in repr(status)
    assert FAKE_TOKEN not in repr(store)
    for item in payload["items"]:
        assert item["provider_id"] in PROVIDER_ALLOWLIST
    configured = [item for item in payload["items"] if item["configured"]]
    assert configured == [
        {"provider_id": "zotero", "kind": "zotero", "configured": True,
         "updated_at": status.updated_at}
    ]
    # Every allowlisted provider is listed so the UI can render the whole form.
    assert len(payload["items"]) == len(PROVIDER_ALLOWLIST)


def test_get_never_returns_a_deleted_credential_even_if_the_adapter_asks_again():
    store, _ = make_store()
    store.put("session-a", "openalex", "retrieval", FAKE_KEY)
    store.delete("session-a", "openalex")
    assert store.get("session-a", "openalex") is None


# --------------------------------------------------------------------------
# rejection rules
# --------------------------------------------------------------------------


def test_unknown_provider_is_rejected():
    store, _ = make_store()
    with pytest.raises(V32Error) as excinfo:
        store.put("session-a", "not_a_real_provider", "retrieval", FAKE_KEY)
    assert excinfo.value.status == 400
    assert excinfo.value.code == "provider_not_allowlisted"
    assert FAKE_KEY not in str(excinfo.value)
    assert store.get("session-a", "not_a_real_provider") is None


def test_wrong_kind_and_bad_secret_are_rejected():
    store, _ = make_store()
    with pytest.raises(V32Error) as excinfo:
        store.put("session-a", "zotero", "retrieval", FAKE_TOKEN)
    assert excinfo.value.code == "credential_kind_mismatch"
    with pytest.raises(V32Error):
        store.put("session-a", "zotero", "zotero", "")
    with pytest.raises(V32Error):
        store.put("session-a", "zotero", "zotero", "x" * 9000)
    with pytest.raises(V32Error):
        store.put("", "zotero", "zotero", FAKE_TOKEN)


def test_a_failed_call_leaks_no_secret_into_the_exception_or_the_log(caplog):
    store, _ = make_store()
    caplog.set_level(logging.DEBUG)
    with pytest.raises(V32Error) as excinfo:
        store.put("session-a", "mystery_provider", "retrieval", FAKE_KEY)

    assert FAKE_KEY not in str(excinfo.value)
    assert FAKE_KEY not in repr(excinfo.value)
    assert FAKE_KEY not in caplog.text
    assert caplog.records, "a rejected write must still be observable in the log"
    for record in caplog.records:
        assert FAKE_KEY not in record.getMessage()
        assert FAKE_KEY not in repr(record.__dict__)
    assert any(record.getMessage() == "credential_write_rejected" for record in caplog.records)


# --------------------------------------------------------------------------
# session scope, TTL, persistence boundary
# --------------------------------------------------------------------------


def test_sessions_are_isolated():
    store, _ = make_store()
    store.put("session-a", "openalex", "retrieval", FAKE_KEY)
    store.put("session-b", "openalex", "retrieval", FAKE_TOKEN)

    assert store.get("session-a", "openalex") == FAKE_KEY
    assert store.get("session-b", "openalex") == FAKE_TOKEN
    assert store.get("session-c", "openalex") is None

    store.delete("session-a", "openalex")
    assert store.get("session-b", "openalex") == FAKE_TOKEN


def test_two_stores_share_no_state_memory_only():
    first, _ = make_store()
    second, _ = make_store()
    first.put("session-a", "openalex", "retrieval", FAKE_KEY)
    assert second.get("session-a", "openalex") is None
    assert second.status("session-a", "openalex").configured is False


def test_idle_purge_uses_the_contract_ttl():
    store, clock = make_store()
    store.put("session-a", "openalex", "retrieval", FAKE_KEY)
    clock.advance(minutes=59)
    assert store.get("session-a", "openalex") == FAKE_KEY  # touching keeps it alive
    clock.advance(minutes=59)
    assert store.get("session-a", "openalex") == FAKE_KEY
    clock.advance(seconds=store.ttl_seconds + 1)
    assert store.get("session-a", "openalex") is None
    assert store.status("session-a", "openalex").configured is False


def test_purge_idle_reports_how_many_it_dropped():
    store, clock = make_store()
    store.put("session-a", "openalex", "retrieval", FAKE_KEY)
    store.put("session-b", "arxiv", "retrieval", FAKE_TOKEN)
    clock.advance(hours=2)
    assert store.purge_idle() == 2
    assert store.purge_idle() == 0


def test_persistence_is_refused_because_no_encrypted_store_is_configured():
    with pytest.raises(V32Error) as excinfo:
        CredentialStore(allowlist=PROVIDER_ALLOWLIST, persistent=True, path="C:/tmp/nope.json")
    assert excinfo.value.status == 503
    assert excinfo.value.code == "credential_persistence_unavailable"
    assert FAKE_KEY not in str(excinfo.value)


# --------------------------------------------------------------------------
# redaction helpers
# --------------------------------------------------------------------------


def test_redact_masks_header_names_values_and_free_text():
    payload = {
        "Authorization": f"Bearer {FAKE_KEY}",
        "api_key": FAKE_KEY,
        "nested": {"token": FAKE_TOKEN, "provider": "openalex"},
        "list": [f"token={FAKE_TOKEN}"],
        "count": 3,
    }
    masked = redact(payload)
    blob = json.dumps(masked, ensure_ascii=False)
    assert FAKE_KEY not in blob
    assert FAKE_TOKEN not in blob
    assert masked["Authorization"] == "***"
    assert masked["api_key"] == "***"
    assert masked["nested"]["token"] == "***"
    assert masked["nested"]["provider"] == "openalex"
    assert masked["count"] == 3
    assert "***" in masked["list"][0]


def test_redact_text_removes_the_secret_value_not_only_its_name():
    text = (
        f"GET /items failed: Authorization: Bearer {FAKE_KEY} "
        f"token={FAKE_TOKEN} api_key=\"{FAKE_KEY}\" "
        '{"token": "'
        + FAKE_TOKEN
        + '"}'
    )
    cleaned = redact_text(text)

    # The secret itself must be gone, in every position, not just the label.
    for secret in (FAKE_KEY, FAKE_TOKEN):
        assert secret not in cleaned
        assert secret[:12] not in cleaned
        assert secret[-16:] not in cleaned
    assert cleaned.count("***") >= 4
    assert "Authorization: ***" in cleaned
    assert f"Bearer {FAKE_KEY}" not in cleaned
    assert "token=***" in cleaned
    assert len(redact_text("x" * 5000)) <= 401


def test_redact_text_covers_a_third_party_error_body_inline():
    body = json.dumps({"error": "forbidden", "api_key": FAKE_KEY, "token": FAKE_TOKEN})
    cleaned = redact_text(body)
    assert FAKE_KEY not in cleaned
    assert FAKE_TOKEN not in cleaned
    assert cleaned.count("***") >= 2


def test_scrub_error_body_drops_the_body_entirely():
    body = json.dumps({"error": "forbidden", "echoed_key": FAKE_KEY}, ensure_ascii=False)
    scrubbed = scrub_error_body(body)
    assert FAKE_KEY not in scrubbed
    assert body not in scrubbed
    assert scrubbed == scrub_error_body(body)
    assert str(len(body)) in scrubbed
