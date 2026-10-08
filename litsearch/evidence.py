"""Observed citation evidence only; no fabricated graph edges.

Two families of relation live here:

**Citation graph** (directed, ``self.graph``) — ``A -> B`` means A cites B.
Only relations we actually observed: a paper's ``reference_ids``, or a
forward/backward discovery trace. This is what PageRank and evidence paths run
on, and it is what v0.6.0 shipped.

**Relation graph** (undirected, ``self.related``) — *derived* co-occurrence
relations computed from the same observed data, never invented:

* ``bibliographic_coupling`` — two papers cite the same work.
* ``co_citation`` — two papers are cited by the same work in this corpus.
* ``semantic`` — TF-IDF cosine similarity over title + abstract.

These are kept separate on purpose. Coupling and co-citation are symmetric and
much denser than citations; folding them into the directed graph would swamp
PageRank and make an evidence path mean something it does not. Each carries a
``weight`` and its supporting evidence so the UI can show *why* two papers are
linked.

Hub references (a reference cited by a huge number of corpus papers) produce
meaningless cliques, so coupling/co-citation skip references above
``MAX_REF_FANOUT``.
"""

from collections import Counter, defaultdict

import networkx as nx
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from litsearch.filters import RelevanceFilter
from litsearch.identifiers import identifier_key, paper_aliases

# A reference shared by more corpus papers than this carries no discriminative
# signal (it is usually a methods paper or a review everything cites).
MAX_REF_FANOUT = 25

# Default TF-IDF cosine for two papers to count as semantically related.
DEFAULT_SEMANTIC_THRESHOLD = 0.30
# Cap per paper so a large corpus does not become a dense similarity clique.
MAX_SEMANTIC_PER_PAPER = 8


def corpus_papers(state):
    papers = list(state.search_papers or state.scoping_papers)
    if state.snowball_result:
        papers.extend(state.snowball_result.all_papers.values())
    papers.extend(p for p, _, _ in state.similar_papers)
    papers.extend(r.paper for r in state.prisma.records.values())
    return RelevanceFilter().deduplicate_by_doi(papers)


