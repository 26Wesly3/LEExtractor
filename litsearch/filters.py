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
makes scores **relative within a run**: a 0.3 from one candidate set is not the
same quantity as a 0.3 from another. Consequences, enforced here:

* Every scoring call gets a `score_context_id`; scores carrying different
  contexts must not be compared, ranked together, or max()-merged.
* The score is for **ranking only**. It never rejects a paper on its own: only
  a `CalibrationRecord` built from human labels (`calibrate_threshold`) may
  drive automatic assistance, and a single-class label set is explicitly
  `insufficient_labels` instead of a confident-looking cut-off.
"""

import hashlib
import math
import re
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from litsearch.identifiers import normalize_doi, paper_aliases
from litsearch.models import Paper

#: Identifies the scoring algorithm that produced a threshold's input scores.
#: Bump it whenever the lexical blend changes; a calibration recorded under a
#: different version is invalid (see `CalibrationRecord.is_valid_for`).
ALGORITHM_VERSION = "lexical_v3_word_char_coverage"

#: Version of the score *scale* itself. Matches `search_manifest["ranking"]`.
SCORE_VERSION = "hybrid_lexical_v1"

STATUS_CALIBRATED = "calibrated"
STATUS_INSUFFICIENT_LABELS = "insufficient_labels"


def corpus_hash(papers) -> str:
    """Stable, order-independent hash of a candidate set's identities.

    Keys on `Paper.canonical_id` (DOI > OpenAlex > S2 > arXiv > fallback), so
    renaming or re-describing a paper does not change the hash, but adding,
    removing or re-identifying one does. Used to invalidate a calibration when
    the scored corpus changes.
    """
    identities = set()
    for paper in papers or ():
        canonical = getattr(paper, "canonical_id", None)
        identities.add(str(canonical) if canonical else str(paper))
    joined = "\n".join(sorted(identities))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]


@dataclass
class CalibrationRecord:
    """A threshold derived from human labels, with its provenance.

    Frozen contract (V090_INTERFACE_CONTRACT.md §8). The threshold is
    *auxiliary*: it may assist screening only while `usable` is True, and it is
    invalid the moment the query, the corpus, the scoring algorithm/version or
    the score context it was derived from changes.

    `threshold is None` for `status == "insufficient_labels"` (zero or
    single-class labels): such a record must never claim a reliable binary
    calibration and must never auto-reject a paper.
    """

    labels: dict[str, int] = field(default_factory=lambda: {"relevant": 0, "irrelevant": 0})
    positive_count: int = 0
    negative_count: int = 0
    query: str = ""
    corpus_hash: str = ""
    algorithm_version: str = ALGORITHM_VERSION
    score_version: str = SCORE_VERSION
    threshold: float | None = None
    created_at: str = ""
    evaluation: dict = field(default_factory=dict)
    status: str = STATUS_INSUFFICIENT_LABELS

    # -- state -----------------------------------------------------------

    @property
    def usable(self) -> bool:
        """True only for a two-class calibration that has not been invalidated."""
        return (
            self.status == STATUS_CALIBRATED
            and self.threshold is not None
            and not self.evaluation.get("invalidated_reason")
        )

    @property
    def reliable(self) -> bool:
        """A usable calibration whose labelled classes are actually separated."""
        return self.usable and bool(self.evaluation.get("separation"))

    @property
    def note(self) -> str:
        """Human-readable explanation shown next to the threshold in the UI."""
        return str(self.evaluation.get("note", ""))

    # -- invalidation ----------------------------------------------------

    def invalidated_by(
        self,
        *,
        query: str | None = None,
        corpus_hash: str | None = None,
        algorithm_version: str | None = None,
        score_version: str | None = None,
        score_context_id: str | None = None,
    ) -> str | None:
        """Reason this calibration may not be used for the given context.

        Passing `None` for a dimension means "do not check it". Returns a
        human-readable reason, or `None` when the record is still valid.
        """
        if self.status != STATUS_CALIBRATED or self.threshold is None:
            return self.evaluation.get("invalidated_reason") or (
                f"status is {self.status!r}: not a two-class calibration"
            )
        stale = self.evaluation.get("invalidated_reason")
        if stale:
            return str(stale)
        if query is not None and query != self.query:
            return f"query changed: calibrated for {self.query!r}, asked about {query!r}"
        if corpus_hash is not None and corpus_hash != self.corpus_hash:
            return "corpus changed (corpus_hash mismatch): the candidate set is not the one that was calibrated"
        if algorithm_version is not None and algorithm_version != self.algorithm_version:
            return (
                f"algorithm_version changed: {self.algorithm_version!r} -> "
                f"{algorithm_version!r}"
            )
        if score_version is not None and score_version != self.score_version:
            return f"score_version changed: {self.score_version!r} -> {score_version!r}"
        calibrated_context = str(self.evaluation.get("score_context_id", ""))
        if score_context_id is not None and score_context_id != calibrated_context:
            return (
                "score_context_id changed: scores come from a different normalisation "
                f"batch ({calibrated_context!r} -> {score_context_id!r})"
            )
        return None

    def is_valid_for(self, **context) -> bool:
        """Inverse of `invalidated_by`; see it for the accepted keywords."""
        return self.invalidated_by(**context) is None

    # -- (de)serialisation ----------------------------------------------

    def to_dict(self) -> dict:
        return {
            "labels": dict(self.labels),
            "positive_count": self.positive_count,
            "negative_count": self.negative_count,
            "query": self.query,
            "corpus_hash": self.corpus_hash,
            "algorithm_version": self.algorithm_version,
            "score_version": self.score_version,
            "threshold": self.threshold,
            "created_at": self.created_at,
            "evaluation": dict(self.evaluation),
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "CalibrationRecord":
        data = data or {}
        labels = data.get("labels") or {}
        threshold = data.get("threshold")
        return cls(
            labels={
                "relevant": int(labels.get("relevant", 0) or 0),
                "irrelevant": int(labels.get("irrelevant", 0) or 0),
            },
            positive_count=int(data.get("positive_count", 0) or 0),
            negative_count=int(data.get("negative_count", 0) or 0),
            query=str(data.get("query", "") or ""),
            corpus_hash=str(data.get("corpus_hash", "") or ""),
            algorithm_version=str(data.get("algorithm_version", "") or ALGORITHM_VERSION),
            score_version=str(data.get("score_version", "") or SCORE_VERSION),
            threshold=None if threshold is None else float(threshold),
            created_at=str(data.get("created_at", "") or ""),
            evaluation=dict(data.get("evaluation") or {}),
            status=str(data.get("status", "") or STATUS_INSUFFICIENT_LABELS),
        )

    def mark_invalid(self, reason: str) -> None:
        """Record why this calibration must not be reused (kept for audit)."""
        self.evaluation["invalidated_reason"] = str(reason)

    def __iter__(self):
        """Deprecated `(threshold, note)` unpacking for pre-0.9.0 callers.

        New code must read `.threshold`/`.note` and check `.usable`: when the
        record is not usable the legacy path yields the caller's default
        (0.15) purely so old UI code keeps working — it is not a calibration.
        """
        legacy = self.threshold
        if legacy is None:
            legacy = self.evaluation.get("legacy_threshold", 0.15)
        yield legacy
        yield self.note

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
        self._last_score_context_id: str = ""

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

    @staticmethod
    def _new_score_context_id(direction_text: str, paper_count: int) -> str:
        """Unique id for one scoring batch (never reused across runs)."""
        seed = f"{direction_text}|{paper_count}|{time.time_ns()}|{uuid.uuid4().hex}"
        return "ctx-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]

    def compute_relevance(
        self,
        papers: list[Paper],
        direction_text: str,
        score_context_id: str | None = None,
    ) -> list[Paper]:
        """Score each paper on a 0-1 scale **relative to this result set**.

        Every call is a separate scoring batch: the returned papers carry a
        `score_context_id`, and scores from different batches are not
        comparable (see `scores_comparable`). Callers that re-score one batch
        on purpose may pass an explicit `score_context_id`.
        """
        self._last_breakdown = []
        if not papers:
            return papers

        batch_id = score_context_id or self._new_score_context_id(direction_text, len(papers))
        self._last_score_context_id = batch_id
        for paper in papers:
            paper.score_context_id = batch_id

        if not direction_text.strip():
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

    @property
    def score_context_id(self) -> str:
        """Batch id of the most recent `compute_relevance` call ("" if none)."""
        return self._last_score_context_id

    @staticmethod
    def score_contexts_of(papers) -> set[str]:
        """The distinct scoring batches present in `papers`."""
        return {str(getattr(p, "score_context_id", "") or "") for p in papers or ()}

    @staticmethod
    def scores_comparable(papers) -> bool:
        """False when `papers` mixes scores from different scoring batches."""
        return len(RelevanceFilter.score_contexts_of(papers)) <= 1

    # ------------------------------------------------------------------
    # Threshold calibration
    # ------------------------------------------------------------------

    @staticmethod
    def calibrate_threshold(
        known_relevant: list[Paper],
        known_irrelevant: list[Paper],
        default: float = 0.15,
        *,
        query: str = "",
        corpus_hash: str = "",
        score_context_id: str = "",
        algorithm_version: str = ALGORITHM_VERSION,
        score_version: str = SCORE_VERSION,
    ) -> CalibrationRecord:
        """Derive a screening threshold from labelled papers.

        Takes the midpoint between the lowest-scoring known-relevant paper and
        the highest-scoring known-irrelevant paper and returns a
        `CalibrationRecord` (contract §8) carrying the labels, the query, the
        corpus hash, the scoring versions and the evaluation.

        Zero labels or a single class cannot form a binary boundary: those
        records get `status="insufficient_labels"` and `threshold=None` — they
        are ranking aids, never an automatic reject.

        Backwards compatibility: the returned record still unpacks as the old
        `(threshold, note)` tuple; that path is deprecated (see
        `CalibrationRecord.__iter__`).
        """
        rel = [p.relevance_score for p in known_relevant if p]
        irr = [p.relevance_score for p in known_irrelevant if p]
        labels = {"relevant": len(rel), "irrelevant": len(irr)}
        base = {
            "labels": labels,
            "positive_count": len(rel),
            "negative_count": len(irr),
            "query": query,
            "corpus_hash": corpus_hash,
            "algorithm_version": algorithm_version,
            "score_version": score_version,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        def insufficient(note: str, legacy_threshold: float, review: int = 0) -> CalibrationRecord:
            return CalibrationRecord(
                **base,
                threshold=None,
                status=STATUS_INSUFFICIENT_LABELS,
                evaluation={
                    "separation": False,
                    "margin": 0.0,
                    "needs_manual_review": review,
                    "reliable_binary_calibration": False,
                    "score_context_id": score_context_id,
                    "legacy_threshold": round(float(legacy_threshold), 4),
                    "note": note,
                },
            )

        if not rel and not irr:
            return insufficient(
                "没有标定样本，沿用默认阈值；分数仅用于排序，未标定不得自动排除文献。",
                default,
            )
        if not rel:
            reference = round(max(irr), 4)
            return insufficient(
                f"只有 {len(irr)} 篇不相关样本，无法形成二分类标定（insufficient_labels）；"
                f"单类别参考值 {reference:.3f} 仅作排序参考，不自动排除文献。"
                "建议补充相关样本以获得更稳的边界。",
                reference,
                review=len(irr),
            )
        if not irr:
            reference = round(min(rel) * 0.8, 4)
            return insufficient(
                f"只有 {len(rel)} 篇相关样本，无法形成二分类标定（insufficient_labels）；"
                f"单类别参考值 {reference:.3f} 仅作排序参考，不自动排除文献。"
                "建议补充不相关样本以确认边界。",
                reference,
                review=len(rel),
            )

        lo_rel, hi_irr = min(rel), max(irr)
        threshold = round((lo_rel + hi_irr) / 2, 4)
        margin = round(lo_rel - hi_irr, 6)
        separated = lo_rel > hi_irr
        boundary = sum(1 for r in rel if r < threshold) + sum(1 for i in irr if i >= threshold)
        if separated:
            note = (
                f"两类样本完全分离（相关最低 {lo_rel:.3f} > 不相关最高 {hi_irr:.3f}），"
                f"阈值取中点 {threshold:.3f}。该阈值只对本候选集与当前检索式有效；"
                "换课题、扩充语料或更换排序算法后必须重新标定。"
            )
        else:
            note = (
                f"两类样本有重叠（相关最低 {lo_rel:.3f} ≤ 不相关最高 {hi_irr:.3f}），"
                f"阈值取中点 {threshold:.3f}。边界附近的 {boundary} 篇文献需要人工复核"
                "——这说明检索式还需要收紧。"
            )
        return CalibrationRecord(
            **base,
            threshold=threshold,
            status=STATUS_CALIBRATED,
            evaluation={
                "separation": separated,
                "margin": margin,
                "needs_manual_review": boundary,
                "reliable_binary_calibration": separated,
                "score_context_id": score_context_id,
                "note": note,
            },
        )

    @staticmethod
    def build_calibration(
        labels: dict[str, list[Paper]],
        *,
        query: str = "",
        corpus_hash: str = "",
        score_context_id: str = "",
        default: float = 0.15,
        algorithm_version: str = ALGORITHM_VERSION,
        score_version: str = SCORE_VERSION,
    ) -> CalibrationRecord:
        """`calibrate_threshold` for callers that already hold a label map.

        `labels` must use exactly the keys `"relevant"` and `"irrelevant"`;
        anything else is a programming error and raises `ValueError`.
        """
        unknown = sorted(set(labels or {}) - {"relevant", "irrelevant"})
        if unknown:
            raise ValueError(
                f"unknown label keys {unknown}; use 'relevant' and 'irrelevant'"
            )
        labels = labels or {}
        return RelevanceFilter.calibrate_threshold(
            list(labels.get("relevant", [])),
            list(labels.get("irrelevant", [])),
            default,
            query=query,
            corpus_hash=corpus_hash,
            score_context_id=score_context_id,
            algorithm_version=algorithm_version,
            score_version=score_version,
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
        """Trim a *ranking* list: low relevance, zero citations, and max length.

        This is a hard filter for building a display/ranking shortlist, not a
        screening decision: the removed papers are not recorded as excluded and
        an uncalibrated score must never be fed here as an automatic reject.
        Papers dropped by `max_papers` are a *selection* (truncation) of a
        larger candidate set, not evidence about what the databases contained.
        """
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
        """Fold `other`'s metadata into `existing`.

        Relevance scores are only merged when the two papers were scored in the
        same batch (`score_context_id`). Max()-ing scores normalised against
        different candidate sets is meaningless — in that case the existing
        score and its context are kept and the caller must re-score the merged
        set.
        """
        if existing is other:
            return
        existing_context = str(getattr(existing, "score_context_id", "") or "")
        other_context = str(getattr(other, "score_context_id", "") or "")
        if existing_context == other_context:
            if other.relevance_score > existing.relevance_score:
                existing.relevance_score = other.relevance_score
                if other.score_breakdown:
                    existing.score_breakdown = dict(other.score_breakdown)
        elif not existing_context and other_context:
            # only the incoming record was ever scored; adopt score + context together
            existing.relevance_score = other.relevance_score
            existing.score_context_id = other_context
            if other.score_breakdown:
                existing.score_breakdown = dict(other.score_breakdown)
        for field_name in ("doi", "year", "venue", "url", "abstract", "authors"):
            if not getattr(existing, field_name) and getattr(other, field_name):
                setattr(existing, field_name, getattr(other, field_name))
        for field_name in ("citation_count", "reference_count"):
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
