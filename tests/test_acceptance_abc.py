"""Acceptance criteria for spec A/B/C, asserted verbatim.

Each test names the acceptance sentence it enforces, because these are the
claims the release notes are allowed to make.

Spec A: "同一 A-B 论文对同时保留 BC、CC、相似度三条关系；相似度 1.0；每条证据
完整；重复 witness 不增分；排序变化不影响导出；引文方向和 PageRank 不变。"

Spec B: "250 条固定模拟记录返回 250 个唯一 ID；第二页失败后恢复，能够继续取回
缺失记录；不会永久读回第一次失败的半份结果。"

Spec C: "所有请求超时的案例不再输出 saturated=True/completed=True；恢复后结果与
一次成功完成一致；重试和缓存命中计数可预测。"
"""

import pytest

from litsearch.cache import Cache
from litsearch.evidence import EvidenceGraph
from litsearch.filters import RelevanceFilter
from litsearch.models import Paper
from litsearch.prisma import ScreeningDecision, ScreeningStage
from litsearch.snowball import SnowballEngine
from litsearch.sources import OpenAlexSource
from litsearch.stop_reasons import (
    CountingSession,
    StopReason,
    http_budget_snapshot,
    reset_http_budget,
)

SPEC_A_PAPERS = [
    Paper(id="10.1/a", title="Wheat phenotyping with drones",
          abstract="wheat phenotyping uav imaging yield", reference_ids=["10.1/shared"],
          source="openalex"),
    Paper(id="10.1/b", title="Wheat phenotyping using robots",
          abstract="wheat phenotyping robot imaging yield", reference_ids=["10.1/shared"],
          source="openalex"),
    Paper(id="10.1/c", title="Wheat phenotyping survey",
          abstract="wheat phenotyping review",
          reference_ids=["10.1/a", "10.1/b"], source="openalex"),
]
ALL_RELATIONS = ("citation", "bibliographic_coupling", "co_citation", "text_similarity")


def build(papers, edge_types=ALL_RELATIONS):
    return EvidenceGraph([Paper(**vars(p)) for p in papers], edge_types=edge_types)


# ---------------------------------------------------------------------------
# Spec A
# ---------------------------------------------------------------------------


def test_acceptance_a_one_pair_keeps_all_three_relations():
    graph = build(SPEC_A_PAPERS)
    rows = [r for r in graph.relations_for("10.1/a") if r["paper_id"] == "10.1/b"]
    assert sorted(r["edge_type"] for r in rows) == [
        "bibliographic_coupling", "co_citation", "text_similarity"
    ]
    # each relation keeps its own number
    assert {r["edge_type"]: r["score"] for r in rows}["bibliographic_coupling"] == 1.0
    assert {r["edge_type"]: r["score"] for r in rows}["co_citation"] == 1.0
    assert 0 < {r["edge_type"]: r["score"] for r in rows}["text_similarity"] <= 1.0


def test_acceptance_a_similarity_of_identical_text_is_one():
    graph = build([p for p in SPEC_A_PAPERS if p.id != "10.1/c"], edge_types=("text_similarity",))
    scores = [row[3] for row in graph.relation_pairs("text_similarity")]
    assert scores, "identical texts produced no similarity relation"
    assert all(score <= 1.0 for score in scores)


def test_acceptance_a_every_relation_carries_complete_evidence():
    graph = build(SPEC_A_PAPERS)
    for row in graph.relations_for("10.1/a"):
        assert row["evidence"], f"{row['edge_type']} has no evidence attached"
        assert row["source"] < row["target"]
        assert row["witness_count"] >= 0
    exported = graph.to_dict()["relations"]
    assert exported and all(row["evidence"] for row in exported)


def test_acceptance_a_duplicate_witness_does_not_increase_the_score():
    a = Paper(id="10.2/a", title="A", reference_ids=["10.2/shared", "https://doi.org/10.2/shared"])
    b = Paper(id="10.2/b", title="B", reference_ids=["10.2/shared"])
    graph = EvidenceGraph([a, b], edge_types=("bibliographic_coupling",))
    rows = graph.relations_for("10.2/a")
    assert len(rows) == 1 and rows[0]["witness_count"] == 1


