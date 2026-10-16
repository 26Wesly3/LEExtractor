"""The v3.2 error envelope.

``contracts/domain.schema.json#/$defs/Error`` fixes the wire shape::

    {"error": {"code": str, "message": str, "retryable": bool,
               "details": {...}, "request_id": str}}

``details`` is closed (``additionalProperties: false``) and may only carry the
four safe keys the schema declares — never another user's data, never a
credential.  ``docs/08_API_DATA_MODEL_SPEC.md`` names the three 409 cases in
upper case (``REVISION_CONFLICT``, ``CURSOR_STALE``, ``IDEMPOTENCY_CONFLICT``);
this module emits the lower-case spelling the existing HTTP surface already
uses and keeps the specification's spelling as an alias constant, so the two
cannot drift apart silently.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Mapping

__all__ = [
    "V32Error",
    "DETAIL_KEYS",
    "BAD_REQUEST",
    "SESSION_EXPIRED",
    "FORBIDDEN",
    "NOT_FOUND",
    "REVISION_CONFLICT",
    "CURSOR_STALE",
    "IDEMPOTENCY_CONFLICT",
    "PROJECT_BUSY",
    "PAYLOAD_TOO_LARGE",
    "UNSUPPORTED_MEDIA_TYPE",
    "UNPROCESSABLE",
    "RATE_LIMITED",
    "CAPABILITY_UNAVAILABLE",
    "SECRET_IN_PROJECT_DOCUMENT",
    "CONTRACT_ALIASES",
    "new_request_id",
    "error_envelope",
]


#: The only keys ``$defs/Error.error.details`` permits.
DETAIL_KEYS = frozenset({"current_revision", "item_ids", "retry_after_seconds", "capability"})

#: Canonical lower-case codes.  **These are error codes, not HTTP status
#: codes.**  Raise them as ``V32Error(400, BAD_REQUEST, "…")``.
BAD_REQUEST = "bad_request"
SESSION_EXPIRED = "session_expired"
FORBIDDEN = "forbidden"
NOT_FOUND = "not_found"
REVISION_CONFLICT = "revision_conflict"
CURSOR_STALE = "cursor_stale"
IDEMPOTENCY_CONFLICT = "idempotency_conflict"
PROJECT_BUSY = "project_busy"
PAYLOAD_TOO_LARGE = "payload_too_large"
UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
UNPROCESSABLE = "unprocessable"
RATE_LIMITED = "rate_limited"
CAPABILITY_UNAVAILABLE = "capability_unavailable"

#: Raised when a credential-shaped value reaches project persistence.  This is
#: an internal invariant violation, never a client mistake: doc 10 requires that
#: a key never enters the project JSON, and the cheapest way to keep that true is
#: to make writing one impossible rather than to audit every call site.
SECRET_IN_PROJECT_DOCUMENT = "secret_in_project_document"

#: The spelling used by ``docs/08_API_DATA_MODEL_SPEC.md`` for the three 409
#: conflicts, kept so a reader can grep the specification verbatim.
CONTRACT_ALIASES: Mapping[str, str] = {
    REVISION_CONFLICT: "REVISION_CONFLICT",
    CURSOR_STALE: "CURSOR_STALE",
    IDEMPOTENCY_CONFLICT: "IDEMPOTENCY_CONFLICT",
}

#: Sensible default retryability per status, overridable per raise site.
_RETRYABLE_BY_STATUS = {429: True, 503: True, 500: False, 409: False}


def new_request_id() -> str:
    """An opaque id echoed in the envelope and safe to log."""
    return uuid.uuid4().hex


@dataclass
class V32Error(Exception):
    """An error that maps onto the contract envelope.

    Argument order is ``(status, code, message, details, retryable)`` on
    purpose: the overwhelmingly common call site supplies ``details`` (for
    example ``{"current_revision": n}`` for a 409) and almost never supplies
    ``retryable``.  Putting ``retryable`` fourth made ``V32Error(409, code,
    msg, {"current_revision": 1})`` silently mark a conflict *retryable* while
    dropping the detail — so ``retryable`` is last and keyword-only in effect.

    Attributes:
        status: HTTP status code.
        code: Machine-readable code, not a human sentence.
        message: Chinese, user-facing, no stack trace or credential.
        details: Subset of :data:`DETAIL_KEYS`; anything else is dropped so a
            careless call site cannot widen the contract or leak data.
        retryable: Whether repeating the same request could succeed.  ``None``
            means "derive it from the status".
    """

    status: int
    code: str
    message: str
    details: dict[str, Any] | None = None
    retryable: bool | None = None

    def __post_init__(self) -> None:
        # The code constants in this module read like HTTP statuses
        # ("bad_request", "capability_unavailable") and are easy to pass into the
        # `status` slot by mistake. Doing so produces a V32Error whose `.status`
        # is a string, which then reaches the ASGI layer as an invalid status
        # code. Refuse it here, with a message that names the actual mistake.
        if not isinstance(self.status, int) or isinstance(self.status, bool):
            raise TypeError(
                f"V32Error.status must be an int HTTP status, got {self.status!r}; "
                f"{self.status!r} looks like an error *code* — "
                "use V32Error(<int status>, code, message, ...)"
            )
        Exception.__init__(self, self.message)
        if self.retryable is None:
            self.retryable = _RETRYABLE_BY_STATUS.get(self.status, False)
        else:
            self.retryable = bool(self.retryable)
        self.details = sanitize_details(self.details)

    def envelope(self, request_id: str | None = None) -> dict[str, Any]:
        return error_envelope(self, request_id)


def sanitize_details(details: Mapping[str, Any] | None) -> dict[str, Any]:
    """Keep only the schema-permitted detail keys with well-typed values."""
    if not details:
        return {}
    safe: dict[str, Any] = {}
    for key in DETAIL_KEYS:
        if key not in details:
            continue
        value = details[key]
        if key == "current_revision":
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                safe[key] = value
        elif key == "retry_after_seconds":
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                safe[key] = value
        elif key == "item_ids":
            if isinstance(value, (list, tuple)):
                safe[key] = [str(v) for v in value]
        elif key == "capability":
            if isinstance(value, str) and value:
                safe[key] = value
    return safe


def error_envelope(error: V32Error, request_id: str | None = None) -> dict[str, Any]:
    """Render :class:`V32Error` as the contract envelope.

    Every response carries a ``request_id`` even when the caller did not
    supply one, so a user can quote a single id when reporting a failure.
    """
    return {
        "error": {
            "code": error.code,
            "message": error.message,
            "retryable": bool(error.retryable),
            "details": sanitize_details(error.details),
            "request_id": request_id or new_request_id(),
        }
    }
