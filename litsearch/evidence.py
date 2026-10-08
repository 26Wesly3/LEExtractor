"""Observed citation evidence only; no fabricated graph edges.

Two families of relation live here, deliberately kept apart.

**Citation graph** (directed, ``self.graph``) — ``A -> B`` means A cites B.
Only relations we actually observed: a paper's ``reference_ids``, or a
forward/backward discovery trace. This is what PageRank and evidence paths run
on. :func:`citation_facts` is the single definition of "an observed citation"
in this package — anything that needs the citation relation must call it rather
than re-deriving the rule, or the two copies drift apart and disagree about
whether a paper has been cited at all.

**Derived relations** (undirected, ``self.relations``) — computed from the same
observed data, never invented:

* ``bibliographic_coupling`` — two papers cite the same work.
* ``co_citation`` — two papers are cited by the same work in this corpus.
* ``text_similarity`` — TF-IDF cosine over title + abstract.

Each relation is stored under ``(min_id, max_id, relation_type)`` and carries
its own ``score``, ``witness_count`` and ``evidence``. **Scores from different
relation types are never added together.** They are different quantities
(counts of distinct shared references; counts of distinct co-citing papers; a
cosine in [0, 1]) and v0.9.0 summed them into one ``weight`` on a single
``nx.Graph`` edge, which meant a paper pair could only ever hold whichever
relation was added first, with the others silently folded into its number. That
is why a pair could not simultaneously be bibliographically coupled, co-cited
and textually similar.

Each score is derived from the **set of distinct witnesses**:

* coupling weight = number of distinct shared references,
* co-citation weight = number of distinct co-citing papers,
* text similarity = the cosine itself, and a pair is scored once.

Distinctness is by resolved identifier, so ``https://doi.org/10.1/x``, bare
``10.1/x`` and a known cross-source alias of the same work are one witness, not
three. Float tolerance is not used anywhere to paper over a double count: a
pair is simply visited once, and identical text yields exactly 1.0.

Hub references (a reference cited by a huge number of corpus papers) produce
meaningless cliques, so coupling/co-citation skip references above
``MAX_REF_FANOUT``.

Relation scope is a per-node top-k union: every node chooses its own ``k``
strongest neighbours for a relation, and the stored edge set is the union of
those choices. Scoring is unaffected by this (a score is still the full witness
count); only which edges are retained is capped, so a large corpus does not
become a dense clique.
"""

from __future__ import annotations

from collections import Counter, defaultdict

import networkx as nx
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from litsearch.filters import RelevanceFilter
from litsearch.identifiers import identifier_key, paper_aliases, reference_witness

# A reference shared by more corpus papers than this carries no discriminative
# signal (it is usually a methods paper or a review everything cites).
MAX_REF_FANOUT = 25

# Default TF-IDF cosine for two papers to count as textually related.
DEFAULT_SEMANTIC_THRESHOLD = 0.30
# Cap per paper so a large corpus does not become a dense similarity clique.
MAX_SEMANTIC_PER_PAPER = 8

#: The derived relations this graph can build.
RELATION_TYPES = ("bibliographic_coupling", "co_citation", "text_similarity")

#: v0.9.0 called the lexical relation "semantic", which overstated it: it is a
#: TF-IDF cosine, not an embedding similarity. The old name still works on
#: input and stays readable on output so saved sessions keep loading.
LEGACY_RELATION_ALIASES = {"semantic": "text_similarity"}

#: Which evidence key holds the witness identifier, per relation.
_WITNESS_KEY = {
    "bibliographic_coupling": "shared_reference",
    "co_citation": "cited_together_by",
}


def canonical_relation_type(edge_type: str | None) -> str:
    """Normalise a relation name, mapping the legacy ``semantic`` alias."""
    name = (edge_type or "").strip()
    return LEGACY_RELATION_ALIASES.get(name, name)


def corpus_papers(state):
    """Compatibility alias for the workflow's single scored-corpus definition."""
    from litsearch.search import current_scored_corpus

    return current_scored_corpus(state)


def _alias_index(papers) -> dict[str, str]:
    """alias -> canonical id, for every paper in the corpus."""
    return {a: p.canonical_id for p in papers for a in paper_aliases(p)}


