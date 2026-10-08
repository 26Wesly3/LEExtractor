import json
import zipfile
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from litsearch.bundle import build_evidence_pack
from litsearch.cache import Cache
from litsearch.demo import demo_state
from litsearch.evidence import EvidenceGraph
from litsearch.filters import RelevanceFilter
from litsearch.identifiers import PaperIdentifiers, normalize_arxiv, normalize_doi
from litsearch.models import DiscoveryTrace, Paper
from litsearch.persistence import load_state, paper_from_dict, paper_to_dict, save_state
from litsearch.prisma import PRISMATracker, ScreeningDecision
from litsearch.search import LiteratureReviewWorkflow, ReviewState
from litsearch.similar import SimilarPaperFinder
from litsearch.sources import (
    ArxivSource,
    OpenAlexSource,
    SemanticScholarSource,
    SourceManager,
)


def response(data=None, text="", status=200):
    result = Mock(status_code=status, text=text, url="https://example.invalid")
    result.json.return_value = data
    return result


def atom_entry(pid, year=2022):
    return f'<entry xmlns="http://www.w3.org/2005/Atom"><id>http://arxiv.org/abs/{pid}</id><title>Plant traits</title><published>{year}-01-01T00:00:00Z</published><summary>Plant images</summary></entry>'


def test_identifiers_merge_doi_urls_provider_aliases_and_keep_trace():
    a = Paper(id="S2-A", title="plant traits", doi="https://doi.org/10.1000/ABC", source="semantic_scholar",
              identifiers=PaperIdentifiers(semantic_scholar_id="S2-A"), discovery_traces=[DiscoveryTrace(method="keyword", provider="semantic_scholar")])
    b = Paper(id="W123", title="plant traits", doi="DOI:10.1000/abc", source="openalex", abstract="richer",
              identifiers=PaperIdentifiers(openalex_id="W123"), discovery_traces=[DiscoveryTrace(method="keyword", provider="openalex")])
    c = Paper(id="S2-A", title="plant traits", source="semantic_scholar", identifiers=PaperIdentifiers(semantic_scholar_id="S2-A"))
    papers = RelevanceFilter().deduplicate_by_doi([a, c, b])
    assert len(papers) == 1
    assert papers[0].canonical_id == "10.1000/abc"
    assert papers[0].identifiers.openalex_id == "W123"
    assert papers[0].abstract == "richer"
    assert len(papers[0].discovery_traces) == 2
    tracker = PRISMATracker()
    tracker.add_papers(papers)
    tracker.screen_paper("https://openalex.org/W123", ScreeningDecision.ACCEPT)
    assert tracker.get_included_papers() == papers


def test_identifier_bridge_merges_two_existing_records():
    a = Paper(id="hash", title="title", source="semantic_scholar", identifiers=PaperIdentifiers(semantic_scholar_id="hash"))
    b = Paper(id="W1", title="title", source="openalex", identifiers=PaperIdentifiers(openalex_id="W1"))
    bridge = Paper(id="10.1/a", doi="10.1/a", title="title", identifiers=PaperIdentifiers(semantic_scholar_id="hash", openalex_id="W1"))
    result = RelevanceFilter().deduplicate_by_doi([a, b, bridge])
    assert len(result) == 1
    assert result[0].doi == "10.1/a"


def test_arxiv_versions_share_identity_without_merging_different_dois():
    assert normalize_arxiv("https://arxiv.org/pdf/1706.03762v3.pdf") == "1706.03762"
    a = Paper(id="1706.03762v2", title="title", source="arxiv")
    b = Paper(id="1706.03762v3", title="title", source="arxiv")
    assert len(RelevanceFilter().deduplicate_by_doi([a, b])) == 1
    assert normalize_doi("DOI:10.1/UPPER") == "10.1/upper"
    x = Paper(id="10.1/a", doi="10.1/a", title="same title")
    y = Paper(id="10.1/b", doi="10.1/b", title="same title")
    assert len(RelevanceFilter().deduplicate_by_doi([x, y])) == 2


def test_s2_url_without_doi_and_no_secret_leaks_to_other_providers(monkeypatch):
    monkeypatch.setenv("S2_API_KEY", "test-secret")
    source = SemanticScholarSource()
    paper = source._paper_from_s2({"paperId": "hash", "url": "https://example.org/paper"})
    assert paper.url == "https://example.org/paper"
    assert "x-api-key" not in OpenAlexSource()._session.headers
    monkeypatch.setattr(source, "_pace", lambda: None)
    source._session.request = Mock(return_value=response())
    source._request("GET", "https://example.invalid")
    assert source._session.headers["x-api-key"] == "test-secret"
    monkeypatch.setenv("S2_API_KEY", "new-secret")
    source._request("GET", "https://example.invalid")
    assert source._session.headers["x-api-key"] == "new-secret"