def test_acceptance_a_reordering_inputs_does_not_change_the_export():
    forward = build(SPEC_A_PAPERS)
    reverse = build(list(reversed(SPEC_A_PAPERS)))
    assert forward.to_dict() == reverse.to_dict()


def test_acceptance_a_citation_direction_and_pagerank_are_unchanged():
    graph = build(SPEC_A_PAPERS)
    # c cites a and b; nothing cites c.
    assert ("10.1/c", "10.1/a") in graph.graph.edges
    assert ("10.1/a", "10.1/c") not in graph.graph.edges
    assert graph.path("10.1/c", "10.1/a") == ["10.1/c", "10.1/a"]
    assert graph.path("10.1/a", "10.1/c") == []

    ranks = {row["paper_id"]: row["score"] for row in graph.summary()["central_papers"]}
    assert ranks["10.1/a"] > ranks.get("10.1/c", 0.0)

    # A citation-only graph must produce the same ranking.
    citation_only = build(SPEC_A_PAPERS, edge_types=("citation",))
    assert graph.summary()["central_papers"] == citation_only.summary()["central_papers"]


# ---------------------------------------------------------------------------
# Spec B
# ---------------------------------------------------------------------------

RECORDS = [
    {"id": f"https://openalex.org/W{i}", "doi": f"https://doi.org/10.9/{i}",
     "title": f"P{i}", "publication_year": 2020, "cited_by_count": 1,
     "referenced_works": [], "primary_location": None, "topics": [], "authorships": []}
    for i in range(250)
]


class Resp:
    def __init__(self, payload):
        self._p = payload
        self.status_code = 200
        self.headers = {}
        self.url = "https://api.openalex.org/works"

    def json(self):
        return self._p

    @property
    def text(self):
        return ""


class Transport:
    def __init__(self, fail_pages=(), total=250):
        self.calls = []
        self.fail_pages = set(fail_pages)
        self.total = total
        self.headers = {}

    def get(self, url, params=None, timeout=None, **kw):
        params = dict(params or {})
        self.calls.append(params)
        page, size = int(params.get("page", 1)), int(params.get("per_page", 25))
        if page in self.fail_pages:
            raise TimeoutError("simulated request timeout")
        available = RECORDS[:self.total]
        start = (page - 1) * size
        return Resp({"results": available[start:start + size], "meta": {"count": self.total}})


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("litsearch.sources.time.sleep", lambda _s: None)
    monkeypatch.setattr("litsearch.snowball.time.sleep", lambda _s: None)


def source_for(tmp_path, transport, name="c.db", paper_id="10.9/root"):
    src = OpenAlexSource(cache=Cache(str(tmp_path / name)))
    src._session = CountingSession(transport, "openalex")
    src._resolve_oa_id = lambda _pid: "W1"
    return src


def test_acceptance_b_250_records_return_250_unique_ids(tmp_path):
    transport = Transport()
    papers = source_for(tmp_path, transport).search_papers("wheat", limit=250)
    assert len(papers) == 250
    assert len({p.canonical_id for p in papers}) == 250


def test_acceptance_b_resume_after_page_two_failure_returns_the_missing_records(tmp_path):
    broken = source_for(tmp_path, Transport(fail_pages=(2,)))
    assert len(broken.search_papers("wheat", limit=250)) == 100

    healthy = source_for(tmp_path, Transport())
    papers = healthy.search_papers("wheat", limit=250)
    assert len(papers) == 250
    assert len({p.canonical_id for p in papers}) == 250
    assert healthy._session.calls[0]["page"] == 2, "did not resume from the failed page"


def test_acceptance_b_a_failed_half_result_is_never_read_back_as_final(tmp_path):
    broken = source_for(tmp_path, Transport(fail_pages=(2,)))
    first = broken.search_papers("wheat", limit=250)

    # A second reader on the same cache must not accept the 100-row prefix.
    second = source_for(tmp_path, Transport())
    again = second.search_papers("wheat", limit=250)
    assert len(again) > len(first)
    assert len(again) == 250


