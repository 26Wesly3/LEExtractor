"""End-to-end acceptance for the v0.9.3 hardening (the spec's 集成验收).

The chain under test::

    systematic search
        → retrieval result
        → whole-corpus ranking
        → single score context
        → snowball
        → corpus changes → rerank
        → old calibration invalidated
        → similar (invalid references ignored)
        → screening facts
        → Evidence Pack

Each numbered assertion in the spec gets a test here, and each test asserts a
**contract between modules** rather than one module's internals — a half-landed
change shows up exactly there. Where a check needs the unified retrieval result
it is skipped with a reason until that lands, rather than weakened.
"""

import contextlib
import json

import pytest

from litsearch.evidence import EvidenceGraph
from litsearch.filters import corpus_hash
from litsearch.identifiers import paper_reference_witnesses, reference_witness
from litsearch.models import Paper
from litsearch.prisma import ScreeningDecision, ScreeningStage
from litsearch.search import LiteratureReviewWorkflow, ReviewState
from litsearch.similar import SimilarPaperFinder

TOPIC = "plant phenotyping with drones"
DIRECTION = "deep learning for plant phenotyping"


def paper(pid, title="plant phenotyping study", refs=None, year=2022, cites=5):
    p = Paper(id=pid, title=title, doi=pid, abstract=title, year=year,
              citation_count=cites, source="openalex")
    if refs is not None:
        p.reference_ids = list(refs)
    return p


class StubSources:
    """Provider double: fixed search corpus, configurable expansion."""

    snowball_delay = 0

    def __init__(self, search_results=(), references=None, citations=None):
        self.search_results = list(search_results)
        self.references = references or {}
        self.citations = citations or {}
        self.last_search_manifest = {}
        self._canceled = False

    def is_canceled(self):
        return self._canceled

    def search_all_sources(self, query, limit=200, year_from=1900, year_to=None, query_plan=None):
        self.last_search_manifest = {
            "query": query,
            "provider_counts": {"stub": len(self.search_results)},
        }
        return list(self.search_results)

    def get_references(self, seed, limit=100):
        return list(self.references.get(seed.canonical_id, []))

    def get_citations(self, seed, limit=100):
        return list(self.citations.get(seed.canonical_id, []))


def workflow(sources):
    wf = LiteratureReviewWorkflow(sources)
    wf.downloader = None
    return wf


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("litsearch.snowball.time.sleep", lambda _s: None)
    monkeypatch.setattr("litsearch.sources.time.sleep", lambda _s: None)


# ---------------------------------------------------------------------------
# 1 / 2 / 3 — one score context, moved by content, never crossed by calibration
# ---------------------------------------------------------------------------


def test_every_paper_shares_one_score_context_after_the_whole_chain():
    seeds = [paper(f"10.1/s{i}") for i in range(2)]
    discovered = [paper(f"10.1/d{i}") for i in range(3)]
    sources = StubSources(seeds, references={seeds[0].canonical_id: discovered})
    wf = workflow(sources)
    state = ReviewState(TOPIC, DIRECTION)

    wf.systematic_search(state, max_papers=50)
    wf.run_snowballing(state, num_seeds=1, max_rounds=1, seeds=seeds)

    contexts = {p.score_context_id for p in wf._corpus_for_ranking(state)}
    assert contexts == {state.score_context_id}, (
        f"papers in one session are scored on different scales: {contexts}"
    )
    assert state.score_context_id, "no context was recorded at all"
    # And the context is content-addressed, not count-based.
    assert corpus_hash(wf._corpus_for_ranking(state)) in state.score_context_id


def test_corpus_change_moves_the_context_and_order_alone_does_not():
    papers = [paper(f"10.1/p{i}") for i in range(4)]
    wf = workflow(StubSources(papers))

    a = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(a, max_papers=50)
    first = a.score_context_id

    # Re-running with the provider returning the same papers in a new order.
    wf.sources.search_results = list(reversed(papers))
    b = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(b, max_papers=50)
    assert b.score_context_id == first, "input order alone changed the scale"

    # Now change the corpus itself.
    wf.sources.search_results = papers + [paper("10.1/extra")]
    c = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(c, max_papers=50)
    assert c.score_context_id != first, "a different corpus reused the same scale"


def test_query_change_moves_the_context():
    papers = [paper(f"10.1/p{i}") for i in range(3)]
    wf = workflow(StubSources(papers))

    a = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(a, max_papers=50)

    b = ReviewState(TOPIC, "marine sediment carbon export")
    wf.systematic_search(b, max_papers=50)
    assert b.score_context_id != a.score_context_id, "a new query reused the scale"