def test_openalex_auth_is_header_only_and_refreshes(monkeypatch):
    source = OpenAlexSource()
    monkeypatch.setenv("OPENALEX_API_KEY", "test-key")
    req = source._session.prepare_request(requests.Request("GET", "https://api.openalex.org/works"))
    assert req.headers["Authorization"] == "Bearer test-key"
    assert "test-key" not in req.url
    monkeypatch.delenv("OPENALEX_API_KEY")
    req = source._session.prepare_request(requests.Request("GET", "https://api.openalex.org/works"))
    assert "Authorization" not in req.headers


def test_openalex_cache_preserves_no_doi_identity_and_reference_ids(tmp_path):
    source = OpenAlexSource(Cache(str(tmp_path / "cache.db")))
    source._session.get = Mock(return_value=response({"results": [{"id": "https://openalex.org/W123", "title": "Plant traits", "referenced_works": ["https://openalex.org/W99"]}]}))
    first = source.search_papers("plant", limit=1)
    second = source.search_papers("plant", limit=1)
    assert first[0].canonical_id == second[0].canonical_id == "openalex:W123"
    assert second[0].reference_ids == ["W99"]
    assert source._session.get.call_count == 1


def test_openalex_reference_batches_use_valid_ids_filter():
    source = OpenAlexSource()
    source.get_paper = Mock(return_value=Paper(id="W1", title="seed", reference_ids=["W2", "W3"]))
    source._session.get = Mock(return_value=response({"results": [{"id": "https://openalex.org/W2", "title": "ref"}]}))
    assert len(source.get_references("W1")) == 1
    assert source._session.get.call_args.kwargs["params"]["filter"] == "ids.openalex:W2|W3"


def test_provider_routing_never_sends_openalex_id_to_s2():
    manager = SourceManager()
    manager.s2.get_citations = Mock(return_value=[])
    manager.oa.get_citations = Mock(return_value=[Paper(id="child", title="child")])
    manager.get_citations("https://openalex.org/W123")
    manager.s2.get_citations.assert_not_called()
    manager.oa.get_citations.assert_called_once_with("W123", 100)
    manager.oa.get_citations.reset_mock()
    manager.get_citations("s2:hash")
    manager.oa.get_citations.assert_not_called()


def test_search_manager_isolates_source_failure_and_preserves_raw_metadata():
    manager = SourceManager()
    manager.s2.search_papers = Mock(side_effect=RuntimeError("provider down"))
    manager.oa.search_papers = Mock(return_value=[Paper(id="10.1/a", doi="10.1/a", title="plant traits", source="openalex")])
    manager.arxiv.search_papers = Mock(return_value=[Paper(id="arxiv", doi="10.1/a", title="plant traits", abstract="richer", source="arxiv")])
    result = manager.search_all_sources("plant", limit=4, year_from=2020, year_to=2024)
    assert len(result) == 2
    merged = RelevanceFilter().deduplicate_by_doi(result)
    assert merged[0].abstract == "richer"
    assert len(merged[0].discovery_traces) == 2
    assert manager.last_search_manifest["provider_counts"]["semantic_scholar"] == 0
    assert manager.arxiv.search_papers.call_args.kwargs["year_from"] == 2020


def test_arxiv_year_filter_applies_to_request_results_and_cache(tmp_path):
    source = ArxivSource(Cache(str(tmp_path / "cache.db")))
    source._session.get = Mock(return_value=response(text='<feed xmlns="http://www.w3.org/2005/Atom">' + atom_entry("2201.00001", 2022) + atom_entry("1701.00001", 2017) + '</feed>'))
    result = source.search_papers("plant", limit=5, year_from=2020, year_to=2024)
    assert [p.year for p in result] == [2022]
    query = source._session.get.call_args.kwargs["params"]["search_query"]
    assert "202001010000" in query and "202412312359" in query
    source.search_papers("plant", limit=5, year_from=2020, year_to=2024)
    assert source._session.get.call_count == 1
    source.search_papers("plant", limit=5, year_from=2010, year_to=2024)
    assert source._session.get.call_count == 2


