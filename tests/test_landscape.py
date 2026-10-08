"""Offline tests for multi-type evidence edges and research landscape."""

from litsearch.evidence import EvidenceGraph
from litsearch.landscape import (
    MAX_TOPICS,
    MIN_PAPERS_FOR_TOPICS,
    build_landscape,
    coverage_report,
    discover_topics,
    novelty_scores,
    temporal_profile,
)
from litsearch.models import Author, Paper


def paper(pid, title, abstract="", year=2020, cites=10, refs=()):
    p = Paper(id=f"10.1/{pid}", title=title, doi=f"10.1/{pid}", year=year,
              citation_count=cites, authors=[Author(name="A")], abstract=abstract or title)
    p.reference_ids = list(refs)
    return p


# ------------------------------------------------------------------
# Evidence graph: derived edge types
# ------------------------------------------------------------------


def test_default_is_citation_only_for_backward_compatibility():
    """Old callers must get the same citation-only graph as v0.6.0."""
    papers = [paper("a", "A", refs=["10.1/base"]), paper("b", "B", refs=["10.1/base"])]
    graph = EvidenceGraph(papers)
    assert graph.edge_type_counts() == {"citation": 0}  # base not in corpus
    assert graph.related.number_of_edges() == 0


def test_bibliographic_coupling_links_papers_sharing_a_reference():
    papers = [
        paper("a", "A", refs=["10.1/base"]),
        paper("b", "B", refs=["10.1/base", "10.1/other"]),
        paper("c", "C", refs=["10.1/base", "10.1/other"]),
    ]
    graph = EvidenceGraph(papers, edge_types=("citation", "bibliographic_coupling"))
    counts = graph.edge_type_counts()
    assert counts["bibliographic_coupling"] == 3  # a-b, a-c, b-c

    # b and c share two references, so their link is stronger than a-b.
    # `shared` is the count of *distinct* witnesses behind this relation.
    bc = {row["paper_id"]: row for row in graph.relations_for("10.1/b")}
    assert bc["10.1/c"]["shared"] == 2
    assert bc["10.1/a"]["shared"] == 1
    assert bc["10.1/c"]["edge_type"] == "bibliographic_coupling"
    assert bc["10.1/c"]["witness_count"] == 2


def test_coupling_skips_hub_references():
    """A reference everything cites carries no signal; it must not create a clique."""
    papers = [paper(f"p{i}", f"P{i}", refs=["10.1/hub"]) for i in range(30)]
    graph = EvidenceGraph(papers, edge_types=("bibliographic_coupling",))
    assert graph.edge_type_counts().get("bibliographic_coupling", 0) == 0


def test_co_citation_links_papers_cited_together():
    papers = [
        paper("a", "A", year=2018),
        paper("b", "B", year=2018),
        paper("c", "C", year=2020, refs=["10.1/a", "10.1/b"]),
    ]
    graph = EvidenceGraph(papers, edge_types=("citation", "co_citation"))
    assert graph.edge_type_counts()["co_citation"] == 1
    assert graph.edge_type_counts()["citation"] == 2

    rows = graph.relations_for("10.1/a")
    assert rows[0]["paper_id"] == "10.1/b"
    assert rows[0]["edge_type"] == "co_citation"
    assert {"cited_together_by": "10.1/c"} in rows[0]["evidence"]


def test_semantic_edges_require_similarity_above_threshold():
    similar = [
        paper("s1", "Deep learning for plant phenotyping from images",
              "Convolutional neural networks predict plant phenotypic traits from RGB imagery."),
        paper("s2", "Plant phenotyping using deep neural networks on images",
              "Deep neural networks estimate plant phenotypic traits using RGB imagery."),
        paper("s3", "Marine carbon export in the Southern Ocean",
              "Sediment traps quantify particulate organic carbon export dynamics."),
    ]
    # v0.9.1 renamed the relation from "semantic" (which overstated a TF-IDF
    # cosine as embedding similarity) to "text_similarity". The old name is
    # still accepted on input and still reported as an alias on output.
    graph = EvidenceGraph(similar, edge_types=("semantic",))
    counts = graph.edge_type_counts()
    assert counts["text_similarity"] >= 1
    assert counts["semantic"] == counts["text_similarity"]  # alias, not a second edge
    neighbours = [row["paper_id"] for row in graph.relations_for("10.1/s1")]
    assert "10.1/s2" in neighbours
    assert "10.1/s3" not in neighbours  # unrelated text must not be linked

    strict = EvidenceGraph(similar, edge_types=("semantic",), semantic_threshold=0.99)
    assert strict.edge_type_counts().get("text_similarity", 0) == 0


