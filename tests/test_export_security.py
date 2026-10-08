"""Export security + journal-metric labelling regressions (spec H1/H8, contract §11).

Two independent properties are pinned down here:

* a CSV opened in a spreadsheet must not execute cell text, while the JSON
  export keeps the original characters untouched;
* an internal citation proxy and an official (source-verified) journal metric
  are separate columns with separate labels — an unverified hardcoded number
  must never be presented as the current official Impact Factor.
"""

from __future__ import annotations

import csv
import io
import json
import shutil
from pathlib import Path

import pytest

from litsearch.export import (
    OFFICIAL_IF_HEADER,
    PROXY_HEADER,
    citation_proxy_record,
    normalize_metric_record,
    official_metric_record,
    to_csv,
    to_json,
)
from litsearch.models import Author, Paper, SearchResult


def paper(**kwargs):
    base = {"id": "10.1/x", "title": "A paper", "year": 2020,
            "authors": [Author(name="Smith")], "venue": "Journal of Tests"}
    base.update(kwargs)
    return Paper(**base)


def rows(csv_text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(csv_text)))


# ---------------------------------------------------------------------------
# H1: CSV formula injection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("payload", ["=1+1", "+1+1", "-1+1", "@SUM(A1)", "\t=1+1", "\r=1+1",
                                     "=cmd|'/c calc'!A1", "=HYPERLINK(\"http://x\")"])
def test_formula_like_cells_are_neutralised(payload):
    text = to_csv([paper(title=payload)], include_abstract=True)
    cell = rows(text)[1][0]
    assert cell.startswith("'"), cell
    assert cell[1:] == payload


def test_formula_neutralisation_covers_every_text_column():
    p = paper(title="=1+1", venue="+venue", abstract="@abstract",
              authors=[Author(name="-author")], doi="=doi")
    row = rows(to_csv([p], include_abstract=True))[1]
    header = rows(to_csv([p], include_abstract=True))[0]
    cells = dict(zip(header, row, strict=True))
    for column in ("Title", "Authors", "Venue", "DOI", "Abstract"):
        assert cells[column].startswith("'"), (column, cells[column])
        assert cells[column][1:] == getattr_text(p, column)


def getattr_text(p: Paper, column: str) -> str:
    return {
        "Title": p.title,
        "Authors": "; ".join(a.name for a in p.authors),
        "Venue": p.venue,
        "DOI": p.doi,
        "Abstract": p.abstract,
    }[column]


def test_json_export_keeps_the_raw_text():
    p = paper(title="=1+1", abstract="@SUM(1)", venue="+cmd")
    result = SearchResult(papers=[p])
    data = json.loads(to_json(result))
    assert data["papers"][0]["title"] == "=1+1"
    assert data["papers"][0]["abstract"] == "@SUM(1)"
    assert data["papers"][0]["venue"] == "+cmd"
    assert "'" not in data["papers"][0]["title"]


def test_plain_text_and_numbers_are_not_touched():
    p = paper(title="Normal title", year=-1000, citation_count=12, relevance_score=0.5)
    row = rows(to_csv([p]))[1]
    assert row[0] == "Normal title"
    assert row[2] == "-1000"
    assert row[5] == "12"
    # A negative *number* is a number, not a formula: it stays machine-readable.
    assert row[6] == "0.5"


def test_neutralised_csv_still_round_trips_through_a_reader():
    p = paper(title="=1+1", abstract="line1\nline2")
    back = rows(to_csv([p], include_abstract=True))
    assert len(back[0]) == len(back[1]) == 8
    assert back[1][0] == "'=1+1"
    assert back[1][7] == "line1\nline2"


def test_evidence_pack_csv_is_neutralised_too():
    """The pack ships a CSV that a reviewer may open in Excel."""
    import zipfile

    from litsearch.bundle import build_evidence_pack
    from litsearch.search import ReviewState

    state = ReviewState(topic="=1+1 topic", research_direction="d")
    state.search_papers = [paper(title="=1+1")]
    pack = build_evidence_pack(state)
    with zipfile.ZipFile(io.BytesIO(pack)) as archive:
        text = archive.read("papers.csv").decode("utf-8-sig")
    body = rows(text)[1][0]
    assert body.startswith("'=1+1")


# ---------------------------------------------------------------------------
# Evidence Pack screening fields (contract §13 / spec S0)
# ---------------------------------------------------------------------------


