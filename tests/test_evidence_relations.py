"""Regression tests for v0.9.1 spec A: multi-relation evidence edges.

Every test here corresponds to a defect reproduced on the v0.9.0 baseline
before the fix; the docstrings record the observed wrong value so the test
cannot be "fixed" by relaxing an assertion.

Baseline evidence (repro before fix):

* one pair held exactly one edge, ``edge_type`` = whichever relation was built
  first, and ``weight`` was the arithmetic sum of every relation's number —
  a pair with one shared reference and one co-citation reported ``weight=2.81``
  for BC alone, with CC and cosine evidence attached to the BC row;
* identical texts produced ``weight=2.0`` for ``text_similarity``.
"""

import copy

import pytest

from litsearch.evidence import (
    DEFAULT_SEMANTIC_THRESHOLD,
    EvidenceGraph,
    canonical_relation_type,
    citation_facts,
)
from litsearch.models import DiscoveryTrace, Paper

SAME_TEXT = "deep learning for plant phenotyping with unmanned aerial vehicles"


def paper(pid, title="Paper", abstract="", refs=(), year=2020, source="openalex"):
    return Paper(
        id=pid, title=title, abstract=abstract, year=year,
        reference_ids=list(refs), source=source,
    )


def three_way_pair():
    """A pair related simultaneously by BC, CC and text similarity.

    ``a`` and ``b`` share a reference (BC), are cited together by ``c`` (CC),
    and carry near-identical text (similarity).
    """
    a = paper("10.1/a", "Wheat phenotyping with drones",
              "wheat phenotyping uav imaging yield", refs=["10.1/shared"])
    b = paper("10.1/b", "Wheat phenotyping using robots",
              "wheat phenotyping robot imaging yield", refs=["10.1/shared"])
    c = paper("10.1/c", "Wheat phenotyping survey",
              "wheat phenotyping review", refs=["10.1/a", "10.1/b"])
    return [a, b, c]


def relations_between(graph, left, right):
    return [
        row for row in graph.relations_for(left)
        if row["paper_id"] == right
    ]


# ---------------------------------------------------------------------------
# A2/A3 — relation types are separate; weights never mix
# ---------------------------------------------------------------------------


def test_one_pair_keeps_bc_cc_and_similarity_as_three_relations():
    graph = EvidenceGraph(
        three_way_pair(),
        edge_types=("citation", "bibliographic_coupling", "co_citation", "text_similarity"),
    )
    rows = relations_between(graph, "10.1/a", "10.1/b")
    types = sorted(row["edge_type"] for row in rows)
    assert types == ["bibliographic_coupling", "co_citation", "text_similarity"]

    by_type = {row["edge_type"]: row for row in rows}
    # Each score is its own quantity, not a shared running total.
    assert by_type["bibliographic_coupling"]["score"] == 1.0
    assert by_type["co_citation"]["score"] == 1.0
    assert 0.0 < by_type["text_similarity"]["score"] <= 1.0

    # Evidence stays with its own relation instead of being pooled.
    assert by_type["bibliographic_coupling"]["evidence"] == [
        {"shared_reference": "10.1/shared"}
    ]
    assert by_type["co_citation"]["evidence"] == [{"cited_together_by": "10.1/c"}]
    assert "cosine" in by_type["text_similarity"]["evidence"][0]

    counts = graph.edge_type_counts()
    assert counts["bibliographic_coupling"] == 1
    assert counts["co_citation"] == 1
    assert counts["text_similarity"] >= 1


def test_relation_score_never_contains_another_relation_number():
    """The v0.9.0 sum: BC 1.0 + CC 1.0 + cosine ≈ 0.81 must not appear as one number."""
    graph = EvidenceGraph(
        three_way_pair(),
        edge_types=("citation", "bibliographic_coupling", "co_citation", "text_similarity"),
    )
    for row in relations_between(graph, "10.1/a", "10.1/b"):
        if row["edge_type"] == "text_similarity":
            assert row["score"] <= 1.0
        else:
            # A count relation can only ever be a whole number of witnesses.
            assert row["score"] == float(int(row["score"]))
            assert row["score"] == row["witness_count"]
    scores = sorted(row["score"] for row in relations_between(graph, "10.1/a", "10.1/b"))
    assert scores != pytest.approx([2.81], abs=0.5)


def test_every_relation_row_carries_the_required_fields():
    graph = EvidenceGraph(three_way_pair(), edge_types=("citation", "bibliographic_coupling"))
    for row in graph.relations_for("10.1/a"):
        for field in ("source", "target", "edge_type", "score", "evidence", "witness_count"):
            assert field in row, field
        # Legacy v0.9.0 names remain available as documented aliases.
        assert row["weight"] == row["score"]
        assert row["shared"] == row["witness_count"]
        assert row["source"] < row["target"]  # sorted storage key


