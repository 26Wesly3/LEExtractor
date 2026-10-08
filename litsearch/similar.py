"""Budgeted bibliographic coupling and co-citation with explicit evidence."""

from collections import Counter, defaultdict

from litsearch.diagnostics import SourceErrorKind, get_diagnostics
from litsearch.filters import RelevanceFilter
from litsearch.identifiers import paper_aliases, paper_reference_witnesses
from litsearch.models import DiscoveryTrace, Paper
from litsearch.retrieval import RetrievalResult, RetrievalStatus, stop_reason_for
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
        self.retrieval_results: list[RetrievalResult] = []
        self.incomplete: bool = False
        self.stop_reason: str = ""

    def _begin(self, method: str, seeds: list[Paper]):
        self._method = method
        self._remaining = self.api_budget
        self._exhausted = False
        self.budget_usage[method] = 0
        self._known = {a: p for p in seeds for a in paper_aliases(p)}

    def _mark_incomplete(self, result: RetrievalResult) -> None:
        self.retrieval_results.append(result)
        if result.ok:
            return
        self.incomplete = True
        self.stop_reason = stop_reason_for(result.status).value
        get_diagnostics().record(
            "similar", SourceErrorKind.UNKNOWN,
            f"{self._method}: {result.status.value}" +
            (f" ({result.error})" if result.error else ""),
        )

    def _call(self, method: str, paper, **kwargs):
        if self._remaining <= 0:
            if not self._exhausted:
                get_diagnostics().record("similar", SourceErrorKind.UNKNOWN,
                                         f"{self._method}: API budget exhausted; results may be incomplete")
                self._exhausted = True
                self._mark_incomplete(RetrievalResult(
                    papers=[], provider="similar",
                    status=RetrievalStatus.BUDGET_EXHAUSTED, complete=False,
                    error="similar logical API budget exhausted",
                ))
            return None if method == "get_paper" else []
        self._remaining -= 1
        self.budget_usage[self._method] += 1
        try:
            # Citation/reference calls use the result API when available so a
            # provider failure never collapses into the same [] as a genuine
            # empty answer. get_paper remains a scalar metadata lookup.
            result_method = getattr(self.sources, f"{method}_result", None)
            if method in {"get_references", "get_citations"} and callable(result_method):
                retrieval = result_method(paper, **kwargs)
                self._mark_incomplete(retrieval)
                result = retrieval.papers
            else:
                result = getattr(self.sources, method)(paper, **kwargs)
            for p in ([result] if isinstance(result, Paper) else result or []):
                for alias in paper_aliases(p):
                    self._known[alias] = p
            return result
        except Exception as exc:
            log_source_failure(exc, "similar", self._method)
            if method in {"get_references", "get_citations"}:
                self._mark_incomplete(RetrievalResult(
                    papers=[], provider="similar",
                    status=RetrievalStatus.PROVIDER_ERROR, complete=False,
                    error=f"{type(exc).__name__}: {exc}"[:200],
                ))
            return None if method == "get_paper" else []

    def _reference_keys(self, paper: Paper, limit: int = 100) -> set[str]:
        """Usable reference witnesses of one paper, canonicalised.

        Delegates to :func:`litsearch.identifiers.paper_reference_witnesses`, the
        same rule the evidence graph uses, so coupling/co-citation and the graph
        agree on what a shared reference is.

        Two defects this closes, both reproduced on v0.9.2:

        * ``reference_ids = [None]`` raised ``AttributeError`` on
          ``value.startswith(...)`` — a provider null crashed the finder;
        * ``""`` / ``"   "`` normalised to the key ``""`` and was *kept*, so two
          papers that each had a blank reference reported a shared one. Empty
          witnesses are dropped, not stored.

        A paper whose references turn out to be entirely unusable is not asked
        for again: ``paper.reference_ids`` is marked as fetched so
        :meth:`_known` behaviour stays stable and the budget is not spent twice.
        """
        if paper.reference_ids is None:
            paper.reference_ids = []
        if not paper.reference_ids:
            fetched = self._call("get_references", paper, limit=limit)
            paper.reference_ids = [getattr(r, "canonical_id", None) for r in (fetched or [])]
            if not paper.reference_ids:
                # Remember that we asked, so an empty answer is not re-fetched
                # on every round (it used to cost one budget unit per call).
                paper.reference_ids = []
        keys = paper_reference_witnesses(paper)
        return {self._known[key].canonical_id if key in self._known else key for key in keys}

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
        self.retrieval_results = []
        self.incomplete = False
        self.stop_reason = ""
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
