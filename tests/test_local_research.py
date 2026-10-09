"""Direction retrieval, local semantic inputs, public venue/source contracts."""

import json
import time

import numpy as np
import pytest
import requests
from fastapi.testclient import TestClient

from litsearch.cache import Cache
from litsearch.conference_sources import (
    ConferenceSource,
    DBLPSource,
    GoogleScholarSource,
    OpenReviewSource,
)
from litsearch.filters import RelevanceFilter
from litsearch.identifiers import identifier_key
from litsearch.local_models import LocalEmbedding, LocalModelError
from litsearch.models import Paper
from litsearch.persistence import paper_from_dict, paper_to_dict
from litsearch.query_context import focused_plan, prepare_query
from litsearch.retrieval import RequestStats, RetrievalResult, RetrievalStatus
from litsearch.search import LiteratureReviewWorkflow, ReviewState, ranking_query
from litsearch.sources import SourceManager
from litsearch.venues import catalog, venue_classification
from litsearch.web import create_app


def test_full_direction_translation_drives_every_query_and_ranking():
    text = "计算机视觉领域的多智能体合作问题"
    context = prepare_query("深度学习", text, translator=lambda raw: "multi-agent cooperation in computer vision")
    plan = focused_plan(context, 2020, 2026)
    state = ReviewState("深度学习", text, search_manifest={"query_context": context})
    assert ranking_query(state) == context["translated"]
    assert all("agent" in row["query"] for row in plan["queries"].values())
    assert "deep learning" not in json.dumps(plan["queries"])
    assert "google_scholar" in plan["queries"] and "dblp" not in plan["queries"]
    assert plan["translation"]["original"] == text


def test_manual_query_bypasses_model_and_keeps_original():
    context = prepare_query("深度学习", "多智能体合作", "cooperative visual perception", translator=lambda _: pytest.fail("No model call for manual English"))
    assert context["engine"] == "manual"
    assert context["original"] == "多智能体合作"


def test_translation_failure_stops_before_job_and_preserves_project(tmp_path):
    def fail(_):
        raise LocalModelError("model unavailable")
    with TestClient(create_app(tmp_path, translator=fail)) as c:
        p = c.post("/api/projects", json={"topic": "深度学习", "research_direction": "多智能体合作"}).json()
        url = f'/api/projects/{p["project_id"]}'
        assert c.post(url + "/search", json={"expected_revision": p["revision"], "translate": True}).status_code == 422
        assert c.get(url).json() == p
        assert not list((tmp_path / "jobs").glob("*.json"))


def test_query_plan_translation_is_read_only(tmp_path):
    with TestClient(create_app(tmp_path, translator=lambda _: "multi-agent visual cooperation")) as c:
        p = c.post("/api/projects", json={"topic": "深度学习", "research_direction": "多智能体合作"}).json()
        url = f'/api/projects/{p["project_id"]}'
        r = c.post(url + "/query-plan", json={"translate": True, "year_from": 2020, "year_to": 2026})
        assert r.status_code == 200
        assert r.json()["translation"]["translated"] == "multi-agent visual cooperation"
        assert c.get(url).json() == p


def test_embeddings_use_title_and_entire_abstract_including_tail(monkeypatch):
    model = LocalEmbedding()
    captured = []
    def vectors(texts):
        captured.extend(texts)
        return np.ones((len(texts), 3))
    monkeypatch.setattr(model, "vectors", vectors)
    abstract = " ".join(["early"] * 200 + ["unique_tail_evidence"])
    assert model.scores([Paper("p", "paper title", abstract=abstract), Paper("q", "title only")], "research query") == pytest.approx([1, 1])
    assert "paper title" in "".join(captured[1:-1])
    assert "unique_tail_evidence" in "".join(captured[1:-1])
    assert captured[-1].startswith("title only")


def test_chunks_preserve_chinese_text_and_obey_actual_token_limit():
    model = LocalEmbedding()
    # Two tokens per character models the failure of whitespace-only chunks.
    class Tokens:
        def encode(self, s, **kwargs):
            return type("Encoded", (), {"ids": list(range(len(s) * 2))})()
    model.tokenizer = Tokens()
    text = "标题与摘要" * 200
    chunks = model._chunks(text)
    assert "".join(chunks) == text
    assert all(len(model.tokenizer.encode(chunk).ids) <= 120 for chunk in chunks)


def test_semantic_model_can_match_without_lexical_overlap():
    class Model:
        def scores(self, papers, query):
            return [0.8 if p.id == "related" else 0.1 for p in papers]
    ranker = RelevanceFilter()
    ranker.embedding_model = Model()
    papers = ranker.compute_relevance([Paper("noise", "Apples"), Paper("related", "Robots")], "协同感知")
    assert papers[0].id == "related"
    assert papers[0].score_breakdown["semantic"] == 0.8


