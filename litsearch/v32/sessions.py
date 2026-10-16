"""Anonymous private sessions and CSRF for the v3.2 API.

``GET /api/session`` establishes or restores an anonymous private session.  It
is emphatically **not** a campus login: the specification forbids building a
ZJU CAS client or any "浙江大学账号登录" affordance this round
(docs/20_ZJU_CAS_DEFERRED.md, AC-28), and ``contracts/defaults.json`` pins
``access.application_sso_enabled = false``.

The owner id is server-generated and high entropy, so a project id cannot be
guessed into someone else's data (docs/10_SECURITY_PRIVACY.md).  The session
cookie carries only an opaque session id; the CSRF token is a separate secret
that every mutating request must echo in ``X-CSRF-Token``.
"""

from __future__ import annotations

import hmac
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .errors import FORBIDDEN, SESSION_EXPIRED, V32Error

__all__ = ["Session", "SessionStore", "SESSION_COOKIE"]

#: Cookie name for the anonymous application session.
SESSION_COOKIE = "leextractor_session"

#: 32 bytes of entropy, hex-encoded — not guessable, not sequential.
_ID_BYTES = 32

#: Anonymous sessions are cheap; keep them for a long working day.
DEFAULT_SESSION_TTL_SECONDS = 12 * 3600


@dataclass
class Session:
    """One anonymous private owner."""

    session_id: str
    owner_id: str
    csrf_token: str
    created_at: str
    expires_at: str
    _expiry: datetime = field(repr=False, default_factory=lambda: datetime.now(timezone.utc))

    def status(self) -> dict:
        """The ``SessionStatus`` DTO.  Never contains the secret itself."""
        return {
            "authenticated": True,
            "mode": "anonymous_private",
            "csrf_token": self.csrf_token,
            "expires_at": self.expires_at,
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SessionStore:
    """In-process session store.

    Sessions are deliberately not persisted: the specification says the default
    public deployment keeps session state in memory and loses it on restart
    (docs/10_SECURITY_PRIVACY.md, ``contracts.credentials``).
    """

    def __init__(self, ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS):
        self.ttl_seconds = int(ttl_seconds)
        self._sessions: dict[str, Session] = {}
        self._lock = threading.RLock()

    # -- lifecycle ---------------------------------------------------------

    def create(self) -> Session:
        with self._lock:
            session_id = secrets.token_hex(_ID_BYTES)
            session = self._build(session_id)
            self._sessions[session_id] = session
            return session

    def _build(self, session_id: str) -> Session:
        created = _now()
        expires = created + timedelta(seconds=self.ttl_seconds)
        return Session(
            session_id=session_id,
            # The owner id is a *different* secret from the session id, so a
            # leaked cookie value alone does not reveal the storage key.
            owner_id=secrets.token_hex(_ID_BYTES),
            csrf_token=secrets.token_hex(32),
            created_at=created.isoformat(),
            expires_at=expires.isoformat(),
            _expiry=expires,
        )

    # -- lookup ------------------------------------------------------------

    def get(self, session_id: str | None) -> Session:
        """The session for ``session_id``, or ``401`` when absent or expired."""
        if not session_id:
            raise V32Error(401, SESSION_EXPIRED, "会话不存在或已过期，请刷新页面重新建立会话")
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise V32Error(401, SESSION_EXPIRED, "会话不存在或已过期，请刷新页面重新建立会话")
            if session._expiry <= _now():
                del self._sessions[session_id]
                raise V32Error(401, SESSION_EXPIRED, "会话已过期，请刷新页面重新建立会话")
            return session

    def get_or_create(self, session_id: str | None) -> tuple[Session, bool]:
        """Restore ``session_id`` when it is still valid, else mint a new one.

        Returns ``(session, created)`` so the HTTP layer knows whether to emit
        a ``Set-Cookie``.
        """
        if session_id:
            with self._lock:
                session = self._sessions.get(session_id)
                if session is not None and session._expiry > _now():
                    return session, False
        return self.create(), True

    # -- authorisation -----------------------------------------------------

    def require_csrf(self, session: Session, provided: str | None) -> None:
        """Constant-time CSRF check for every mutating request."""
        if not provided or not hmac.compare_digest(str(provided), session.csrf_token):
            raise V32Error(403, FORBIDDEN, "请求校验失败，请刷新页面后重试")

    def owner_of(self, session_id: str | None) -> str:
        return self.get(session_id).owner_id

    # -- maintenance -------------------------------------------------------

    def purge_expired(self) -> int:
        now = _now()
        with self._lock:
            expired = [sid for sid, s in self._sessions.items() if s._expiry <= now]
            for sid in expired:
                del self._sessions[sid]
            return len(expired)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()
