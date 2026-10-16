"""M4 domain map, timeline and trends.

No network.  Every threshold (node/edge minimums, isolate fraction, seed 42,
resolution, community size, trend windows) is read from
``contracts/defaults.json`` through :mod:`litsearch.v32.contracts`.
"""

from __future__ import annotations

import dataclasses
import json

from litsearch.v32 import contracts as C
from litsearch.v32 import mapping as M

MAPPING = C.mapping_defaults()


def _nodes(count):
    return [f"n{index:02d}" for index in range(count)]


def _citation_cycles(nodes, clusters=3):
    """``clusters`` citation cycles — a clean, non-degraded structure."""
    size = len(nodes) // clusters
    edges = []
    for cluster in range(clusters):
        group = nodes[cluster * size : (cluster + 1) * size]
        for index, paper_id in enumerate(group):
            edges.append(
                {
                    "from_paper_id": paper_id,
                    "to_paper_id": group[(index + 1) % len(group)],
                    "relation": "citation",
                    "weight": 1.0,
                }
            )
    return edges


def _texts(nodes):
    return {
        paper_id: f"shared words alpha beta topic{int(paper_id[1:]) // 8} specific"
        for paper_id in nodes
    }


def test_identical_input_produces_byte_identical_output():
    nodes = _nodes(24)
    edges = _citation_cycles(nodes)
    first = M.build_map(edges, nodes, texts=_texts(nodes))
    second = M.build_map(edges, nodes, texts=_texts(nodes))

    assert first == second
    assert first.graph_hash == second.graph_hash
    assert first.degraded is False
    assert first.algorithm == M.ALGORITHM_NAME
    assert first.algorithm_version == M.NETWORKX_VERSION
    assert json.dumps(dataclasses.asdict(first), allow_nan=False, sort_keys=True) == json.dumps(
        dataclasses.asdict(second), allow_nan=False, sort_keys=True
    )


def test_communities_are_found_and_labelled_from_text():
    nodes = _nodes(24)
    result = M.build_map(_citation_cycles(nodes), nodes, texts=_texts(nodes))
    labelled = [
        community for community in result.communities if community.community_id != "ungrouped"
    ]

    assert len(labelled) == 3
    for community in labelled:
        assert len(community.paper_ids) >= int(MAPPING["min_community_size"])
        assert community.label
        assert community.label_origin == "text_statistics"
        assert len(community.label.split(" / ")) <= M.MAX_LABEL_KEYWORDS

    covered = [paper_id for community in result.communities for paper_id in community.paper_ids]
    assert sorted(covered) == nodes  # every identity appears exactly once
    assert len(covered) == len(set(covered))
    assert result.isolated_paper_ids == []
    assert result.isolate_fraction == 0.0


def test_fewer_nodes_than_minimum_degrades_without_communities():
    nodes = _nodes(int(MAPPING["min_nodes"]) - 15)
    result = M.build_map([], nodes)

    assert result.degraded is True
    assert result.communities == []
    assert result.degraded_reason is not None
    assert "关系数据不足" in result.degraded_reason
    assert result.isolated_paper_ids == sorted(nodes)


def test_fewer_edges_than_minimum_degrades():
    nodes = _nodes(24)
    edges = _citation_cycles(nodes)[: int(MAPPING["min_edges"]) - 10]
    result = M.build_map(edges, nodes)

    assert result.degraded is True
    assert result.communities == []
    assert "有效关系" in result.degraded_reason


def test_isolate_fraction_above_the_maximum_degrades():
    nodes = _nodes(24)
    connected = nodes[:11]
    edges = [
        {
            "from_paper_id": connected[index],
            "to_paper_id": connected[(index + 1) % len(connected)],
            "relation": "citation",
        }
        for index in range(len(connected))
    ]
    edges += [
        {
            "from_paper_id": connected[index],
            "to_paper_id": connected[(index + 2) % len(connected)],
            "relation": "citation",
        }
        for index in range(len(connected) - 2)
    ]
    assert len(edges) == 20
    assert len(edges) >= int(MAPPING["min_edges"])

    result = M.build_map(edges, nodes)

    assert len(result.isolated_paper_ids) == 13
    assert result.isolate_fraction > float(MAPPING["max_isolate_fraction"])
    assert result.degraded is True
    assert result.communities == []
    assert "孤立率" in result.degraded_reason


def test_text_similarity_is_never_a_citation_edge():
    nodes = _nodes(24)
    similar = [
        {
            "from_paper_id": nodes[index],
            "to_paper_id": nodes[(index + 1) % len(nodes)],
            "relation": "text_similarity",
        }
        for index in range(len(nodes))
    ]
    result = M.build_map(similar, nodes)

    assert result.degraded is True
    assert result.communities == []
    assert "有效关系 0" in result.degraded_reason

    layers = M.edge_layers(similar, nodes)
    assert layers["citation"] == []
    assert len(layers["text_similarity"]) == len(nodes)