def citation_facts(papers) -> set[tuple[str, str]]:
    """Every observed citation in the corpus as ``(citing_id, cited_id)``.

    The one place that decides what counts as an observed citation: a resolved
    entry in ``reference_ids``, or a ``forward_citation`` / ``backward_citation``
    discovery trace. Self-citations are dropped.

    Both the evidence graph and the research-question generators must use this,
    otherwise one of them reports "nothing cites this paper" while the other
    draws the edge.
    """
    papers = list(papers)
    aliases = _alias_index(papers)
    facts: set[tuple[str, str]] = set()

    for p in papers:
        citer = p.canonical_id
        for ref in p.reference_ids:
            target = aliases.get(identifier_key(ref, p.source)) or aliases.get(
                identifier_key(ref)
            )
            if target and target != citer:
                facts.add((citer, target))
        for trace in p.discovery_traces:
            if trace.method not in ("forward_citation", "backward_citation"):
                continue
            seed = aliases.get(identifier_key(trace.seed_id))
            if not seed:
                continue
            if trace.method == "forward_citation":
                # p was found because p cites the seed.
                pair = (p.canonical_id, seed)
            else:
                # p was found because the seed cites p.
                pair = (seed, p.canonical_id)
            if pair[0] != pair[1]:
                facts.add(pair)
    return facts