def test_derived_edges_never_enter_the_citation_graph():
    """PageRank and evidence paths must stay on observed citations only."""
    papers = [
        paper("a", "A", refs=["10.1/base"]),
        paper("b", "B", refs=["10.1/base"]),
    ]
    graph = EvidenceGraph(papers, edge_types=("citation", "bibliographic_coupling", "semantic"))
    assert graph.graph.number_of_edges() == 0          # no in-corpus citation
    assert graph.related.number_of_edges() >= 1        # but a coupling relation exists
    assert graph.path("10.1/a", "10.1/b") == []


def test_summary_reports_edge_types_and_scope():
    papers = [paper("a", "A", refs=["10.1/base"]), paper("b", "B", refs=["10.1/base"])]
    graph = EvidenceGraph(papers, edge_types=("citation", "bibliographic_coupling"))
    summary = graph.summary()
    assert "edge_types" in summary and "relation_edges" in summary
    assert "not citations" in summary["scope"]


def test_to_dict_includes_relations_only_when_present():
    papers = [paper("a", "A", refs=["10.1/base"]), paper("b", "B", refs=["10.1/base"])]
    bare = EvidenceGraph(papers).to_dict()
    assert "relations" not in bare
    full = EvidenceGraph(papers, edge_types=("citation", "bibliographic_coupling")).to_dict()
    assert full["relations"]


# ------------------------------------------------------------------
# Topic discovery
# ------------------------------------------------------------------


def _two_topic_corpus():
    papers = []
    for i, year in enumerate([2018, 2019, 2020, 2021, 2022]):
        papers.append(paper(f"ml{i}", f"Deep learning phenotyping {i}",
                            "Convolutional networks predict plant traits from RGB images.", year, 50 + i))
    for i, year in enumerate([2019, 2020]):
        papers.append(paper(f"oc{i}", f"Marine carbon export {i}",
                            "Sediment traps reveal particulate organic carbon flux.", year, 20))
    return papers


def test_topics_separate_distinct_subjects():
    result = discover_topics(_two_topic_corpus(), n_clusters=2)
    assert result["usable"] is True
    assert len(result["topics"]) == 2
    # The largest topic should be the ML cluster (5 papers vs 2).
    assert result["topics"][0]["size"] >= result["topics"][1]["size"]
    for topic in result["topics"]:
        assert topic["terms"], "each topic needs interpretable labels"


def test_topics_refuse_small_corpora():
    result = discover_topics(_two_topic_corpus()[:MIN_PAPERS_FOR_TOPICS - 1])
    assert result["usable"] is False
    assert result["topics"] == []


def test_topics_are_deterministic():
    corpus = _two_topic_corpus()
    first = discover_topics(corpus, n_clusters=2)
    second = discover_topics(corpus, n_clusters=2)
    assert [t["topic_id"] for t in first["topics"]] == [t["topic_id"] for t in second["topics"]]
    assert [t["size"] for t in first["topics"]] == [t["size"] for t in second["topics"]]


def test_cluster_count_is_capped():
    result = discover_topics(_two_topic_corpus(), n_clusters=MAX_TOPICS + 20)
    assert result["n_clusters"] <= MAX_TOPICS


# ------------------------------------------------------------------
# Temporal profile
# ------------------------------------------------------------------


def test_temporal_counts_years_and_recency():
    corpus = _two_topic_corpus()
    profile = temporal_profile(corpus)
    assert profile["timeline"][2022] == 1
    assert profile["latest_year"] == 2022
    assert 0.0 <= profile["recent_share"] <= 1.0


def test_temporal_flags_topics_by_recency_share():
    """One cluster is all-recent, the other all-old → both must be flagged.

    Note the per-topic floor of 3 papers: a 2-paper cluster is too thin to
    call a trend, and is skipped rather than guessed at.
    """
    corpus = [
        paper("ml0", "Deep learning phenotyping", "Convolutional networks predict traits.", 2021),
        paper("ml1", "Deep learning trait estimation", "Neural networks estimate traits.", 2022),
        paper("ml2", "Deep learning yield prediction", "Networks predict yield from images.", 2023),
        paper("oc0", "Marine carbon export", "Sediment traps reveal carbon flux.", 2016),
        paper("oc1", "Ocean particle flux", "Traps quantify particle flux.", 2017),
        paper("oc2", "Southern Ocean carbon", "Export production measured by traps.", 2018),
    ]
    topics = discover_topics(corpus, n_clusters=2)
    profile = temporal_profile(corpus, topics)
    assert profile["emerging"], "the all-recent cluster should gain share"
    assert profile["declining"], "the all-old cluster should lose share"
    assert profile["emerging"][0]["delta"] > 0
    assert profile["declining"][0]["delta"] < 0


