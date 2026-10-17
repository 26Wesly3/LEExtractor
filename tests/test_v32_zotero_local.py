"""S11：本机 Zotero 接入（只读本地 API）。测试用注入 transport，不依赖 Zotero。"""
from __future__ import annotations

import json

import pytest

from litsearch.v32 import zotero_local as zl
from tests.test_v32_api import _csrf, app, client  # noqa: F401  (fixtures)


def _entry(key, title, *, doi=None, year="2021", venue="Plant Methods", item_type="journalArticle"):
    return {
        "key": key,
        "data": {
            "key": key,
            "itemType": item_type,
            "title": title,
            "creators": [
                {"creatorType": "author", "firstName": "Ada", "lastName": "Lovelace"},
                {"creatorType": "author", "name": "Mint Lab"},
            ],
            "date": year,
            "DOI": doi,
            "publicationTitle": venue,
            "abstractNote": "An abstract.",
            "url": "https://example.org/x",
            "tags": [{"tag": "mint"}],
        },
    }


def _transport(routes):
    def call(url, timeout):
        for fragment, payload in routes.items():
            if fragment in url:
                return 200, payload
        return 404, None
    return call


def test_items_normalise_the_zotero_shape():
    rows = zl.items(transport=_transport({"/items/top": [_entry("ABC12345", "Drought transcriptome", doi="10.1/x")]}))
    assert rows[0]["item_key"] == "ABC12345"
    assert rows[0]["title"] == "Drought transcriptome"
    assert rows[0]["creators"] == ["Ada Lovelace", "Mint Lab"]
    assert rows[0]["year"] == 2021
    assert rows[0]["doi"] == "10.1/x"
    assert rows[0]["venue"] == "Plant Methods"
    assert rows[0]["tags"] == ["mint"]


def test_status_reports_a_running_library():
    transport = _transport({
        "/items/top": [_entry("ABC12345", "t")],
        "/collections": [{"data": {"key": "C1", "name": "Mint"}}],
    })
    payload = zl.status(transport=transport)
    assert payload["running"] is True
    assert payload["base_url"].startswith("http")
    assert payload["sample_item"] == "t"


def test_status_degrades_when_zotero_is_not_running():
    def broken(url, timeout):
        raise zl.ZoteroLocalError("zotero_unavailable", "连不上本机 Zotero。")

    payload = zl.status(transport=broken)
    assert payload["running"] is False
    assert payload["code"] == "zotero_unavailable"
    assert "Zotero" in payload["message"]


def test_to_paper_produces_a_storable_record():
    row = zl.items(transport=_transport({"/items/top": [_entry("ABC12345", "Drought", doi="10.1/x")]}))[0]
    paper = zl.to_paper(row)
    assert paper["paper_id"] == "zotero-ABC12345"
    assert paper["aliases"] == ["10.1/x", "zotero-ABC12345"]
    assert paper["source_records"][0]["provider"] == "zotero_local"
    assert paper["doc_type"] == "journalArticle"
    assert paper["landing_url"] == "https://example.org/x"


def test_item_rejects_a_blank_key():
    with pytest.raises(zl.ZoteroLocalError) as error:
        zl.item("  ", transport=_transport({}))
    assert error.value.code == "item_key_required"


def test_local_status_endpoint_requires_a_session(client):
    assert client.get("/api/v3/zotero/local/status").status_code == 401


def test_local_items_endpoint_returns_the_normalised_rows(client, monkeypatch):
    csrf = _csrf(client)
    monkeypatch.setattr(zl, "items", lambda **kwargs: [{"item_key": "ABC12345", "title": "t"}])
    response = client.get("/api/v3/zotero/local/items?q=mint", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert response.json()["items"][0]["item_key"] == "ABC12345"
