"""S10：本地期刊缓存。不联网，只用临时目录。"""
from __future__ import annotations

import json

from litsearch.v32 import venue_cache as vc
from tests.test_v32_api import _csrf, app, client  # noqa: F401  (fixtures)


def test_record_is_idempotent_per_project(tmp_path):
    rows = [
        {"venue": "Mint Journal", "year": 2020},
        {"venue": "Mint Journal", "year": 2024},
        {"venue": "", "year": 2020},
    ]
    vc.record(tmp_path, "p1", rows)
    vc.record(tmp_path, "p1", rows)
    summary = vc.summary(tmp_path)
    assert [row["name"] for row in summary] == ["Mint Journal"]
    assert summary[0]["count"] == 2
    assert summary[0]["project_count"] == 1
    assert summary[0]["year_from"] == 2020
    assert summary[0]["year_to"] == 2024


def test_counts_add_up_across_projects(tmp_path):
    vc.record(tmp_path, "p1", [{"venue": "Plant Methods", "year": 2021}])
    vc.record(tmp_path, "p2", [{"venue": "Plant Methods", "year": 2022}, {"venue": "Plant Methods", "year": 2023}])
    row = vc.summary(tmp_path)[0]
    assert row["count"] == 3
    assert row["project_count"] == 2


def test_ccf_rank_comes_from_the_bundled_catalogue(tmp_path):
    assert vc.lookup_rank("TOCS")["rank"] == "A"
    assert vc.lookup_rank("not a real venue") is None
    vc.record(tmp_path, "p1", [{"venue": "TOCS", "year": 2019}])
    row = vc.summary(tmp_path)[0]
    assert row["ccf_rank"] == "A"
    assert row["ccf_kind"] == "journal"
    assert "2026" in (row["ccf_edition"] or "")


def test_cache_file_is_plain_json_without_credentials(tmp_path):
    vc.record(tmp_path, "p1", [{"venue": "Mint Journal", "year": 2020}])
    document = json.loads(vc.cache_path(tmp_path).read_text(encoding="utf-8"))
    assert document["version"] == vc._VERSION
    assert "api_key" not in json.dumps(document)
    assert vc.clear(tmp_path) is True
    assert vc.summary(tmp_path) == []


def test_venues_endpoint_needs_a_session_and_then_lists_the_cache(client):
    assert client.get("/api/v3/venues").status_code == 401
    _csrf(client)
    payload = client.get("/api/v3/venues").json()
    assert payload == {"items": [], "total": 0}
