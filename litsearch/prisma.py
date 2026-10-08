"""PRISMA 2020 flow tracking for systematic literature reviews.

Tracks papers through the standard PRISMA 2020 stages, with the two screening
stages kept strictly separate:

1. **Identification** — records found via database search + other methods
2. **Screening** — title/abstract screening (records excluded here are
   `records_excluded_title_abstract`)
3. **Eligibility** — full-text reports sought, retrieved and assessed
   (reports excluded here are `full_text_excluded`, with reasons)
4. **Included** — final set for synthesis

The previous implementation conflated stages 2 and 3: `full_text_assessed`
was just the number of accepted records, so `full_text_excluded` was always 0
and the diagram was PRISMA-shaped but not PRISMA-compliant.

Backwards compatibility: if no full-text decision has been recorded yet, the
eligibility stage counts as "not run" and `studies_included` falls back to the
title/abstract acceptances (the old behaviour).
"""

from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum

from litsearch.filters import RelevanceFilter
from litsearch.identifiers import identifier_key, paper_aliases
from litsearch.models import Paper


class ScreeningDecision(Enum):
    PENDING = "pending"
    ACCEPT = "accept"
    REJECT = "reject"
    MAYBE = "maybe"


class ScreeningStage(Enum):
    """The two PRISMA screening stages."""

    TITLE_ABSTRACT = "title_abstract"
    FULL_TEXT = "full_text"


FULL_TEXT_EXCLUSION_REASONS = [
    "全文无法获取 / Full text not retrievable",
    "非研究论文（综述/社论/摘要）/ Not a primary study",
    "研究对象不符 / Wrong population",
    "方法或干预不符 / Wrong intervention or method",
    "结局指标不符 / Wrong outcome",
    "数据不完整 / Insufficient data",
    "重复发表 / Duplicate publication",
    "其他 / Other",
]


@dataclass
class PRISMARecord:
    """Tracks a single paper through the PRISMA pipeline."""

    paper: Paper
    source: str = ""           # "database_search" | "snowballing" | "similar" | "manual"

    # Stage 2 — title/abstract screening
    screening_decision: ScreeningDecision = ScreeningDecision.PENDING
    screening_reason: str = ""

    # Stage 3 — full-text eligibility
    full_text_retrieved: bool = False
    full_text_decision: ScreeningDecision = ScreeningDecision.PENDING
    full_text_reason: str = ""

    rejection_reason: str = ""   # legacy alias for screening_reason
    relevance_score: float = 0.0

    @property
    def passed_screening(self) -> bool:
        return self.screening_decision == ScreeningDecision.ACCEPT

    @property
    def included(self) -> bool:
        return self.full_text_decision == ScreeningDecision.ACCEPT