def _pack(state):
    import zipfile

    from litsearch.bundle import build_evidence_pack

    return zipfile.ZipFile(io.BytesIO(build_evidence_pack(state)))


def test_evidence_pack_carries_the_four_screening_fields():
    import json as _json

    from litsearch.search import ReviewState

    state = ReviewState(topic="t", research_direction="d")
    state.search_papers = [paper()]
    with _pack(state) as archive:
        assert "screening.json" in archive.namelist()
        facts = _json.loads(archive.read("screening.json").decode("utf-8"))
        handoff = archive.read("AGENT_HANDOFF.md").decode("utf-8")
        lines = [line for line in archive.read("papers.jsonl").decode("utf-8").splitlines()
                 if line.strip()]
    for field in ("screening_status", "threshold_calibrated", "requires_manual_review",
                  "full_text_review_completed"):
        assert field in facts, field
        assert field in handoff, field
    assert facts["screening_status"] == "not_started"
    assert facts["full_text_review_completed"] is False
    assert facts["requires_manual_review"] is True
    assert _json.loads(lines[0])["screening_status"] == "not_started"


def test_evidence_pack_never_claims_a_completed_review_without_full_text():
    from litsearch.prisma import ScreeningDecision, ScreeningStage
    from litsearch.search import ReviewState

    state = ReviewState(topic="t", research_direction="d")
    p = paper(id="10.1/pack")
    state.search_papers = [p]
    state.prisma.add_papers([p])
    state.prisma.screen_paper(p.id, ScreeningDecision.ACCEPT)
    with _pack(state) as archive:
        handoff = archive.read("AGENT_HANDOFF.md").decode("utf-8")
        facts = json.loads(archive.read("screening.json").decode("utf-8"))
    assert facts["screening_status"] == "preliminary_included"
    assert facts["full_text_review_completed"] is False
    assert "preliminary" in handoff.lower()
    assert ScreeningStage.FULL_TEXT  # stage exists; nothing auto-screens it here


# ---------------------------------------------------------------------------
# H8: Citation Proxy vs official metric
# ---------------------------------------------------------------------------


def test_citation_proxy_record_declares_source_year_and_formula():
    record = citation_proxy_record(1.23, year=2026)
    assert record["label"].startswith("Citation Proxy")
    assert "not" in record["label"].lower() and "impact factor" in record["label"].lower()
    assert record["source"]
    assert record["formula"]
    assert record["year"] == 2026
    assert record["verified"] is False
    assert record["kind"] == "citation_proxy"
    assert record["needs_source_verification"] is True
    assert record["display"].startswith("Citation Proxy")


def test_a_proxy_can_never_be_declared_verified():
    record = citation_proxy_record(1.23, verified=True, needs_source_verification=False)
    assert record["verified"] is False
    assert record["needs_source_verification"] is True


def test_official_metric_requires_a_source_and_a_year():
    record = official_metric_record(50.5, source="JCR 2024", year=2024, verified=True)
    assert record["value"] == 50.5
    assert record["verified"] is True
    unverified = official_metric_record(50.5)
    assert unverified["verified"] is False
    assert "verif" in unverified["note"].lower()


def test_official_and_proxy_land_in_separate_columns():
    metrics = {
        "Journal of Tests": {
            "official": official_metric_record(8.3, source="JCR 2024", year=2024, verified=True),
            "proxy": citation_proxy_record(1.23, year=2026),
        }
    }
    text = to_csv([paper()], journal_metrics=metrics)
    header, first = rows(text)[0], rows(text)[1]
    assert OFFICIAL_IF_HEADER in header
    assert PROXY_HEADER in header
    assert OFFICIAL_IF_HEADER != PROXY_HEADER
    official_cell = first[header.index(OFFICIAL_IF_HEADER)]
    proxy_cell = first[header.index(PROXY_HEADER)]
    assert "8.3" in official_cell and "JCR 2024" in official_cell
    assert "Citation Proxy" in proxy_cell and "not" in proxy_cell.lower()
    assert "8.3" not in proxy_cell


def test_unverified_hardcoded_number_is_never_shown_as_the_official_if():
    metrics = {"Journal of Tests": {"official": official_metric_record(50.5)}}
    text = to_csv([paper()], journal_metrics=metrics)
    header, first = rows(text)[0], rows(text)[1]
    cell = first[header.index(OFFICIAL_IF_HEADER)]
    assert "verif" in cell.lower()
    assert not cell.strip().startswith("50.5")
    assert "IF:" not in cell


