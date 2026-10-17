"""演示中发现的覆盖状态矛盾：有候选却报 no_results。"""
from __future__ import annotations

from litsearch.v32.pipeline import coverage_dto


def test_coverage_never_says_no_results_when_candidates_exist():
    row = coverage_dto(status="no_results", candidate_count=75, failed_sources=["openalex"], stop_reasons=["budget"])
    assert row["status"] == "partial"
    assert row["candidate_count"] == 75


def test_coverage_keeps_no_results_for_an_empty_run():
    row = coverage_dto(status="no_results", candidate_count=0)
    assert row["status"] == "no_results"


def test_coverage_passes_known_statuses_through():
    for status in ("within_budget", "partial", "failed"):
        assert coverage_dto(status=status, candidate_count=3)["status"] == status
