"""Relevance scoring, noise filtering, and deduplication for search results.

Scoring is a 3-way hybrid rather than a single TF-IDF cosine, because a
research direction is typically a short sentence ("deep learning for plant
phenotyping") while candidate papers are long abstracts:

1. **Word-level TF-IDF cosine** — the classic baseline. Reliable when the
   direction shares vocabulary with the paper.
2. **Character 3-5 gram cosine** — catches morphological variants and
   compound terms ("phenotype" / "phenotyping" / "phenotypic"), which word
   n-grams miss entirely. This is what rescues short queries.
3. **Query-term coverage** — the fraction of *distinct* query terms that
   appear in title or abstract. Cosine similarity is length-sensitive; a
   paper that hits every query term but is short can still score low. Coverage
   is length-insensitive and gives a floor of interpretable signal.

The three are min-max normalised per result set and blended by weights. This
makes scores **relative within a run** (comparable across runs only loosely),
so `calibrate_threshold` exists: give it a handful of papers you know are
relevant/irrelevant and it derives a defensible cut-off instead of leaving you
with a magic 0.15.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from litsearch.identifiers import normalize_doi, paper_aliases
from litsearch.models import Paper

# Words too generic to count as evidence of topical relevance.
_STOPWORDS = {
    "the", "and", "for", "from", "with", "that", "this", "are", "was",
    "were", "have", "has", "been", "its", "not", "but", "can", "may",
    "also", "using", "based", "used", "use", "via", "into", "such",
    "each", "more", "how", "however", "within", "than", "which", "these",
    "their", "both", "between", "other", "we", "our", "study", "studies",
    "paper", "results", "show", "shown", "propose", "proposed", "approach",
    "method", "methods", "model", "models", "data", "analysis", "review",
}


@dataclass
class ScoreBreakdown:
    """Per-paper component scores, kept for explainability in the GUI."""

    paper_id: str
    word_score: float
    char_score: float
    coverage: float
    final: float


class RelevanceFilter:
    """Scores papers against a research direction using a hybrid of three signals."""

    def __init__(
        self,
        word_weight: float = 0.45,
        char_weight: float = 0.30,
        coverage_weight: float = 0.25,
    ):
        total = word_weight + char_weight + coverage_weight
        if total <= 0:
            raise ValueError("At least one relevance weight must be positive")
        self.word_weight = word_weight / total
        self.char_weight = char_weight / total
        self.coverage_weight = coverage_weight / total

        self._word_vectorizer = TfidfVectorizer(
            max_features=8000,
            stop_words="english",
            ngram_range=(1, 2),
            sublinear_tf=True,
        )
        # char_wb operates inside word boundaries, so it does not glue
        # unrelated words together the way char analyzer does.
        self._char_vectorizer = TfidfVectorizer(
            analyzer="char_wb",
            ngram_range=(3, 5),
            max_features=12000,
            sublinear_tf=True,
        )
        self._last_breakdown: list[ScoreBreakdown] = []

    # ------------------------------------------------------------------
    # Text preparation
    # ------------------------------------------------------------------

    @staticmethod
    def _paper_to_text(paper: Paper) -> str:
        """Title is repeated to weight it above the abstract.

        A repeated title is the cheap, well-understood way to approximate
        field weighting without training anything.
        """
        parts = [paper.title, paper.title, paper.title]
        if paper.abstract:
            parts.append(paper.abstract)
        if paper.topics:
            parts.append(" ".join(paper.topics))
        return " ".join(p for p in parts if p)

    @staticmethod
    def _stem(word: str) -> str:
        """Naive suffix stripping. Applied identically to query and text so
        that "phenotyping" and "phenotype" collapse to the same stem."""
        w = re.sub(r"(ies)$", "y", word.lower())
        w = re.sub(r"(sses|shes|ches|xes)$", "", w)
        w = re.sub(r"(s|ing|ed)$", "", w)
        return w if len(w) > 2 else word.lower()

    @classmethod
    def _query_terms(cls, text: str) -> list[str]:
        """Distinct content words of the query, stemmed.

        De-duplicated on purpose: coverage measures "how much of what I asked
        for did this paper hit", not how often a word repeats.
        """
        seen: list[str] = []
        for w in re.findall(r"[a-zA-Z][a-zA-Z\-]{2,}", text.lower()):
            if w in _STOPWORDS:
                continue
            stem = cls._stem(w)
            if stem and stem not in seen:
                seen.append(stem)
        return seen

    @classmethod
    def _coverage(cls, query_terms: list[str], paper: Paper) -> float:
        """Fraction of query concepts present in title or abstract.

        Matching is stem-prefix based rather than equality: one pass of suffix
        stripping cannot align every inflection ("phenotyp" vs "phenotype" vs
        "phenotypic"), so a query stem matches any text stem sharing its
        first 4+ characters. Without this, morphologically related terms score
        zero coverage despite being exactly on topic.
        """
        if not query_terms:
            return 0.0
        text = f"{paper.title} {paper.abstract or ''}".lower()
        text_stems = {
            cls._stem(w) for w in re.findall(r"[a-zA-Z][a-zA-Z\-]{2,}", text)
        }
        prefix_index = sorted(text_stems)

        hits = 0
        for term in query_terms:
            k = max(4, len(term) - 2)
            if any(s.startswith(term[:k]) for s in prefix_index):
                hits += 1
        return hits / len(query_terms)

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _minmax(values: list[float]) -> list[float]:
        """Normalise to [0, 1]; a degenerate set maps to all-zeros."""
        if not values:
            return []
        lo, hi = min(values), max(values)
        if hi - lo < 1e-12:
            return [min(1.0, max(0.0, v)) for v in values]
        return [(v - lo) / (hi - lo) for v in values]

    def compute_relevance(
        self, papers: list[Paper], direction_text: str
    ) -> list[Paper]:
        """Score each paper on a 0-1 scale relative to this result set."""
        self._last_breakdown = []
        if not papers or not direction_text.strip():
            return papers

        query = direction_text.strip()
        docs = [self._paper_to_text(p) for p in papers]
        terms = self._query_terms(query)

        # Each vectorizer can fail independently (e.g. empty vocabulary after
        # stop-word removal), so degrade one signal at a time rather than
        # losing the whole score.
        try:
            m = self._word_vectorizer.fit_transform([query] + docs)
            word_scores = cosine_similarity(m[0:1], m[1:]).flatten().tolist()
        except ValueError:
            word_scores = [0.0] * len(papers)

        try:
            m2 = self._char_vectorizer.fit_transform([query] + docs)
            char_scores = cosine_similarity(m2[0:1], m2[1:]).flatten().tolist()
        except ValueError:
            char_scores = [0.0] * len(papers)

        cov_scores = [self._coverage(terms, p) for p in papers]

        # A dead signal must not drag the blend down: re-weight over the
        # signals that actually produced variance.
        wn = self.word_weight * (1.0 if max(word_scores, default=0) > 0 else 0.0)
        cn = self.char_weight * (1.0 if max(char_scores, default=0) > 0 else 0.0)
        vn = self.coverage_weight * (1.0 if max(cov_scores, default=0) > 0 else 0.0)
        norm = wn + cn + vn
        if norm < 1e-12:
            for p in papers:
                p.relevance_score = 0.0
            return papers

        word_n = self._minmax(word_scores)
        char_n = self._minmax(char_scores)

        breakdown: list[ScoreBreakdown] = []
        for i, paper in enumerate(papers):
            final = (wn * word_n[i] + cn * char_n[i] + vn * cov_scores[i]) / norm
            paper.relevance_score = round(float(final), 6)
            paper.score_breakdown = {"word": word_n[i], "character": char_n[i], "coverage": cov_scores[i], "lexical": paper.relevance_score}
            breakdown.append(
                ScoreBreakdown(
                    paper_id=paper.id,
                    word_score=round(word_n[i], 4),
                    char_score=round(char_n[i], 4),
                    coverage=round(cov_scores[i], 4),
                    final=paper.relevance_score,
                )
            )

        # Rank by score so downstream `[:n]` slices get the best papers.
        papers.sort(key=lambda p: p.relevance_score, reverse=True)
        by_id = {b.paper_id: b for b in breakdown}
        self._last_breakdown = [by_id[p.id] for p in papers if p.id in by_id]
        return papers

    @property
    def last_breakdown(self) -> list[ScoreBreakdown]:
        """Component scores from the most recent `compute_relevance` call."""
        return list(self._last_breakdown)

    # ------------------------------------------------------------------
    # Threshold calibration
    # ------------------------------------------------------------------

    @staticmethod
    def calibrate_threshold(
        known_relevant: list[Paper],
        known_irrelevant: list[Paper],
        default: float = 0.15,
    ) -> tuple[float, str]:
        """Derive a defensible screening threshold from labelled papers.

        Takes the midpoint between the lowest-scoring known-relevant paper and
        the highest-scoring known-irrelevant paper. If the two groups overlap
        (which honest labelling usually reveals), the midpoint is still
        returned but the caller is told to expect manual review at the margin.
        """
        rel = [p.relevance_score for p in known_relevant if p]
        irr = [p.relevance_score for p in known_irrelevant if p]

        if not rel and not irr:
            return default, "没有标定样本，沿用默认阈值。"
        if not rel:
            return round(max(irr), 4), (
                f"只有 {len(irr)} 篇不相关样本，阈值取其最高分 {max(irr):.3f} 之下。"
                "建议补充相关样本以获得更稳的边界。"
            )
        if not irr:
            return round(min(rel) * 0.8, 4), (
                f"只有 {len(rel)} 篇相关样本，阈值取其最低分 {min(rel):.3f} 的 80%。"
                "建议补充不相关样本以确认边界。"
            )

        lo_rel, hi_irr = min(rel), max(irr)
        if lo_rel > hi_irr:
            threshold = round((lo_rel + hi_irr) / 2, 4)
            return threshold, (
                f"两类样本完全分离（相关最低 {lo_rel:.3f} > 不相关最高 {hi_irr:.3f}），"
                f"阈值取中点 {threshold:.3f}，自动筛选在这个数据集上表现可靠。"
            )
        threshold = round((lo_rel + hi_irr) / 2, 4)
        return threshold, (
            f"两类样本有重叠（相关最低 {lo_rel:.3f} ≤ 不相关最高 {hi_irr:.3f}），"
            f"阈值取中点 {threshold:.3f}。边界附近的 {sum(1 for r in rel if r < threshold) + sum(1 for i in irr if i >= threshold)} "
            "篇文献需要人工复核——这说明检索式还需要收紧。"
        )

    # ------------------------------------------------------------------
    # Filtering and dedup
    # ------------------------------------------------------------------

    def filter_noise(
        self,
        papers: list[Paper],
        min_relevance: float = 0.05,
        min_citations: int = 0,
        max_papers: int = 200,
    ) -> list[Paper]:
        """Remove low-relevance papers, zero-citation papers, and trim to max."""
        filtered = [
            p for p in papers
            if p.relevance_score >= min_relevance
            and p.citation_count >= min_citations
        ]
        # Composite: relevance gated by a log-citation bonus. Using
        # relevance * log(cites+2) means a well-cited but off-topic paper can
        # never outrank an on-topic one, which pure log-cites would allow.
        filtered.sort(
            key=lambda p: p.relevance_score * math.log(p.citation_count + 2),
            reverse=True,
        )
        return filtered[:max_papers]

    def deduplicate_by_doi(self, papers: list[Paper]) -> list[Paper]:
        """Merge shared strong IDs, retaining provider IDs and discovery evidence."""
        seen: dict[str, Paper] = {}
        result: list[Paper] = []
        for p in papers:
            p.doi = normalize_doi(p.doi or p.identifiers.doi) or None
            aliases = paper_aliases(p)
            matches = list({id(seen[k]): seen[k] for k in sorted(aliases) if k in seen}.values())
            priority = {id(record): i for i, record in enumerate(result)}
            matches.sort(key=lambda record: priority[id(record)])
            existing = matches[0] if matches else p
            if not matches:
                result.append(p)
            for other in [*matches[1:], p] if matches else []:
                self.merge_metadata(existing, other)
                for key, record in list(seen.items()):
                    if record is other:
                        seen[key] = existing
                if other is not p:
                    result = [record for record in result if record is not other]
            for key in aliases | paper_aliases(existing):
                seen[key] = existing
        return result

    @staticmethod
    def merge_metadata(existing: Paper, other: Paper) -> None:
        if existing is other:
            return
        if other.relevance_score > existing.relevance_score and other.score_breakdown:
            existing.score_breakdown = dict(other.score_breakdown)
        for field_name in ("doi", "year", "venue", "url", "abstract", "authors"):
            if not getattr(existing, field_name) and getattr(other, field_name):
                setattr(existing, field_name, getattr(other, field_name))
        for field_name in ("citation_count", "reference_count", "relevance_score"):
            setattr(existing, field_name, max(getattr(existing, field_name), getattr(other, field_name)))
        for field_name in ("topics", "citation_ids", "reference_ids"):
            setattr(existing, field_name, sorted(set(getattr(existing, field_name) + getattr(other, field_name))))
        for field_name in vars(existing.identifiers):
            if not getattr(existing.identifiers, field_name):
                setattr(existing.identifiers, field_name, getattr(other.identifiers, field_name))
        for trace in other.discovery_traces:
            if trace not in existing.discovery_traces:
                existing.discovery_traces.append(trace)

    @staticmethod
    def extract_keywords(text: str, top_n: int = 10) -> list[str]:
        """Extract likely keywords from a text snippet for display."""
        words = re.findall(r"[a-zA-Z]{3,}", text.lower())
        words = [w for w in words if w not in _STOPWORDS and len(w) > 2]
        return [w for w, _ in Counter(words).most_common(top_n)]