def test_ranking_mode_invalidates_calibration_context():
    p = [Paper("p", "visual agents", abstract="cooperative perception")]
    state = ReviewState("vision", "cooperative agents")
    lexical = LiteratureReviewWorkflow.build_score_context(state, p)
    state.search_manifest["ranking_mode"] = "semantic"
    assert lexical != LiteratureReviewWorkflow.build_score_context(state, p)


def test_openreview_filters_reviews_and_retains_publication_status():
    source = OpenReviewSource()
    note = {"id": "AbC", "forum": "AbC", "content": {"title": {"value": "Agents"}, "abstract": {"value": "Visual cooperation"}, "venue": {"value": "Submitted to ICLR 2025"}, "authors": {"value": ["A"]}}}
    rows = [row for row in source.extract({"notes": [note, {"id": "review", "forum": "AbC"}]}) if source.is_paper(row)]
    assert len(rows) == 1
    paper = source.to_paper(rows[0])
    assert paper.abstract == "Visual cooperation" and paper.year == 2025
    assert paper.publication_status == "submission"
    assert paper_from_dict(paper_to_dict(paper)).identifiers.openreview_id == "AbC"
    assert source._get_references_result("AbC").failed
    assert source.params("agents", 2, 100)["source"] == "forum"
    assert source.params("agents", 2, 100) == {"term": "agents", "type": "terms", "content": "all", "source": "forum", "limit": 100, "offset": 100}


def test_dblp_single_author_doi_and_missing_abstract():
    paper = DBLPSource().to_paper({"info": {"key": "conf/cvpr/AbC25", "title": "Agents", "venue": "CVPR", "year": "2025", "doi": "https://doi.org/10.1234/A", "authors": {"author": {"text": "Alice"}}}})
    assert paper.doi == "10.1234/a" and paper.abstract is None
    assert paper.authors[0].name == "Alice"
    assert venue_classification(paper)["rank"] == "A"


def test_native_ids_are_never_sent_to_semantic_scholar(tmp_path):
    manager = SourceManager(Cache(str(tmp_path / "cache.db")))
    assert [s.name for s, _ in manager._routes("openreview:AbC")] == ["openreview"]
    assert [s.name for s, _ in manager._routes("dblp:conf/cvpr/AbC25")] == ["dblp"]
    assert [s.name for s, _ in manager._routes("google_scholar:AbC")] == ["google_scholar"]
    assert identifier_key("AbC", "openreview") != identifier_key("abc", "openreview")


@pytest.mark.parametrize("venue,rank", [("ICLR 2025 poster", "A"), ("IJCAI", "B"), ("BMVC", "C"), ("CVPR Workshops", None), ("Findings of ACL", None), ("Imaginary Conference", None)])
def test_current_ccf_ranks_do_not_mislabel_workshops(venue, rank):
    result = venue_classification(Paper("p", "title", venue=venue))
    assert result["rank"] == rank
    assert result["eligibility"] != "verified_full_paper"


def test_catalog_counts_match_official_pdf():
    entries = catalog()["entries"]
    assert len(entries) == 681
    assert sum(e["kind"] == "conference" for e in entries) == 386


def test_dblp_html_challenge_is_failure_not_empty_or_retried(monkeypatch):
    calls = []
    response = requests.Response()
    response.status_code = 200
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    response._content = b"<html>access verification</html>"
    monkeypatch.setattr(requests.Session, "request", lambda *a, **k: calls.append(k) or response)
    result = DBLPSource()._search_papers_result("agents", 10, 2020, 2026)
    assert result.failed and not result.genuine_empty
    assert "ParseError" in result.error and "HTML" in result.error
    assert len(calls) == 1 and result.request_stats.retries == 0


def test_openreview_cap_includes_both_api_generations(monkeypatch):
    def retrieve(source, *args):
        prefix = "new" if "api2" in source.endpoint else "old"
        return RetrievalResult(papers=[Paper(prefix + str(i), "agents", year=2025 if prefix == "new" else 2022) for i in range(4)],
                               provider="openreview", status=RetrievalStatus.TRUNCATED, complete=False, request_stats=RequestStats(requests=1))
    monkeypatch.setattr(ConferenceSource, "_search_papers_result", retrieve)
    source = OpenReviewSource()
    result = source._search_papers_result("agents", 4, 2020, 2026)
    assert [p.id for p in result.papers] == ["new0", "old0", "new1", "old1"]
    assert result.request_stats.requests == 2 and not result.complete
    assert len(source.api_coverage) == 2 and "api2" in source.endpoint


