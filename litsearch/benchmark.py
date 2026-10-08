"""Frozen retrieval/graph benchmark: dataset format, metrics and runners.

Why this exists
---------------
Before v0.9.0 the project could not answer the only question that justifies a
retrieval upgrade: *what does this method actually find that plain keyword
search misses?* Without a frozen case set and a fixed denominator, "semantic
retrieval helps" is unfalsifiable, and any improvement claim is unfalsifiable
too.

What this module does **not** do
--------------------------------
It does not invent labels. Expert relevance judgements can only come from a
human, so a dataset is either genuinely labelled or it is explicitly marked as
unlabelled and metrics are skipped for it. Fabricating a gold standard would
make every number derived from it meaningless, so
:class:`BenchmarkDataset` refuses to score unlabelled cases rather than
guessing.

Units and conventions
---------------------
* A **case** is one research topic with one query, a search date, the providers
  used, and a set of labelled papers.
* A **label** is one of ``core_relevant``, ``peripheral_relevant``,
  ``known_irrelevant``, ``key_review``, ``seed``. ``key_review`` is a
  *peripheral-relevant* document (it is on topic, it is not a primary result),
  and ``seed`` is the starting set — it is excluded from the relevant set
  unless it is also labelled ``core_relevant``/``peripheral_relevant``.
* **Recall denominators** are the labelled relevant set, and only that. A
  known-paper set is not a complete ground truth for a field, so recall here
  answers "of the relevant work we know about, how much did it find", never
  "how much of the field did it find". Reports must say so.
* Unlabelled candidates are neither credited nor penalised: they are counted
  separately as ``unlabelled_in_top_k`` so a reader can see how much of the
  top-k the denominator could not judge.

Every metric is computed at a fixed cutoff on a fixed candidate set, so a
difference between two systems is a difference in ranking or candidate
generation — not in how many requests each one happened to make.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

#: Label vocabulary. Order matters only for stable reporting.
CORE_RELEVANT = "core_relevant"
PERIPHERAL_RELEVANT = "peripheral_relevant"
KNOWN_IRRELEVANT = "known_irrelevant"
KEY_REVIEW = "key_review"
SEED = "seed"

LABELS = (CORE_RELEVANT, PERIPHERAL_RELEVANT, KNOWN_IRRELEVANT, KEY_REVIEW, SEED)

#: Labels that count towards the recall denominator.
RELEVANT_LABELS = frozenset({CORE_RELEVANT, PERIPHERAL_RELEVANT, KEY_REVIEW})

#: Label weights for graded metrics (nDCG).
LABEL_GAIN = {
    CORE_RELEVANT: 3.0,
    KEY_REVIEW: 2.0,
    PERIPHERAL_RELEVANT: 2.0,
    SEED: 0.0,
    KNOWN_IRRELEVANT: 0.0,
}

#: The five baselines the spec requires, kept as separate entries: BC and CC
#: must not be merged into one arm, or their individual contributions are
#: unmeasurable.
BASELINES = (
    "lexical",
    "lexical_snowball",
    "lexical_bc",
    "lexical_cc",
    "lexical_snowball_bc_cc",
)

#: Optional post-freeze arms. Present so the interface is ready before the
#: embedding work starts, and reported separately from the frozen baselines.
SEMANTIC_BASELINES = (
    "semantic_only",
    "lexical_semantic_rrf",
    "lexical_semantic_citation",
    "lexical_semantic_weighted_sum",
)

DEFAULT_CUTOFFS = (20, 50)


class UnlabelledCaseError(RuntimeError):
    """Raised when metrics are requested for a case with no expert labels."""


@dataclass
class LabelledPaper:
    """One labelled paper inside a case."""

    paper_id: str
    label: str
    title: str = ""
    year: int | None = None
    evidence_notes: str = ""

    def __post_init__(self):
        if self.label not in LABELS:
            raise ValueError(
                f"unknown label {self.label!r}; expected one of {LABELS}"
            )
        if not self.paper_id:
            raise ValueError("a labelled paper needs a canonical id")


@dataclass
class BenchmarkCase:
    """One frozen research case."""

    case_id: str
    domain: str
    query: str
    search_date: str
    providers: list[str] = field(default_factory=list)
    notes: str = ""
    labelled_by: str = ""
    papers: list[LabelledPaper] = field(default_factory=list)

    @property
    def is_labelled(self) -> bool:
        """True only when a human actually judged relevance.

        A case with only seeds has no denominator, so it cannot be scored. This
        is deliberately strict: an empty relevant set would otherwise produce
        recall 0/0 rendered as a confident 0.0.
        """
        return bool(self.labelled_by.strip()) and any(
            p.label in RELEVANT_LABELS for p in self.papers
        )

    @property
    def relevant_ids(self) -> set[str]:
        return {p.paper_id for p in self.papers if p.label in RELEVANT_LABELS}

    @property
    def core_ids(self) -> set[str]:
        return {p.paper_id for p in self.papers if p.label == CORE_RELEVANT}

    @property
    def irrelevant_ids(self) -> set[str]:
        return {p.paper_id for p in self.papers if p.label == KNOWN_IRRELEVANT}

    @property
    def seed_ids(self) -> set[str]:
        return {p.paper_id for p in self.papers if p.label == SEED}

    def label_of(self, paper_id: str) -> str | None:
        for paper in self.papers:
            if paper.paper_id == paper_id:
                return paper.label
        return None


@dataclass
class BenchmarkDataset:
    """A set of cases plus the freeze metadata that makes it reproducible."""

    name: str
    frozen_on: str
    cases: list[BenchmarkCase] = field(default_factory=list)
    label_guide: str = ""
    environment: dict = field(default_factory=dict)

    # -- loading --------------------------------------------------------

    @classmethod
    def from_dict(cls, payload: dict) -> BenchmarkDataset:
        if not isinstance(payload, dict):
            raise ValueError("a benchmark dataset must be a JSON object")
        for key in ("name", "frozen_on", "cases"):
            if key not in payload:
                raise ValueError(f"benchmark dataset is missing {key!r}")
        cases = []
        for raw in payload["cases"]:
            cases.append(BenchmarkCase(
                case_id=raw["case_id"],
                domain=raw.get("domain", ""),
                query=raw["query"],
                search_date=raw.get("search_date", ""),
                providers=list(raw.get("providers") or []),
                notes=raw.get("notes", ""),
                labelled_by=raw.get("labelled_by", ""),
                papers=[
                    LabelledPaper(
                        paper_id=row["paper_id"],
                        label=row["label"],
                        title=row.get("title", ""),
                        year=row.get("year"),
                        evidence_notes=row.get("evidence_notes", ""),
                    )
                    for row in raw.get("papers") or []
                ],
            ))
        return cls(
            name=payload["name"],
            frozen_on=payload["frozen_on"],
            cases=cases,
            label_guide=payload.get("label_guide", ""),
            environment=dict(payload.get("environment") or {}),
        )

    @classmethod
    def load(cls, path: str | Path) -> BenchmarkDataset:
        text = Path(path).read_text(encoding="utf-8")
        return cls.from_dict(json.loads(text))

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "frozen_on": self.frozen_on,
            "label_guide": self.label_guide,
            "environment": self.environment,
            "cases": [
                {
                    "case_id": case.case_id,
                    "domain": case.domain,
                    "query": case.query,
                    "search_date": case.search_date,
                    "providers": case.providers,
                    "notes": case.notes,
                    "labelled_by": case.labelled_by,
                    "papers": [
                        {
                            "paper_id": p.paper_id,
                            "label": p.label,
                            "title": p.title,
                            "year": p.year,
                            "evidence_notes": p.evidence_notes,
                        }
                        for p in case.papers
                    ],
                }
                for case in self.cases
            ],
        }

    @property
    def labelled_cases(self) -> list[BenchmarkCase]:
        return [case for case in self.cases if case.is_labelled]

    @property
    def domain_coverage(self) -> list[str]:
        return sorted({case.domain for case in self.cases if case.domain})

    def label_gaps(self) -> list[str]:
        """Cases that cannot be scored, with the reason.

        Reported explicitly so a partial dataset is never described as a
        finished benchmark.
        """
        gaps = []
        for case in self.cases:
            if case.is_labelled:
                continue
            if not case.labelled_by.strip():
                gaps.append(f"{case.case_id}: no human labeller recorded")
            else:
                gaps.append(f"{case.case_id}: no relevant/irrelevant labels recorded")
        return gaps


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def precision_at_k(ranked_ids: list[str], relevant: set[str], k: int) -> float:
    """Fraction of the top-k that is relevant. Denominator is k, not len(k)."""
    if k <= 0:
        raise ValueError("k must be positive")
    top = ranked_ids[:k]
    if not top:
        return 0.0
    return len([pid for pid in top if pid in relevant]) / k


def recall_at_k(ranked_ids: list[str], relevant: set[str], k: int) -> float:
    """Fraction of the *labelled* relevant set found in the top-k.

    The denominator is the labelled set, never the field. A report using this
    number must state the label coverage it was computed over.
    """
    if k <= 0:
        raise ValueError("k must be positive")
    if not relevant:
        raise UnlabelledCaseError("recall needs at least one labelled relevant paper")
    return len([pid for pid in ranked_ids[:k] if pid in relevant]) / len(relevant)


def reciprocal_rank(ranked_ids: list[str], relevant: set[str]) -> float:
    """1 / rank of the first relevant hit, else 0.0."""
    for position, pid in enumerate(ranked_ids, start=1):
        if pid in relevant:
            return 1.0 / position
    return 0.0


def ndcg_at_k(ranked_ids: list[str], case: BenchmarkCase, k: int) -> float:
    """Graded nDCG@k over the label gains in :data:`LABEL_GAIN`."""
    if k <= 0:
        raise ValueError("k must be positive")
    gains = [LABEL_GAIN.get(case.label_of(pid) or "", 0.0) for pid in ranked_ids[:k]]
    dcg = sum(gain / math.log2(position + 1) for position, gain in enumerate(gains, start=1))

    ideal_gains = sorted(
        (LABEL_GAIN.get(p.label, 0.0) for p in case.papers), reverse=True
    )[:k]
    idcg = sum(
        gain / math.log2(position + 1)
        for position, gain in enumerate(ideal_gains, start=1)
    )
    return dcg / idcg if idcg else 0.0


def keyword_missed_recovery(
    ranked_ids: list[str], case: BenchmarkCase, lexical_ids: set[str], k: int
) -> list[str]:
    """Relevant papers this ranking found that the lexical baseline missed.

    This is the metric the whole comparison exists for: it measures papers
    that were *retrievable but not retrieved by keywords*, which is the only
    thing an expansion method can uniquely contribute.
    """
    return [
        pid for pid in ranked_ids[:k]
        if pid in case.relevant_ids and pid not in lexical_ids
    ]


@dataclass
class CaseResult:
    """Metrics for one case under one system."""

    case_id: str
    system: str
    cutoffs: tuple[int, ...] = DEFAULT_CUTOFFS
    ranked_ids: list[str] = field(default_factory=list)
    requests: int = 0
    latency_seconds: float = 0.0
    rate_limited: int = 0
    failures: int = 0
    candidate_ids: set[str] = field(default_factory=set)
    notes: str = ""

    def metrics(self, case: BenchmarkCase) -> dict:
        if not case.is_labelled:
            raise UnlabelledCaseError(
                f"case {case.case_id!r} has no expert labels; scoring it would "
                f"produce invented numbers"
            )
        relevant = case.relevant_ids
        out: dict = {
            "recall_denominator": len(relevant),
            "relevant_labelled": len(relevant),
            "top_k_unlabelled": len([
                pid for pid in self.ranked_ids[: max(self.cutoffs)]
                if case.label_of(pid) is None
            ]),
            "mrr": round(reciprocal_rank(self.ranked_ids, relevant), 4),
            "requests": self.requests,
            "latency_seconds": round(self.latency_seconds, 3),
            "rate_limited": self.rate_limited,
            "failures": self.failures,
            "candidates": len(self.candidate_ids) or len(self.ranked_ids),
        }
        for k in self.cutoffs:
            out[f"recall@{k}"] = round(recall_at_k(self.ranked_ids, relevant, k), 4)
            out[f"precision@{k}"] = round(precision_at_k(self.ranked_ids, relevant, k), 4)
            out[f"ndcg@{k}"] = round(ndcg_at_k(self.ranked_ids, case, k), 4)
        return out


@dataclass
class SystemComparison:
    """Aggregate report across cases for a set of systems."""

    systems: list[str]
    per_case: dict[str, dict[str, dict]] = field(default_factory=dict)
    shared_candidate_set: bool = False
    label_coverage: dict = field(default_factory=dict)

    #: Fields that describe one case rather than measuring a system. Averaging
    #: them would invent a number no reader can interpret ("mean recall
    #: denominator"), so they are summed or listed instead.
    NON_AVERAGED = frozenset({"recall_denominator", "relevant_labelled", "cases_reported"})

    def to_dict(self) -> dict:
        return {
            "systems": list(self.systems),
            "shared_candidate_set": self.shared_candidate_set,
            "label_coverage": dict(self.label_coverage),
            "per_case": self.per_case,
            "aggregate": self.aggregate(),
        }

    @property
    def cases_scored(self) -> list[str]:
        return sorted(
            case_id for case_id, systems in self.per_case.items()
            if any("skipped" not in metrics for metrics in systems.values())
        )

    @property
    def cases_skipped(self) -> list[str]:
        return sorted(
            case_id for case_id, systems in self.per_case.items()
            if all("skipped" in metrics for metrics in systems.values())
        )

    def aggregate(self) -> dict:
        """Mean of each genuine metric across the scored cases.

        Reports how many cases contributed, so a mean over one case is never
        mistaken for a benchmark result.
        """
        totals: dict[str, float] = {}
        counts: dict[str, int] = {}
        for systems in self.per_case.values():
            for metrics in systems.values():
                if "skipped" in metrics:
                    continue
                for key, value in metrics.items():
                    if key in self.NON_AVERAGED:
                        continue
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        totals[key] = totals.get(key, 0.0) + float(value)
                        counts[key] = counts.get(key, 0) + 1
        means = {key: round(totals[key] / counts[key], 4) for key in sorted(totals)}
        means["cases_scored"] = len(self.cases_scored)
        means["cases_skipped"] = len(self.cases_skipped)
        return means


def compare_systems(
    results: list[CaseResult],
    dataset: BenchmarkDataset,
    systems: list[str] | None = None,
    *,
    shared_candidate_set: bool = False,
) -> SystemComparison:
    """Build the comparison report.

    ``shared_candidate_set`` says whether every system ranked the *same*
    candidate pool. This must be stated, because with a shared pool a
    comparison isolates ranking quality and cannot show that a paper outside
    the pool was recovered — the limitation the spec calls out for
    embedding reranking.
    """
    by_case = {case.case_id: case for case in dataset.cases}
    comparison = SystemComparison(
        systems=list(systems or sorted({r.system for r in results})),
        shared_candidate_set=shared_candidate_set,
        label_coverage={
            "cases_total": len(dataset.cases),
            "cases_labelled": len(dataset.labelled_cases),
            "domains": dataset.domain_coverage,
            "label_gaps": dataset.label_gaps(),
        },
    )
    for result in results:
        case = by_case.get(result.case_id)
        if case is None:
            continue
        try:
            metrics = result.metrics(case)
        except UnlabelledCaseError as exc:
            comparison.per_case.setdefault(result.case_id, {})[result.system] = {
                "skipped": str(exc)
            }
            continue
        comparison.per_case.setdefault(result.case_id, {})[result.system] = metrics
    return comparison
