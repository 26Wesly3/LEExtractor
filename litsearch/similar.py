"""Co-citation and bibliographic coupling based similar paper discovery.

Implements two similarity methods:
1. Bibliographic coupling: two papers share references → likely on same topic
2. Co-citation: two papers cited together by later papers → intellectually related
"""

import logging
from collections import Counter

from litsearch.models import Paper
from litsearch.sources import SourceManager

logger = logging.getLogger(__name__)


class SimilarPaperFinder:
    """Discover similar papers via co-citation and bibliographic coupling."""

    def __init__(self, sources: SourceManager, api_budget: int = 10):
        self.sources = sources
        # Number of extra API lookups allowed per discovery method.  Without a
        # budget, co-citation analysis can explode into hundreds of requests.
        self.api_budget = api_budget

    def _ensure_references(self, paper: Paper, limit: int = 100) -> list[str]:
        """Return a paper's reference IDs, fetching them if the source omitted them.

        Semantic Scholar search results do not carry references, which used to
        leave bibliographic coupling and co-citation permanently empty.
        """
        if paper.reference_ids:
            return paper.reference_ids
        if self.api_budget <= 0:
            return []
        self.api_budget -= 1
        try:
            refs = self.sources.get_references(paper.id, limit=limit)
        except Exception as e:
            logger.warning("reference fetch failed for %s: %s", paper.id, e)
            return []
        paper.reference_ids = [r.id or r.doi for r in refs if (r.id or r.doi)]
        return paper.reference_ids

    def by_bibliographic_coupling(
        self, seed_papers: list[Paper], top_k: int = 20
    ) -> list[tuple[Paper, float]]:
        """Find papers sharing many references with seed papers.

        Two papers that cite the same sources are likely on the same topic.
        """
        # Count how often each reference is cited by seed papers
        ref_counter: Counter = Counter()
        for sp in seed_papers[: self.api_budget]:
            for ref_id in self._ensure_references(sp):
                ref_counter[ref_id] += 1

        # Shared references (cited by >= 2 seeds) are "signature refs"
        signature_refs = {r for r, c in ref_counter.items() if c >= 2}

        if not signature_refs:
            return []

        # Find other papers that also cite these signature refs
        candidates: dict[str, tuple[Paper, int]] = {}
        for ref_id in list(signature_refs)[:10]:  # Limit API calls
            try:
                citers = self.sources.get_citations(ref_id, limit=20)
                for c in citers:
                    if any(c.id == sp.id or (c.doi and c.doi == sp.doi)
                           for sp in seed_papers):
                        continue  # Skip seed papers themselves
                    key = c.doi or c.id
                    if key in candidates:
                        candidates[key] = (c, candidates[key][1] + 1)
                    else:
                        candidates[key] = (c, 1)
            except Exception:
                pass

        # Score: papers citing more signature refs are more similar
        scored = [(p, count / len(signature_refs)) for p, count in candidates.values()]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def by_cocitation(
        self, seed_papers: list[Paper], top_k: int = 20
    ) -> list[tuple[Paper, float]]:
        """Find papers frequently co-cited with seed papers.

        If paper X is often cited alongside seed papers, X is intellectually related.
        """
        # For each seed, find its co-cited papers
        cocite_counter: Counter = Counter()
        seed_ids = {sp.id for sp in seed_papers}

        for sp in seed_papers[:5]:  # Limit to top 5 seeds for API efficiency
            try:
                # Find papers citing this seed
                citers = self.sources.get_citations(sp.id, limit=30)
                for citer in citers[:10]:
                    # For each citer, get its references (co-cited papers)
                    for ref_id in self._ensure_references(citer, limit=50):
                        if ref_id not in seed_ids:
                            cocite_counter[ref_id] += 1
            except Exception:
                pass

        # Fetch metadata for top co-cited papers
        top_cocited = cocite_counter.most_common(30)
        results: list[tuple[Paper, float]] = []

        for ref_id, count in top_cocited[:top_k]:
            try:
                paper = self.sources.get_paper(ref_id)
                if paper:
                    score = count / max(1, len(seed_papers))
                    results.append((paper, score))
            except Exception:
                pass

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def find_similar(
        self, seed_papers: list[Paper], top_k: int = 30
    ) -> list[tuple[Paper, float, str]]:
        """Combined similarity: bibliographic coupling + co-citation.

        Returns list of (paper, score, method).
        """
        results: list[tuple[Paper, float, str]] = []

        bc = self.by_bibliographic_coupling(seed_papers, top_k=top_k // 2)
        for paper, score in bc:
            results.append((paper, score, "bibliographic_coupling"))

        cc = self.by_cocitation(seed_papers, top_k=top_k // 2)
        for paper, score in cc:
            # Avoid duplicates
            if not any(r[0].id == paper.id for r in results):
                results.append((paper, score, "co_citation"))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]
