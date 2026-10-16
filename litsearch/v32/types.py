"""Closed enumerations and shared numeric helpers for the v3.2 layer.

The enumerations are the ``enum`` arrays of ``contracts/domain.schema.json``
and ``contracts/job_states.json`` transcribed once.  Every module in
``litsearch.v32`` imports them from here instead of re-typing string literals,
so a schema change lands in exactly one place.

:func:`round_tie` is the single implementation of the specification's
"判断并列前将浮点值按 12 位小数 round" rule (doc 04).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from enum import Enum
from typing import Any

__all__ = [
    "JobState",
    "JobStage",
    "ImportStatus",
    "JudgementValue",
    "ExclusionReason",
    "Role",
    "RoleOrigin",
    "Eligibility",
    "Signal",
    "RankingStage",
    "CoverageStatus",
    "CapabilityStatus",
    "Availability",
    "AccessKind",
    "AccessGranularity",
    "EntryMode",
    "MetricType",
    "VerificationStatus",
    "StatementType",
    "VerificationStatusLead",
    "TIE_DECIMALS",
    "round_tie",
    "utc_now",
    "iso_now",
    "safe_float",
]


class _StrEnum(str, Enum):
    """``str``-valued enum that serialises to its bare value in JSON."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)


class JobState(_StrEnum):
    """``contracts/job_states.json`` transitions keys."""

    QUEUED = "queued"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"
    INTERRUPTED = "interrupted"


class JobStage(_StrEnum):
    QUEUED = "queued"
    PARSING = "parsing"
    MATCHING = "matching"
    RETRIEVING = "retrieving"
    FAST_RANKING = "fast_ranking"
    ENHANCING = "enhancing"
    MAPPING = "mapping"
    EXPORTING = "exporting"
    FINISHED = "finished"


class ImportStatus(_StrEnum):
    UPLOADED = "uploaded"
    PARSING = "parsing"
    MATCHING = "matching"
    MATCHED = "matched"
    NEEDS_CONFIRMATION = "needs_confirmation"
    LOCAL_SEED = "local_seed"
    NEEDS_OCR = "needs_ocr"
    ENCRYPTED_PDF = "encrypted_pdf"
    INVALID_PDF = "invalid_pdf"
    FAILED = "failed"


#: Import statuses that no longer change without user action.
IMPORT_TERMINAL = frozenset(
    {
        ImportStatus.MATCHED,
        ImportStatus.NEEDS_CONFIRMATION,
        ImportStatus.LOCAL_SEED,
        ImportStatus.NEEDS_OCR,
        ImportStatus.ENCRYPTED_PDF,
        ImportStatus.INVALID_PDF,
        ImportStatus.FAILED,
    }
)

#: Statuses whose item may be retried by id (doc 16: "重试只针对 retryable 项").
IMPORT_RETRYABLE = frozenset(
    {
        ImportStatus.NEEDS_OCR,
        ImportStatus.NEEDS_CONFIRMATION,
        ImportStatus.FAILED,
    }
)


class JudgementValue(_StrEnum):
    UNJUDGED = "unjudged"
    CORE = "core"
    RELEVANT = "relevant"
    EXCLUDED = "excluded"


class ExclusionReason(_StrEnum):
    UNSPECIFIED = "unspecified"
    NOT_RELEVANT = "not_relevant"
    READ = "read"
    DUPLICATE = "duplicate"
    NOT_NOW = "not_now"


#: Only ``not_relevant`` may act as negative feedback (doc 02, decisions D03).
NEGATIVE_FEEDBACK_REASONS = frozenset({ExclusionReason.NOT_RELEVANT})


class Role(_StrEnum):
    FOUNDATIONAL = "foundational"
    REPRESENTATIVE = "representative"
    REVIEW = "review"
    METHODOLOGY = "methodology"
    FRONTIER = "frontier"
    APPLICATION = "application"
    BACKGROUND = "background"
    PERIPHERAL = "peripheral"
    UNKNOWN = "unknown"


class RoleOrigin(_StrEnum):
    METADATA = "metadata"
    RULE = "rule"
    MODEL_INFERENCE = "model_inference"
    UNKNOWN = "unknown"


class Eligibility(_StrEnum):
    RECOMMENDED = "recommended"
    EXPLORATION = "exploration"
    EXCLUDED = "excluded"


class Signal(_StrEnum):
    TOPIC = "topic"
    IMPACT = "impact"
    GRAPH = "graph"
    SEED = "seed"
    FEEDBACK = "feedback"


class RankingStage(_StrEnum):
    FAST = "fast"
    LOCAL_ENHANCED = "local_enhanced"
    AI_REFINED = "ai_refined"


class CoverageStatus(_StrEnum):
    WITHIN_BUDGET = "within_budget"
    PARTIAL = "partial"
    NO_RESULTS = "no_results"
    FAILED = "failed"


class CapabilityStatus(_StrEnum):
    AVAILABLE = "available"
    NOT_CONFIGURED = "not_configured"
    UNAVAILABLE = "unavailable"
    EXTERNAL_BLOCKED = "external_blocked"


class Availability(_StrEnum):
    UNKNOWN = "unknown"
    PUBLIC = "public"
    REQUIRES_INSTITUTION = "requires_institution"
    ENTRY_ONLY = "entry_only"
    UNAVAILABLE = "unavailable"


class AccessKind(_StrEnum):
    METADATA_API = "metadata_api"
    INSTITUTION_LINK = "institution_link"
    FULLTEXT_LINK = "fulltext_link"
    RESOLVER = "resolver"


class AccessGranularity(_StrEnum):
    ARTICLE = "article"
    PORTAL = "portal"
    DATABASE_GUIDE = "database_guide"


class EntryMode(_StrEnum):
    TOPIC = "topic"
    PDF = "pdf"
    MIXED = "mixed"


class MetricType(_StrEnum):
    OFFICIAL_JIF = "official_jif"
    JCR_QUARTILE = "jcr_quartile"
    CAS_PARTITION = "cas_partition"
    CCF_RANK = "ccf_rank"
    CITATION_PROXY = "citation_proxy"
    OPENALEX_H_INDEX = "openalex_h_index"


class VerificationStatus(_StrEnum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    UNAVAILABLE = "unavailable"


class StatementType(_StrEnum):
    FACT = "fact"
    INFERENCE = "inference"
    HYPOTHESIS = "hypothesis"


class VerificationStatusLead(_StrEnum):
    CHECKED_IN_CURRENT_SOURCES = "checked_in_current_sources"
    NEEDS_VERIFICATION = "needs_verification"


#: Doc 04: "判断并列前将浮点值按 12 位小数 round".
TIE_DECIMALS = 12


def round_tie(value: float | None) -> float | None:
    """Round a comparable score to the contract's tie precision.

    ``None`` stays ``None``; a non-finite input is not a legal score and is
    normalised to ``None`` so it can never reach JSON as ``NaN`` (doc 09/AC-07:
    "null 与 0 分开 ... 无 NaN").
    """
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return round(number, TIE_DECIMALS)


def utc_now() -> datetime:
    """Timezone-aware UTC now; the only clock the v3.2 layer may read."""
    return datetime.now(timezone.utc)


def iso_now() -> str:
    """RFC3339 UTC timestamp with a ``Z``-compatible offset."""
    return utc_now().isoformat()


def safe_float(value: Any) -> float | None:
    """Coerce to a finite float, or ``None``.  Never returns NaN or infinity.

    Contract rule: an unknown number is ``null``, never ``0`` — callers decide
    which of the two a missing value means, this helper only refuses to invent
    a non-finite one.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