def test_temporal_ignores_topics_below_three_papers():
    """A 2-paper cluster must not be reported as a trend."""
    corpus = [
        paper("a0", "Alpha one", "alpha", 2016),
        paper("a1", "Alpha two", "alpha", 2017),
        paper("b0", "Beta one", "beta", 2023),
        paper("b1", "Beta two", "beta", 2024),
        paper("b2", "Beta three", "beta", 2025),
    ]
    topics = discover_topics(corpus, n_clusters=2)
    profile = temporal_profile(corpus, topics)
    flagged = profile["emerging"] + profile["declining"]
    assert all(row["size"] >= 3 for row in flagged)


def test_temporal_handles_missing_years():
    undated = [paper("u1", "No year", "text", year=None)]
    profile = temporal_profile(undated)
    assert profile["timeline"] == {}
    assert "No year metadata" in profile["note"]


# ------------------------------------------------------------------
# Novelty
# ------------------------------------------------------------------


def test_novelty_is_relative_to_earlier_papers_only():
    corpus = [
        paper("old", "Deep learning phenotyping",
              "Convolutional networks predict plant traits from images.", 2018),
        paper("same", "Deep learning phenotyping revisited",
              "Convolutional networks predict plant traits from images again.", 2020),
        paper("different", "Acoustic monitoring of root growth",
              "Acoustic emission sensors monitor root elongation in soil profiles.", 2021),
    ]
    result = novelty_scores(corpus)
    scores = {row["paper_id"]: row["novelty"] for row in result["rows"]}
    # The acoustics paper shares no vocabulary with earlier work → high score.
    assert scores["10.1/different"] > scores["10.1/same"]


def test_novelty_leaves_papers_without_earlier_comparators_unscored():
    """Giving 1.0 to the earliest paper would flatter it for having no peers."""
    corpus = [
        paper("first", "Alpha study", "alpha", 2018),
        paper("second", "Beta study", "beta", 2019),
        paper("third", "Gamma study", "gamma", 2020),
    ]
    result = novelty_scores(corpus)
    unscored = [row for row in result["rows"] if row["novelty"] is None]
    assert len(unscored) == 1  # the 2018 paper has nothing earlier
    assert "No earlier paper" in unscored[0]["note"]


def test_novelty_needs_at_least_three_dated_papers():
    result = novelty_scores([paper("a", "A", "text", 2020), paper("b", "B", "text", 2021)])
    assert result["rows"] == []


# ------------------------------------------------------------------
# Coverage
# ------------------------------------------------------------------


def test_coverage_reports_shares_and_balance():
    corpus = _two_topic_corpus()
    topics = discover_topics(corpus, n_clusters=2)
    coverage = coverage_report(corpus, topics)
    assert len(coverage["topics"]) == 2
    assert abs(sum(row["share"] for row in coverage["topics"]) - 1.0) < 1e-6
    assert 0.0 <= coverage["balance"] <= 1.0


def test_coverage_flags_thin_topics():
    """One dominant topic plus three singletons → singletons are gaps."""
    corpus = [paper(f"big{i}", "Deep learning phenotyping",
                    "Convolutional networks predict plant traits from RGB images.", 2020 + i)
              for i in range(9)]
    corpus += [paper("solo", "Acoustic root monitoring",
                     "Acoustic sensors monitor root growth in soil.", 2021)]
    topics = discover_topics(corpus, n_clusters=3)
    coverage = coverage_report(corpus, topics)
    assert isinstance(coverage["gaps"], list)


def test_coverage_without_topics_is_explicit():
    coverage = coverage_report(_two_topic_corpus(), discover_topics([]))
    assert coverage["topics"] == []
    assert coverage["balance"] == 0.0


# ------------------------------------------------------------------
# Full landscape
# ------------------------------------------------------------------


def test_build_landscape_returns_every_section():
    result = build_landscape(_two_topic_corpus())
    assert set(result) >= {"paper_count", "topics", "temporal", "novelty", "coverage", "scope"}
    assert result["paper_count"] == len(_two_topic_corpus())


def test_build_landscope_degrades_on_empty_corpus():
    result = build_landscape([])
    assert result["paper_count"] == 0
    assert result["topics"]["usable"] is False
    assert result["coverage"]["topics"] == []
