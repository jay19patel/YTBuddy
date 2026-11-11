"""Server-side web sessions and OAuth state. The browser only holds random ids; the DB stores their sha256."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ytstudio.adapters.persistence import tables as t
from ytstudio.adapters.persistence.db import session_for

SESSION_DAYS = 14
STATE_MINUTES = 10


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


@dataclass
class SessionInfo:
    user_id: int
    csrf: str


@dataclass
class PendingAuth:
    purpose: str
    code_verifier: str
    user_id: int | None


class WebSessions:
    def __init__(self, url: str):
        self.url = url

    def create(self, user_id: int) -> str:
        cookie = secrets.token_urlsafe(32)
        with session_for(self.url) as s:
            s.add(
                t.WebSession(
                    id=digest(cookie),
                    user_id=user_id,
                    csrf=secrets.token_urlsafe(24),
                    expires_at=datetime.now(UTC) + timedelta(days=SESSION_DAYS),
                )
            )
            s.commit()
        return cookie

    def get(self, cookie: str | None) -> SessionInfo | None:
        if not cookie:
            return None
        with session_for(self.url) as s:
            row = s.get(t.WebSession, digest(cookie))
        if not row or _aware(row.expires_at) < datetime.now(UTC):
            return None
        return SessionInfo(row.user_id, row.csrf)

    def delete(self, cookie: str | None) -> None:
        if not cookie:
            return
        with session_for(self.url) as s:
            if row := s.get(t.WebSession, digest(cookie)):
                s.delete(row)
                s.commit()

    def save_state(self, state: str, purpose: str, code_verifier: str, nonce: str, user_id: int | None) -> None:
        with session_for(self.url) as s:
            s.add(
                t.OAuthState(
                    state=state,
                    purpose=purpose,
                    code_verifier=code_verifier,
                    binding=digest(nonce),
                    user_id=user_id,
                    expires_at=datetime.now(UTC) + timedelta(minutes=STATE_MINUTES),
                )
            )
            s.commit()

    def pop_state(self, state: str, nonce: str | None) -> PendingAuth | None:
        """One-time use; must come back to the same browser that started it (login-CSRF protection)."""
        with session_for(self.url) as s:
            row = s.get(t.OAuthState, state)
            if not row:
                return None
            s.delete(row)
            s.commit()
        if not nonce or not secrets.compare_digest(row.binding, digest(nonce)):
            return None
        if _aware(row.expires_at) < datetime.now(UTC):
            return None
        return PendingAuth(row.purpose, row.code_verifier, row.user_id)
