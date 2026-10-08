"""Offline regression tests (no network access required).

Run with:  python -m pytest tests -q
"""

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from litsearch.cache import Cache
from litsearch.config import current_year, year_from_years_back
from litsearch.diagnostics import (
    DiagnosticsLog,
    PermanentSourceError,
    SourceErrorKind,
    classify_http_error,
    get_diagnostics,
    reset_diagnostics,
)
from litsearch.filters import RelevanceFilter
from litsearch.models import Author, Paper
from litsearch.prisma import PRISMATracker, ScreeningDecision, ScreeningStage
from litsearch.search import LiteratureReviewWorkflow
from litsearch.snowball import paper_key
from litsearch.sources import (
    ImpactFactorLookup,
    OpenAlexSource,
    _reconstruct_abstract,
    s2_paper_id,
)


def make_paper(title, doi=None, pid=None, year=2020, citations=5, abstract=""):
    return Paper(
        id=pid or doi or title,
        title=title,
        abstract=abstract,
        authors=[Author(name="A. Author")],
        year=year,
        venue="Test Journal",
        doi=doi,
        citation_count=citations,
    )


# --------------------------------------------------------------------------
# Identifier handling
# --------------------------------------------------------------------------


def test_s2_doi_gets_prefix():
    """Bare DOIs 404 on Semantic Scholar — they need the `DOI:` prefix."""
    assert s2_paper_id("10.1038/nature1") == "DOI:10.1038/nature1"
    assert s2_paper_id("abc123corpusid") == "abc123corpusid"
    assert s2_paper_id("DOI:10.1038/nature1") == "DOI:10.1038/nature1"


def test_paper_key_is_case_insensitive():
    p1 = make_paper("T", doi="10.1000/ABC")
    p2 = make_paper("T", doi="10.1000/abc")
    assert paper_key(p1) == paper_key(p2)


# --------------------------------------------------------------------------
# OpenAlex payload parsing
# --------------------------------------------------------------------------


def test_abstract_reconstructed_from_inverted_index():
    data = {"abstract_inverted_index": {"Deep": [0], "learning": [1], "is": [2], "here": [3]}}
    assert _reconstruct_abstract(data) == "Deep learning is here"
    assert _reconstruct_abstract({}) == ""


def test_openalex_paper_parsing():
    src = OpenAlexSource(cache=None)
    paper = src._paper_from_oa(
        {
            "id": "https://openalex.org/W123",
            "doi": "https://doi.org/10.1000/xyz",
            "title": "A study",
            "abstract_inverted_index": {"Hello": [0], "world": [1]},
            "authorships": [{"author": {"display_name": "Jane Doe", "id": "A1"}}],
            "publication_year": 2021,
            "cited_by_count": 7,
            "referenced_works": ["https://openalex.org/W9"],
            "primary_location": {"source": {"display_name": "Nature"}},
        }
    )
    assert paper.doi == "10.1000/xyz"
    assert paper.abstract == "Hello world"
    assert paper.year == 2021
    assert paper.reference_ids == ["W9"]


def test_openalex_select_uses_inverted_index_only():
    """`abstract` is no longer a valid OpenAlex select field — it 400s the request."""
    source = Path(__file__).resolve().parent.parent / "litsearch" / "sources.py"
    text = source.read_text(encoding="utf-8")
    assert "abstract_inverted_index" in text
    assert "abstract,authorships" not in text  # the old, broken select string


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------


def test_cache_ttl(tmp_path):
    cache = Cache(db_path=str(tmp_path / "c.db"), ttl_seconds=1)
    cache.set({"a": 1}, "k")
    assert cache.get("k") == {"a": 1}
    time.sleep(1.1)
    assert cache.get("k") is None

    cache.set({"b": 2}, "k")
    cache.delete("k")
    assert cache.get("k") is None


# --------------------------------------------------------------------------
# PRISMA
# --------------------------------------------------------------------------


