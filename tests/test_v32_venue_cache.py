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


def test_import_csv_merges_impact_factor_without_touching_counts(tmp_path):
    vc.record(tmp_path, "p1", [{"venue": "Mint Journal", "year": 2024}, {"venue": "Mint Journal", "year": 2022}])
    source = tmp_path / "journals.csv"
    source.write_text("期刊,影响因子,分区\nMint Journal,4.2,Q1\n", encoding="utf-8")
    result = vc.import_csv(tmp_path, source)
    assert result["merged"] == 1
    row = vc.summary(tmp_path)[0]
    assert row["impact_factor"] == "4.2"
    assert row["quartile"] == "Q1"
    assert row["count"] == 2  # 导入元数据不影响计数
    # 同一份文件再导一次，不产生重复条目
    vc.import_csv(tmp_path, source)
    assert len(vc.summary(tmp_path)) == 1


def test_import_csv_accepts_english_headers_and_skips_nameless_rows(tmp_path):
    source = tmp_path / "if.csv"
    source.write_text("name,IF,quartile\nPlant Methods,3.1,Q2\n,9.9,Q1\n", encoding="utf-8")
    result = vc.import_csv(tmp_path, source)
    assert result["merged"] == 1
    assert result["skipped"] == 1
    rows = {row["name"]: row for row in vc.summary(tmp_path)}
    assert rows["Plant Methods"]["impact_factor"] == "3.1"


def test_venues_endpoint_needs_a_session_and_then_lists_the_cache(client):
    assert client.get("/api/v3/venues").status_code == 401
    _csrf(client)
    payload = client.get("/api/v3/venues").json()
    assert payload == {"items": [], "total": 0}