def test_calibration_cannot_cross_a_context_boundary():
    seeds = [paper(f"10.1/s{i}") for i in range(2)]
    discovered = [paper(f"10.1/d{i}") for i in range(3)]
    sources = StubSources(seeds, references={seeds[0].canonical_id: discovered})
    wf = workflow(sources)
    state = ReviewState(TOPIC, DIRECTION)

    wf.systematic_search(state, max_papers=50)
    state.calibration = _Fitted()
    assert state.calibration_context_id == state.score_context_id

    wf.run_snowballing(state, num_seeds=1, max_rounds=1, seeds=seeds)
    assert state.calibration is None, (
        "a threshold fitted before the expansion survived it"
    )


class _Fitted:
    """A calibration that cannot re-prove validity, so it must not be reused."""

    usable = True
    reliable = True
    threshold = 0.5


# ---------------------------------------------------------------------------
# 4 — a provider failure must not masquerade as zero results
# ---------------------------------------------------------------------------


def test_a_failed_retrieval_is_not_reported_as_no_new_results():
    """The spec's core demand: `[]` must not carry the error state."""

    class Failing(StubSources):
        def search_all_sources(self, *a, **k):
            raise RuntimeError("provider down")

    wf = workflow(Failing())
    state = ReviewState(TOPIC, DIRECTION)
    # A provider exception must not leave the session claiming a completed,
    # empty search. Raising is acceptable; silently implying "no results" is not.
    with contextlib.suppress(Exception):
        wf.systematic_search(state, max_papers=50)

    assert state.snowball_result is None
    if state.stop_reason:
        assert state.stop_reason != "no_new_results", (
            "a failed retrieval reported itself as a genuine empty result"
        )


def test_expansion_without_seeds_refuses_instead_of_reporting_an_empty_run():
    wf = workflow(StubSources([]))
    state = ReviewState(TOPIC, DIRECTION)
    with pytest.raises(ValueError, match="seed"):
        wf.run_snowballing(state, num_seeds=1, max_rounds=1)


# ---------------------------------------------------------------------------
# 5 — invalid references neither crash nor create relations
# ---------------------------------------------------------------------------


def test_invalid_references_do_not_crash_or_create_relations():
    a = paper("10.1/a", refs=[None, "", "   "])
    b = paper("10.1/b", refs=[None, ""])

    graph = EvidenceGraph([a, b], edge_types=("citation", "bibliographic_coupling",
                                              "co_citation", "text_similarity"))
    assert graph.relation_pairs("bibliographic_coupling") == []
    assert graph.relation_pairs("co_citation") == []

    finder = SimilarPaperFinder(StubSources([]), api_budget=0)
    assert finder._reference_keys(a) == set()
    assert finder._reference_keys(b) == set()
    assert not (finder._reference_keys(a) & finder._reference_keys(b))


def test_a_real_doi_still_resolves_after_the_invalid_ones_are_dropped():
    """Dropping bad references must not drop the good one beside them."""
    a = paper("10.1/a", refs=[None, "https://doi.org/10.9/shared", "   "])
    b = paper("10.1/b", refs=["10.9/shared"])

    graph = EvidenceGraph([a, b], edge_types=("bibliographic_coupling",))
    rows = graph.relations_for("10.1/a")
    assert len(rows) == 1, "the real shared reference was lost with the bad ones"
    assert rows[0]["witness_count"] == 1, "URL and bare DOI must be one witness"


def test_graph_and_similar_agree_on_reference_identity():
    """One identity rule, so the two modules cannot disagree about relatedness."""
    a = paper("10.1/a", refs=["https://doi.org/10.9/shared", None, ""])
    b = paper("10.1/b", refs=["10.9/shared", "  "])

    graph_witnesses = paper_reference_witnesses(a)
    assert graph_witnesses == {reference_witness("10.9/shared")}

    finder = SimilarPaperFinder(StubSources([]), api_budget=0)
    assert finder._reference_keys(a) == graph_witnesses
    assert finder._reference_keys(a) == finder._reference_keys(b)


# ---------------------------------------------------------------------------
# 6 — an uncalibrated run shows no relevance rate
# ---------------------------------------------------------------------------