def test_prisma_records_keep_relevance():
    """Regression: relevance_score was never copied, so auto-screen rejected all."""
    tracker = PRISMATracker()
    papers = [make_paper("Relevant paper", doi="10.1/a"), make_paper("Off topic", doi="10.1/b")]
    papers[0].relevance_score = 0.42
    papers[1].relevance_score = 0.01
    tracker.add_papers(papers, source="database_search")

    queue = tracker.get_screening_queue()
    assert [r.relevance_score for r in queue] == [0.42, 0.01]

    for r in queue:
        decision = (
            ScreeningDecision.ACCEPT if r.relevance_score >= 0.15 else ScreeningDecision.REJECT
        )
        tracker.screen_paper(r.paper.id, decision)

    report = tracker.generate_report()
    assert report.records_screened == 2
    assert report.studies_included == 1
    assert report.records_excluded_title_abstract == 1


def test_prisma_screen_by_doi_alias():
    tracker = PRISMATracker()
    p = make_paper("P", doi="10.1/zzz")
    p.relevance_score = 0.5
    tracker.add_papers([p])
    tracker.screen_paper("10.1/ZZZ", ScreeningDecision.ACCEPT)  # different case
    assert len(tracker.get_included_papers()) == 1


# --------------------------------------------------------------------------
# PRISMA 2020 two-stage screening
# --------------------------------------------------------------------------


def _tracker_with_three():
    tracker = PRISMATracker()
    papers = [
        make_paper("Highly relevant", doi="10.1/a"),
        make_paper("Medium", doi="10.1/b"),
        make_paper("Off topic", doi="10.1/c"),
    ]
    for p, score in zip(papers, (0.5, 0.3, 0.01), strict=False):
        p.relevance_score = score
    tracker.add_papers(papers, source="database_search")
    return tracker, papers


def test_two_stage_counts_are_separate():
    """Regression: full_text_excluded used to be hard-wired to 0."""
    tracker, papers = _tracker_with_three()

    # Stage 1 — title/abstract: keep the two relevant ones
    tracker.screen_paper("10.1/a", ScreeningDecision.ACCEPT, stage=ScreeningStage.TITLE_ABSTRACT)
    tracker.screen_paper("10.1/b", ScreeningDecision.ACCEPT, stage=ScreeningStage.TITLE_ABSTRACT)
    tracker.screen_paper("10.1/c", ScreeningDecision.REJECT, stage=ScreeningStage.TITLE_ABSTRACT)

    # Stage 2 — full text: one retrieved & accepted, one impossible to retrieve
    tracker.mark_full_text_retrieved("10.1/a", True)
    tracker.screen_paper("10.1/a", ScreeningDecision.ACCEPT, stage=ScreeningStage.FULL_TEXT)
    tracker.mark_full_text_retrieved("10.1/b", False)

    report = tracker.generate_report()
    assert report.records_screened == 3
    assert report.records_excluded_title_abstract == 1
    assert report.reports_sought == 2
    assert report.full_text_assessed == 1
    assert report.full_text_excluded == 0  # unavailable reports were never assessed
    assert report.reports_not_retrieved == 1
    assert report.studies_included == 1
    assert report.full_text_stage_enabled is True
    assert report.full_text_exclusion_reasons == {}

    # The final set must come from the eligibility stage, not from screening.
    assert [p.title for p in tracker.get_included_papers()] == ["Highly relevant"]


def test_full_text_stage_disabled_falls_back_to_screening():
    """Without any full-text decision, behaviour matches the old single stage."""
    tracker, _ = _tracker_with_three()
    tracker.screen_paper("10.1/a", ScreeningDecision.ACCEPT, stage=ScreeningStage.TITLE_ABSTRACT)
    report = tracker.generate_report()
    assert report.full_text_stage_enabled is False
    assert report.studies_included == 1
    assert report.full_text_excluded == 0