def test_missing_metrics_leave_the_csv_shape_unchanged():
    text = to_csv([paper()])
    assert OFFICIAL_IF_HEADER not in rows(text)[0]
    assert PROXY_HEADER not in rows(text)[0]


def test_json_export_separates_official_and_proxy_metrics():
    metrics = {
        "Journal of Tests": {
            "official": official_metric_record(8.3, source="JCR 2024", year=2024, verified=True),
            "proxy": citation_proxy_record(1.23, year=2026),
        }
    }
    data = json.loads(to_json(SearchResult(papers=[paper()]), journal_metrics=metrics))
    assert data["papers"][0]["journal_metrics"]["official"]["value"] == 8.3
    assert data["papers"][0]["journal_metrics"]["proxy"]["value"] == 1.23
    assert data["papers"][0]["journal_metrics"]["official"]["source"] == "JCR 2024"


def test_export_source_never_calls_the_proxy_an_estimated_impact_factor():
    """Static guard: the old 'estimated IF' wording must not come back."""
    source = (Path(__file__).resolve().parents[1] / "litsearch" / "export.py").read_text(
        encoding="utf-8")
    lowered = source.lower()
    assert "estimated if" not in lowered
    assert "estimated impact factor" not in lowered
    assert "citation proxy" in lowered


# ---------------------------------------------------------------------------
# Cross-module agreement with litsearch/sources.py (one number, one story)
# ---------------------------------------------------------------------------


@pytest.fixture()
def workdir():
    path = Path(__file__).resolve().parent / "_workspace_tmp" / "export_metrics"
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_export_agrees_with_the_citation_proxy_from_sources(workdir):
    from litsearch.cache import Cache
    from litsearch.sources import ImpactFactorLookup

    lookup = ImpactFactorLookup(cache=Cache(db_path=str(workdir / "if.db")))
    proxy = lookup.citation_proxy("nature")          # vendored table: offline
    assert proxy["verified"] is False
    assert proxy["needs_source_verification"] is True

    normalized = normalize_metric_record(proxy)
    assert normalized["value"] == proxy["value"] == 50.5
    assert normalized["verified"] is False
    assert normalized["needs_source_verification"] is True
    assert normalized["display"] == proxy["display"]
    assert normalized["source"] == proxy["source"]

    text = to_csv([paper(venue="Nature")], journal_metrics={"Nature": {"proxy": proxy}})
    header, first = rows(text)[0], rows(text)[1]
    cell = first[header.index(PROXY_HEADER)]
    assert "Citation Proxy" in cell
    assert "not" in cell.lower() and "impact factor" in cell.lower()
    assert not cell.strip().startswith("50.5")
    # …and the official column stays empty rather than borrowing the proxy.
    assert first[header.index(OFFICIAL_IF_HEADER)] == ""


def test_a_bare_number_is_treated_as_a_proxy_not_as_an_official_if():
    text = to_csv([paper(venue="Journal of Tests")],
                  journal_metrics={"Journal of Tests": {"official": 50.5, "proxy": 1.2}})
    header, first = rows(text)[0], rows(text)[1]
    official = first[header.index(OFFICIAL_IF_HEADER)]
    proxy = first[header.index(PROXY_HEADER)]
    assert official.startswith("NEEDS SOURCE VERIFICATION")
    assert "Citation Proxy" in proxy


def test_the_evidence_pack_uses_canonical_relation_names_only():
    """No consumer may see both 'text_similarity' and the legacy 'semantic'."""
    import zipfile

    from litsearch.bundle import build_evidence_pack
    from litsearch.models import Paper
    from litsearch.search import ReviewState

    state = ReviewState(topic="plant phenotyping", research_direction="deep learning")
    state.search_papers = [
        Paper(id="10.1/a", title="deep learning for plant phenotyping", abstract="a" * 50),
        Paper(id="10.1/b", title="deep learning for plant phenotyping", abstract="a" * 50),
    ]
    with zipfile.ZipFile(io.BytesIO(build_evidence_pack(state))) as archive:
        graph = json.loads(archive.read("evidence_graph.json").decode("utf-8"))
    edge_types = {row["edge_type"] for row in graph.get("relations", [])}
    assert "semantic" not in edge_types
    assert edge_types <= {"citation", "bibliographic_coupling", "co_citation", "text_similarity"}
