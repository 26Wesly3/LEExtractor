"""One retrieval, one result object — including *why* it ended.

Every provider used to answer with ``list[Paper]``. Completeness therefore
travelled through side state (``last_retrieval_meta``, the diagnostics log, a
cache envelope), and a consumer holding ``[]`` could not tell two very
different outcomes apart:

* the provider failed / was rate limited / timed out / was cancelled, and
* the provider answered normally and genuinely holds zero records.

Those two must never be the same value. The first means "this run is
incomplete and resumable"; the second is the only answer that may be reported
as coverage. Reporting the first as the second turns "my search failed" into
"the literature contains nothing more" — a false statement about the evidence
base, which is exactly what v0.9.1's ``stop_reasons`` module was created to
prevent at the run level and what this module prevents at the provider level.

The result object is deliberately small and dependency-free: a status, the
papers that *were* retrieved, completeness, an optional resume cursor and real
request statistics. ``papers`` is never cleared by a failure — a partial result
is still evidence, it is just not a complete one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from litsearch.models import Paper
from litsearch.stop_reasons import StopReason


class RetrievalStatus(str, Enum):
    """How one logical retrieval (search, references or citations) ended."""

    SUCCESS = "success"              # 正常完成
    SUCCESS_EMPTY = "success_empty"  # 正常完成且确实 0 结果
    TRUNCATED = "truncated"          # 按调用方 limit 正常截断；可用但不完整
    PARTIAL = "partial"              # 因失败/超时等只拿到一部分
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    CANCELED = "canceled"
    BUDGET_EXHAUSTED = "budget_exhausted"
    PROVIDER_ERROR = "provider_error"


#: Statuses a consumer may act on without treating the run as broken.
OK_STATUSES = frozenset({
    RetrievalStatus.SUCCESS,
    RetrievalStatus.SUCCESS_EMPTY,
    RetrievalStatus.TRUNCATED,
})

#: Statuses that describe a finished, trustworthy retrieval.
COMPLETE_STATUSES = frozenset({
    RetrievalStatus.SUCCESS,
    RetrievalStatus.SUCCESS_EMPTY,
})

#: ``RetrievalStatus`` -> the frozen run-level :class:`StopReason`.
#:
#: ``TRUNCATED`` and ``PARTIAL`` are deliberately separate. ``TRUNCATED``
#: means the provider behaved normally but the caller's record limit stopped
#: the sequence; ``PARTIAL`` means an abnormal ending after some evidence had
#: already arrived. They are both incomplete, but only the latter is a failure.
STATUS_TO_STOP_REASON: dict[RetrievalStatus, StopReason] = {
    RetrievalStatus.SUCCESS: StopReason.SATURATED,
    RetrievalStatus.SUCCESS_EMPTY: StopReason.NO_NEW_RESULTS,
    RetrievalStatus.TRUNCATED: StopReason.TRUNCATED,
    RetrievalStatus.PARTIAL: StopReason.API_FAILURE,
    RetrievalStatus.RATE_LIMITED: StopReason.API_FAILURE,
    RetrievalStatus.TIMEOUT: StopReason.API_FAILURE,
    RetrievalStatus.CANCELED: StopReason.CANCELED,
    RetrievalStatus.BUDGET_EXHAUSTED: StopReason.BUDGET_EXHAUSTED,
    RetrievalStatus.PROVIDER_ERROR: StopReason.API_FAILURE,
}


def coerce_status(value) -> RetrievalStatus:
    """Coerce a str/RetrievalStatus/None into a :class:`RetrievalStatus`.

    Unknown text becomes :attr:`RetrievalStatus.PROVIDER_ERROR`, the same safe
    reading :func:`litsearch.stop_reasons.normalize_stop_reason` uses: "we do
    not know how this ended" must never be read as a completed search.
    """
    if isinstance(value, RetrievalStatus):
        return value
    if not value:
        return RetrievalStatus.PROVIDER_ERROR
    try:
        return RetrievalStatus(str(value))
    except ValueError:
        return RetrievalStatus.PROVIDER_ERROR


def stop_reason_for(status) -> StopReason:
    """Run-level :class:`StopReason` implied by a retrieval status."""
    return STATUS_TO_STOP_REASON[coerce_status(status)]


@dataclass
class RequestStats:
    """What one logical retrieval actually cost.

    ``requests`` counts calls that reached the transport — retries and page
    requests included — because "one method call" is not a request: it may page,
    retry and fall back many times.
    """

    requests: int = 0
    retries: int = 0
    cache_hits: int = 0
    rate_limited: int = 0
    errors: int = 0
    elapsed_seconds: float = 0.0
    pages: int = 0

    def to_dict(self) -> dict:
        return {
            "requests": int(self.requests),
            "retries": int(self.retries),
            "cache_hits": int(self.cache_hits),
            "rate_limited": int(self.rate_limited),
            "errors": int(self.errors),
            "elapsed_seconds": float(self.elapsed_seconds),
            "pages": int(self.pages),
        }


@dataclass
class RetrievalResult:
    """The outcome of one logical retrieval, complete with its status.

    Consumers must branch on :attr:`ok` / :attr:`genuine_empty` /
    :attr:`failed` (or on ``status``) instead of on ``len(papers)``: an empty
    list is not a result, it is a question.
    """

    papers: list[Paper] = field(default_factory=list)
    provider: str = ""
    status: RetrievalStatus = RetrievalStatus.SUCCESS
    complete: bool = True
    error: str | None = None
    next_cursor: str | None = None
    request_stats: RequestStats = field(default_factory=RequestStats)
    truncated: bool = False

    def __post_init__(self) -> None:
        """Enforce the §1 invariants on every construction path.

        Making these structural (rather than a convention every call site has
        to remember) is what keeps ``SUCCESS_EMPTY`` honest: it can only exist
        for a retrieval that finished, returned nothing and reported no error.
        """
        self.status = coerce_status(self.status)
        self.papers = list(self.papers)
        if self.error is not None:
            self.error = str(self.error)

        if self.status is RetrievalStatus.SUCCESS:
            if self.error:
                # "completed" plus an error is a contradiction; believe the error.
                self.status = RetrievalStatus.PROVIDER_ERROR
            elif not self.complete:
                self.status = RetrievalStatus.PARTIAL
            elif not self.papers:
                # Contract §1: an empty answer that finished without error *is*
                # a genuine empty. Leaving it as SUCCESS would make the two
                # indistinguishable again at the consumer's end.
                self.status = RetrievalStatus.SUCCESS_EMPTY
        elif self.status is RetrievalStatus.SUCCESS_EMPTY:
            if self.papers:
                self.status = RetrievalStatus.SUCCESS
            elif self.error or not self.complete:
                self.status = (
                    RetrievalStatus.PROVIDER_ERROR if self.error
                    else RetrievalStatus.PARTIAL
                )

        if self.status in {RetrievalStatus.PARTIAL, RetrievalStatus.TRUNCATED}:
            self.complete = False
        elif self.status not in OK_STATUSES:
            # An explicit failure is never a complete retrieval, whatever the
            # caller passed in: that inversion is the reported defect.
            self.complete = False

    # -- convenience verdicts (consumers use these, not hand-built conditions)

    @property
    def ok(self) -> bool:
        """True when the retrieval produced a usable answer."""
        return self.status in OK_STATUSES and self.error is None

    @property
    def genuine_empty(self) -> bool:
        """True only for a finished retrieval that really holds zero results."""
        return (
            self.status is RetrievalStatus.SUCCESS_EMPTY
            and not self.papers
            and self.complete
            and self.error is None
        )

    @property
    def failed(self) -> bool:
        """True when the logical retrieval did not finish normally.

        ``PARTIAL`` may still carry useful papers, but it is nevertheless a
        failed/incomplete retrieval and must never be interpreted as coverage.
        Consumers preserve ``papers`` independently of this verdict.
        ``TRUNCATED`` is different: it stopped intentionally at the caller's
        limit, so it is usable (``ok``) while still ``complete=False``.
        """
        return not self.ok

    def to_dict(self) -> dict:
        """JSON-serialisable view for manifests and the PRISMA ledger."""
        return {
            "provider": self.provider,
            "status": self.status.value,
            "ok": self.ok,
            "failed": self.failed,
            "genuine_empty": self.genuine_empty,
            "complete": bool(self.complete),
            "truncated": bool(self.truncated),
            "error": self.error,
            "next_cursor": self.next_cursor,
            "paper_count": len(self.papers),
            "paper_ids": [p.canonical_id for p in self.papers],
            "request_stats": self.request_stats.to_dict(),
            "stop_reason": stop_reason_for(self.status).value,
        }