def test_full_text_queue_only_contains_accepted_records():
    tracker, _ = _tracker_with_three()
    tracker.screen_paper("10.1/a", ScreeningDecision.ACCEPT, stage=ScreeningStage.TITLE_ABSTRACT)
    tracker.screen_paper("10.1/c", ScreeningDecision.REJECT, stage=ScreeningStage.TITLE_ABSTRACT)
    queue = tracker.get_full_text_queue()
    assert [r.paper.title for r in queue] == ["Highly relevant"]
    # pending title/abstract decisions are no longer in the screening queue
    assert {r.paper.title for r in tracker.get_screening_queue()} == {"Medium"}


def test_prisma_report_mermaid():
    tracker = PRISMATracker()
    p = make_paper("P", doi="10.1/m")
    p.relevance_score = 0.9
    tracker.add_papers([p])
    tracker.screen_paper(p.id, ScreeningDecision.ACCEPT)
    mermaid = tracker.generate_report().to_mermaid()
    assert mermaid.startswith("```mermaid")
    assert "Studies included in review" in mermaid
    assert "Reports assessed for eligibility" in mermaid
    assert "Reports not retrieved" in mermaid


# --------------------------------------------------------------------------
# Session persistence (refresh-safe progress)
# --------------------------------------------------------------------------


def test_state_roundtrip(tmp_path, monkeypatch):
    import litsearch.persistence as persistence

    monkeypatch.setattr(persistence, "SESSION_DIR", str(tmp_path))

    from litsearch.search import ReviewPhase, ReviewState

    p1 = make_paper("Persisted paper", doi="10.9/keep", year=2021, citations=3,
                    abstract="an abstract")
    p1.relevance_score = 0.42
    state = ReviewState(topic="persistence topic", research_direction="persistence")
    state.search_papers = [p1]
    state.scoping_papers = [p1]
    state.year_distribution = {2021: 1}
    state.key_journals = [("Test Journal", 1)]
    state.prisma.add_papers([p1], source="database_search")
    state.prisma.screen_paper("10.9/keep", ScreeningDecision.ACCEPT,
                              stage=ScreeningStage.TITLE_ABSTRACT)
    state.prisma.mark_full_text_retrieved("10.9/keep", True)
    state.prisma.screen_paper("10.9/keep", ScreeningDecision.ACCEPT,
                              stage=ScreeningStage.FULL_TEXT)
    state.phase = ReviewPhase.SCREENING

    path = persistence.save_state(state)
    assert os.path.exists(path)

    restored = persistence.load_state()
    assert restored is not None
    assert restored.topic == "persistence topic"
    assert restored.phase == ReviewPhase.SCREENING
    assert len(restored.search_papers) == 1
    assert restored.search_papers[0].doi == "10.9/keep"
    assert restored.search_papers[0].abstract == "an abstract"
    assert restored.year_distribution == {2021: 1}
    # decisions survive, including the two-stage split
    assert restored.prisma.full_text_stage_enabled is True
    assert len(restored.prisma.get_included_papers()) == 1

    sessions = persistence.list_sessions()
    assert sessions and sessions[0]["topic"] == "persistence topic"


def test_load_state_missing_file_returns_none(tmp_path, monkeypatch):
    import litsearch.persistence as persistence

    monkeypatch.setattr(persistence, "SESSION_DIR", str(tmp_path / "nope"))
    assert persistence.load_state() is None


# --------------------------------------------------------------------------
# Semantic Scholar pacing / 429 handling
# --------------------------------------------------------------------------


def test_s2_request_retries_on_429(monkeypatch):
    """429 must trigger a bounded retry, not an infinite loop."""
    from litsearch.sources import SemanticScholarSource, reset_s2_stats, s2_stats

    src = SemanticScholarSource(cache=None)
    reset_s2_stats()
    monkeypatch.setattr(src, "_pace", lambda: None)
    monkeypatch.setattr(time, "sleep", lambda *_a: None)

    responses = [type("R", (), {"status_code": 429, "headers": {}})() for _ in range(3)]
    responses.append(type("R", (), {"status_code": 200, "headers": {}})())
    calls = {"n": 0}

    def fake_request(method, url, **kwargs):
        calls["n"] += 1
        return responses[min(calls["n"] - 1, len(responses) - 1)]

    monkeypatch.setattr(src._session, "request", fake_request)
    resp = src._request("GET", "https://example.invalid")
    assert resp.status_code == 200
    assert calls["n"] == 4
    assert s2_stats()["rate_limited"] == 3