def test_edge_layers_keep_relations_separate_and_directed_per_contract():
    nodes = _nodes(4)
    edges = [
        {"from_paper_id": "n00", "to_paper_id": "n01", "relation": "citation", "weight": 2.0},
        {"from_paper_id": "n02", "to_paper_id": "n03", "relation": "co_citation", "weight": 1.0},
        {"from_paper_id": "n00", "to_paper_id": "n02", "relation": "bibliographic_coupling"},
        {"from_paper_id": "n01", "to_paper_id": "n03", "relation": "text_similarity"},
    ]
    layers = M.edge_layers(edges, nodes)

    assert len(layers["citation"]) == 1
    assert layers["citation"][0]["directed"] is True
    assert len(layers["co_citation"]) == 1
    assert layers["co_citation"][0]["directed"] is False
    assert len(layers["bibliographic_coupling"]) == 1
    assert len(layers["text_similarity"]) == 1
    assert all(layers[relation] == [] for relation in ("version_link",))
    # A caller cannot smuggle a direction that contradicts the contract.
    forged = [
        {"from_paper_id": "n00", "to_paper_id": "n01", "relation": "co_citation", "directed": True}
    ]
    assert M.edge_layers(forged, nodes)["co_citation"][0]["directed"] is False


def test_fold_view_never_removes_an_identity_or_an_edge():
    nodes = _nodes(24)
    edges = _citation_cycles(nodes)
    view = M.fold_view(nodes, [["n00", "n05"], ["n10", "n11", "n12"]])

    assert view["paper_ids"] == nodes
    assert len(view["paper_ids"]) == 24
    assert view["representative_of"] == {
        "n00": "n00",
        "n05": "n00",
        "n10": "n10",
        "n11": "n10",
        "n12": "n10",
    }
    assert len(view["representatives"]) == 24 - 3

    result = M.build_map(
        edges + [{"from_paper_id": "n00", "to_paper_id": "n05", "relation": "version_link"}], nodes
    )
    covered = {paper_id for community in result.communities for paper_id in community.paper_ids}
    assert covered == set(nodes)  # folding is a view, the map keeps every id


# --------------------------------------------------------------------------
# timeline and trends
# --------------------------------------------------------------------------


def test_timeline_counts_real_years_and_lists_unknown_separately():
    papers = [
        {"paper_id": "p1", "year": 2021},
        {"paper_id": "p2", "year": 2021},
        {"paper_id": "p3", "year": 2023},
        {"paper_id": "p4", "year": None},
        {"paper_id": "p5", "year": "not-a-year"},
        {"paper_id": "p6", "year": 42},
    ]
    points = M.timeline(papers)

    assert points[0] == {"year": 2021, "count": 2, "paper_ids": ["p1", "p2"]}
    assert points[1] == {"year": 2023, "count": 1, "paper_ids": ["p3"]}
    assert points[-1] == {"year": None, "count": 3, "paper_ids": ["p4", "p5", "p6"]}
    assert json.dumps(points, allow_nan=False)


def test_trends_say_insufficient_for_a_thin_corpus():
    report = M.trends([{"paper_id": f"p{i}", "year": 2020 + i} for i in range(3)])

    assert report["trend_status"] == "insufficient_data"
    assert report["trends"] == []
    assert report["note"] == "暂不足以判断趋势"
    assert json.dumps(report, allow_nan=False)


def test_trends_use_the_smoothed_growth_over_complete_year_pairs():
    papers = []
    for year, count in ((2020, 1), (2021, 1), (2022, 3), (2023, 3), (2024, 4), (2025, 5)):
        papers.extend({"paper_id": f"p{year}-{index}", "year": year} for index in range(count))
    report = M.trends(papers, config={"mapping": {"as_of_year": 2030}})

    assert report["trend_status"] == "available"
    assert len(report["trends"]) == 1
    trend = report["trends"][0]
    assert trend["prior_count"] == 6
    assert trend["recent_count"] == 9
    assert trend["window_years"] == [2022, 2023, 2024, 2025]
    assert abs(trend["growth"] - ((9 + 1) / (6 + 1) - 1)) < 1e-12


def test_trends_never_compare_the_unfinished_current_year():
    papers = []
    for year, count in ((2020, 1), (2021, 1), (2022, 3), (2023, 3), (2024, 4), (2025, 5)):
        papers.extend({"paper_id": f"p{year}-{index}", "year": year} for index in range(count))
    baseline = M.trends(papers, config={"mapping": {"as_of_year": 2030}})
    with_current = M.trends(
        papers + [{"paper_id": f"now-{index}", "year": 2030} for index in range(50)],
        config={"mapping": {"as_of_year": 2030}},
    )

    assert baseline["trends"] == with_current["trends"]
    assert baseline["trend_status"] == with_current["trend_status"] == "available"


def test_trends_require_prior_volume():
    papers = []
    for year, count in ((2020, 1), (2021, 1), (2022, 1), (2023, 1), (2024, 5), (2025, 5)):
        papers.extend({"paper_id": f"p{year}-{index}", "year": year} for index in range(count))
    report = M.trends(papers, config={"mapping": {"as_of_year": 2030}})

    assert report["trend_status"] == "insufficient_data"
    assert report["trends"] == []
