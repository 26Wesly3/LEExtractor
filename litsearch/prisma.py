"""PRISMA 2020 flow tracking for systematic literature reviews.

Tracks papers through the standard PRISMA 2020 stages, with the two screening
stages kept strictly separate:

1. **Identification** — records found via database search + other methods
2. **Screening** — title/abstract screening (records excluded here are
   `records_excluded_title_abstract`)
3. **Eligibility** — full-text reports sought, retrieved and assessed
   (reports excluded here are `full_text_excluded`, with reasons)
4. **Included** — final set for synthesis

Three properties this module is responsible for (v0.9.1):

* **Identity bridging** — `add_papers` merges *every* existing record an
  incoming paper identifies with, so a DOI record and an S2 record that later
  receive bridging metadata collapse into one entity. Contradicting screening
  decisions found during such a merge are recorded in `record.conflicts` and
  the merged entity is moved to `MAYBE` for human review: nothing is silently
  picked or overwritten.
* **Count ledger** — `record_retrieval_ledger` accounts for the whole process
  from the raw provider returns (provider_raw → cross-source duplicates →
  unique → year/citation filters → ranking truncation → screened →
  preliminary/final included). A ranking truncation is reported as a
  *selection* decision, never as "the database only contained this many".
* **Counting unit** — every count here is **record/report level**. One study
  published as several reports is *not* merged into one study: study-level
  counting is a future capability, not an implemented one.

Backwards compatibility: if no full-text decision has been recorded yet, the
eligibility stage counts as "not run" and `studies_included` falls back to the
title/abstract acceptances (the old behaviour). `PRISMAReport.studies_included`
remains a read-only alias of `final_included`.

Automation guard: `screen_by_calibrated_threshold` is the only automatic
screening entry point and it refuses to run without a usable
`CalibrationRecord` (see `litsearch.filters`), refuses the FULL_TEXT stage
outright, and refuses a queue whose scores come from more than one scoring
batch.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from litsearch.filters import STATUS_CALIBRATED, CalibrationRecord, RelevanceFilter
from litsearch.identifiers import identifier_key, paper_aliases
from litsearch.models import Paper

#: Reviewer string written into the audit trail by automatic screening.
AUTO_REVIEWER = "auto:calibrated_threshold"
#: Reviewer string used when a caller does not say who decided.
UNSPECIFIED_REVIEWER = "unspecified"
#: Counting unit implemented here; see the module docstring.
LEDGER_UNITS = "record"
#: Explanation attached to every truncation count.
TRUNCATION_NOTE = (
    "候选集按相关性排序截断（selection/truncation），不是数据库总量："
    "数据库原始返回见 provider_raw。"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ScreeningDecision(Enum):
    PENDING = "pending"
    ACCEPT = "accept"
    REJECT = "reject"
    MAYBE = "maybe"


class ScreeningStage(Enum):
    """The two PRISMA screening stages."""

    TITLE_ABSTRACT = "title_abstract"
    FULL_TEXT = "full_text"


def _decision_value(decision: ScreeningDecision | str) -> str:
    return decision.value if isinstance(decision, ScreeningDecision) else str(decision)


#: Decisions that express an actual judgement (as opposed to "not yet looked").
DECIDED = (
    ScreeningDecision.ACCEPT,
    ScreeningDecision.REJECT,
    ScreeningDecision.MAYBE,
)


class AutoScreeningRefused(RuntimeError):
    """Automatic screening was asked to run without a usable calibration.

    Raised for a missing/insufficient/invalidated `CalibrationRecord`, for the
    forbidden FULL_TEXT stage, and for a queue mixing scoring batches.
    """


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
class RetrievalLedger:
    """Provider-raw count ledger (contract §9), record/report level.

    Field names are frozen; `units` states the counting unit and `truncation`
    labels a ranking cut as a selection decision. Entries are kept so the
    totals can be audited and a re-recorded retrieval can replace its own
    numbers instead of inflating them.
    """

    provider_raw: int = 0
    cross_source_duplicates: int = 0
    unique_records: int = 0
    filtered_year: int = 0
    filtered_citations: int = 0
    truncated: int = 0
    records_screened: int = 0
    preliminary_included: int = 0
    final_included: int = 0
    units: str = LEDGER_UNITS
    entries: list[dict] = field(default_factory=list)
    truncation: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.truncation:
            self.truncation = {
                "dropped": self.truncated,
                "kind": "selection",
                "applied": self.truncated > 0,
                "note": TRUNCATION_NOTE,
            }

    @property
    def recorded(self) -> bool:
        """True when at least one retrieval was registered from provider raw."""
        return bool(self.entries)

    def add_entry(self, entry: dict) -> None:
        """Append (or replace, when `retrieval_id` repeats) one retrieval."""
        retrieval_id = str(entry.get("retrieval_id", "") or "")
        if retrieval_id:
            self.entries = [
                e for e in self.entries if str(e.get("retrieval_id", "")) != retrieval_id
            ]
        self.entries.append(entry)
        self._recompute()

    def _recompute(self) -> None:
        for name in (
            "provider_raw", "cross_source_duplicates", "unique_records",
            "filtered_year", "filtered_citations", "truncated",
        ):
            setattr(self, name, sum(int(e.get(name, 0) or 0) for e in self.entries))
        self.truncation = {
            "dropped": self.truncated,
            "kind": "selection",
            "applied": self.truncated > 0,
            "note": TRUNCATION_NOTE,
        }

    def to_dict(self) -> dict:
        return {
            "provider_raw": self.provider_raw,
            "cross_source_duplicates": self.cross_source_duplicates,
            "unique_records": self.unique_records,
            "filtered_year": self.filtered_year,
            "filtered_citations": self.filtered_citations,
            "truncated": self.truncated,
            "records_screened": self.records_screened,
            "preliminary_included": self.preliminary_included,
            "final_included": self.final_included,
            "units": self.units,
            "truncation": dict(self.truncation),
            "entries": [dict(e) for e in self.entries],
        }


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
    #: Why retrieval failed, when it did. Kept apart from ``full_text_reason``
    #: (which is a human's exclusion reason) so an acquisition failure is never
    #: reported as an eligibility verdict.
    retrieval_failure_reason: str = ""
    #: True once a retrieval was actually attempted. Separates "nobody has tried
    #: to fetch this yet" from "we tried and could not get it".
    retrieval_attempted: bool = False

    rejection_reason: str = ""   # legacy alias for screening_reason
    relevance_score: float = 0.0
    #: Scoring batch the relevance score came from (mirrors `Paper.score_context_id`).
    score_context_id: str = ""
    #: Set when a merge found contradicting decisions or an unreliable calibration.
    requires_manual_review: bool = False
    #: `{"stage","existing","incoming","at","note"}` entries (contract §9).
    conflicts: list[dict] = field(default_factory=list)
    #: `{"reviewer","timestamp","stage","before","after","reason"}` entries.
    history: list[dict] = field(default_factory=list)

    @property
    def passed_screening(self) -> bool:
        return self.screening_decision == ScreeningDecision.ACCEPT

    @property
    def included(self) -> bool:
        return self.full_text_decision == ScreeningDecision.ACCEPT

    # -- audit helpers ---------------------------------------------------

    def sync_relevance(self) -> None:
        """Mirror the paper's current score and scoring batch onto the record."""
        self.relevance_score = float(self.paper.relevance_score or 0.0)
        self.score_context_id = str(getattr(self.paper, "score_context_id", "") or "")

    def record_change(
        self,
        *,
        stage: ScreeningStage,
        before: ScreeningDecision | str,
        after: ScreeningDecision | str,
        reason: str = "",
        reviewer: str = "",
    ) -> dict:
        """Append one auditable decision change (reviewer/time/stage/before/after/reason)."""
        entry = {
            "reviewer": reviewer or UNSPECIFIED_REVIEWER,
            "timestamp": _now(),
            "stage": stage.value if isinstance(stage, ScreeningStage) else str(stage),
            "before": _decision_value(before),
            "after": _decision_value(after),
            "reason": reason or "",
        }
        self.history.append(entry)
        return entry

    def add_conflict(
        self,
        *,
        stage: ScreeningStage | str,
        existing: ScreeningDecision | str,
        incoming: ScreeningDecision | str,
        note: str,
    ) -> dict:
        """Record a contradiction that needs human review (never silently resolved)."""
        entry = {
            "stage": stage.value if isinstance(stage, ScreeningStage) else str(stage),
            "existing": _decision_value(existing),
            "incoming": _decision_value(incoming),
            "at": _now(),
            "note": note,
        }
        self.conflicts.append(entry)
        self.requires_manual_review = True
        return entry


