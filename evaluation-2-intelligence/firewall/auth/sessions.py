from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, replace
from typing import Protocol

from firewall.auth.limits import Clock


IDLE_SECONDS = 30 * 60
ABSOLUTE_SECONDS = 8 * 60 * 60


@dataclass(frozen=True)
class Session:
    session_id: str
    username: str
    csrf_token: str
    created_at: float
    last_seen: float


class SessionStore(Protocol):
    def create(self, username: str) -> Session: ...

    def get(self, session_id: str) -> Session | None: ...

    def delete(self, session_id: str) -> None: ...

    def delete_for_user(self, username: str) -> int: ...


class InMemorySessionStore:
    def __init__(
        self,
        clock: Clock,
        idle_seconds: float = IDLE_SECONDS,
        absolute_seconds: float = ABSOLUTE_SECONDS,
    ) -> None:
        self._clock = clock
        self._idle = idle_seconds
        self._absolute = absolute_seconds
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    @property
    def absolute_seconds(self) -> float:
        return self._absolute

    def create(self, username: str) -> Session:
        now = self._clock()
        session = Session(
            session_id=secrets.token_urlsafe(32),
            username=username,
            csrf_token=secrets.token_urlsafe(32),
            created_at=now,
            last_seen=now,
        )
        with self._lock:
            self._sweep(now)
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        now = self._clock()
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if now - session.last_seen > self._idle or now - session.created_at > self._absolute:
                del self._sessions[session_id]
                return None
            session = replace(session, last_seen=now)
            self._sessions[session_id] = session
            return session

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def delete_for_user(self, username: str) -> int:
        with self._lock:
            doomed = [key for key, value in self._sessions.items() if value.username == username]
            for key in doomed:
                del self._sessions[key]
            return len(doomed)

    def _sweep(self, now: float) -> None:
        expired = [
            key
            for key, value in self._sessions.items()
            if now - value.last_seen > self._idle or now - value.created_at > self._absolute
        ]
        for key in expired:
            del self._sessions[key]
