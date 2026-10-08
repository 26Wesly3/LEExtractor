"""Budgeted bibliographic coupling and co-citation with explicit evidence."""

from collections import Counter, defaultdict

from litsearch.diagnostics import SourceErrorKind, get_diagnostics
from litsearch.filters import RelevanceFilter
from litsearch.identifiers import identifier_key, paper_aliases
from litsearch.models import DiscoveryTrace, Paper
from litsearch.sources import SourceManager, log_source_failure


class SimilarPaperFinder:
    def __init__(self, sources: SourceManager, api_budget: int = 10):
        self.sources = sources
        self.api_budget = max(0, api_budget)
        self.budget_usage: dict[str, int] = {}
        self._remaining = 0
        self._method = ""
        self._exhausted = False
        self._known: dict[str, Paper] = {}

    def _begin(self, method: str, seeds: list[Paper]):
        self._method = method
        self._remaining = self.api_budget
        self._exhausted = False
        self.budget_usage[method] = 0
        self._known = {a: p for p in seeds for a in paper_aliases(p)}

    def _call(self, method: str, paper, **kwargs):
        if self._remaining <= 0:
            if not self._exhausted:
                get_diagnostics().record("similar", SourceErrorKind.UNKNOWN,
                                         f"{self._method}: API budget exhausted; results may be incomplete")
                self._exhausted = True
            return None if method == "get_paper" else []
        self._remaining -= 1
        self.budget_usage[self._method] += 1
        try:
            result = getattr(self.sources, method)(paper, **kwargs)
            for p in ([result] if isinstance(result, Paper) else result or []):
                for alias in paper_aliases(p):
                    self._known[alias] = p
            return result
        except Exception as exc:
            log_source_failure(exc, "similar", self._method)
            return None if method == "get_paper" else []

    def _reference_keys(self, paper: Paper, limit: int = 100) -> set[str]:
        if not paper.reference_ids:
            refs = self._call("get_references", paper, limit=limit)
            paper.reference_ids = [r.canonical_id for r in refs]
        keys = set()
        for value in paper.reference_ids:
            key = identifier_key(value, paper.source if not value.startswith(("s2:", "arxiv:", "openalex:")) else "")
            keys.add(self._known[key].canonical_id if key in self._known else key)
        return keys

    def by_bibliographic_coupling(self, seed_papers: list[Paper], top_k: int = 20) -> list[tuple[Paper, float]]:
        self._begin("bibliographic_coupling", seed_papers)
        counter: Counter = Counter()
        for seed in seed_papers:
            counter.update(self._reference_keys(seed))
        signature = sorted(r for r, count in counter.items() if count >= 2)
        seeds = {a for p in seed_papers for a in paper_aliases(p)}
        candidates: dict[str, Paper] = {}
        evidence = defaultdict(set)
        for ref in signature[:10]:
            for candidate in self._call("get_citations", ref, limit=20):
                if paper_aliases(candidate) & seeds:
                    continue
                key = candidate.canonical_id
                if key in candidates:
                    RelevanceFilter.merge_metadata(candidates[key], candidate)
                else:
                    candidates[key] = candidate
                evidence[key].add(ref)
        results = []
        for key, paper in candidates.items():
            score = len(evidence[key]) / max(1, len(signature))
            paper.discovery_traces.append(DiscoveryTrace(method=self._method, provider=paper.source,
                                                        score=score, evidence_ids=sorted(evidence[key])))
            results.append((paper, score))
        return sorted(results, key=lambda item: (-item[1], item[0].canonical_id))[:top_k]

    def by_cocitation(self, seed_papers: list[Paper], top_k: int = 20) -> list[tuple[Paper, float]]:
        self._begin("co_citation", seed_papers)
        seed_aliases = {a for p in seed_papers for a in paper_aliases(p)}
        evidence = defaultdict(set)
        citers = {}
        for seed in seed_papers[:5]:
            for citer in self._call("get_citations", seed, limit=30)[:10]:
                citers[citer.canonical_id] = citer
        for citer in citers.values():
            for ref in self._reference_keys(citer, limit=50):
                if ref not in seed_aliases:
                    evidence[ref].add(citer.canonical_id)
        results = []
        for ref in sorted(evidence, key=lambda r: (-len(evidence[r]), r)):
            if len(results) >= top_k:
                break
            paper = self._known.get(ref) or self._call("get_paper", ref)
            if paper and not (paper_aliases(paper) & seed_aliases):
                score = len(evidence[ref]) / max(1, len(citers))
                paper.discovery_traces.append(DiscoveryTrace(method=self._method, provider=paper.source,
                                                            score=score, evidence_ids=sorted(evidence[ref])))
                results.append((paper, score))
        return results

    def find_similar(self, seed_papers: list[Paper], top_k: int = 30) -> list[tuple[Paper, float, str]]:
        if top_k <= 0 or not seed_papers:
            return []
        bc = self.by_bibliographic_coupling(seed_papers, top_k=top_k)
        cc = self.by_cocitation(seed_papers, top_k=top_k)
        merged = {}
        for method, records in (("bibliographic_coupling", bc), ("co_citation", cc)):
            for paper, score in records:
                key = paper.canonical_id
                if key in merged:
                    existing, old_score, old_method = merged[key]
                    RelevanceFilter.merge_metadata(existing, paper)
                    merged[key] = (existing, max(old_score, score), old_method)
                else:
                    merged[key] = (paper, score, method)
        return sorted(merged.values(), key=lambda item: (-item[1], item[0].canonical_id))[:top_k]