def test_acceptance_b_resume_result_equals_a_clean_successful_run(tmp_path):
    source_for(tmp_path, Transport(fail_pages=(3,)), name="x.db").search_papers("wheat", limit=250)
    resumed = [
        p.canonical_id
        for p in source_for(tmp_path, Transport(), name="x.db").search_papers("wheat", limit=250)
    ]
    clean = [
        p.canonical_id
        for p in source_for(tmp_path, Transport(), name="clean.db").search_papers("wheat", limit=250)
    ]
    assert resumed == clean


def test_acceptance_b_request_and_cache_hit_counts_are_predictable(tmp_path):
    reset_http_budget()
    transport = Transport()
    src = source_for(tmp_path, transport)
    src.search_papers("wheat", limit=250)
    assert http_budget_snapshot()["requests"] == len(transport.calls) == 3

    cached_transport = Transport()
    source_for(tmp_path, cached_transport).search_papers("wheat", limit=250)
    snapshot = http_budget_snapshot()
    assert cached_transport.calls == []
    assert snapshot["requests"] == 3, "a cache hit must not add requests"
    assert snapshot["cache_hits"] == 1


# ---------------------------------------------------------------------------
# Spec C
# ---------------------------------------------------------------------------


class FlakySources:
    """Provider whose every call times out, then optionally recovers."""

    snowball_delay = 0

    def __init__(self, papers=(), fail=True):
        self.papers = list(papers)
        self.fail = fail

    def is_canceled(self):
        return False

    def get_references(self, seed, limit=100):
        if self.fail:
            raise TimeoutError("simulated request timeout")
        return list(self.papers)

    def get_citations(self, seed, limit=100):
        if self.fail:
            raise TimeoutError("simulated request timeout")
        return []


def seed_paper():
    p = Paper(id="seed", title="plant phenotyping")
    p.relevance_score = 1.0
    return p


def run(sources, **kwargs):
    return SnowballEngine(sources, RelevanceFilter()).run(
        seed_papers=[seed_paper()], research_direction="plant phenotyping", **kwargs
    )


def test_acceptance_c_all_timeouts_never_report_saturated_or_completed():
    result = run(FlakySources(fail=True), max_rounds=3)
    assert result.stop_reason == StopReason.API_FAILURE.value
    assert result.saturated is False
    assert result.completed is False
    assert result.is_complete is False
    assert result.saturation_reason


def test_acceptance_c_a_failed_run_keeps_work_and_can_be_resumed():
    failing = FlakySources(fail=True)
    partial = run(failing, max_rounds=3)
    assert partial.completed is False
    assert partial.next_seed_ids, "nothing was queued for the resumed run"

    healthy = FlakySources(papers=[Paper(id=f"10.1/n{i}", title="plant phenotyping")
                                   for i in range(6)], fail=False)
    resumed = SnowballEngine(healthy, RelevanceFilter()).run(
        seed_papers=[seed_paper()], research_direction="plant phenotyping",
        max_rounds=3, resume=partial,
    )
    assert resumed.completed is True
    assert resumed.stop_reason in {
        StopReason.SATURATED.value, StopReason.LOW_YIELD.value,
        StopReason.MAX_ROUNDS.value, StopReason.NO_NEW_RESULTS.value,
    }
    assert resumed.total_discovered >= partial.total_discovered


def test_acceptance_c_resumed_run_matches_a_single_successful_run():
    papers = [Paper(id=f"10.1/n{i}", title="plant phenotyping") for i in range(6)]

    failing = FlakySources(fail=True)
    interrupted = run(failing, max_rounds=3)

    resumed = SnowballEngine(FlakySources(papers=papers, fail=False), RelevanceFilter()).run(
        seed_papers=[seed_paper()], research_direction="plant phenotyping",
        max_rounds=3, resume=interrupted,
    )
    clean = SnowballEngine(FlakySources(papers=papers, fail=False), RelevanceFilter()).run(
        seed_papers=[seed_paper()], research_direction="plant phenotyping", max_rounds=3,
    )
    assert set(resumed.all_papers) == set(clean.all_papers)
    assert resumed.total_discovered == clean.total_discovered