@dataclass
class PRISMAReport:
    """Complete PRISMA 2020 flow data."""

    # Identification
    database_results: int = 0
    snowball_results: int = 0
    similar_results: int = 0
    manual_additions: int = 0
    duplicates_removed: int = 0

    # After deduplication
    records_after_dedup: int = 0

    # Screening (title/abstract)
    records_screened: int = 0
    records_excluded_title_abstract: int = 0

    # Eligibility (full text)
    full_text_stage_enabled: bool = False
    reports_sought: int = 0
    reports_not_retrieved: int = 0
    reports_pending_retrieval: int = 0
    reports_pending_assessment: int = 0
    full_text_assessed: int = 0
    full_text_excluded: int = 0
    full_text_exclusion_reasons: dict[str, int] = field(default_factory=dict)

    # Included
    studies_included: int = 0

    # All records with decisions
    records: list[PRISMARecord] = field(default_factory=list)

    def to_flow_dict(self) -> dict:
        """Export as dict suitable for rendering a PRISMA flow diagram."""
        return {
            "identification": {
                "database_search": self.database_results,
                "citation_searching": self.snowball_results + self.similar_results,
                "other_methods": self.manual_additions,
                "total": (
                    self.database_results + self.snowball_results
                    + self.similar_results + self.manual_additions
                ),
                "duplicates_removed": self.duplicates_removed,
            },
            "screening": {
                "after_dedup": self.records_after_dedup,
                "screened": self.records_screened,
                "excluded_title_abstract": self.records_excluded_title_abstract,
            },
            "eligibility": {
                "stage_enabled": self.full_text_stage_enabled,
                "reports_sought": self.reports_sought,
                "reports_not_retrieved": self.reports_not_retrieved,
                "reports_pending_retrieval": self.reports_pending_retrieval,
                "reports_pending_assessment": self.reports_pending_assessment,
                "full_text_assessed": self.full_text_assessed,
                "full_text_excluded": self.full_text_excluded,
                "reasons": self.full_text_exclusion_reasons,
            },
            "included": {
                "studies": self.studies_included,
            },
        }

    def to_mermaid(self) -> str:
        """Generate a Mermaid PRISMA 2020 flow diagram (both stages)."""
        d = self.to_flow_dict()
        ident = d["identification"]
        screen = d["screening"]
        elig = d["eligibility"]
        incl = d["included"]

        if elig["reasons"]:
            reasons = "<br/>".join(
                f"{r}: {c}" for r, c in sorted(
                    elig["reasons"].items(), key=lambda x: -x[1]
                )[:4]
            )
            excluded_node = (
                f'    H["Reports excluded<br/>{reasons}'
                f'<br/><b>n = {elig["full_text_excluded"]}</b>"]'
            )
        else:
            excluded_node = (
                f'    H["Reports excluded<br/><b>n = {elig["full_text_excluded"]}</b>"]'
            )
        note = "" if elig["stage_enabled"] else "⚠ 全文评估阶段尚未启用<br/>"

        return f"""```mermaid
flowchart TD
    A["Records identified<br/>Databases: {ident['database_search']}<br/>Citation searching: {ident['citation_searching']}<br/>Other: {ident['other_methods']}<br/><b>Total: {ident['total']}</b>"]
    B["Duplicate records removed<br/><b>n = {ident['duplicates_removed']}</b>"]
    C["Records screened<br/>(title/abstract)<br/><b>n = {screen['screened']}</b>"]
    D["Records excluded<br/><b>n = {screen['excluded_title_abstract']}</b>"]
    E["Reports sought for retrieval<br/><b>n = {elig['reports_sought']}</b>"]
    F["Reports not retrieved<br/><b>n = {elig['reports_not_retrieved']}</b>"]
    G["Reports assessed for eligibility<br/><b>n = {elig['full_text_assessed']}</b>"]
{excluded_node}
    I["Studies included in review<br/>{note}<b>n = {incl['studies']}</b>"]

    A --> B
    B --> C
    C --> D
    C --> E
    E --> F
    E --> G
    G --> H
    G --> I
```"""