class EvidenceGraph:
    """A → B means A cites B. Only resolvable in-corpus edges enter PageRank.

    Derived relations are reachable through :meth:`relations_for`,
    :meth:`relation_pairs` and :meth:`to_dict`.
    """

    def __init__(
        self,
        papers,
        edge_types=("citation",),
        semantic_threshold: float = DEFAULT_SEMANTIC_THRESHOLD,
    ):
        self.papers = sorted(
            RelevanceFilter().deduplicate_by_doi(list(papers)),
            key=lambda p: p.canonical_id,
        )
        self.graph = nx.DiGraph()  # citation only: PageRank / evidence paths
        # Compatibility view: one attribute dict per unordered pair, rebuilt
        # from `relations`. Never the storage of record.
        self.related = nx.Graph()
        self.aliases = _alias_index(self.papers)
        self.evidence: dict[tuple[str, str], list[dict]] = {}
        #: ``relations[a][b][edge_type]`` with ``a < b``. A pair may appear
        #: under all three relation types.
        self.relations: dict[str, dict[str, dict[str, dict]]] = defaultdict(dict)
        #: ``_neighbours[a]`` -> every node related to ``a``, from *both* sides
        #: of the unordered pair. Looking pairs up by their sorted left key
        #: alone loses the neighbourhood of any node that only ever appears on
        #: the right, which silently hid half the graph.
        self._neighbours: dict[str, set[str]] = defaultdict(set)
        self.edge_types = tuple(
            canonical_relation_type(t) if t != "citation" else t for t in edge_types
        )
        self.semantic_threshold = semantic_threshold

        for p in self.papers:
            attrs = {"title": p.title, "year": p.year, "relevance": p.relevance_score}
            self.graph.add_node(p.canonical_id, **attrs)
            self.related.add_node(p.canonical_id, **attrs)

        if "citation" in self.edge_types:
            self._build_citations()
        if "bibliographic_coupling" in self.edge_types:
            self._build_coupling()
        if "co_citation" in self.edge_types:
            self._build_co_citation()
        if "text_similarity" in self.edge_types:
            self._build_text_similarity(semantic_threshold)

        self._rebuild_compat_view()

    # ------------------------------------------------------------------
    # Citation edges (observed)
    # ------------------------------------------------------------------

    def _build_citations(self):
        for source, target in sorted(citation_facts(self.papers)):
            details = []
            citing = next(
                (p for p in self.papers if p.canonical_id == source), None
            )
            for ref in (citing.reference_ids if citing else []):
                resolved = self.aliases.get(
                    identifier_key(ref, citing.source if citing else "")
                ) or self.aliases.get(identifier_key(ref))
                if resolved == target:
                    details.append(
                        {"method": "reference_metadata", "provider": citing.source}
                    )
            for trace in (citing.discovery_traces if citing else []):
                seed = self.aliases.get(identifier_key(trace.seed_id))
                if trace.method in ("forward_citation", "backward_citation") and seed:
                    pair = (
                        (source, seed) if trace.method == "forward_citation" else (seed, source)
                    )
                    if pair == (source, target):
                        details.append({
                            "method": trace.method,
                            "provider": trace.provider,
                            "round_no": trace.round_no,
                        })
            for detail in details or [
                {"method": "observed", "provider": citing.source if citing else ""}
            ]:
                self._link(source, target, detail)

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

    @staticmethod
    def _pair(a: str, b: str) -> tuple[str, str]:
        """Unordered storage key: always ``(min, max)``."""
        return (a, b) if a < b else (b, a)

    def _row(self, a: str, b: str, edge_type: str) -> dict:
        """Fetch-or-create the row for ``(min, max, relation_type)``.

        The relation type is part of the key, not a column on a pair: that is
        exactly the v0.9.0 bug, where one pair held a single edge and each new
        relation type either overwrote the previous one's type or was folded
        into its weight.
        """
        left, right = self._pair(a, b)
        pair = self.relations[left].setdefault(right, {})
        self._neighbours[left].add(right)
        self._neighbours[right].add(left)
        return pair.setdefault(
            edge_type,
            {"edge_type": edge_type, "score": 0.0, "witnesses": set(), "evidence": []},
        )

    def _add_witness(self, a, b, edge_type, witness, evidence):
        """Add one witness to pair ``(a, b)`` for ``edge_type``.

        The stored score is always derived from the witness *set*, so adding
        the same witness twice — from a URL form and a bare DOI form of one
        reference, say — cannot inflate it.
        """
        if a == b:
            return
        row = self._row(a, b, edge_type)
        if witness in row["witnesses"]:
            if evidence not in row["evidence"]:
                row["evidence"].append(evidence)
            return
        row["witnesses"].add(witness)
        row["evidence"].append(evidence)
        row["score"] = float(len(row["witnesses"]))

    def _set_score(self, a, b, edge_type, score, evidence):
        """Store a scored (non-witness) relation for one pair and type."""
        if a == b:
            return
        row = self._row(a, b, edge_type)
        row["score"] = float(score)
        if evidence not in row["evidence"]:
            row["evidence"].append(evidence)

    def _reference_witness(self, ref: str, paper_source: str) -> str:
        """Resolve a reference to one canonical witness id, or ``""`` for none.

        Delegates to :func:`litsearch.identifiers.reference_witness` so the graph
        and the coupling/co-citation finder cannot disagree about what counts as
        a shared reference. Returning ``""`` for an unusable reference is the
        point: v0.9.1 mapped every empty/blank/``None`` entry to the same key, so
        two papers that each carried one blank reference were reported as
        sharing a reference, and every paper with a blank entry joined one giant
        false clique. A missing reference is missing evidence, not shared
        evidence.
        """
        return reference_witness(ref, paper_source, self.aliases)

    def _build_coupling(self):
        """Two papers cite the same work → bibliographic coupling."""
        by_reference: dict[str, list[str]] = defaultdict(list)
        for p in self.papers:
            for ref in p.reference_ids:
                witness = self._reference_witness(ref, p.source)
                if not witness:
                    continue  # unusable reference: contributes no evidence
                by_reference[witness].append(p.canonical_id)

        for ref, citing in by_reference.items():
            distinct = sorted(set(citing))
            if not (2 <= len(distinct) <= MAX_REF_FANOUT):
                continue
            for i, a in enumerate(distinct):
                for b in distinct[i + 1:]:
                    self._add_witness(
                        a, b, "bibliographic_coupling", ref,
                        {"shared_reference": ref},
                    )

    def _build_co_citation(self):
        """Two papers cited by the same corpus paper → co-citation."""
        for p in self.papers:
            cited = set()
            for ref in p.reference_ids:
                witness = self._reference_witness(ref, p.source)
                if witness:
                    cited.add(witness)
            cited &= set(self.aliases.values())
            cited.discard(p.canonical_id)
            if not (2 <= len(cited) <= MAX_REF_FANOUT):
                continue
            ordered = sorted(cited)
            for i, a in enumerate(ordered):
                for b in ordered[i + 1:]:
                    self._add_witness(
                        a, b, "co_citation", p.canonical_id,
                        {"cited_together_by": p.canonical_id},
                    )

    def _build_text_similarity(self, threshold: float):
        """TF-IDF cosine over title + abstract; only pairs above threshold.

        Each *unordered* pair is scored with ``j > i`` only. The similarity
        matrix is symmetric, so visiting both orientations would add the same
        cosine twice and could report 2.0 for identical text.
        """
        usable = [p for p in self.papers if (p.title or p.abstract)]
        if len(usable) < 2:
            return
        docs = [f"{p.title or ''} {p.abstract or ''}".strip() for p in usable]
        try:
            matrix = TfidfVectorizer(
                max_features=8000, stop_words="english",
                ngram_range=(1, 2), sublinear_tf=True,
            ).fit_transform(docs)
            sims = cosine_similarity(matrix)
        except ValueError:
            return  # empty vocabulary: no basis for similarity

        ids = [p.canonical_id for p in usable]
        # Per-node top-k selection, then the union of the choices.
        selected: dict[str, list[tuple[str, float]]] = defaultdict(list)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                score = float(sims[i][j])
                if score < threshold:
                    continue
                # Clamp for float noise only; a genuine double count would be
                # ~2.0 and is prevented by the i<j iteration, not by clamping.
                score = min(1.0, max(0.0, score))
                selected[ids[i]].append((ids[j], score))
                selected[ids[j]].append((ids[i], score))

        keep: set[tuple[str, str]] = set()
        for node, neighbours in selected.items():
            for other, _ in sorted(
                neighbours, key=lambda item: (-item[1], item[0])
            )[:MAX_SEMANTIC_PER_PAPER]:
                keep.add(self._pair(node, other))

        for left, right in sorted(keep):
            score = min(1.0, max(0.0, float(sims[ids.index(left)][ids.index(right)])))
            self._set_score(
                left, right, "text_similarity", score, {"cosine": round(score, 4)}
            )

    # ------------------------------------------------------------------
    # Compatibility view
    # ------------------------------------------------------------------

    def _rebuild_compat_view(self):
        """Rebuild the legacy single-edge-per-pair ``related`` graph.

        Kept because pruning the corpus, the UI's neighbourhood lookup and the
        saved-session readers still ask for it. When a pair holds more than one
        relation it holds the strongest, with ``all_edge_types`` listing every
        relation on that pair and ``weight`` mapping onto that strongest
        relation's score — it is never a sum across relations.
        """
        self.related = nx.Graph()
        for p in self.papers:
            self.related.add_node(
                p.canonical_id, title=p.title, year=p.year, relevance=p.relevance_score
            )
        for left in sorted(self.relations):
            for right in sorted(self.relations[left]):
                rows = self.relations[left][right]
                if not rows:
                    continue
                strongest = max(
                    rows.values(),
                    key=lambda row: (row["score"], row["edge_type"]),
                )
                self.related.add_edge(
                    left, right,
                    edge_type=strongest["edge_type"],
                    all_edge_types=sorted(rows),
                    weight=round(float(strongest["score"]), 4),
                    score=round(float(strongest["score"]), 4),
                    witness_count=len(strongest["witnesses"]),
                    # v0.9.0 meaning: how many distinct witnesses support the
                    # relation. Kept as the witness count of this single
                    # relation (never a sum across relation types).
                    shared=len(strongest["witnesses"]),
                    # How many relation types this pair carries at once.
                    relation_count=len(rows),
                )

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def edge_type_counts(self) -> dict[str, int]:
        """How many edges of each type exist (citation + derived).

        ``text_similarity`` is the canonical name. The legacy ``semantic`` key
        is reported alongside it as an alias of the same count so that saved
        sessions, dashboards and third-party readers written against v0.9.0
        keep working; it is never a separate edge count.
        """
        counts = {"citation": self.graph.number_of_edges()}
        for left in self.relations:
            for by_type in self.relations[left].values():
                for row in by_type.values():
                    key = row["edge_type"]
                    counts[key] = counts.get(key, 0) + 1
        if counts.get("text_similarity"):
            counts["semantic"] = counts["text_similarity"]
        return counts

    def relation_pairs(
        self, edge_type: str | None = None
    ) -> list[tuple[str, str, str, float, int]]:
        """``(source, target, edge_type, score, witness_count)``, deterministically sorted.

        Sorting matters: without it the exported order follows dict insertion
        order, which follows the input paper order, so the same corpus exported
        twice produced two different files.
        """
        wanted = canonical_relation_type(edge_type) if edge_type else None
        rows = []
        for left in self.relations:
            for right, by_type in self.relations[left].items():
                for relation_type, row in by_type.items():
                    if wanted and relation_type != wanted:
                        continue
                    rows.append((
                        left, right, relation_type, round(float(row["score"]), 6),
                        len(row["witnesses"]),
                    ))
        rows.sort(key=lambda r: (r[2], r[0], r[1]))
        return rows

    def relations_for(self, paper_id: str, edge_type: str | None = None) -> list[dict]:
        """Derived neighbours of a paper, strongest first, with why.

        Every row carries its own ``edge_type``/``score``/``witness_count``: a
        pair related three ways produces three rows, and no row's score
        contains another relation's number.
        """
        node = self._resolve_node(paper_id)
        wanted = canonical_relation_type(edge_type) if edge_type else None
        out = []
        for neighbour in sorted(self._neighbours.get(node, ())):
            left, right = self._pair(node, neighbour)
            rows = self.relations.get(left, {}).get(right, {})
            for relation_type, row in rows.items():
                if wanted and relation_type != wanted:
                    continue
                witnesses = len(row["witnesses"])
                out.append({
                    "paper_id": neighbour,
                    "source": left,
                    "target": right,
                    "edge_type": relation_type,
                    "score": round(float(row["score"]), 4),
                    "witness_count": witnesses,
                    "evidence": list(row["evidence"]),
                    # Legacy aliases (v0.9.0 names): `shared` used to count
                    # *all* relations merged onto the pair, so it now reports
                    # this single relation's witness count, never a mixed total.
                    "weight": round(float(row["score"]), 4),
                    "shared": witnesses,
                })
        out.sort(key=lambda r: (-r["score"], r["edge_type"], r["paper_id"]))
        return out

    def _resolve_node(self, paper_id: str) -> str:
        """Map any identifier form onto the graph's node id.

        Falls back to a canonical comparison: providers hand out bare ids
        (``a``) and DOI-style ids (``10.1/a``) for the same work, and a lookup
        by the "wrong" form used to return an empty neighbourhood rather than
        the edges that exist.
        """
        if not paper_id:
            return ""
        resolved = self.aliases.get(identifier_key(paper_id), paper_id)
        if resolved in self._neighbours:
            return resolved
        wanted = identifier_key(paper_id)
        for node in self._neighbours:
            if identifier_key(node) == wanted or node == paper_id:
                return node
        return resolved

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
            "edge_types": self.edge_type_counts(),
            "relation_edges": len(self.relation_pairs()),
            "relation_types": sorted({
                row[2] for row in self.relation_pairs()
            }),
            "scope": (
                "observed corpus only; citation centrality is not research quality or causal influence; "
                "bibliographic_coupling / co_citation / text_similarity are derived co-occurrence, not citations; "
                "scores of different relation types are never summed"
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
                for u, v in sorted(self.graph.edges)
            ],
        }
        if self.relations:
            rows = []
            for source, target, edge_type, score, witnesses in self.relation_pairs():
                row = self.relations[source][target][edge_type]
                rows.append({
                    "source": source, "target": target,
                    "edge_type": edge_type,
                    "score": round(score, 4),
                    "witness_count": witnesses,
                    "evidence": list(row["evidence"]),
                    # Legacy v0.9.0 fields, now unambiguous per relation type.
                    "weight": round(score, 4),
                    "shared": witnesses,
                })
            payload["relations"] = rows
        return payload