def test_arxiv_paginates_beyond_one_hundred(monkeypatch):
    source = ArxivSource()
    pages = ['<feed xmlns="http://www.w3.org/2005/Atom">' + ''.join(atom_entry(f"2201.{i:05}") for i in range(100)) + '</feed>',
             '<feed xmlns="http://www.w3.org/2005/Atom">' + ''.join(atom_entry(f"2201.{i:05}") for i in range(100, 105)) + '</feed>']
    source._session.get = Mock(side_effect=[response(text=text) for text in pages])
    monkeypatch.setattr("litsearch.sources.time.sleep", lambda _: None)
    assert len(source.search_papers("plant", 105, 2020, 2024)) == 105
    assert source._session.get.call_args.kwargs["params"]["start"] == 100


def test_cache_keys_do_not_collide_and_old_unversioned_rows_are_ignored(tmp_path):
    import sqlite3
    cache = Cache(str(tmp_path / "cache.db"))
    cache.set("a", "x|y", "z")
    cache.set("b", "x", "y|z")
    assert cache.get("x|y", "z") == "a"
    assert cache.get("x", "y|z") == "b"
    with sqlite3.connect(cache._db_path) as connection:
        connection.execute("INSERT INTO cache VALUES ('old|key', '{}', 9999999999)")
    assert cache.get("old", "key") is None


def test_identical_or_singleton_relevance_never_loses_all_signal():
    papers = [Paper(id="a", title="deep learning"), Paper(id="b", title="deep learning")]
    RelevanceFilter().compute_relevance(papers, "deep learning")
    assert all(p.relevance_score > 0.8 for p in papers)
    single = [Paper(id="a", title="深度学习植物表型")]
    RelevanceFilter().compute_relevance(single, "深度学习植物表型")
    assert single[0].relevance_score > 0.7


def test_similar_method_budgets_are_independent_and_count_all_calls():
    seed1 = Paper(id="s1", title="seed", reference_ids=["r1", "r2"])
    seed2 = Paper(id="s2", title="seed", reference_ids=["r1", "r2"])
    candidate = Paper(id="c", title="candidate")
    citer = Paper(id="later", title="later", reference_ids=["s1", "s2", "c"])
    source = SimpleNamespace(get_citations=Mock(side_effect=lambda ref, limit: [citer] if isinstance(ref, Paper) else [candidate, candidate]), get_paper=Mock(return_value=candidate), get_references=Mock(return_value=[]))
    finder = SimilarPaperFinder(source, api_budget=3)
    result = finder.find_similar([seed1, seed2])
    assert finder.budget_usage == {"bibliographic_coupling": 2, "co_citation": 3}
    assert result[0][0].id == "c"
    assert all(0 <= score <= 1 for _, score, _ in result)
    assert {t.method for t in candidate.discovery_traces} == {"bibliographic_coupling", "co_citation"}


def test_snowball_checkpoints_save_current_round_and_resume(tmp_path, monkeypatch):
    monkeypatch.setattr("litsearch.snowball.time.sleep", lambda _: None)
    seed = Paper(id="seed", title="plant traits", relevance_score=1)
    known = Paper(id="known", title="plant traits")
    children = [Paper(id=f"n{i}", title="plant traits") for i in range(6)]
    final = Paper(id="final", title="plant traits")
    def refs(paper, limit):
        return [known, *children] if paper.id == "seed" else [final]
    source = SimpleNamespace(get_references=refs, get_citations=lambda paper, limit: [children[0]] if paper.id == "seed" else [])
    workflow = LiteratureReviewWorkflow(source)
    state = ReviewState("plant", "plant traits", search_papers=[seed, known])
    state.prisma.add_papers(state.search_papers)
    saved = tmp_path / "partial.json"
    def interrupt(number, total, found):
        assert found == 6
        save_state(state, str(saved))
        raise RuntimeError("simulated interruption")
    with pytest.raises(RuntimeError, match="simulated interruption"):
        workflow.run_snowballing(state, num_seeds=1, max_rounds=2, on_round=interrupt)
    restored = load_state(str(saved))
    assert restored.snowball_result.completed is False
    assert restored.snowball_result.total_discovered == 6
    assert len(restored.prisma.records) == 8
    first = restored.snowball_result.rounds[0]
    assert (first.raw_count, first.unique_count, first.count, first.cumulative_unique) == (8, 6, 6, 6)
    workflow.run_snowballing(restored, num_seeds=1, max_rounds=2, resume=True)
    assert restored.snowball_result.total_discovered == 7
    assert len(restored.snowball_result.rounds) == 2
    assert restored.snowball_result.completed is True
    assert restored.phase.value == "snowballing"