class PRISMATracker:
    """Manages the PRISMA pipeline for a literature review session."""

    def __init__(self):
        self.records: dict[str, PRISMARecord] = {}  # paper ID -> record
        self._id_counter: dict[str, int] = defaultdict(int)
        self._alt_keys: dict[str, str] = {}  # paper.id / doi -> records key
        self._duplicates_seen: int = 0
        self.full_text_stage_enabled: bool = False

    # -- registration ---------------------------------------------------

    def add_papers(
        self, papers: list[Paper], source: str = "database_search"
    ) -> int:
        """Add papers to the identification phase. Returns count of new papers."""
        added = 0
        for p in papers:
            self._id_counter[source] += 1
            key = self._find_key(p.id) or next((self._alt_keys[a] for a in sorted(paper_aliases(p)) if a in self._alt_keys), p.canonical_id)
            if key not in self.records:
                # Propagate the paper's relevance score — without this every
                # record stayed at 0.0 and auto-screening rejected everything.
                self.records[key] = PRISMARecord(
                    paper=p, source=source, relevance_score=float(p.relevance_score or 0.0)
                )
                added += 1
            else:
                self._duplicates_seen += 1
                if p.relevance_score > self.records[key].relevance_score:
                    self.records[key].relevance_score = float(p.relevance_score)
                RelevanceFilter.merge_metadata(self.records[key].paper, p)
            for alt in paper_aliases(p) | {p.id.lower()}:
                if alt:
                    self._alt_keys[alt] = key
        return added

    # -- lookup ---------------------------------------------------------

    def _find_key(self, paper_id: str) -> str | None:
        """Resolve a paper_id to the internal records key."""
        key = identifier_key(paper_id or "")
        if key in self.records:
            return key
        if key in self._alt_keys:
            return self._alt_keys[key]
        if paper_id.lower() in self._alt_keys:
            return self._alt_keys[paper_id.lower()]
        # Fallback: linear scan (older records added before the index existed)
        for k, rec in self.records.items():
            if rec.paper.id.lower() == key:
                return k
            if rec.paper.doi and rec.paper.doi.lower() == key:
                return k
        return None

    # -- stage 2: title / abstract --------------------------------------

    def get_screening_queue(
        self, stage: ScreeningStage = ScreeningStage.TITLE_ABSTRACT
    ) -> list[PRISMARecord]:
        """Pending records for a given stage, most relevant first."""
        if stage == ScreeningStage.FULL_TEXT:
            pending = [
                r for r in self.records.values()
                if r.passed_screening and r.full_text_decision == ScreeningDecision.PENDING
            ]
        else:
            pending = [
                r for r in self.records.values()
                if r.screening_decision == ScreeningDecision.PENDING
            ]
        pending.sort(key=lambda r: r.relevance_score, reverse=True)
        return pending

    def screen_paper(
        self,
        paper_id: str,
        decision: ScreeningDecision,
        reason: str = "",
        stage: ScreeningStage = ScreeningStage.TITLE_ABSTRACT,
    ) -> None:
        """Record a screening decision for one stage."""
        key = self._find_key(paper_id)
        if not key:
            return
        record = self.records[key]
        if stage == ScreeningStage.FULL_TEXT:
            self.full_text_stage_enabled = True
            record.full_text_decision = decision
            record.full_text_reason = reason
            if decision in (ScreeningDecision.ACCEPT, ScreeningDecision.REJECT):
                record.full_text_retrieved = True
        else:
            record.screening_decision = decision
            record.screening_reason = reason
            record.rejection_reason = reason
            if decision != ScreeningDecision.ACCEPT:
                record.full_text_decision = ScreeningDecision.PENDING
                record.full_text_retrieved = False
                record.full_text_reason = ""

    def screen_batch(
        self,
        decisions: dict[str, ScreeningDecision],
        stage: ScreeningStage = ScreeningStage.TITLE_ABSTRACT,
    ) -> None:
        """Batch screening: {paper_id: decision}."""
        for pid, decision in decisions.items():
            self.screen_paper(pid, decision, stage=stage)

    # -- stage 3: full text ---------------------------------------------

    def mark_full_text_retrieved(self, paper_id: str, retrieved: bool = True) -> None:
        """Record whether the full text could actually be obtained."""
        key = self._find_key(paper_id)
        if not key:
            return
        self.full_text_stage_enabled = True
        self.records[key].full_text_retrieved = bool(retrieved)
        if retrieved and self.records[key].full_text_reason == FULL_TEXT_EXCLUSION_REASONS[0]:
            self.records[key].full_text_decision = ScreeningDecision.PENDING
            self.records[key].full_text_reason = ""
        if not retrieved:
            # "Not retrievable" is itself an exclusion at the eligibility stage.
            self.records[key].full_text_decision = ScreeningDecision.REJECT
            self.records[key].full_text_reason = FULL_TEXT_EXCLUSION_REASONS[0]

    def get_full_text_queue(self) -> list[PRISMARecord]:
        """Records accepted at screening and awaiting full-text assessment."""
        return self.get_screening_queue(ScreeningStage.FULL_TEXT)

    # -- results ---------------------------------------------------------

    def get_included_papers(self) -> list[Paper]:
        """Papers that passed the last completed stage."""
        if self.full_text_stage_enabled:
            return [r.paper for r in self.records.values()
                    if r.passed_screening and r.full_text_decision == ScreeningDecision.ACCEPT]
        return [r.paper for r in self.records.values()
                if r.screening_decision == ScreeningDecision.ACCEPT]

    def get_accepted_ids(self) -> set[str]:
        """IDs of papers that passed the last completed stage."""
        if self.full_text_stage_enabled:
            return {k for k, r in self.records.items()
                    if r.passed_screening and r.full_text_decision == ScreeningDecision.ACCEPT}
        return {k for k, r in self.records.items()
                if r.screening_decision == ScreeningDecision.ACCEPT}

    def generate_report(self) -> PRISMAReport:
        """Generate a PRISMA 2020 flow report from current state."""
        report = PRISMAReport()

        # Count by source
        for r in self.records.values():
            if r.source == "database_search":
                report.database_results += 1
            elif r.source == "snowballing":
                report.snowball_results += 1
            elif r.source == "similar":
                report.similar_results += 1
            elif r.source == "manual":
                report.manual_additions += 1
        if self._id_counter:
            report.database_results = self._id_counter.get("database_search", 0)
            report.snowball_results = self._id_counter.get("snowballing", 0)
            report.similar_results = self._id_counter.get("similar", 0)
            report.manual_additions = self._id_counter.get("manual", 0)

        identified = (
            report.database_results + report.snowball_results
            + report.similar_results + report.manual_additions
        )
        report.records = list(self.records.values())
        report.records_after_dedup = len(self.records)
        report.duplicates_removed = self._duplicates_seen or max(
            0, identified - len(self.records)
        )

        # Stage 2 — title/abstract
        report.records_screened = sum(
            1 for r in self.records.values()
            if r.screening_decision != ScreeningDecision.PENDING
        )
        report.records_excluded_title_abstract = sum(
            1 for r in self.records.values()
            if r.screening_decision == ScreeningDecision.REJECT
        )

        # Stage 3 — full text
        report.full_text_stage_enabled = self.full_text_stage_enabled
        report.reports_sought = sum(
            1 for r in self.records.values() if r.passed_screening
        )
        report.reports_not_retrieved = sum(
            1 for r in self.records.values()
            if r.passed_screening and not r.full_text_retrieved and r.full_text_decision == ScreeningDecision.REJECT
        )
        report.reports_pending_retrieval = sum(1 for r in self.records.values() if r.passed_screening and not r.full_text_retrieved and r.full_text_decision != ScreeningDecision.REJECT)
        report.reports_pending_assessment = sum(1 for r in self.records.values() if r.passed_screening and r.full_text_retrieved and r.full_text_decision in (ScreeningDecision.PENDING, ScreeningDecision.MAYBE))
        report.full_text_assessed = sum(
            1 for r in self.records.values()
            if r.passed_screening and r.full_text_retrieved and r.full_text_decision != ScreeningDecision.PENDING
        )
        report.full_text_excluded = sum(
            1 for r in self.records.values()
            if r.passed_screening and r.full_text_retrieved and r.full_text_decision == ScreeningDecision.REJECT
        )
        reasons: dict[str, int] = defaultdict(int)
        for r in self.records.values():
            if r.passed_screening and r.full_text_retrieved and r.full_text_decision == ScreeningDecision.REJECT:
                reasons[r.full_text_reason or "未说明 / not stated"] += 1
        report.full_text_exclusion_reasons = dict(reasons)

        # Included
        report.studies_included = len(self.get_included_papers())
        return report

    # -- serialisation ---------------------------------------------------

    def to_dict(self) -> dict:
        """Serialise decisions only (papers live in ReviewState)."""
        return {
            "full_text_stage_enabled": self.full_text_stage_enabled,
            "duplicates_seen": self._duplicates_seen,
            "identification_counts": dict(self._id_counter),
            "records": [
                {
                    "key": k,
                    "source": r.source,
                    "screening_decision": r.screening_decision.value,
                    "screening_reason": r.screening_reason,
                    "full_text_retrieved": r.full_text_retrieved,
                    "full_text_decision": r.full_text_decision.value,
                    "full_text_reason": r.full_text_reason,
                    "relevance_score": r.relevance_score,
                }
                for k, r in self.records.items()
            ],
        }
