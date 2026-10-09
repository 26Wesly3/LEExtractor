"""Why a retrieval run stopped, and how many HTTP requests it really made.

Two things this module exists to prevent.

**Stop reasons.** v0.9.0 reported a single ``saturated`` boolean. Every kind of
ending collapsed into it, so a run that died on a rate limit, hit the round cap
or exhausted its API budget looked exactly like a run that had genuinely
searched the literature to exhaustion. That is not a cosmetic problem: it turns
"my search failed" into "the field contains nothing more", which is a false
statement about the literature. Each ending now has its own name, and only a
real, completed search may claim coverage.

**HTTP budget.** The GUI previously explained cost as "papers × rounds = HTTP
requests". That identity is simply false: one ``SourceManager`` call pages,
retries, resolves identifiers and falls back, so it may issue one request or
twenty. Counting is therefore done in the transport layer, where a request is a
request, instead of at the call site.
"""

from __future__ import annotations

import threading
import time
from enum import Enum


class StopReason(str, Enum):
    """Mutually exclusive reasons a retrieval run ended.

    Only :attr:`SATURATED` and :attr:`NO_NEW_RESULTS` describe a *completed*
    search. Everything else means the run is incomplete and resumable, so it
    must never be reported as coverage.
    """

    SATURATED = "saturated"
    """Every source answered normally and no further new papers were found."""

    NO_NEW_RESULTS = "no_new_results"
    """Every source answered normally with an empty result set."""

    LOW_YIELD = "low_yield"
    """Fewer than the product floor of new papers.

    A heuristic stop condition, **not** proof of coverage: the field may be
    large and the query bad. Reported separately so it never reads as
    saturation.
    """

    MAX_ROUNDS = "max_rounds"
    """The configured round cap was reached."""

    TRUNCATED = "truncated"
    """A provider stopped normally at the caller's requested record limit.

    The returned prefix is usable, but it is not evidence that the provider was
    exhausted; therefore it is incomplete for coverage claims without being an
    API failure.
    """

    BUDGET_EXHAUSTED = "budget_exhausted"
    """The API request budget ran out before retrieval finished."""

    CANCELED = "canceled"
    """The user cancelled."""

    API_FAILURE = "api_failure"
    """A transport, rate-limit or parse failure ended the run early."""
    LOCAL_MODEL_FAILURE = "local_model_failure"


#: Reasons after which the run must be treated as incomplete and resumable.
INCOMPLETE_REASONS = frozenset({
    StopReason.MAX_ROUNDS,
    StopReason.TRUNCATED,
    StopReason.BUDGET_EXHAUSTED,
    StopReason.CANCELED,
    StopReason.API_FAILURE,
    StopReason.LOCAL_MODEL_FAILURE,
})

#: Reasons that allow a claim of real (completed) coverage.
COMPLETE_REASONS = frozenset({
    StopReason.SATURATED,
    StopReason.NO_NEW_RESULTS,
})

#: Below this many new papers a round is a low-yield stop, not saturation.
LOW_YIELD_THRESHOLD = 5

_REASON_LABELS = {
    StopReason.SATURATED: "检索饱和：所有来源正常返回且无新增 / saturated on all sources",
    StopReason.NO_NEW_RESULTS: "所有来源正常返回空结果 / all sources returned empty",
    StopReason.LOW_YIELD: "新增不足 5 篇（产品启发式，非覆盖证明）/ low yield, not coverage",
    StopReason.MAX_ROUNDS: "达到轮数上限 / round cap reached",
    StopReason.TRUNCATED: "达到调用方记录上限（结果可用但不完整）/ caller limit reached",
    StopReason.BUDGET_EXHAUSTED: "API 请求预算耗尽 / API budget exhausted",
    StopReason.CANCELED: "用户取消 / canceled by user",
    StopReason.API_FAILURE: "来源请求失败导致提前结束 / ended by source failure",
    StopReason.LOCAL_MODEL_FAILURE: "本地模型未加载完成 / local model unavailable",
}


def normalize_stop_reason(value) -> StopReason:
    """Coerce a str/StopReason/None into a :class:`StopReason`.

    Unknown text maps to :attr:`StopReason.API_FAILURE`, because the safe
    reading of "we do not know why this stopped" is "assume incomplete".
    """
    if isinstance(value, StopReason):
        return value
    if not value:
        return StopReason.API_FAILURE
    try:
        return StopReason(str(value))
    except ValueError:
        return StopReason.API_FAILURE


def is_complete(value) -> bool:
    """True only when the reason permits claiming a completed search."""
    return normalize_stop_reason(value) in COMPLETE_REASONS


def reason_label(value) -> str:
    """Bilingual one-liner for the GUI and manifests."""
    return _REASON_LABELS[normalize_stop_reason(value)]


def classify_failure(exc: BaseException) -> StopReason:
    """Which stop reason a given failure implies.

    Deliberately implemented without importing :mod:`litsearch.sources` (which
    imports this module) to keep the dependency one-way: every exception that
    ends a retrieval early — transport, rate limit, contract or parse — means
    the same thing for reporting purposes, namely "this run is incomplete".
    """
    return StopReason.API_FAILURE


# ---------------------------------------------------------------------------
# Real HTTP accounting
# ---------------------------------------------------------------------------