def test_new_search_resets_old_screening_expansion_and_limits_corpus():
    source = SimpleNamespace(search_all_sources=lambda *a, **k: [Paper(id=str(i), title="plant traits") for i in range(10)])
    workflow = LiteratureReviewWorkflow(source)
    state = ReviewState("plant traits", "中文研究问题")
    state.prisma.add_papers([Paper(id="old", title="old")])
    state.similar_papers = [(Paper(id="extra", title="extra"), 1, "bc")]
    workflow.systematic_search(state, max_papers=3)
    assert len(state.search_papers) == len(state.prisma.records) == 3
    assert state.similar_papers == []
    assert state.search_manifest["ranking_query"] == "plant traits"
    assert state.research_direction == "中文研究问题"


def test_evidence_graph_has_correct_direction_and_preserves_discovery_path():
    seed = Paper(id="10.1/seed", doi="10.1/seed", title="seed", identifiers=PaperIdentifiers(openalex_id="W1"))
    backward = Paper(id="back", title="foundation", discovery_traces=[DiscoveryTrace(method="backward_citation", seed_id=seed.canonical_id)])
    forward = Paper(id="forward", title="later", reference_ids=["W1", "missing"])
    graph = EvidenceGraph([seed, backward, forward])
    assert graph.path("forward", "back") == ["forward", "10.1/seed", "back"]
    assert graph.path("back", "forward") == []
    assert graph.graph.number_of_nodes() == 3
    assert graph.graph.number_of_edges() == 2
    assert graph.summary()["central_papers"][0]["paper_id"] == "back"


def test_evidence_pack_is_complete_restorable_and_does_not_truncate_abstract():
    state = demo_state()
    state.search_papers[0].abstract = "A" * 2000
    with zipfile.ZipFile(BytesIO(build_evidence_pack(state))) as archive:
        assert {"AGENT_HANDOFF.md", "papers.jsonl", "references.bib", "references.ris", "session.json", "search_manifest.json", "discovery_trace.jsonl", "evidence_graph.json", "papers.csv"} <= set(archive.namelist())
        records = [json.loads(line) for line in archive.read("papers.jsonl").decode().splitlines()]
        assert len(records[0]["abstract"]) == 2000
        assert b"A" * 2000 in archive.read("references.ris")
        restored = paper_from_dict(records[0])
        assert restored.identifiers == state.search_papers[0].identifiers
        assert paper_to_dict(restored)["canonical_id"] == records[0]["canonical_id"]


def test_s2_cache_keeps_external_identifiers_for_doi_papers(tmp_path, monkeypatch):
    source = SemanticScholarSource(Cache(str(tmp_path / "cache.db")))
    source._request = Mock(return_value=response({"data": [{"paperId": "hash", "title": "title", "externalIds": {"DOI": "10.1/a", "ArXiv": "1706.03762"}}]}))
    monkeypatch.setattr("litsearch.sources.time.sleep", lambda _: None)
    source.search_papers("title", limit=1)
    cached = source.search_papers("title", limit=1)[0]
    assert cached.identifiers.semantic_scholar_id == "hash"
    assert cached.identifiers.arxiv_id == "1706.03762"
    assert source._request.call_count == 1


def test_s2_relationship_cache_skips_nulls_and_avoids_repeat_api_calls(tmp_path, monkeypatch):
    source = SemanticScholarSource(Cache(str(tmp_path / "cache.db")))
    source._request = Mock(side_effect=[response({"data": [{"citingPaper": None}, {"citingPaper": {"paperId": "hash", "title": "paper"}}]}), response({"data": []})])
    monkeypatch.setattr("litsearch.sources.time.sleep", lambda _: None)
    first = source.get_citations("10.1/a", limit=2)
    second = source.get_citations("10.1/a", limit=2)
    assert len(first) == len(second) == 1
    assert source._request.call_count == 2


def test_snowball_identity_enrichment_does_not_count_a_known_paper_as_new(monkeypatch):
    from litsearch.snowball import SnowballEngine
    monkeypatch.setattr("litsearch.snowball.time.sleep", lambda _: None)
    seed = Paper(id="W1", title="seed", identifiers=PaperIdentifiers(openalex_id="W1"))
    enrichment = Paper(id="10.1/a", doi="10.1/a", title="seed", identifiers=PaperIdentifiers(openalex_id="W1"))
    source = SimpleNamespace(get_references=lambda *a, **k: [enrichment], get_citations=lambda *a, **k: [])
    result = SnowballEngine(source, RelevanceFilter()).run([seed], "seed", max_rounds=1)
    assert result.total_discovered == 0
    assert result.initial_ids == ["10.1/a"]
    assert result.rounds[0].cumulative_unique == 0
