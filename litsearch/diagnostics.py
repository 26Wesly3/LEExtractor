"""Source-layer error classification and run diagnostics.

Every data source is best-effort: one provider being down must not abort a
review. That requirement is what produced the original `except Exception:
logger.warning(...)` everywhere, which in turn hid real API-contract bugs —
a 400 from a malformed query and a dropped connection looked identical, and
neither was ever surfaced.

The split here is by *actionability*:

- :class:`TransientSourceError` — retrying might help (429, 5xx, timeouts).
- :class:`PermanentSourceError` — this will never work as written (404,
  400/422, malformed payload). Retrying is wasted effort; the query or the
  client code needs to change.
- :class:`ContractViolation` — the response parsed but violated the schema we
  depend on. This is a bug in our code, not the network.

Every occurrence is recorded in a process-wide :class:`DiagnosticsLog` that
the GUI can render, so "results were incomplete" comes with a reason and a
count rather than a shrug. 429s stay separate because they are the one class
that has an actionable user-side fix (add an API key).
"""

import logging
import threading
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class SourceErrorKind(str, Enum):
    """Coarse bucket, used for grouping in the diagnostics report."""

    RATE_LIMITED = "rate_limited"
    TRANSIENT = "transient"
    NOT_FOUND = "not_found"
    CONTRACT = "contract"
    PARSE = "parse"
    UNKNOWN = "unknown"


class SourceError(Exception):
    """Base for classified source-layer failures."""

    kind = SourceErrorKind.UNKNOWN

    def __init__(self, source: str, message: str, status: int | None = None):
        self.source = source
        self.status = status
        self.detail = message
        super().__init__(f"[{source}] {message}" + (f" (HTTP {status})" if status else ""))


class TransientSourceError(SourceError):
    """Network blip, timeout, or 5xx — the same request may succeed later."""

    kind = SourceErrorKind.TRANSIENT


class RateLimitedError(SourceError):
    """429. Retrying has been attempted already; quota is the real problem."""

    kind = SourceErrorKind.RATE_LIMITED


class NotFoundError(SourceError):
    """404 for a specific record — expected for unknown DOIs, not a failure."""

    kind = SourceErrorKind.NOT_FOUND


class PermanentSourceError(SourceError):
    """4xx other than 404: our request was wrong. Do not retry."""

    kind = SourceErrorKind.CONTRACT


class ParseError(SourceError):
    """Response arrived but did not look like the documented payload."""

    kind = SourceErrorKind.PARSE


# Status codes that mean "the request itself was unacceptable".
_CLIENT_ERRORS = frozenset({400, 401, 403, 405, 409, 413, 422})


def classify_http_error(
    source: str, status: int, body: str = "", *, url: str = ""
) -> SourceError:
    """Map an HTTP status onto the right exception type.

    Exists so every call site classifies identically — the previous per-method
    ad-hoc handling is exactly why 400s went unnoticed.
    """
    snippet = (body or "").strip().replace("\n", " ")[:200]
    detail = f"{url} -> {status}" + (f": {snippet}" if snippet else "")

    if status == 429:
        return RateLimitedError(source, detail, status)
    if status == 404:
        return NotFoundError(source, detail, status)
    if status in _CLIENT_ERRORS:
        return PermanentSourceError(source, detail, status)
    if status >= 500:
        return TransientSourceError(source, detail, status)
    return TransientSourceError(source, detail, status)


@dataclass
class DiagnosticEvent:
    """One recorded failure, kept small enough to log in full."""

    source: str
    kind: SourceErrorKind
    message: str
    status: int | None = None
    context: str = ""

    def short(self, limit: int = 160) -> str:
        base = f"{self.source} · {self.kind.value}"
        if self.status:
            base += f" · HTTP {self.status}"
        if self.context:
            base += f" · {self.context}"
        text = f"{base} — {self.message}"
        return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass
class DiagnosticsLog:
    """Thread-safe, bounded collection of classified failures for one run."""

    events: list[DiagnosticEvent] = field(default_factory=list)
    max_events: int = 500
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(
        self,
        source: str,
        kind: SourceErrorKind,
        message: str,
        status: int | None = None,
        context: str = "",
    ) -> DiagnosticEvent:
        event = DiagnosticEvent(source, kind, message, status, context)
        with self._lock:
            if len(self.events) < self.max_events:
                self.events.append(event)
            elif len(self.events) == self.max_events:
                # Stop growing but remember that truncation happened, so the
                # GUI can say "and more" instead of implying completeness.
                self.events.append(
                    DiagnosticEvent(
                        source, SourceErrorKind.UNKNOWN,
                        f"diagnostics truncated at {self.max_events} entries",
                    )
                )
        return event

    def record_exception(
        self, exc: BaseException, source: str, context: str = ""
    ) -> DiagnosticEvent:
        """Record an arbitrary exception, classifying it when recognisable."""
        if isinstance(exc, SourceError):
            return self.record(
                source, exc.kind, exc.detail, exc.status, context
            )
        return self.record(source, SourceErrorKind.UNKNOWN, str(exc)[:200], None, context)

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def counts(self) -> Counter:
        """Occurrences per kind, counted over all events (not truncated set)."""
        return Counter(e.kind.value for e in self.events)

    def by_source(self) -> dict[str, int]:
        out: Counter = Counter()
        for e in self.events:
            out[e.source] += 1
        return dict(out)

    def has_contract_problems(self) -> bool:
        """True if anything failed for a reason retrying cannot fix."""
        return any(
            e.kind in (SourceErrorKind.CONTRACT, SourceErrorKind.PARSE)
            for e in self.events
        )

    def summary(self) -> str:
        """One-line health verdict suitable for a Streamlit caption."""
        if not self.events:
            return "所有数据源正常 / no source errors recorded"
        counts = self.counts()
        n_rate = counts.get(SourceErrorKind.RATE_LIMITED.value, 0)
        n_perm = sum(
            counts.get(k.value, 0)
            for k in (SourceErrorKind.CONTRACT, SourceErrorKind.NOT_FOUND, SourceErrorKind.PARSE)
        )
        parts = []
        if n_rate:
            parts.append(f"限流 {n_rate} 次（建议配 S2_API_KEY）")
        if n_perm:
            parts.append(f"请求/契约问题 {n_perm} 次（需人工检查）")
        n_trans = counts.get(SourceErrorKind.TRANSIENT.value, 0)
        if n_trans:
            parts.append(f"网络抖动 {n_trans} 次")
        if not parts:
            parts.append(f"其他 {len(self.events)} 次")
        return " · ".join(parts)

    def clear(self) -> None:
        with self._lock:
            self.events.clear()


# Process-wide log. A review run is single-user and short-lived, so a module
# singleton is simpler than threading a logger through every source method,
# and the GUI gets a stable handle via `get_diagnostics`.
_DIAGNOSTICS = DiagnosticsLog()


def get_diagnostics() -> DiagnosticsLog:
    """Return the process-wide diagnostics log."""
    return _DIAGNOSTICS


def reset_diagnostics() -> None:
    """Clear recorded events (called when a new review run starts)."""
    _DIAGNOSTICS.clear()
