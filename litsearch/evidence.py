"""Observed citation evidence only; no fabricated graph edges."""

from collections import Counter

import networkx as nx

from litsearch.filters import RelevanceFilter
from litsearch.identifiers import identifier_key, paper_aliases


def corpus_papers(state):
    papers = list(state.search_papers or state.scoping_papers)
    if state.snowball_result:
        papers.extend(state.snowball_result.all_papers.values())
    papers.extend(p for p, _, _ in state.similar_papers)
    papers.extend(r.paper for r in state.prisma.records.values())
    return RelevanceFilter().deduplicate_by_doi(papers)


class EvidenceGraph:
    """A → B means A cites B. Only resolvable in-corpus edges enter PageRank."""

    def __init__(self, papers):
        self.papers = RelevanceFilter().deduplicate_by_doi(list(papers))
        self.graph = nx.DiGraph()
        self.aliases = {a: p.canonical_id for p in self.papers for a in paper_aliases(p)}
        self.evidence: dict[tuple[str, str], list[dict]] = {}
        for p in self.papers:
            self.graph.add_node(p.canonical_id, title=p.title, year=p.year, relevance=p.relevance_score)
        for p in self.papers:
            for ref in p.reference_ids:
                target = self.aliases.get(identifier_key(ref, p.source)) or self.aliases.get(identifier_key(ref))
                if target:
                    self._link(p.canonical_id, target, {"method": "reference_metadata", "provider": p.source})
            for trace in p.discovery_traces:
                seed = self.aliases.get(identifier_key(trace.seed_id))
                if seed and trace.method in ("forward_citation", "backward_citation"):
                    source, target = (p.canonical_id, seed) if trace.method == "forward_citation" else (seed, p.canonical_id)
                    self._link(source, target, {"method": trace.method, "provider": trace.provider, "round_no": trace.round_no})

    def _link(self, source, target, evidence):
        if source == target:
            return
        self.graph.add_edge(source, target, edge_type="citation")
        items = self.evidence.setdefault((source, target), [])
        if evidence not in items:
            items.append(evidence)

    def summary(self):
        if not self.papers:
            return {"nodes": 0, "edges": 0, "central_papers": [], "communities": [], "timeline": {}}
        central = nx.pagerank(self.graph) if self.graph.number_of_edges() else {}
        undirected = self.graph.to_undirected()
        communities = nx.community.louvain_communities(undirected, seed=0) if undirected.number_of_edges() else []
        return {
            "nodes": len(self.graph), "edges": self.graph.number_of_edges(),
            "central_papers": [{"paper_id": key, "score": score} for key, score in sorted(central.items(), key=lambda x: (-x[1], x[0]))[:10]],
            "communities": [sorted(group) for group in sorted(communities, key=lambda g: (-len(g), min(g)))],
            "timeline": dict(sorted(Counter(p.year for p in self.papers if p.year).items())),
            "scope": "observed corpus only; citation centrality is not research quality or causal influence",
        }

    def path(self, source_id: str, target_id: str) -> list[str]:
        source = self.aliases.get(identifier_key(source_id), source_id)
        target = self.aliases.get(identifier_key(target_id), target_id)
        try:
            return nx.shortest_path(self.graph, source, target)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []

    def to_dict(self):
        return {
            "directed": True, "edge_direction": "citing_paper -> cited_paper",
            "nodes": [{"id": key, **attrs} for key, attrs in self.graph.nodes(data=True)],
            "links": [{"source": u, "target": v, "edge_type": "citation", "evidence": self.evidence[(u, v)]} for u, v in self.graph.edges],
        }