def test_focus_aliases_do_not_discard_other_domain_terms():
    query = "multi-agent collaboration in computer vision for agriculture"
    visual = Paper("visual", "Multi-agent collaborative visual perception")
    crop = Paper("crop", "Multi-agent collaborative visual perception for agriculture")
    assert RelevanceFilter._concept_coverage(query, crop) > RelevanceFilter._concept_coverage(query, visual)


def test_model_failure_preserves_corpus_and_reports_local_error(tmp_path, monkeypatch):
    def fail():
        raise LocalModelError("local embedding unavailable")
    monkeypatch.setattr("litsearch.local_models.EMBEDDER.ensure_ready", fail)
    app = create_app(tmp_path)
    with TestClient(app) as client:
        p = client.post("/api/projects", json={"topic": "visual cooperation"}).json()
        url = f'/api/projects/{p["project_id"]}'
        project = app.state.repository.get(p["project_id"])
        project.state.papers = [Paper("existing", "Previously screened paper", year=2024)]
        app.state.repository.save(project, project.state)
        p = client.get(url).json()
        before = client.get(url + "/papers").json()["total"]
        response = client.post(url + "/search", json={"expected_revision": p["revision"], "ranking_mode": "semantic", "search_keywords": "visual cooperation"})
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        for _ in range(100):
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["status"] not in {"running", "queued"}:
                break
            time.sleep(0.01)
        assert job["stop_reason"] == "local_model_failure"
        assert job["error"]["message"] == "local embedding unavailable"
        assert client.get(url + "/papers").json()["total"] == before
        assert job["http_budget"]["requests"] == 0


def test_scholar_missing_key_sends_no_requests(monkeypatch):
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    result = GoogleScholarSource()._search_papers_result("visual agents")
    assert result.failed and not result.genuine_empty and "SerpApi" in result.error
    assert result.request_stats.requests == 0


def test_scholar_paging_years_snippets_cache_and_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "scholar-test-secret")
    calls = []
    def transport(session, method, url, **kwargs):
        params = kwargs["params"]
        calls.append(params)
        start = params["start"]
        response = requests.Response()
        response.status_code = 200
        response.url = url + "?api_key=scholar-test-secret"
        response._content = json.dumps({"organic_results": [{"result_id": f"Case{i}", "title": "Visual agents",
            "link": "https://example.org/paper", "snippet": "Truncated search excerpt…",
            "publication_info": {"summary": "A Author - CVPR, 2025 - example.org", "authors": [{"name": "A Author"}]}}
            for i in range(start, start + (20 if start == 0 else 5))]}).encode()
        return response
    monkeypatch.setattr(requests.Session, "request", transport)
    monkeypatch.setattr("litsearch.sources.time.sleep", lambda _: None)
    source = GoogleScholarSource(Cache(str(tmp_path / "cache.db")))
    result = source._search_papers_result("agents", 30, 2020, 2026)
    assert result.complete and len(result.papers) == 25
    assert [p["start"] for p in calls] == [0, 20]
    assert all(p["num"] == 20 and p["as_ylo"] == 2020 and p["as_yhi"] == 2026 for p in calls)
    paper = result.papers[0]
    assert paper.abstract is None and paper.search_snippet.endswith("…")
    assert paper_from_dict(paper_to_dict(paper)).identifiers.google_scholar_id == "Case0"
    assert venue_classification(paper)["rank"] == "A"
    again = source._search_papers_result("agents", 30, 2020, 2026)
    assert again.request_stats.cache_hits == 1 and again.request_stats.requests == 0 and len(calls) == 2
    assert "scholar-test-secret" not in (tmp_path / "cache.db").read_bytes().decode(errors="ignore")


def test_scholar_error_redacts_key_and_never_claims_empty(monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "scholar-secret")
    response = requests.Response()
    response.status_code = 401
    response.url = "https://serpapi.com/search.json?api_key=scholar-secret"
    response._content = b'{"error":"Invalid scholar-secret API key"}'
    monkeypatch.setattr(requests.Session, "request", lambda *a, **k: response)
    result = GoogleScholarSource()._search_papers_result("agents")
    assert result.failed and "scholar-secret" not in result.error


def test_scholar_settings_store_key_without_returning_it(tmp_path, monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "")
    with TestClient(create_app(tmp_path)) as client:
        response = client.post("/api/settings", json={"serpapi_api_key": "scholar-private-key"})
        assert response.status_code == 200 and response.json()["providers"]["google_scholar"]["configured"]
        assert "scholar-private-key" not in response.text
        assert "scholar-private-key" not in client.get("/api/settings").text