def test_shared_counts_distinct_witnesses_not_declarations():
    """A hub reference listed twice by one paper is one witness, not two."""
    a = paper("10.1/a", refs=["10.1/x", "10.1/x"])
    b = paper("10.1/b", refs=["10.1/x"])
    graph = EvidenceGraph([a, b], edge_types=("bibliographic_coupling",))
    rows = relations_between(graph, "10.1/a", "10.1/b")
    assert len(rows) == 1
    assert rows[0]["witness_count"] == 1
    assert rows[0]["score"] == 1.0


def test_url_and_bare_doi_forms_of_one_reference_are_one_witness():
    """``https://doi.org/X`` and ``X`` are the same reference, not two."""
    a = paper("10.1/a", refs=["https://doi.org/10.9/shared"])
    b = paper("10.1/b", refs=["10.9/shared"])
    graph = EvidenceGraph([a, b], edge_types=("bibliographic_coupling",))
    rows = relations_between(graph, "10.1/a", "10.1/b")
    assert len(rows) == 1
    assert rows[0]["witness_count"] == 1, "URL and bare DOI double-counted as two witnesses"


def test_known_cross_source_alias_is_one_witness():
    """A reference that resolves to an in-corpus paper counts once, whichever form is used."""
    target = paper("10.2/target", title="Shared work")
    a = paper("10.2/a", refs=["10.2/target"])
    b = paper("10.2/b", refs=["https://doi.org/10.2/target"])
    graph = EvidenceGraph([a, b, target], edge_types=("bibliographic_coupling",))
    rows = relations_between(graph, "10.2/a", "10.2/b")
    assert len(rows) == 1
    assert rows[0]["witness_count"] == 1


# ---------------------------------------------------------------------------
# A4 — similarity once per undirected pair, range [0, 1]
# ---------------------------------------------------------------------------


def test_identical_text_gives_exactly_one():
    graph = EvidenceGraph(
        [paper("10.3/x", "Identical", SAME_TEXT), paper("10.3/y", "Identical", SAME_TEXT)],
        edge_types=("text_similarity",),
    )
    rows = relations_between(graph, "10.3/x", "10.3/y")
    assert len(rows) == 1
    assert rows[0]["score"] == pytest.approx(1.0), "identical text must be 1.0 exactly"
    assert rows[0]["score"] <= 1.0


def test_similarity_is_stored_once_per_unordered_pair():
    graph = EvidenceGraph(
        [paper("10.4/x", "Identical", SAME_TEXT), paper("10.4/y", "Identical", SAME_TEXT)],
        edge_types=("text_similarity",),
    )
    pairs = list(graph.relation_pairs("text_similarity"))
    assert len(pairs) == 1, "the same unordered pair was stored twice (A-B and B-A)"
    assert pairs[0][0] < pairs[0][1]


def test_similarity_scores_stay_within_unit_range():
    papers = [
        paper("10.5/a", "Deep learning for plant phenotyping", SAME_TEXT),
        paper("10.5/b", "Plant phenotyping via deep learning", SAME_TEXT),
        paper("10.5/c", "Marine carbon export in the Southern Ocean",
              "Sediment traps quantify particulate organic carbon export dynamics."),
    ]
    graph = EvidenceGraph(papers, edge_types=("text_similarity",))
    for _source, _target, _edge_type, score, _witnesses in graph.relation_pairs("text_similarity"):
        assert 0.0 <= score <= 1.0


def test_threshold_still_filters_unrelated_text():
    papers = [
        paper("10.6/a", "Deep learning for plant phenotyping", SAME_TEXT),
        paper("10.6/b", "Marine carbon export in the Southern Ocean",
              "Sediment traps quantify particulate organic carbon export dynamics."),
    ]
    graph = EvidenceGraph(papers, edge_types=("text_similarity",),
                          semantic_threshold=DEFAULT_SEMANTIC_THRESHOLD)
    assert graph.relation_pairs("text_similarity") == []


# ---------------------------------------------------------------------------
# A6 — export is independent of input order
# ---------------------------------------------------------------------------


def test_export_is_byte_identical_when_input_order_changes():
    papers = three_way_pair()
    forward = EvidenceGraph(
        copy.deepcopy(papers),
        edge_types=("citation", "bibliographic_coupling", "co_citation", "text_similarity"),
    )
    reverse = EvidenceGraph(
        copy.deepcopy(list(reversed(papers))),
        edge_types=("citation", "bibliographic_coupling", "co_citation", "text_similarity"),
    )
    assert forward.to_dict() == reverse.to_dict()


