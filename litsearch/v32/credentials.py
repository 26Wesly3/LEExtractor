"""Session-scoped credentials for the v3.2 layer (doc 10, doc 11).

The specification is unambiguous about the shape of this store:

* a secret is scoped by ``provider_id`` **plus** ``session_id`` (doc 10 "凭据");
* in the public / ``server_basic`` mode it lives **in memory only** and is
  dropped after ``credentials.idle_ttl_seconds`` of inactivity — nothing is
  written to the project file, and a restart simply loses it;
* a secret never appears in the project JSON, an exception message, a log
  line, a URL, a statistic, a model prompt or an export;
* the read surface returns ``configured`` / ``kind`` / ``updated_at`` and
  nothing else (``credentials.return_secret == false``);
* provider ids come from the server allowlist, not from the request.

Only :meth:`CredentialStore.get` hands the raw secret back, and it exists for
the provider adapters that must send it on the wire — never for a DTO.  This
module performs no I/O at all, so a request cannot be smuggled out of it.

This round deliberately does not implement the persistence path: doc 10 allows
it only with an encrypted store plus an external master key ("另行启用"), and
writing a plaintext key to disk is exactly the failure it forbids.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, Callable, Mapping

from litsearch.v32 import contracts
from litsearch.v32.errors import V32Error
from litsearch.v32.types import utc_now

__all__ = [
    "PROVIDER_ALLOWLIST",
    "CredentialStatus",
    "CredentialStore",
    "credential_kinds",
    "redact",
    "redact_text",
    "scrub_error_body",
]

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# The server allowlist
# --------------------------------------------------------------------------

#: ``provider_id -> kind`` for every provider a user may configure.  The ids
#: mirror the discovery provider names (``discovery.provider_priority``) so a
#: configured key and a provider read the same word, plus the single model
#: provider id and ``zotero``.  A provider that is not listed here is rejected
#: outright — the client does not get to name new providers.
PROVIDER_ALLOWLIST: dict[str, str] = {
    "openalex": "retrieval",
    "semantic_scholar": "retrieval",
    "crossref": "retrieval",
    "arxiv": "retrieval",
    "openreview": "retrieval",
    "ai_model": "model",
    "zotero": "zotero",
}


@lru_cache(maxsize=1)
def credential_kinds() -> tuple[str, ...]:
    """The ``kind`` enum, read from the domain schema instead of re-typed."""
    schema = contracts.domain_schema()
    return tuple(schema["$defs"]["CredentialStatus"]["properties"]["kind"]["enum"])


@lru_cache(maxsize=1)
def _secret_max_length() -> int:
    schema = contracts.domain_schema()
    return int(schema["$defs"]["CredentialWrite"]["properties"]["secret"]["maxLength"])


@lru_cache(maxsize=1)
def _idle_ttl_seconds() -> int:
    return int(contracts.credentials_defaults()["idle_ttl_seconds"])


# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------

#: Field names that must never carry a value into a log record or DTO.
SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|access[_-]?key|token|secret|password|passwd|authorization|cookie|bearer)",
    re.I,
)

#: A credential introduced by its own name: ``Authorization: …``,
#: ``api_key=…``, ``"token": "…"``.  The *value* — including a bearer scheme
#: word in front of it — is what gets replaced; masking only the name would
#: leave the secret sitting in the line behind a ``***`` marker.
_SECRET_NAME = (
    r"(?:authorization|proxy-authorization|x-api-key|api[_-]?key|access[_-]?key"
    r"|access[_-]?token|auth[_-]?token|client[_-]?secret|token|secret|password|passwd)"
)
SCHEME_WORD = r"(?:bearer|basic|apikey|token|oauth)\s+"
SECRET_PAIR_RE = re.compile(
    rf"(?i)(?P<name>\b{_SECRET_NAME}\b)(?P<sep>[\"']?\s*[:=]\s*[\"']?)(?:{SCHEME_WORD})?"
    r"(?P<value>[^\s\"',;)}\]]+)"
)

#: A credential introduced only by its scheme (``Bearer <value>``) with no name
#: in front of it.  ``basic`` is deliberately absent here: it is an ordinary
#: English word, and masking whatever follows it would destroy log text rather
#: than protect a secret.
AUTH_SCHEME_RE = re.compile(r"(?i)\b(bearer|apikey|oauth)\s+[A-Za-z0-9._~+/=\-]{8,}")

#: Provider keys of the shapes this project ships (S2, OpenAI-compatible, HF…).
SECRET_VALUE_RE = re.compile(
    r"(?i)\b(?:s2k|sk|hf|ghp|gho|glpat)[-_][A-Za-z0-9_\-]{12,}\b"
)

#: What replaces every credential value.
MASK = "***"

#: Longest free-text fragment that may reach a log line after redaction.
MAX_LOG_TEXT = 400

#: What a third-party response body is replaced with.  A provider error body is
#: uncontrolled input that routinely echoes the request (including its
#: credentials), so the safe summary is "it existed and had this length" —
#: never its content.
_SCRUBBED_BODY = "<第三方响应正文已隐藏>"


def redact_text(text: Any) -> str:
    """Remove credential values from one free-text value.

    Covers the three shapes doc 10 names — an ``Authorization`` header, an
    ``api_key``/``token`` parameter (quoted or not) and a bare scheme token —
    and then any provider-key-shaped run.  Whatever the shape, the *value* is
    removed, never merely the name in front of it.
    """
    if text is None:
        return ""
    value = str(text)
    value = SECRET_PAIR_RE.sub(lambda m: f"{m.group('name')}{m.group('sep')}{MASK}", value)
    value = AUTH_SCHEME_RE.sub(lambda m: f"{m.group(1)} {MASK}", value)
    value = SECRET_VALUE_RE.sub(MASK, value)
    if len(value) > MAX_LOG_TEXT:
        value = value[:MAX_LOG_TEXT] + "…"
    return value


def redact(value: Any) -> Any:
    """Recursively drop credential values from a structure safe to log.

    Mapping keys that name a credential are replaced wholesale; strings are
    passed through :func:`redact_text`; other values are returned unchanged so
    a numeric field keeps its type.
    """
    if isinstance(value, Mapping):
        return {
            key: (MASK if SECRET_KEY_RE.search(str(key)) else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def scrub_error_body(body: Any) -> str:
    """Return the safe stand-in for a third-party error body.

    Doc 10 requires log filtering to cover "第三方错误正文"; a provider body is
    arbitrary text that may echo the API key that produced the error, so it is
    reported as a bounded, content-free summary rather than being filtered
    line by line.  ``len`` is useful for diagnosis and cannot leak a value.
    """
    if body is None:
        return _SCRUBBED_BODY
    if isinstance(body, bytes):
        length = len(body)
    else:
        length = len(str(body))
    return f"{_SCRUBBED_BODY}({length}B)"


# --------------------------------------------------------------------------
# Status DTO
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CredentialStatus:
    """The only thing a user-facing surface may say about a credential.

    Field set is exactly ``$defs/CredentialStatus``: ``provider_id``, ``kind``,
    ``configured``, ``updated_at``.  There is no field that could carry a
    secret, which is what makes the read surface safe-by-construction.
    """

    provider_id: str
    kind: str
    configured: bool
    updated_at: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "kind": self.kind,
            "configured": bool(self.configured),
            "updated_at": self.updated_at,
        }


@dataclass
class _Entry:
    """One stored secret.  ``repr`` is disabled for the value on purpose."""

    secret: str = field(repr=False)
    kind: str
    updated_at: datetime
    last_used: datetime

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return f"_Entry(kind={self.kind!r}, updated_at={self.updated_at.isoformat()!r})"


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------


class CredentialStore:
    """In-memory, per-session credential store.

    Args:
        allowlist: ``provider_id -> kind``.  Anything absent is rejected.
        ttl_seconds: idle lifetime; defaults to
            ``credentials.idle_ttl_seconds`` from the contract.
        persistent: accepted for API compatibility; this round refuses it
            because no encrypted store / external master key is configured
            (doc 10 "另行启用").
        path: reserved for the persistence implementation that is not enabled.
        clock: injectable clock, so a test can age a credential without
            sleeping for an hour.
    """

    def __init__(
        self,
        *,
        allowlist: Mapping[str, str] | None = None,
        ttl_seconds: int | None = None,
        persistent: bool = False,
        path: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if persistent:
            raise V32Error(
                503,
                "credential_persistence_unavailable",
                "本轮凭据仅保存在内存中：持久化需要加密存储与外部主密钥，未启用。",
            )
        self.allowlist: dict[str, str] = dict(allowlist or PROVIDER_ALLOWLIST)
        self.ttl_seconds = int(_idle_ttl_seconds() if ttl_seconds is None else ttl_seconds)
        self.persistent = False
        self.path = path
        self._clock = clock or utc_now
        self._entries: dict[tuple[str, str], _Entry] = {}

    # -- introspection ----------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return f"CredentialStore(providers={len(self.allowlist)}, configured={len(self._entries)})"

    def provider_kind(self, provider_id: str) -> str | None:
        """The kind this store associates with ``provider_id``, else ``None``."""
        return self.allowlist.get(str(provider_id))

    # -- writes -----------------------------------------------------------

    def put(self, session_id: str, provider_id: str, kind: str, secret: str) -> CredentialStatus:
        """Store ``secret`` for one session and provider.

        Raises:
            V32Error: 400 when the provider is not allowlisted, when ``kind``
                disagrees with the allowlist, or when the secret is not a
                usable string.  No message ever contains the secret.
        """
        session = _require_opaque_id(session_id, "session_id")
        provider = _require_opaque_id(provider_id, "provider_id")
        expected = self.allowlist.get(provider)
        if expected is None:
            logger.warning(
                "credential_write_rejected",
                extra={
                    "event": "credential_write_rejected",
                    "component": "credentials",
                    "provider": provider,
                    "reason": "provider_not_allowlisted",
                },
            )
            raise V32Error(400, "provider_not_allowlisted", "该 provider 不在服务器允许清单内。")
        if kind not in credential_kinds() or str(kind) != expected:
            logger.warning(
                "credential_write_rejected",
                extra={
                    "event": "credential_write_rejected",
                    "component": "credentials",
                    "provider": provider,
                    "reason": "kind_mismatch",
                },
            )
            raise V32Error(
                400,
                "credential_kind_mismatch",
                f"provider {provider} 的凭据类型应为 {expected}。",
            )
        if not isinstance(secret, str) or not secret.strip():
            raise V32Error(400, "credential_secret_invalid", "凭据内容不能为空。")
        if len(secret) > _secret_max_length():
            raise V32Error(
                400,
                "credential_secret_too_long",
                f"凭据长度超过上限 {_secret_max_length()}。",
            )

        now = self._clock()
        self._entries[(session, provider)] = _Entry(
            secret=secret, kind=expected, updated_at=now, last_used=now
        )
        logger.info(
            "credential_stored",
            extra={
                "event": "credential_stored",
                "component": "credentials",
                "provider": provider,
            },
        )
        return CredentialStatus(
            provider_id=provider, kind=expected, configured=True, updated_at=now.isoformat()
        )

    def delete(self, session_id: str, provider_id: str) -> None:
        """Forget one credential.  Deleting an absent credential is a no-op."""
        key = (str(session_id), str(provider_id))
        if self._entries.pop(key, None) is not None:
            logger.info(
                "credential_deleted",
                extra={
                    "event": "credential_deleted",
                    "component": "credentials",
                    "provider": str(provider_id),
                },
            )

    def purge_idle(self) -> int:
        """Drop every credential idle for longer than the contract TTL.

        Returns the number of purged entries so a caller can report it without
        ever naming a provider.
        """
        now = self._clock()
        cutoff = now - timedelta(seconds=self.ttl_seconds)
        stale = [key for key, entry in self._entries.items() if entry.last_used <= cutoff]
        for key in stale:
            self._entries.pop(key, None)
        return len(stale)

    # -- reads ------------------------------------------------------------

    def get(self, session_id: str, provider_id: str) -> str | None:
        """Return the raw secret for the adapters, or ``None``.

        This is the only accessor that returns a secret and it is never part
        of an HTTP response; a purged or never-configured credential reads as
        ``None`` so a caller cannot act on a credential the user removed.
        """
        entry = self._live_entry(session_id, provider_id, touch=True)
        return entry.secret if entry is not None else None

    def status(self, session_id: str, provider_id: str) -> CredentialStatus:
        """The safe status of one provider for this session."""
        provider = str(provider_id)
        kind = self.allowlist.get(provider, "retrieval")
        entry = self._live_entry(session_id, provider, touch=False)
        return CredentialStatus(
            provider_id=provider,
            kind=entry.kind if entry is not None else kind,
            configured=entry is not None,
            updated_at=entry.updated_at.isoformat() if entry is not None else None,
        )

    def statuses(self, session_id: str) -> list[CredentialStatus]:
        """Every allowlisted provider with its safe status, in allowlist order."""
        return [self.status(session_id, provider) for provider in self.allowlist]

    def payload(self, session_id: str) -> dict[str, Any]:
        """``$defs/CredentialStatuses``: ``{"items": [...]}`` with no secrets."""
        return {"items": [status.as_dict() for status in self.statuses(session_id)]}

    # -- internals --------------------------------------------------------

    def _live_entry(self, session_id: str, provider_id: str, *, touch: bool) -> _Entry | None:
        key = (str(session_id), str(provider_id))
        entry = self._entries.get(key)
        if entry is None:
            return None
        now = self._clock()
        if now - entry.last_used > timedelta(seconds=self.ttl_seconds):
            self._entries.pop(key, None)
            return None
        if touch:
            entry.last_used = now
        return entry


def _require_opaque_id(value: Any, name: str) -> str:
    """Validate an opaque identifier without ever echoing a secret."""
    if not isinstance(value, str) or not value.strip():
        raise V32Error(400, "invalid_identifier", f"{name} 必须是非空字符串。")
    if len(value) > 128:
        raise V32Error(400, "invalid_identifier", f"{name} 超长。")
    return value