class EvidenceGraph:
    """A → B means A cites B. Only resolvable in-corpus edges enter PageRank."""

    def __init__(
        self,
        papers,
        edge_types=("citation",),
        semantic_threshold: float = DEFAULT_SEMANTIC_THRESHOLD,
    ):
        self.papers = RelevanceFilter().deduplicate_by_doi(list(papers))
        self.graph = nx.DiGraph()  # citation only: PageRank / evidence paths
        self.related = nx.Graph()  # derived BC / CC / semantic relations
        self.aliases = {a: p.canonical_id for p in self.papers for a in paper_aliases(p)}
        self.evidence: dict[tuple[str, str], list[dict]] = {}
        self.related_evidence: dict[tuple[str, str], list[dict]] = {}
        self.edge_types = tuple(edge_types)

        for p in self.papers:
            self.graph.add_node(
                p.canonical_id, title=p.title, year=p.year, relevance=p.relevance_score
            )
            self.related.add_node(
                p.canonical_id, title=p.title, year=p.year, relevance=p.relevance_score
            )

        if "citation" in self.edge_types:
            self._build_citations()
        if "bibliographic_coupling" in self.edge_types:
            self._build_coupling()
        if "co_citation" in self.edge_types:
            self._build_co_citation()
        if "semantic" in self.edge_types:
            self._build_semantic(semantic_threshold)

    # ------------------------------------------------------------------
    # Citation edges (observed)
    # ------------------------------------------------------------------

    def _build_citations(self):
        for p in self.papers:
            for ref in p.reference_ids:
                target = self.aliases.get(identifier_key(ref, p.source)) or self.aliases.get(
                    identifier_key(ref)
                )
                if target:
                    self._link(p.canonical_id, target, {"method": "reference_metadata", "provider": p.source})
            for trace in p.discovery_traces:
                seed = self.aliases.get(identifier_key(trace.seed_id))
                if seed and trace.method in ("forward_citation", "backward_citation"):
                    source, target = (
                        (p.canonical_id, seed)
                        if trace.method == "forward_citation"
                        else (seed, p.canonical_id)
                    )
                    self._link(
                        source, target,
                        {"method": trace.method, "provider": trace.provider, "round_no": trace.round_no},
                    )

    def _link(self, source, target, evidence):
        if source == target:
            return
        self.graph.add_edge(source, target, edge_type="citation")
        items = self.evidence.setdefault((source, target), [])
        if evidence not in items:
            items.append(evidence)

    # ------------------------------------------------------------------
    # Derived relation edges
    # ------------------------------------------------------------------

    def _add_related(self, a, b, edge_type, weight, evidence):
        """Record an undirected derived relation, merging weight if it exists."""
        if a == b:
            return
        key = (a, b) if a < b else (b, a)
        if self.related.has_edge(*key):
            self.related.edges[key]["weight"] += weight
            self.related.edges[key]["shared"] += 1
        else:
            self.related.add_edge(*key, edge_type=edge_type, weight=weight, shared=1)
        items = self.related_evidence.setdefault(key, [])
        if evidence not in items:
            items.append(evidence)

    def _build_coupling(self):
        """Two papers cite the same work → bibliographic coupling."""
        by_reference: dict[str, list[str]] = defaultdict(list)
        for p in self.papers:
            for ref in p.reference_ids:
                target = self.aliases.get(identifier_key(ref, p.source)) or self.aliases.get(
                    identifier_key(ref)
                )
                if target:
                    by_reference[target].append(p.canonical_id)
                else:
                    # Reference outside the corpus: still usable as shared evidence.
                    by_reference[identifier_key(ref)].append(p.canonical_id)

        for ref, citing in by_reference.items():
            if not (2 <= len(citing) <= MAX_REF_FANOUT):
                continue
            for i, a in enumerate(citing):
                for b in citing[i + 1:]:
                    self._add_related(
                        a, b, "bibliographic_coupling", 1.0,
                        {"shared_reference": ref},
                    )

    def _build_co_citation(self):
        """Two papers cited by the same corpus paper → co-citation."""
        for p in self.papers:
            cited = []
            for ref in p.reference_ids:
                target = self.aliases.get(identifier_key(ref, p.source)) or self.aliases.get(
                    identifier_key(ref)
                )
                if target:
                    cited.append(target)
            if not (2 <= len(cited) <= MAX_REF_FANOUT):
                continue
            for i, a in enumerate(cited):
                for b in cited[i + 1:]:
                    self._add_related(
                        a, b, "co_citation", 1.0, {"cited_together_by": p.canonical_id}
                    )

    def _build_semantic(self, threshold: float):
        """TF-IDF cosine over title + abstract; only pairs above threshold."""
        usable = [p for p in self.papers if (p.title or p.abstract)]
        if len(usable) < 2:
            return
        docs = [f"{p.title or ''} {p.abstract or ''}".strip() for p in usable]
        try:
            matrix = TfidfVectorizer(max_features=8000, stop_words="english",
                                     ngram_range=(1, 2), sublinear_tf=True).fit_transform(docs)
            sims = cosine_similarity(matrix)
        except ValueError:
            return  # empty vocabulary: no basis for similarity

        ids = [p.canonical_id for p in usable]
        for i, pid in enumerate(ids):
            ranked = sorted(
                ((j, sims[i][j]) for j in range(len(ids)) if j != i),
                key=lambda x: -x[1],
            )[:MAX_SEMANTIC_PER_PAPER]
            for j, score in ranked:
                if score < threshold:
                    continue
                self._add_related(
                    pid, ids[j], "semantic", float(score),
                    {"cosine": round(float(score), 4)},
                )

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def edge_type_counts(self) -> dict[str, int]:
        """How many edges of each type exist (citation + derived)."""
        counts = {"citation": self.graph.number_of_edges()}
        for _, _, data in self.related.edges(data=True):
            key = data.get("edge_type", "unknown")
            counts[key] = counts.get(key, 0) + 1
        return counts

    def relations_for(self, paper_id: str, edge_type: str | None = None) -> list[dict]:
        """Derived neighbours of a paper, strongest first, with why."""
        node = self.aliases.get(identifier_key(paper_id), paper_id)
        if node not in self.related:
            return []
        out = []
        for other, data in self.related[node].items():
            if edge_type and data.get("edge_type") != edge_type:
                continue
            key = (node, other) if node < other else (other, node)
            out.append({
                "paper_id": other,
                "edge_type": data.get("edge_type"),
                "weight": round(float(data.get("weight", 0.0)), 4),
                "shared": data.get("shared", 1),
                "evidence": self.related_evidence.get(key, []),
            })
        out.sort(key=lambda row: (-row["weight"], row["paper_id"]))
        return out

    def summary(self):
        if not self.papers:
            return {"nodes": 0, "edges": 0, "central_papers": [], "communities": [], "timeline": {}}
        central = nx.pagerank(self.graph) if self.graph.number_of_edges() else {}
        undirected = self.graph.to_undirected()
        communities = (
            nx.community.louvain_communities(undirected, seed=0)
            if undirected.number_of_edges()
            else []
        )
        return {
            "nodes": len(self.graph), "edges": self.graph.number_of_edges(),
            "central_papers": [
                {"paper_id": key, "score": score}
                for key, score in sorted(central.items(), key=lambda x: (-x[1], x[0]))[:10]
            ],
            "communities": [sorted(group) for group in sorted(communities, key=lambda g: (-len(g), min(g)))],
            "timeline": dict(sorted(Counter(p.year for p in self.papers if p.year).items())),
            # New in v0.7.0: derived relations are reported separately so they
            # are never mistaken for observed citations.
            "edge_types": self.edge_type_counts(),
            "relation_edges": self.related.number_of_edges(),
            "scope": (
                "observed corpus only; citation centrality is not research quality or causal influence; "
                "bibliographic_coupling / co_citation / semantic edges are derived co-occurrence, not citations"
            ),
        }

    def path(self, source_id: str, target_id: str) -> list[str]:
        source = self.aliases.get(identifier_key(source_id), source_id)
        target = self.aliases.get(identifier_key(target_id), target_id)
        try:
            return nx.shortest_path(self.graph, source, target)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []

    def to_dict(self):
        payload = {
            "directed": True, "edge_direction": "citing_paper -> cited_paper",
            "nodes": [{"id": key, **attrs} for key, attrs in self.graph.nodes(data=True)],
            "links": [
                {"source": u, "target": v, "edge_type": "citation", "evidence": self.evidence[(u, v)]}
                for u, v in self.graph.edges
            ],
        }
        if self.related.number_of_edges():
            payload["relations"] = [
                {
                    "source": u, "target": v,
                    "edge_type": data.get("edge_type"),
                    "weight": round(float(data.get("weight", 0.0)), 4),
                    "shared": data.get("shared", 1),
                    "evidence": self.related_evidence.get((u, v), []),
                }
                for u, v, data in self.related.edges(data=True)
            ]
        return payload