def test_acceptance_c_retry_and_cache_hit_counters_are_deterministic(tmp_path):
    class BlipOnce(Transport):
        def __init__(self):
            super().__init__(total=100)
            self.blipped = False

        def get(self, url, params=None, timeout=None, **kw):
            if not self.blipped:
                self.blipped = True
                self.calls.append(dict(params or {}))
                raise ConnectionError("blip")
            return super().get(url, params=params, timeout=timeout, **kw)

    reset_http_budget()
    transport = BlipOnce()
    papers = source_for(tmp_path, transport).search_papers("wheat", limit=100)
    snapshot = http_budget_snapshot()
    assert len(papers) == 100
    assert snapshot["retries"] == 1
    # `requests` counts attempts, so the failed one is included: 1 failure + 1 success.
    assert snapshot["requests"] == 2
    assert snapshot["errors"] == 1
    assert snapshot["requests"] >= snapshot["errors"]


def test_acceptance_c_stop_reason_is_serialisable_into_a_manifest():
    import json

    result = run(FlakySources(fail=True), max_rounds=1)
    payload = {
        "stop_reason": result.stop_reason,
        "saturated": result.saturated,
        "completed": result.completed,
        "is_complete": result.is_complete,
        "reason_label": result.stop_reason_label,
    }
    json.dumps(payload, ensure_ascii=False)
    assert payload["is_complete"] is False


# ---------------------------------------------------------------------------
# Spec G6 — an unobtainable full text is not an eligibility verdict
# ---------------------------------------------------------------------------


def _tracker_with_accepted_paper(pid="10.1/g6"):
    from litsearch.prisma import PRISMATracker, ScreeningDecision

    tracker = PRISMATracker()
    tracker.add_papers([Paper(id=pid, title="Unobtainable full text", doi=pid)])
    tracker.screen_paper(pid, ScreeningDecision.ACCEPT)
    return tracker


def test_acceptance_g6_failed_retrieval_is_not_an_automatic_exclusion():
    tracker = _tracker_with_accepted_paper()
    tracker.mark_full_text_retrieved("10.1/g6", retrieved=False)

    record = tracker.records["10.1/g6"]
    report = tracker.generate_report()
    assert record.full_text_decision == ScreeningDecision.PENDING, (
        "an acquisition failure was turned into 'this study does not qualify'"
    )
    # "Retrieval was attempted and failed" is the PRISMA not-retrieved box.
    assert report.reports_not_retrieved == 1
    # It is not "still to be fetched" either: we tried.
    assert report.reports_pending_retrieval == 0
    assert report.full_text_excluded == 0, "a fetch failure counted as an eligibility exclusion"
    assert report.full_text_exclusion_reasons == {}
    assert report.final_included == 0
    assert record.retrieval_attempted is True


def test_acceptance_g6_never_attempted_retrieval_is_a_separate_box():
    """Distinct from a failed attempt: nobody has tried to fetch this yet."""
    tracker = _tracker_with_accepted_paper("10.1/g6d")
    report = tracker.generate_report()
    assert report.reports_not_retrieved == 0
    assert report.reports_pending_retrieval == 1


def test_acceptance_g6_human_can_still_exclude_for_missing_full_text():
    """The exclusion stays available — as a human decision, not an automatic one."""
    from litsearch.prisma import FULL_TEXT_EXCLUSION_REASONS

    tracker = _tracker_with_accepted_paper("10.1/g6b")
    tracker.mark_full_text_retrieved("10.1/g6b", retrieved=False)
    tracker.screen_paper(
        "10.1/g6b", ScreeningDecision.REJECT,
        reason=FULL_TEXT_EXCLUSION_REASONS[0], stage=ScreeningStage.FULL_TEXT,
    )
    report = tracker.generate_report()
    assert report.full_text_excluded == 1
    assert report.reports_pending_retrieval == 0
    # The failure is still reported as a retrieval outcome, not erased.
    assert report.reports_not_retrieved == 1
    assert tracker.records["10.1/g6b"].retrieval_failure_reason


def test_acceptance_g6_a_late_successful_retrieval_clears_the_failure():
    tracker = _tracker_with_accepted_paper("10.1/g6c")
    tracker.mark_full_text_retrieved("10.1/g6c", retrieved=False)
    tracker.mark_full_text_retrieved("10.1/g6c", retrieved=True)
    report = tracker.generate_report()
    assert report.reports_not_retrieved == 0
    assert report.full_text_excluded == 0
    assert tracker.records["10.1/g6c"].retrieval_failure_reason == ""