class HttpBudget:
    """Thread-safe counter of actual HTTP calls, retries and cache hits.

    "Request" here means one call that reached the transport — i.e. one entry
    in a log a network engineer would recognise. Retries are counted on top of
    the request they retried, so ``requests`` is the honest total.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.requests = 0
            self.retries = 0
            self.cache_hits = 0
            self.rate_limited = 0
            self.errors = 0
            self.canceled = False
            self._by_source: dict[str, dict[str, int]] = {}
            self._started_at = time.time()

    def _bucket(self, source: str) -> dict[str, int]:
        return self._by_source.setdefault(
            source or "unknown", {"requests": 0, "retries": 0, "rate_limited": 0, "errors": 0}
        )

    def note_request(self, source: str, status: int | None = None) -> None:
        """Count one attempted request.

        ``requests`` is the number of times we went to the transport, whether
        or not it answered. A call that raised still cost a request (and, if it
        was a timeout, still cost the user time), so counting only successes
        would under-report the budget the spec asks us to expose. ``errors`` is
        the subset that failed, so ``requests`` is always at least ``errors``.
        """
        with self._lock:
            self.requests += 1
            bucket = self._bucket(source)
            bucket["requests"] += 1
            if status == 429:
                self.rate_limited += 1
                bucket["rate_limited"] += 1

    def note_retry(self, source: str) -> None:
        """Count one retry *in addition to* the request it retried."""
        with self._lock:
            self.retries += 1
            self._bucket(source)["retries"] += 1

    def note_error(self, source: str) -> None:
        """Count one attempt that raised, and the request it consumed."""
        with self._lock:
            self.errors += 1
            self.requests += 1
            bucket = self._bucket(source)
            bucket["errors"] += 1
            bucket["requests"] += 1

    def note_cache_hit(self, source: str) -> None:
        with self._lock:
            self.cache_hits += 1
            self._bucket(source)  # ensure the source appears in the breakdown

    def note_canceled(self) -> None:
        with self._lock:
            self.canceled = True

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "requests": self.requests,
                "retries": self.retries,
                "cache_hits": self.cache_hits,
                "rate_limited": self.rate_limited,
                "errors": self.errors,
                "canceled": self.canceled,
                "elapsed_seconds": round(time.time() - self._started_at, 3),
                "by_source": {k: dict(v) for k, v in sorted(self._by_source.items())},
            }


_BUDGET = HttpBudget()


def get_http_budget() -> HttpBudget:
    """The process-wide HTTP budget counter."""
    return _BUDGET


def reset_http_budget() -> None:
    """Start a fresh accounting window (called at the start of a run)."""
    _BUDGET.reset()


def http_budget_snapshot() -> dict:
    """Current counters, safe to serialise into a manifest."""
    return _BUDGET.snapshot()


def note_cache_hit(source: str) -> None:
    _BUDGET.note_cache_hit(source)


def note_canceled() -> None:
    _BUDGET.note_canceled()


class CountingSession:
    """A ``requests.Session`` wrapper that counts what actually goes out.

    Counting happens at exactly one funnel, :meth:`request`, and nothing else
    is shadowed. That matters for two reasons:

    * The offline test-suite blocks the network by monkeypatching
      ``requests.Session.request``. A subclass that overrode ``request`` would
      route around that guard, so this is a delegating wrapper.
    * Tests routinely replace a *verb* on the session (``source._session.get =
      Mock(...)``). Defining ``get`` here would shadow such a replacement and
      send the call to the real transport instead. So the wrapper defines no
      verbs: ``.get``/``.post`` resolve through :meth:`__getattr__` to whatever
      the wrapped session currently has, including a later assignment.
    """

    def __init__(self, session, source: str = ""):
        object.__setattr__(self, "_session", session)
        object.__setattr__(self, "_source", source)
        object.__setattr__(self, "_budget", _BUDGET)

    def __setattr__(self, name, value):
        """Forward public attribute writes to the wrapped session.

        Without this, ``session.auth = handler`` and
        ``session.get = Mock(...)`` would land on the wrapper and never reach
        the object that performs the request — so OpenAlex lost its Bearer
        auth header and test doubles were bypassed in favour of the real
        network. A wrapper must not silently swallow configuration.
        """
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            setattr(object.__getattribute__(self, "_session"), name, value)

    # -- counting -------------------------------------------------------

    def request(self, method: str, url: str, **kwargs):
        """Send one request, counting the attempt (success or failure).

        Only reached when the wrapped session has no shimmed verb; sources that
        must also count replaced verbs do their accounting in
        ``BaseSource._transport_request``. Both paths feed the same budget
        object, and neither counts an attempt twice.
        """
        try:
            resp = self._dispatch(method, url, **kwargs)
        except BaseException:
            self._budget.note_error(self._source)
            raise
        self._budget.note_request(self._source, getattr(resp, "status_code", None))
        return resp

    def _dispatch(self, method: str, url: str, **kwargs):
        """Send one call through the wrapped session.

        A real :class:`requests.Session` has ``request``; a lightweight test
        double may only implement the verb helpers, so fall back to those
        rather than failing with an attribute error.
        """
        request = getattr(self._session, "request", None)
        if callable(request):
            return request(method, url, **kwargs)
        verb = getattr(self._session, method.lower(), None)
        if callable(verb):
            return verb(url, **kwargs)
        raise AttributeError(
            f"{type(self._session).__name__} supports neither .request nor .{method.lower()}"
        )

    def note_retry(self) -> None:
        """Call from a retry loop, once per retried attempt."""
        self._budget.note_retry(self._source)

    @property
    def source(self) -> str:
        return self._source

    # -- delegation -----------------------------------------------------

    def __getattr__(self, name):
        # Only consulted when normal lookup (instance dict, then class) misses,
        # so an attribute assigned onto this wrapper always wins.
        try:
            return getattr(object.__getattribute__(self, "_session"), name)
        except AttributeError:
            raise AttributeError(
                f"{type(self).__name__!r} object has no attribute {name!r}"
            ) from None

    def __enter__(self):
        self._session.__enter__()
        return self

    def __exit__(self, *exc):
        return self._session.__exit__(*exc)