@dataclass
class PRISMAReport:
    """Complete PRISMA 2020 flow data.

    Counting unit: **record/report level** (`units`). Study-level merging is not
    implemented, so `final_included` counts included records/reports, not
    distinct studies.
    """

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
    records_maybe: int = 0
    records_pending_screening: int = 0

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
    final_included: int = 0

    # Count ledger (contract §9), built from provider raw returns when recorded
    provider_raw: int = 0
    cross_source_duplicates: int = 0
    unique_records: int = 0
    filtered_year: int = 0
    filtered_citations: int = 0
    truncated: int = 0
    preliminary_included: int = 0
    truncation: dict = field(default_factory=dict)
    ledger: dict = field(default_factory=dict)
    #: "recorded_retrieval_ledger" or "inferred_from_tracker" (no raw counts given).
    ledger_basis: str = ""
    #: Counting unit actually implemented (record/report level).
    units: str = LEDGER_UNITS

    @property
    def studies_included(self) -> int:
        """Legacy alias of `final_included` (read-only, kept for old callers)."""
        return self.final_included

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
                "provider_raw": self.provider_raw,
                "cross_source_duplicates": self.cross_source_duplicates,
                "unique_records": self.unique_records,
                "filtered_year": self.filtered_year,
                "filtered_citations": self.filtered_citations,
            },
            "screening": {
                "after_dedup": self.records_after_dedup,
                "screened": self.records_screened,
                "excluded_title_abstract": self.records_excluded_title_abstract,
                "maybe": self.records_maybe,
                "pending": self.records_pending_screening,
                "preliminary_included": self.preliminary_included,
                "truncated": self.truncated,
                "truncation": self.truncation,
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
                "studies": self.final_included,
                "final_included": self.final_included,
            },
            "units": self.units,
            "ledger_basis": self.ledger_basis,
            "ledger": self.ledger,
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
        truncated = ""
        if screen["truncated"]:
            truncated = (
                f"<br/>排序截断 selection: -{screen['truncated']}"
                f"（非数据库总量，原始返回 {ident['provider_raw']}）"
            )

        return f"""```mermaid
flowchart TD
    A["Records identified<br/>Databases: {ident['database_search']}<br/>Citation searching: {ident['citation_searching']}<br/>Other: {ident['other_methods']}<br/><b>Total: {ident['total']}</b>"]
    B["Duplicate records removed<br/><b>n = {ident['duplicates_removed']}</b>"]
    C["Records screened<br/>(title/abstract)<br/><b>n = {screen['screened']}</b>{truncated}"]
    D["Records excluded<br/><b>n = {screen['excluded_title_abstract']}</b>"]
    E["Reports sought for retrieval<br/><b>n = {elig['reports_sought']}</b>"]
    F["Reports not retrieved<br/><b>n = {elig['reports_not_retrieved']}</b>"]
    G["Reports assessed for eligibility<br/><b>n = {elig['full_text_assessed']}</b>"]
{excluded_node}
    I["Studies included in review<br/>(record level; study-level merging not implemented)<br/>{note}<b>n = {incl['studies']}</b>"]

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
        self.ledger = RetrievalLedger()

    # -- registration ---------------------------------------------------

    def add_papers(
        self, papers: list[Paper], source: str = "database_search"
    ) -> int:
        """Add papers to the identification phase. Returns count of new papers.

        Every existing record the incoming paper identifies with is merged into
        one entity: aliases, decisions, history, conflicts and the ledger's
        duplicate count all follow. Merging never silently picks a side — a
        contradiction is recorded in `record.conflicts` and needs review.
        """
        added = 0
        for p in papers:
            self._id_counter[source] += 1
            matches = self._matching_keys(p)
            if not matches:
                key = p.canonical_id
                record = PRISMARecord(paper=p, source=source)
                record.sync_relevance()
                self.records[key] = record
                added += 1
            else:
                key = matches[0]
                survivor = self.records[key]
                self._duplicates_seen += 1
                for other_key in matches[1:]:
                    other = self.records.pop(other_key)
                    self._duplicates_seen += 1
                    self._absorb(survivor, other)
                    self._repoint_aliases(other_key, other.paper, key)
                RelevanceFilter.merge_metadata(survivor.paper, p)
                survivor.sync_relevance()
            self._register_aliases(key, self.records[key].paper)
            self._register_aliases(key, p)
        return added

    def _matching_keys(self, paper: Paper) -> list[str]:
        """Every existing record this paper identifies with, in insertion order."""
        found: list[str] = []

        def note(key: str | None) -> None:
            if key and key in self.records and key not in found:
                found.append(key)

        note(paper.canonical_id)
        for alias in sorted(paper_aliases(paper)):
            note(alias if alias in self.records else None)
            note(self._alt_keys.get(alias))
        raw_id = (paper.id or "").lower()
        note(raw_id if raw_id in self.records else None)
        note(self._alt_keys.get(raw_id))
        note(identifier_key(paper.id or "", paper.source))
        # records registered before the alias index existed have no _alt_keys entry
        doi = (paper.doi or "").lower()
        for key, record in self.records.items():
            candidate_id = (record.paper.id or "").lower()
            if candidate_id and candidate_id in {raw_id, identifier_key(paper.id or "", paper.source)}:
                note(key)
            if doi and (record.paper.doi or "").lower() == doi:
                note(key)
        order = {key: index for index, key in enumerate(self.records)}
        return sorted(found, key=lambda key: order[key])

    def _register_aliases(self, key: str, paper: Paper) -> None:
        for alt in paper_aliases(paper) | {(paper.id or "").lower()}:
            if alt:
                self._alt_keys[alt] = key

    def _repoint_aliases(self, old_key: str, old_paper: Paper, new_key: str) -> None:
        for alias, key in list(self._alt_keys.items()):
            if key == old_key:
                self._alt_keys[alias] = new_key
        for alias in paper_aliases(old_paper) | {(old_paper.id or "").lower()}:
            if alias:
                self._alt_keys[alias] = new_key
        self._alt_keys[old_key.lower()] = new_key

    @staticmethod
    def _merge_stage_decision(
        existing: ScreeningDecision, incoming: ScreeningDecision
    ) -> tuple[ScreeningDecision, bool]:
        """(effective decision, conflicted?) for one stage of a record merge."""
        if existing is incoming:
            return existing, False
        if existing not in DECIDED:
            return incoming, False
        if incoming not in DECIDED:
            return existing, False
        # two reviewers decided differently: never pick one, ask for review
        return ScreeningDecision.MAYBE, True

    def _absorb(self, survivor: PRISMARecord, other: PRISMARecord) -> None:
        """Merge the duplicate record `other` into `survivor` (audited)."""
        RelevanceFilter.merge_metadata(survivor.paper, other.paper)
        if not survivor.source:
            survivor.source = other.source
        for stage, decision_attr, reason_attr in (
            (ScreeningStage.TITLE_ABSTRACT, "screening_decision", "screening_reason"),
            (ScreeningStage.FULL_TEXT, "full_text_decision", "full_text_reason"),
        ):
            before = getattr(survivor, decision_attr)
            incoming = getattr(other, decision_attr)
            effective, conflicted = self._merge_stage_decision(before, incoming)
            if conflicted:
                survivor.add_conflict(
                    stage=stage,
                    existing=before,
                    incoming=incoming,
                    note=(
                        "合并同一文献的多条记录时发现既有筛选决定冲突，已置为"
                        "待复核（maybe），需人工确认后重新决定。"
                    ),
                )
                setattr(survivor, decision_attr, effective)
                survivor.record_change(
                    stage=stage,
                    before=before,
                    after=effective,
                    reason="identity bridge merge found conflicting decisions",
                    reviewer="system:merge",
                )
            if not getattr(survivor, reason_attr):
                setattr(survivor, reason_attr, getattr(other, reason_attr))
        survivor.rejection_reason = survivor.screening_reason
        survivor.full_text_retrieved = survivor.full_text_retrieved or other.full_text_retrieved
        survivor.requires_manual_review = (
            survivor.requires_manual_review or other.requires_manual_review
        )
        survivor.conflicts.extend(dict(c) for c in other.conflicts)
        survivor.history.extend(dict(h) for h in other.history)
        survivor.sync_relevance()

    def sync_relevance_scores(self) -> None:
        """Copy every record's score/context from its paper (keeps the copy honest)."""
        for record in self.records.values():
            record.sync_relevance()

    # -- count ledger ----------------------------------------------------

    def record_retrieval_ledger(
        self,
        *,
        provider_raw: int,
        unique_records: int | None = None,
        cross_source_duplicates: int = 0,
        filtered_year: int = 0,
        filtered_citations: int = 0,
        truncated: int = 0,
        query: str = "",
        sources: dict[str, int] | None = None,
        retrieval_id: str = "",
        stage: str = "systematic_search",
    ) -> RetrievalLedger:
        """Register one provider retrieval from its raw returns.

        Call this where the provider results come back — *before* dedup,
        filtering and truncation — so the process stays reconstructible:
        `provider_raw = unique_records + cross_source_duplicates`, then the
        year/citation filters and finally the ranking truncation.

        `retrieval_id` makes the call idempotent: re-running the same search
        replaces its own numbers instead of inflating them. `unique_records`
        defaults to `provider_raw - cross_source_duplicates`; passing a value
        that breaks the identity raises `ValueError`.

        Counting unit: record/report level (`RetrievalLedger.units`).
        """
        provider_raw = int(provider_raw)
        cross_source_duplicates = int(cross_source_duplicates)
        filtered_year = int(filtered_year)
        filtered_citations = int(filtered_citations)
        truncated = int(truncated)
        if unique_records is None:
            unique_records = max(0, provider_raw - cross_source_duplicates)
        unique_records = int(unique_records)
        if provider_raw - cross_source_duplicates != unique_records:
            raise ValueError(
                "inconsistent retrieval ledger: provider_raw "
                f"({provider_raw}) - cross_source_duplicates ({cross_source_duplicates}) "
                f"!= unique_records ({unique_records})"
            )
        for name, value in (
            ("provider_raw", provider_raw),
            ("cross_source_duplicates", cross_source_duplicates),
            ("unique_records", unique_records),
            ("filtered_year", filtered_year),
            ("filtered_citations", filtered_citations),
            ("truncated", truncated),
        ):
            if value < 0:
                raise ValueError(f"{name} must not be negative")
        self.ledger.add_entry({
            "retrieval_id": retrieval_id,
            "stage": stage,
            "query": query,
            "sources": dict(sources or {}),
            "provider_raw": provider_raw,
            "cross_source_duplicates": cross_source_duplicates,
            "unique_records": unique_records,
            "filtered_year": filtered_year,
            "filtered_citations": filtered_citations,
            "truncated": truncated,
            "recorded_at": _now(),
        })
        return self.ledger

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

    def get_actionable_queue(
        self, stage: ScreeningStage = ScreeningStage.TITLE_ABSTRACT
    ) -> list[PRISMARecord]:
        """Everything still needing a human at `stage`: PENDING **and** MAYBE.

        `get_screening_queue` keeps its old meaning (never looked at). MAYBE is
        a real, actionable state — a reviewer must be able to come back to it,
        so it is never dropped from the workload.
        """
        if stage == ScreeningStage.FULL_TEXT:
            actionable = [
                r for r in self.records.values()
                if r.passed_screening
                and r.full_text_decision in (ScreeningDecision.PENDING, ScreeningDecision.MAYBE)
            ]
        else:
            actionable = [
                r for r in self.records.values()
                if r.screening_decision in (ScreeningDecision.PENDING, ScreeningDecision.MAYBE)
            ]
        actionable.sort(key=lambda r: r.relevance_score, reverse=True)
        return actionable

    def screen_paper(
        self,
        paper_id: str,
        decision: ScreeningDecision,
        reason: str = "",
        stage: ScreeningStage = ScreeningStage.TITLE_ABSTRACT,
        reviewer: str = "",
    ) -> bool:
        """Record a screening decision for one stage.

        Returns True when the recorded state actually changed. Every change is
        appended to `record.history` with reviewer, timestamp, stage, before
        state, after state and reason, so a decision can always be traced and
        undone.
        """
        key = self._find_key(paper_id)
        if not key:
            return False
        record = self.records[key]
        if stage == ScreeningStage.FULL_TEXT:
            self.full_text_stage_enabled = True
            before, before_reason = record.full_text_decision, record.full_text_reason
            if before is decision and before_reason == reason:
                return False
            record.full_text_decision = decision
            record.full_text_reason = reason
            # Legacy callers decide a full-text outcome without separately
            # recording retrieval, so a decision implies the text was in hand.
            # An *explicit* retrieval mark wins over that inference: a human
            # excluding a paper because the full text could not be obtained must
            # not simultaneously erase the fact that retrieval was attempted and
            # failed.
            if (
                decision in (ScreeningDecision.ACCEPT, ScreeningDecision.REJECT)
                and not record.retrieval_attempted
            ):
                record.full_text_retrieved = True
            record.record_change(
                stage=stage, before=before, after=decision, reason=reason, reviewer=reviewer
            )
        else:
            before, before_reason = record.screening_decision, record.screening_reason
            if before is decision and before_reason == reason:
                return False
            record.screening_decision = decision
            record.screening_reason = reason
            record.rejection_reason = reason
            if decision != ScreeningDecision.ACCEPT:
                if (
                    record.full_text_decision is not ScreeningDecision.PENDING
                    or record.full_text_retrieved
                    or record.full_text_reason
                ):
                    record.record_change(
                        stage=ScreeningStage.FULL_TEXT,
                        before=record.full_text_decision,
                        after=ScreeningDecision.PENDING,
                        reason="reset by the title/abstract decision",
                        reviewer=reviewer,
                    )
                record.full_text_decision = ScreeningDecision.PENDING
                record.full_text_retrieved = False
                record.full_text_reason = ""
            record.record_change(
                stage=stage, before=before, after=decision, reason=reason, reviewer=reviewer
            )
        return True

    def screen_batch(
        self,
        decisions: dict[str, ScreeningDecision],
        stage: ScreeningStage = ScreeningStage.TITLE_ABSTRACT,
        reviewer: str = "",
    ) -> None:
        """Batch screening: {paper_id: decision}."""
        for pid, decision in decisions.items():
            self.screen_paper(pid, decision, stage=stage, reviewer=reviewer)

    # -- calibrated automatic assistance ---------------------------------

    @staticmethod
    def _require_usable_calibration(
        calibration: CalibrationRecord | None, *, query: str = "", corpus_hash: str = ""
    ) -> CalibrationRecord:
        if calibration is None:
            raise AutoScreeningRefused(
                "没有标定记录：未标定的相关性分数只能用于排序，不得自动筛选或排除文献。"
            )
        if not isinstance(calibration, CalibrationRecord):
            raise AutoScreeningRefused(
                f"calibration must be a CalibrationRecord, got {type(calibration).__name__}"
            )
        if calibration.status != STATUS_CALIBRATED or calibration.threshold is None:
            raise AutoScreeningRefused(
                f"标定状态为 {calibration.status!r}：单一类别/无标签不构成二分类标定，"
                "不得自动筛选或排除文献。"
            )
        stale = calibration.invalidated_by(
            query=query or None, corpus_hash=corpus_hash or None
        )
        if stale:
            raise AutoScreeningRefused(f"标定已失效，不能复用：{stale}")
        return calibration

    def screen_by_calibrated_threshold(
        self,
        calibration: CalibrationRecord | None,
        *,
        stage: ScreeningStage = ScreeningStage.TITLE_ABSTRACT,
        query: str = "",
        corpus_hash: str = "",
        threshold: float | None = None,
        reviewer: str = AUTO_REVIEWER,
    ) -> list[PRISMARecord]:
        """Apply a *calibrated* threshold to the pending queue (stage 1 only).

        Refuses (raising `AutoScreeningRefused`) when the calibration is
        missing, insufficient, invalidated for this query/corpus, or when the
        full-text stage is requested — full-text inclusion is human-only. It
        also refuses a queue whose scores come from more than one scoring
        batch, because those scores are not comparable.

        Returns the records whose decision changed.
        """
        calibration = self._require_usable_calibration(
            calibration, query=query, corpus_hash=corpus_hash
        )
        if stage == ScreeningStage.FULL_TEXT:
            raise AutoScreeningRefused(
                "全文（FULL_TEXT）纳入决定必须由人工明确评估，不能用阈值自动判定。"
            )
        pending = self.get_screening_queue(stage)
        if not pending:
            return []
        contexts = {r.score_context_id for r in pending}
        calibrated_context = str(calibration.evaluation.get("score_context_id", "") or "")
        if len(contexts) > 1:
            raise AutoScreeningRefused(
                f"待筛队列混用了 {sorted(contexts)} 个评分批次，分数不可直接比较；"
                "请先对同一候选集重新评分。"
            )
        if calibrated_context and calibrated_context not in contexts:
            raise AutoScreeningRefused(
                f"标定所用评分批次是 {calibrated_context!r}，当前队列来自 "
                f"{sorted(contexts)}；阈值不可跨批次复用。"
            )
        cut = float(calibration.threshold if threshold is None else threshold)
        if not 0.0 <= cut <= 1.0:
            raise ValueError("threshold must be within the 0-1 score range")

        decided: list[PRISMARecord] = []
        for record in pending:
            decision = (
                ScreeningDecision.ACCEPT
                if record.relevance_score >= cut
                else ScreeningDecision.REJECT
            )
            if not calibration.reliable:
                # overlapping labels: the boundary is not trustworthy, so every
                # assisted decision stays flagged for a human.
                record.requires_manual_review = True
            self.screen_paper(
                record.paper.id,
                decision,
                reason=(
                    f"标定阈值 {cut:.3f}，分数 {record.relevance_score:.3f}"
                    f"（批次 {record.score_context_id or 'unbatched'}）；自动建议，可人工修改。"
                ),
                stage=stage,
                reviewer=reviewer,
            )
            decided.append(record)
        return decided

    # -- stage 3: full text ---------------------------------------------

    def mark_full_text_retrieved(
        self, paper_id: str, retrieved: bool = True, reviewer: str = ""
    ) -> None:
        """Record whether the full text could actually be obtained.

        A failed retrieval is **not** an eligibility verdict. PRISMA 2020 keeps
        "reports not retrieved" in its own box, before assessment; only a human
        who read the report can decide that it is excluded. v0.9.0 auto-set
        ``full_text_decision = REJECT`` here, which turned "the library could
        not fetch this PDF" into "this study does not qualify" — a judgement
        nobody made.

        So a failed retrieval records the fact, counts in
        ``reports_not_retrieved``, and leaves the decision ``PENDING`` so the
        record stays in the eligibility queue. Excluding it because no full
        text was obtainable remains available as an explicit human decision
        through :meth:`screen_paper`.
        """
        key = self._find_key(paper_id)
        if not key:
            return
        self.full_text_stage_enabled = True
        record = self.records[key]
        was_retrieved = record.full_text_retrieved
        record.retrieval_attempted = True
        record.full_text_retrieved = bool(retrieved)

        if retrieved:
            record.retrieval_failure_reason = ""
            if record.full_text_reason == FULL_TEXT_EXCLUSION_REASONS[0]:
                record.record_change(
                    stage=ScreeningStage.FULL_TEXT,
                    before=record.full_text_decision,
                    after=ScreeningDecision.PENDING,
                    reason="full text became available",
                    reviewer=reviewer,
                )
                record.full_text_decision = ScreeningDecision.PENDING
                record.full_text_reason = ""
            return

        # Not retrieved: leave the eligibility decision to a human, but never
        # silently discard a verdict that a human already recorded.
        record.retrieval_failure_reason = (
            "未获取全文 / full text not retrieved"
            + (" (was retrieved before)" if was_retrieved else "")
        )
        if record.full_text_decision == ScreeningDecision.PENDING:
            record.record_change(
                stage=ScreeningStage.FULL_TEXT,
                before=ScreeningDecision.PENDING,
                after=ScreeningDecision.PENDING,
                reason=record.retrieval_failure_reason + " — awaiting human assessment",
                reviewer=reviewer,
            )

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

    def requires_manual_review(self) -> list[PRISMARecord]:
        """Records a human still has to look at (conflicts, flagged decisions)."""
        return [r for r in self.records.values() if r.requires_manual_review]

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
        report.records_maybe = sum(
            1 for r in self.records.values()
            if r.screening_decision == ScreeningDecision.MAYBE
        )
        report.records_pending_screening = sum(
            1 for r in self.records.values()
            if r.screening_decision == ScreeningDecision.PENDING
        )
        report.preliminary_included = sum(
            1 for r in self.records.values() if r.passed_screening
        )

        # Stage 3 — full text
        report.full_text_stage_enabled = self.full_text_stage_enabled
        report.reports_sought = sum(
            1 for r in self.records.values() if r.passed_screening
        )
        # "Reports not retrieved" is its own PRISMA box: retrieval was attempted
        # and failed. It is deliberately NOT "no decision yet" — a record nobody
        # has tried to fetch belongs in reports_pending_retrieval, and v0.9.0
        # got both wrong by counting only what it had itself auto-rejected.
        report.reports_not_retrieved = sum(
            1 for r in self.records.values()
            if r.passed_screening and not r.full_text_retrieved
            and r.retrieval_attempted
        )
        # Never fetched, or fetching is still outstanding: not yet a retrieval
        # outcome at all.
        report.reports_pending_retrieval = sum(
            1 for r in self.records.values()
            if r.passed_screening and not r.full_text_retrieved
            and not r.retrieval_attempted
        )
        # Assessment counts follow the *human decision*, not the retrieval flag.
        # A reviewer who could not obtain the full text and therefore excluded
        # the report has assessed it; the retrieval outcome is reported beside
        # it in reports_not_retrieved.
        report.reports_pending_assessment = sum(
            1 for r in self.records.values()
            if r.passed_screening and r.full_text_retrieved
            and r.full_text_decision in (ScreeningDecision.PENDING, ScreeningDecision.MAYBE)
        )
        report.full_text_assessed = sum(
            1 for r in self.records.values()
            if r.passed_screening
            and r.full_text_decision != ScreeningDecision.PENDING
        )
        report.full_text_excluded = sum(
            1 for r in self.records.values()
            if r.passed_screening and r.full_text_decision == ScreeningDecision.REJECT
        )
        reasons: dict[str, int] = defaultdict(int)
        for r in self.records.values():
            if r.passed_screening and r.full_text_decision == ScreeningDecision.REJECT:
                reasons[r.full_text_reason or "未说明 / not stated"] += 1
        report.full_text_exclusion_reasons = dict(reasons)

        # Included (final stage)
        report.final_included = len(self.get_included_papers())

        # Count ledger — recorded from provider raw returns when available,
        # otherwise explicitly marked as inferred from the tracker state.
        ledger = self.ledger
        report.provider_raw = ledger.provider_raw if ledger.recorded else identified
        report.cross_source_duplicates = (
            ledger.cross_source_duplicates if ledger.recorded else report.duplicates_removed
        )
        report.unique_records = ledger.unique_records if ledger.recorded else len(self.records)
        report.filtered_year = ledger.filtered_year
        report.filtered_citations = ledger.filtered_citations
        report.truncated = ledger.truncated
        report.truncation = dict(ledger.truncation)
        report.ledger_basis = (
            "recorded_retrieval_ledger" if ledger.recorded else "inferred_from_tracker"
        )
        snapshot = ledger.to_dict()
        snapshot["records_screened"] = report.records_screened
        snapshot["preliminary_included"] = report.preliminary_included
        snapshot["final_included"] = report.final_included
        report.ledger = snapshot
        report.units = LEDGER_UNITS
        return report

    # -- serialisation ---------------------------------------------------

    def to_dict(self) -> dict:
        """Serialise decisions only (papers live in ReviewState)."""
        return {
            "full_text_stage_enabled": self.full_text_stage_enabled,
            "duplicates_seen": self._duplicates_seen,
            "identification_counts": dict(self._id_counter),
            "ledger": self.ledger.to_dict(),
            "records": [
                {
                    "key": k,
                    "source": r.source,
                    "screening_decision": r.screening_decision.value,
                    "screening_reason": r.screening_reason,
                    "full_text_retrieved": r.full_text_retrieved,
                    "full_text_decision": r.full_text_decision.value,
                    "full_text_reason": r.full_text_reason,
                    "retrieval_attempted": r.retrieval_attempted,
                    "retrieval_failure_reason": r.retrieval_failure_reason,
                    "relevance_score": r.relevance_score,
                    "score_context_id": r.score_context_id,
                    "requires_manual_review": r.requires_manual_review,
                    "conflicts": [dict(c) for c in r.conflicts],
                    "history": [dict(h) for h in r.history],
                }
                for k, r in self.records.items()
            ],
        }