def test_s2_request_gives_up_after_max_retries(monkeypatch):
    from litsearch.sources import SemanticScholarSource

    src = SemanticScholarSource(cache=None)
    monkeypatch.setattr(src, "_pace", lambda: None)
    monkeypatch.setattr(time, "sleep", lambda *_a: None)
    monkeypatch.setattr(
        src._session, "request",
        lambda *a, **k: type("R", (), {"status_code": 429, "headers": {}})(),
    )
    resp = src._request("GET", "https://example.invalid")
    assert resp.status_code == 429  # caller degrades gracefully instead of hanging


# --------------------------------------------------------------------------
# Filters
# --------------------------------------------------------------------------


def test_dedup_merges_metadata():
    a = make_paper("Same", doi="10.1/dup", citations=1)
    b = make_paper("Same", doi="10.1/dup", citations=9, abstract="abstract text")
    out = RelevanceFilter().deduplicate_by_doi([a, b])
    assert len(out) == 1
    assert out[0].citation_count == 9
    assert out[0].abstract == "abstract text"


def test_relevance_scoring_orders_papers():
    papers = [
        make_paper("deep learning for plant phenotyping", abstract="deep learning phenotyping"),
        make_paper("astrophysics of black holes", abstract="black holes"),
    ]
    RelevanceFilter().compute_relevance(papers, "deep learning plant phenotyping")
    assert papers[0].relevance_score > papers[1].relevance_score


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------


def test_year_helpers():
    assert year_from_years_back(20) == max(1900, current_year() - 20)
    assert year_from_years_back(500, floor=1990) == 1990


# --------------------------------------------------------------------------
# Downloader policy
# --------------------------------------------------------------------------


def test_no_scihub_in_downloader():
    """The Sci-Hub scraping tier must be gone (only the docstring may mention it)."""
    src = Path(__file__).resolve().parent.parent / "litsearch" / "downloader.py"
    text = src.read_text(encoding="utf-8").lower()
    assert "SCI_HUB_DOMAINS" not in src.read_text(encoding="utf-8")
    for domain in ("sci-hub.se", "sci-hub.st", "sci-hub.ru"):
        assert domain not in text
    assert "beautifulsoup" not in text


