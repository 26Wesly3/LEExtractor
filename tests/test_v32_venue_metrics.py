"""M5 venue metrics: reuse the verified catalogue, never fabricate an official metric.

The tests pin three things: an absent licensed source stays absent, the CCF
rank comes from the one existing catalogue accessor, and the internal citation
proxy is never dressed up as an impact factor (doc 13, AC-24).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from litsearch.v32 import contracts
from litsearch.v32 import venue_metrics as VM

V32_DIR = Path(contracts.__file__).resolve().parent


def _validator(definition: str) -> Draft202012Validator:
    schema = contracts.domain_schema()
    sub = dict(schema["$defs"][definition])
    sub["$defs"] = schema["$defs"]
    return Draft202012Validator(sub)


def _metrics_of_type(report, metric_type):
    return [metric for metric in report["metrics"] if metric["metric_type"] == metric_type]


class FakeLicensedAdapter:
    """A stand-in licensed source: whatever it declares, the module checks."""

    def __init__(self, records):
        self.records = records

    def metric(self, venue, metric_type):
        return self.records.get(metric_type)


def _licensed_record(**overrides):
    record = {
        "value": 12.3,
        "source_name": "JCR 2024（机构许可）",
        "source_url": "https://example.invalid/jcr/2024",
        "license_scope": "institutional_licence_display_allowed",
        "metric_year": 2024,
        "edition": "JCR 2024",
        "category": None,
        "verification_status": "verified",
        "note": "来源已核验。",
    }
    record.update(overrides)
    return record


JOURNAL = {"venue_id": "v-tocs", "name": "TOCS", "venue_type": "journal", "issns": ["0734-2071"]}


# --------------------------------------------------------------------------
# official metrics without a licensed source
# --------------------------------------------------------------------------


def test_official_metrics_are_unavailable_without_a_licensed_source():
    report = VM.venue_metrics(JOURNAL)
    _validator("VenueMetrics").validate(report)
    for metric in report["metrics"]:
        _validator("VenueMetric").validate(metric)

    assert [metric["metric_type"] for metric in report["metrics"]] == [
        "ccf_rank",
        "official_jif",
        "jcr_quartile",
        "cas_partition",
    ]
    for metric_type in VM.LICENSED_METRIC_TYPES:
        metric = _metrics_of_type(report, metric_type)[0]
        assert metric["value"] is None
        assert metric["verification_status"] == "unavailable"
        assert metric["metric_year"] is None
        assert metric["source_url"] is None
        assert metric["category"] is None

    assert VM.official_jif(JOURNAL) is None
    assert VM.jcr_quartile(JOURNAL) is None
    assert VM.cas_partition(JOURNAL) == []


def test_no_official_number_is_invented_for_an_unknown_venue():
    venue = {"name": "Zzzz Unlisted Journal"}
    report = VM.venue_metrics(venue)
    _validator("VenueMetrics").validate(report)
    assert report["venue_id"] is None
    assert report["venue_type"] == "unknown"
    assert report["issns"] == []
    assert report["metrics"] == []
    assert VM.official_jif(venue) is None


def test_an_adapter_cannot_smuggle_in_an_unlicensed_or_unverified_metric():
    unlicensed = FakeLicensedAdapter(
        {
            "official_jif": _licensed_record(license_scope=None),
            "jcr_quartile": _licensed_record(verification_status="unverified"),
            "cas_partition": _licensed_record(source_url=None, category="工程技术"),
        }
    )
    venue = dict(JOURNAL)
    assert VM.official_jif(venue, adapters=[unlicensed]) is None
    assert VM.jcr_quartile(venue, adapters=[unlicensed]) is None
    assert VM.cas_partition(venue, adapters=[unlicensed]) == []
    report = VM.venue_metrics(venue, adapters=[unlicensed])
    for metric_type in VM.LICENSED_METRIC_TYPES:
        assert _metrics_of_type(report, metric_type)[0]["value"] is None


def test_a_fully_provenanced_metric_is_used_and_keeps_its_category():
    adapter = FakeLicensedAdapter(
        {
            "official_jif": _licensed_record(value=12.3),
            "jcr_quartile": _licensed_record(value="Q1", category="COMPUTER SCIENCE, THEORY & METHODS"),
            "cas_partition": [
                _licensed_record(value="1区", category="工程技术", edition="2023"),
                _licensed_record(value="2区", category="计算机科学", edition="2023"),
            ],
        }
    )
    venue = dict(JOURNAL)
    jif = VM.official_jif(venue, adapters=[adapter])
    assert jif is not None and jif["value"] == 12.3
    assert jif["verification_status"] == "verified"
    _validator("VenueMetric").validate(jif)

    partitions = VM.cas_partition(venue, adapters=[adapter])
    assert [entry["category"] for entry in partitions] == ["工程技术", "计算机科学"]
    assert [entry["value"] for entry in partitions] == ["1区", "2区"]
    for entry in partitions:
        _validator("VenueMetric").validate(entry)

    report = VM.venue_metrics(venue, adapters=[adapter])
    _validator("VenueMetrics").validate(report)
    assert len(_metrics_of_type(report, "cas_partition")) == 2
    assert _metrics_of_type(report, "official_jif")[0]["value"] == 12.3


# --------------------------------------------------------------------------
# CCF reuses the existing catalogue
# --------------------------------------------------------------------------


def test_ccf_rank_comes_from_the_existing_catalogue_accessor():
    from types import SimpleNamespace

    from litsearch.venues import catalog, venue_classification

    metric = VM.ccf_metric(JOURNAL)
    assert metric is not None
    assert metric["metric_type"] == "ccf_rank"
    assert metric["value"] == "A"
    assert metric["category"] == "journal"
    assert metric["source_url"] == catalog()["source"]
    _validator("VenueMetric").validate(metric)

    # The shared accessor gives the same answer: there is one catalogue, not two.
    shared = venue_classification(
        SimpleNamespace(venue="TOCS", identifiers=SimpleNamespace(dblp_key=""))
    )
    assert shared["rank"] == metric["value"]
    assert shared["kind"] == metric["category"]


def test_ccf_rank_is_unknown_for_a_venue_the_catalogue_cannot_place():
    assert VM.ccf_metric({"name": "Zzzz Unlisted Journal"}) is None
    assert VM.ccf_metric({"name": ""}) is None
    assert VM.ccf_metric({}) is None
    # A workshop track is not the main conference and is never awarded its rank.
    assert VM.ccf_metric({"name": "NeurIPS Workshop on Example"}) is None
    report = VM.venue_metrics({"name": "Zzzz Unlisted Journal", "venue_type": "journal"})
    assert not _metrics_of_type(report, "ccf_rank")


# --------------------------------------------------------------------------
# citation proxy
# --------------------------------------------------------------------------


def test_citation_proxy_is_labelled_an_internal_metric_not_an_impact_factor():
    metric = VM.citation_proxy({"name": "Nature"})
    assert metric is not None
    assert metric["metric_type"] == "citation_proxy"
    assert metric["value"] == 50.5
    assert VM.CITATION_PROXY_LABEL in metric["note"]
    assert "内部引用代理指标" in metric["note"]
    assert metric["verification_status"] == "unverified"
    assert metric["license_scope"] == "internal_proxy_not_for_official_display"
    assert metric["source_name"] == "litsearch vendored journal table"
    # The wording says what it is not, and never claims to be an impact factor.
    assert "not an official" in metric["note"].lower() or "不是官方" in metric["note"]
    _validator("VenueMetric").validate(metric)


def test_citation_proxy_uses_the_existing_formula_on_recorded_statistics():
    metric = VM.citation_proxy(
        {"name": "Journal of Example Studies"},
        stats={"cited_by_count": 100, "works_count": 50, "h_index": 20},
    )
    assert metric is not None
    # The M0-verified formula in litsearch.sources: 0.15*cited/works, blended
    # 0.7/0.3 with h_index/100 -> 0.27.  A second implementation would drift.
    assert metric["value"] == pytest.approx(0.27)
    assert "formula" in metric["note"]
    assert VM.CITATION_PROXY_LABEL in metric["note"]
    _validator("VenueMetric").validate(metric)


def test_citation_proxy_is_none_when_nothing_is_known_and_makes_no_request():
    assert VM.citation_proxy({"name": "Journal of Example Studies"}) is None
    assert VM.citation_proxy({}) is None
    report = VM.venue_metrics({"name": "Journal of Example Studies", "venue_type": "journal"})
    assert not _metrics_of_type(report, "citation_proxy")


def test_a_recorded_h_index_is_reported_without_being_estimated():
    metric = VM.openalex_h_index(
        {
            "name": "TOCS",
            "h_index": 42,
            "h_index_source": "OpenAlex /sources",
            "h_index_source_url": "https://openalex.org/S123",
        }
    )
    assert metric is not None and metric["value"] == 42
    assert metric["metric_type"] == "openalex_h_index"
    assert metric["verification_status"] == "unverified"
    _validator("VenueMetric").validate(metric)
    assert VM.openalex_h_index({"name": "TOCS"}) is None


# --------------------------------------------------------------------------
# metrics never touch the ranking
# --------------------------------------------------------------------------


def test_venue_metrics_expose_no_score_and_import_no_ranking_module():
    source = (V32_DIR / "venue_metrics.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any("core_rank" in module or "feedback" in module for module in imported)

    identifiers = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    identifiers |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    for forbidden in ("core_score", "core_score_raw", "rrf_k", "starter_list", "recommended_paper_ids"):
        assert forbidden not in identifiers

    public = set(VM.__all__)
    assert not any("rank" in name or "score" in name for name in public)


def test_the_metric_document_is_closed_and_carries_no_weight_or_contribution():
    report = VM.venue_metrics(JOURNAL, adapters=[FakeLicensedAdapter({"official_jif": _licensed_record()})])
    assert set(report) == {"venue_id", "name", "venue_type", "issns", "metrics"}
    declared = set(contracts.domain_schema()["$defs"]["VenueMetric"]["properties"])
    for metric in report["metrics"]:
        assert set(metric) == declared
        assert "weight" not in metric and "contribution" not in metric
        assert metric["metric_type"] in {str(value) for value in VM.MetricType}