def test_relation_and_citation_keys_agree_with_stored_direction():
    """Stored citation keys are ordered pairs; export must read the same key."""
    target = paper("10.7/shared", "Shared ref")
    a = paper("10.7/aaa", refs=["10.7/shared"])
    z = paper("10.7/zzz", refs=["10.7/shared"])
    graph = EvidenceGraph([z, target, a], edge_types=("citation",))

    for link in graph.to_dict()["links"]:
        assert graph.evidence[(link["source"], link["target"])] == link["evidence"]
    assert ("10.7/zzz", "10.7/shared") in graph.evidence
    assert ("10.7/shared", "10.7/zzz") not in graph.evidence


def test_to_dict_links_are_sorted_for_stable_exports():
    target = paper("10.8/shared", "Shared")
    graph = EvidenceGraph(
        [paper("10.8/zzz", refs=["10.8/shared"]), paper("10.8/aaa", refs=["10.8/shared"]), target],
        edge_types=("citation",),
    )
    links = graph.to_dict()["links"]
    assert links == sorted(links, key=lambda link: (link["source"], link["target"]))


# ---------------------------------------------------------------------------
# A7 — rename with a working legacy alias
# ---------------------------------------------------------------------------


def test_legacy_semantic_name_maps_to_text_similarity():
    assert canonical_relation_type("semantic") == "text_similarity"
    assert canonical_relation_type("text_similarity") == "text_similarity"

    graph = EvidenceGraph(
        [paper("10.9/x", "Identical", SAME_TEXT), paper("10.9/y", "Identical", SAME_TEXT)],
        edge_types=("semantic",),  # old name on input
    )
    pairs = graph.relation_pairs()
    assert [row[2] for row in pairs] == ["text_similarity"]

    counts = graph.edge_type_counts()
    assert counts["text_similarity"] == 1
    # Alias of the same edge, never a second one.
    assert counts["semantic"] == counts["text_similarity"]


def test_relations_for_accepts_the_legacy_edge_type_filter():
    graph = EvidenceGraph(
        [paper("10.10/x", "Identical", SAME_TEXT), paper("10.10/y", "Identical", SAME_TEXT)],
        edge_types=("semantic",),
    )
    assert graph.relations_for("10.10/x", edge_type="semantic")
    assert graph.relations_for("10.10/x", edge_type="co_citation") == []


# ---------------------------------------------------------------------------
# Citation graph isolation (spec A1) and the shared citation-fact definition (F)
# ---------------------------------------------------------------------------


def test_derived_relations_never_enter_the_citation_graph():
    papers = [
        paper("10.11/a", refs=["10.11/base"]),
        paper("10.11/b", refs=["10.11/base"]),
    ]
    graph = EvidenceGraph(papers, edge_types=("citation", "bibliographic_coupling"))
    assert graph.graph.number_of_edges() == 0, "BC must not create citation edges"
    assert graph.related.number_of_edges() == 1
    assert graph.summary()["edges"] == 0


def test_pagerank_ignores_derived_relations():
    """Citation-only PageRank must not move when derived relations are added."""
    citing = paper("10.12/citing", refs=["10.12/target"])
    target = paper("10.12/target")
    other = paper("10.12/other", refs=["10.12/target"])

    citation_only = EvidenceGraph([citing, target, other], edge_types=("citation",))
    with_derived = EvidenceGraph(
        [citing, target, other],
        edge_types=("citation", "bibliographic_coupling", "co_citation", "text_similarity"),
    )
    assert citation_only.summary()["central_papers"] == with_derived.summary()["central_papers"]


def test_citation_facts_is_the_single_definition_of_an_observed_citation():
    """reference_ids and forward/backward traces must agree with the graph."""
    target = paper("10.13/target", "Target")
    by_reference = paper("10.13/byref", "A", refs=["10.13/target"])
    by_forward = paper("10.13/byfwd", "B")
    by_forward.discovery_traces.append(DiscoveryTrace(
        method="forward_citation", provider="semantic_scholar",
        seed_id=target.canonical_id, round_no=1,
    ))
    seed = paper("10.13/seed", "S")
    by_backward = paper("10.13/byback", "C")
    by_backward.discovery_traces.append(DiscoveryTrace(
        method="backward_citation", provider="semantic_scholar",
        seed_id=seed.canonical_id, round_no=1,
    ))

    papers = [target, by_reference, by_forward, seed, by_backward]
    facts = citation_facts(papers)
    assert (by_reference.canonical_id, target.canonical_id) in facts
    assert (by_forward.canonical_id, target.canonical_id) in facts
    assert (seed.canonical_id, by_backward.canonical_id) in facts

    graph = EvidenceGraph(papers, edge_types=("citation",))
    assert facts == set(graph.graph.edges)


def test_citation_facts_drops_self_citations_and_unknown_endpoints():
    lone = paper("10.14/lone", "Lone", refs=["10.14/lone", "10.14/not-in-corpus"])
    assert citation_facts([lone]) == set()