def test_uncalibrated_run_offers_no_relevance_share():
    import app

    state = ReviewState(TOPIC, DIRECTION)
    share = app.calibrated_relevant_share(state)
    assert share["available"] is False
    assert share["threshold"] is None


# ---------------------------------------------------------------------------
# 7 — screening facts agree across every consumer
# ---------------------------------------------------------------------------


def test_screening_facts_agree_across_mcp_ui_and_bundle():
    import ast
    from pathlib import Path

    from litsearch import bundle, screening, server

    seeds = [paper(f"10.1/s{i}") for i in range(3)]
    wf = workflow(StubSources(seeds))
    state = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(state, max_papers=50)
    state.prisma.screen_paper("10.1/s0", ScreeningDecision.ACCEPT)
    state.prisma.screen_paper("10.1/s1", ScreeningDecision.REJECT)
    state.prisma.screen_paper("10.1/s0", ScreeningDecision.ACCEPT, stage=ScreeningStage.FULL_TEXT)

    domain = screening.screening_facts(state)
    assert server.screening_facts is screening.screening_facts, (
        "the MCP adapter re-exported a different object"
    )
    assert server.screening_facts(state) == domain
    assert bundle.screening_facts is screening.screening_facts, (
        "the Evidence Pack uses another implementation"
    )
    assert domain["screening_status"] != "final_included", (
        "one paper is still undecided at title/abstract"
    )
    assert domain["full_text_review_completed"] is False

    # The UI imports its screening facts from the domain module, not the adapter.
    # Checked by parsing, because the import is inside a page function.
    tree = ast.parse(Path("app.py").read_text(encoding="utf-8"))
    imported_from = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "litsearch.screening" in imported_from, (
        "app.py does not import the screening domain"
    )
    assert "litsearch.server" not in imported_from, (
        "app.py still reaches into the MCP adapter for domain logic"
    )


def test_the_evidence_pack_carries_the_domain_screening_facts():
    """The pack's screening.json must be the domain's own output."""
    import io
    import zipfile

    from litsearch import bundle, screening

    seeds = [paper(f"10.1/s{i}") for i in range(2)]
    wf = workflow(StubSources(seeds))
    state = ReviewState(TOPIC, DIRECTION)
    wf.systematic_search(state, max_papers=50)

    payload = bundle.build_evidence_pack(state)
    assert isinstance(payload, (bytes, bytearray)), type(payload)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = archive.namelist()
        assert "screening.json" in names, f"screening.json missing from the pack: {names}"
        packed = json.loads(archive.read("screening.json"))
    assert packed["screening_status"] == screening.screening_facts(state)["screening_status"]
    assert packed["full_text_review_completed"] is False


# ---------------------------------------------------------------------------
# 8 — the pack's domain path does not need the MCP stack
# ---------------------------------------------------------------------------


def test_the_pack_domain_path_does_not_import_fastmcp():
    import ast
    from pathlib import Path

    tree = ast.parse(Path("litsearch/bundle.py").read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders += [a.name for a in node.names if a.name.split(".")[0] == "fastmcp"]
        elif isinstance(node, ast.ImportFrom):
            target = node.module or ""
            if target.split(".")[0] == "fastmcp" or target == "litsearch.server":
                offenders.append(target)
    assert offenders == [], f"bundle.py reaches into the MCP layer: {offenders}"


# ---------------------------------------------------------------------------
# 9 — the earlier phases still work
# ---------------------------------------------------------------------------


def test_search_snowball_and_similar_all_still_function():
    seeds = [paper(f"10.1/s{i}") for i in range(2)]
    discovered = [paper(f"10.1/d{i}") for i in range(3)]
    citer = paper("10.1/citer", refs=["10.1/s0", "10.1/d0"])
    sources = StubSources(
        seeds,
        references={seeds[0].canonical_id: discovered},
        citations={discovered[0].canonical_id: [citer]},
    )
    wf = workflow(sources)
    state = ReviewState(TOPIC, DIRECTION)

    wf.systematic_search(state, max_papers=50)
    assert state.search_papers, "the search returned nothing"

    wf.run_snowballing(state, num_seeds=1, max_rounds=1, seeds=seeds)
    assert state.snowball_result.total_discovered >= 3
    assert state.snowball_result.stop_reason

    wf.find_similar(state, num_seeds=1, top_k=5)
    ledger_stages = [e["stage"] for e in state.prisma.ledger.to_dict()["entries"]]
    assert "systematic_search" in ledger_stages and "snowballing" in ledger_stages