def test_journal_metric_is_labelled_a_citation_proxy_not_an_impact_factor(tmp_path, monkeypatch):
    """Spec 2.3 / H8: only an official metric may be called an Impact Factor.

    v0.9.0 printed ``IF: 50.5`` for a vendored table value and
    ``IF: ~8.3 (est.)`` for a derived one, so both read as Journal Impact
    Factors. Neither is one.
    """
    lookup = ImpactFactorLookup(cache=Cache(db_path=str(tmp_path / "if.db")))

    record = lookup.citation_proxy("nature")
    assert record["kind"] == "citation_proxy"
    assert record["value"] == 50.5
    assert record["verified"] is False
    assert record["needs_source_verification"] is True
    assert record["source"] and record["year"] and record["formula"]
    assert "Citation Proxy" in record["display"]
    assert "IF" not in record["display"].replace("Citation Proxy", ""), (
        "the display string must not invite reading it as an Impact Factor"
    )

    # No network in tests: force the OpenAlex lookup to fail
    def boom(*a, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr(lookup._session, "get", boom)
    missing = lookup.citation_proxy("journal of unknown things")
    assert missing["value"] is None
    assert missing["display"] == "Citation Proxy: N/A"
    assert missing["verified"] is False
    # Absence of a proxy is not a judgement about the journal.
    assert "not evidence" in missing["note"]


def test_citation_proxy_never_claims_official_verification(tmp_path):
    lookup = ImpactFactorLookup(cache=Cache(db_path=str(tmp_path / "if2.db")))
    for venue in ("nature", "unknown journal xyz"):
        record = lookup.citation_proxy(venue)
        assert record["verified"] is False
        assert record["kind"] == "citation_proxy"


def test_workflow_constructs():
    """The workflow object must build without touching the network."""
    wf = LiteratureReviewWorkflow(source_manager=object())  # type: ignore[arg-type]
    assert wf.filters is not None
    assert wf.downloader is not None


# ------------------------------------------------------------------
# Relevance scoring (hybrid word/char/coverage + threshold calibration)
# ------------------------------------------------------------------


def _paper(pid, title, abstract="", cites=10):
    return Paper(id=pid, title=title, abstract=abstract, year=2020,
                 citation_count=cites, authors=[Author(name="A")])


def test_hybrid_scoring_separates_on_topic_from_off_topic():
    """The whole point of the hybrid: short query must still separate."""
    papers = [
        _paper("a", "Deep learning for plant phenotyping in wheat",
               "Convolutional networks predict plant phenotypic traits from images.", 120),
        _paper("b", "Plant phenotyping using deep convolutional networks",
               "Deep learning for non-destructive plant phenotype estimation.", 80),
        _paper("c", "Marine snow flux in the Southern Ocean",
               "Sediment traps reveal particulate carbon export.", 300),
        _paper("d", "CRISPR knockout screens in zebrafish",
               "Genome editing in vertebrate development.", 400),
    ]
    f = RelevanceFilter()
    scored = f.compute_relevance(papers, "deep learning for plant phenotyping")

    # The two on-topic papers must occupy the top two slots; the relative
    # order of the two off-topic papers is not meaningful.
    assert [p.id for p in scored[:2]] == ["a", "b"]
    assert scored[0].relevance_score > scored[1].relevance_score > scored[2].relevance_score
    # scores stay inside the 0-1 range the threshold slider assumes
    assert all(0.0 <= p.relevance_score <= 1.0 for p in scored)


def test_coverage_matches_inflected_variants():
    """'phenotyping' must count as a hit for the concept 'phenotyp'."""
    terms = RelevanceFilter._query_terms("plant phenotyping")
    assert "phenotyp" in terms
    hits = RelevanceFilter._coverage(
        terms, _paper("x", "Estimating plant phenotype from images")
    )
    assert hits > 0.5, f"expected both concepts matched, got {hits}"


def test_coverage_ignores_stopword_padding():
    """A paper repeating only query stopwords scores zero coverage."""
    terms = RelevanceFilter._query_terms("the use of a study on results")
    filtered = RelevanceFilter._query_terms("the use of a study on results")
    assert filtered == [], "all-stopword query must yield no terms"
    assert RelevanceFilter._coverage(terms, _paper("x", "A study of results")) == 0.0


def test_scoring_degrades_gracefully_on_empty_corpus():
    """Empty input and empty query must not raise."""
    f = RelevanceFilter()
    assert f.compute_relevance([], "anything") == []
    p = _paper("a", "Title only")
    assert f.compute_relevance([p], "   ") == [p]
    assert p.relevance_score == 0.0


def test_score_breakdown_explains_final():
    """Every scored paper gets a breakdown row summing to the final score."""
    papers = [_paper("a", "Deep learning phenotyping"), _paper("b", "Marine snow flux")]
    f = RelevanceFilter()
    f.compute_relevance(papers, "deep learning phenotyping")
    bd = f.last_breakdown
    assert len(bd) == 2
    assert {b.paper_id for b in bd} == {"a", "b"}
    for b in bd:
        assert 0.0 <= b.final <= 1.0
        assert 0.0 <= b.coverage <= 1.0


def test_calibrate_threshold_separated_and_overlapping():
    """Separated labels → threshold in the gap; overlapping → still a number."""
    rel = [_paper("r1", "x"), _paper("r2", "y")]
    irr = [_paper("i1", "z")]
    for p, s in [(rel[0], 0.80), (rel[1], 0.62), (irr[0], 0.20)]:
        p.relevance_score = s

    value, note = RelevanceFilter.calibrate_threshold(rel, irr)
    assert 0.20 < value < 0.62, value
    assert "分离" in note

    irr[0].relevance_score = 0.70  # now overlaps
    value2, note2 = RelevanceFilter.calibrate_threshold(rel, irr)
    assert 0.62 < value2 < 0.80, value2
    assert "重叠" in note2


def test_calibrate_threshold_degrades_with_one_sided_labels():
    """One-sided labels must still return a usable number plus a caveat."""
    only_rel = [_paper("r", "x")]
    only_rel[0].relevance_score = 0.5
    value, note = RelevanceFilter.calibrate_threshold(only_rel, [])
    assert 0 < value < 0.5
    assert "补充" in note

    value2, note2 = RelevanceFilter.calibrate_threshold([], [])
    assert value2 == 0.15, "no labels at all must fall back to the default"


# ------------------------------------------------------------------
# Error classification and diagnostics
# ------------------------------------------------------------------


def test_http_status_classification():
    """Each status must land in the bucket that says whether retrying helps."""
    from litsearch.diagnostics import (
        NotFoundError,
        PermanentSourceError,
        RateLimitedError,
        TransientSourceError,
    )

    assert isinstance(classify_http_error("s2", 429), RateLimitedError)
    assert isinstance(classify_http_error("s2", 404), NotFoundError)
    for st in (400, 401, 403, 422):
        assert isinstance(classify_http_error("s2", st), PermanentSourceError), st
    for st in (500, 502, 503):
        assert isinstance(classify_http_error("s2", st), TransientSourceError), st

    # A 400 must NOT be retryable — that inversion is the bug this fixes.
    assert classify_http_error("s2", 400).kind.value == "contract"
    assert classify_http_error("s2", 429).kind.value == "rate_limited"


def test_as_source_error_records_every_failure_class():
    """requests-level and parse-level exceptions both land in diagnostics."""
    import requests

    from litsearch.sources import as_source_error

    reset_diagnostics()
    diag = get_diagnostics()

    resp = requests.Response()
    resp.status_code = 400
    resp.url = "https://api.semanticscholar.org/graph/v1/paper/search"
    err = requests.exceptions.HTTPError("400 Client Error", response=resp)

    as_source_error(err, "semantic_scholar", "search")
    as_source_error(requests.exceptions.Timeout("timed out"), "openalex", "search")
    as_source_error(KeyError("results"), "openalex", "parse")

    kinds = diag.counts()
    assert kinds["contract"] == 1
    assert kinds["transient"] == 1
    assert kinds["parse"] == 1
    assert diag.has_contract_problems()


def test_diagnostics_bounded_but_counted():
    """Overflow must be visible, not silently dropped."""
    log = DiagnosticsLog(max_events=5)
    for i in range(20):
        log.record("s", SourceErrorKind.TRANSIENT, f"err{i}")
    assert len(log.events) == 6  # 5 real + 1 truncation notice
    assert "truncated" in log.events[-1].message
    assert log.counts()[SourceErrorKind.TRANSIENT.value] == 5


def test_diagnostics_summary_is_actionable():
    log = DiagnosticsLog()
    log.record("s2", SourceErrorKind.RATE_LIMITED, "x", 429)
    assert "S2_API_KEY" in log.summary()

    log.clear()
    log.record("s2", SourceErrorKind.CONTRACT, "bad field", 400)
    assert "人工检查" in log.summary()


def test_raise_for_data_classifies_and_raises():
    """raise_for_data must raise a classified SourceError, not HTTPError."""
    import requests

    from litsearch.sources import raise_for_data

    reset_diagnostics()
    ok = requests.Response()
    ok.status_code = 200
    raise_for_data(ok, "s2")  # must not raise

    bad = requests.Response()
    bad.status_code = 422
    bad.url = "https://example.org"
    with pytest.raises(PermanentSourceError):
        raise_for_data(bad, "openalex", "search")
    assert get_diagnostics().counts()["contract"] == 1
