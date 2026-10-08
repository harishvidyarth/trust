from __future__ import annotations

import hashlib
import json
import math
import secrets
import threading
from dataclasses import asdict
from typing import Any

from firewall.auth.audit import AuditEntry, InMemoryAuditLog
from firewall.auth.limits import Clock
from firewall.auth.records import (
    InMemoryOverrideStore,
    InMemoryOwnershipRegistry,
    OverrideRecord,
)
from firewall.auth.sessions import ABSOLUTE_SECONDS, IDLE_SECONDS, InMemorySessionStore, Session
from firewall.redis_layer.client import KEY_PREFIX, RedisLink
from firewall.redis_layer.counters import FixedWindowCounter, RedisFixedWindowCounter


AUDIT_LIMIT = 10_000
AUDIT_TTL_S = 400 * 24 * 3600
RECORD_TTL_S = 90 * 24 * 3600


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class RedisSessionStore:
    def __init__(
        self,
        link: RedisLink,
        clock: Clock,
        idle_seconds: float = IDLE_SECONDS,
        absolute_seconds: float = ABSOLUTE_SECONDS,
        fallback: InMemorySessionStore | None = None,
    ) -> None:
        self._link = link
        self._clock = clock
        self._idle = idle_seconds
        self._absolute = absolute_seconds
        self._fallback = fallback or InMemorySessionStore(clock, idle_seconds, absolute_seconds)

    @property
    def absolute_seconds(self) -> float:
        return self._absolute

    @staticmethod
    def _key(session_id: str) -> str:
        return f"{KEY_PREFIX}sess:{_token_hash(session_id)}"

    @staticmethod
    def _user_key(username: str) -> str:
        return f"{KEY_PREFIX}sessu:{_token_hash(username)}"

    def _ttl(self, session: Session, now: float) -> int:
        remaining = self._absolute - (now - session.created_at)
        return max(1, math.ceil(min(self._idle, remaining)))

    @staticmethod
    def _encode(session: Session) -> str:
        return json.dumps(
            {
                "username": session.username,
                "csrf_token": session.csrf_token,
                "created_at": session.created_at,
                "last_seen": session.last_seen,
            },
            separators=(",", ":"),
        )

    def create(self, username: str) -> Session:
        now = self._clock()
        session = Session(
            session_id=secrets.token_urlsafe(32),
            username=username,
            csrf_token=secrets.token_urlsafe(32),
            created_at=now,
            last_seen=now,
        )

        def operation(client):
            pipe = client.pipeline(transaction=True)
            pipe.set(self._key(session.session_id), self._encode(session), ex=self._ttl(session, now))
            pipe.sadd(self._user_key(username), _token_hash(session.session_id))
            pipe.expire(self._user_key(username), math.ceil(self._absolute))
            pipe.execute()
            return session

        return self._link.call(operation, lambda: self._fallback.create(username))

    def get(self, session_id: str) -> Session | None:
        now = self._clock()

        def operation(client):
            raw = client.get(self._key(session_id))
            if raw is None:
                return None
            try:
                data = json.loads(raw)
                session = Session(
                    session_id=session_id,
                    username=str(data["username"]),
                    csrf_token=str(data["csrf_token"]),
                    created_at=float(data["created_at"]),
                    last_seen=float(data["last_seen"]),
                )
            except (ValueError, KeyError, TypeError):
                client.delete(self._key(session_id))
                return None
            if now - session.last_seen > self._idle or now - session.created_at > self._absolute:
                client.delete(self._key(session_id))
                client.srem(self._user_key(session.username), _token_hash(session_id))
                return None
            touched = Session(session_id, session.username, session.csrf_token, session.created_at, now)
            client.set(self._key(session_id), self._encode(touched), ex=self._ttl(touched, now))
            return touched

        found = self._link.call(operation, lambda: None)
        return found if found is not None else self._fallback.get(session_id)

    def delete(self, session_id: str) -> None:
        def operation(client):
            raw = client.get(self._key(session_id))
            client.delete(self._key(session_id))
            if raw is not None:
                try:
                    client.srem(self._user_key(str(json.loads(raw)["username"])), _token_hash(session_id))
                except (ValueError, KeyError, TypeError):
                    pass

        self._link.call(operation, lambda: None)
        self._fallback.delete(session_id)

    def delete_for_user(self, username: str) -> int:
        def operation(client):
            hashes = list(client.smembers(self._user_key(username)))
            removed = 0
            for item in hashes:
                removed += int(client.delete(f"{KEY_PREFIX}sess:{item}"))
            client.delete(self._user_key(username))
            return removed

        return self._link.call(operation, lambda: 0) + self._fallback.delete_for_user(username)


class RedisRateLimiter:
    def __init__(
        self,
        link: RedisLink,
        prefix: str,
        limit: int,
        window_seconds: float,
        lockout_seconds: float,
        clock: Clock,
    ) -> None:
        self._link = link
        self._prefix = prefix
        self._limit = limit
        self._window = max(1, math.ceil(window_seconds))
        self._lockout = max(1, math.ceil(lockout_seconds))
        self._clock = clock
        self._counter = RedisFixedWindowCounter(link, fallback=FixedWindowCounter())
        self._local_locks: dict[str, float] = {}
        self._lock = threading.Lock()

    def _counter_key(self, key: str) -> str:
        return f"{self._prefix}:{key}"

    def _lock_key(self, key: str) -> str:
        return f"{KEY_PREFIX}lock:{self._prefix}:{key}"

    def blocked(self, key: str) -> bool:
        remote = self._link.call(lambda client: bool(client.exists(self._lock_key(key))), lambda: False)
        if remote:
            return True
        with self._lock:
            until = self._local_locks.get(key)
            if until is None:
                return False
            if self._clock() >= until:
                del self._local_locks[key]
                return False
            return True

    def hit(self, key: str) -> None:
        count = self._counter.hit(self._counter_key(key), self._window)
        if count < self._limit:
            return

        def remember_locally() -> None:
            with self._lock:
                self._local_locks[key] = self._clock() + self._lockout

        self._link.call(lambda client: client.set(self._lock_key(key), "1", ex=self._lockout), remember_locally)

    def reset(self, key: str) -> None:
        self._counter.reset(self._counter_key(key))
        self._link.call(lambda client: client.delete(self._lock_key(key)), lambda: None)
        with self._lock:
            self._local_locks.pop(key, None)


class RedisAuditLog:
    def __init__(self, link: RedisLink, clock: Clock, fallback: InMemoryAuditLog) -> None:
        self._link = link
        self._clock = clock
        self._fallback = fallback
        self._list_key = f"{KEY_PREFIX}audit"
        self._seq_key = f"{KEY_PREFIX}audit:seq"

    def append(
        self,
        event: str,
        actor: str,
        target: str = "",
        ip: str = "",
        detail: dict[str, object] | None = None,
    ) -> AuditEntry:
        local = self._fallback.append(event, actor, target, ip, detail)

        def operation(client):
            seq = int(client.incr(self._seq_key))
            entry = AuditEntry(seq, local.at, event, actor, target, ip, dict(detail or {}))
            pipe = client.pipeline(transaction=True)
            pipe.lpush(self._list_key, json.dumps(asdict(entry), sort_keys=True, default=str))
            pipe.ltrim(self._list_key, 0, AUDIT_LIMIT - 1)
            pipe.expire(self._list_key, AUDIT_TTL_S)
            pipe.expire(self._seq_key, AUDIT_TTL_S)
            pipe.execute()
            return entry

        return self._link.call(operation, lambda: local)

    def entries(self, limit: int = 200) -> list[AuditEntry]:
        def operation(client):
            found = []
            for raw in client.lrange(self._list_key, 0, max(0, limit) - 1):
                try:
                    found.append(AuditEntry(**json.loads(raw)))
                except (ValueError, TypeError):
                    continue
            return found

        return self._link.call(operation, lambda: self._fallback.entries(limit))


class RedisOwnershipRegistry:
    def __init__(self, link: RedisLink, fallback: InMemoryOwnershipRegistry | None = None) -> None:
        self._link = link
        self._fallback = fallback or InMemoryOwnershipRegistry()

    @staticmethod
    def _key(application_id: str) -> str:
        return f"{KEY_PREFIX}own:{application_id}"

    @staticmethod
    def _user_key(username: str) -> str:
        return f"{KEY_PREFIX}ownu:{_token_hash(username)}"

    def bind(self, application_id: str, username: str) -> None:
        def operation(client):
            if client.set(self._key(application_id), username, ex=RECORD_TTL_S, nx=True):
                pipe = client.pipeline(transaction=True)
                pipe.lpush(self._user_key(username), application_id)
                pipe.expire(self._user_key(username), RECORD_TTL_S)
                pipe.execute()

        self._link.call(operation, lambda: self._fallback.bind(application_id, username))

    def owner(self, application_id: str) -> str | None:
        remote = self._link.call(lambda client: client.get(self._key(application_id)), lambda: None)
        return remote if remote is not None else self._fallback.owner(application_id)

    def owned_by(self, username: str) -> list[str]:
        remote = self._link.call(lambda client: list(client.lrange(self._user_key(username), 0, -1)), lambda: [])
        found = [item.decode() if isinstance(item, bytes) else str(item) for item in remote]
        seen = set(found)
        return found + [item for item in self._fallback.owned_by(username) if item not in seen]


class RedisOverrideStore:
    def __init__(self, link: RedisLink, fallback: InMemoryOverrideStore | None = None) -> None:
        self._link = link
        self._fallback = fallback or InMemoryOverrideStore()

    @staticmethod
    def _key(application_id: str) -> str:
        return f"{KEY_PREFIX}ovr:{application_id}"

    def add(self, record: OverrideRecord) -> None:
        raw = json.dumps(asdict(record), sort_keys=True)

        def operation(client):
            pipe = client.pipeline(transaction=True)
            pipe.rpush(self._key(record.application_id), raw)
            pipe.expire(self._key(record.application_id), RECORD_TTL_S)
            pipe.execute()

        self._link.call(operation, lambda: self._fallback.add(record))

    def for_application(self, application_id: str) -> list[OverrideRecord]:
        def operation(client):
            found: list[OverrideRecord] = []
            for raw in client.lrange(self._key(application_id), 0, -1):
                try:
                    found.append(OverrideRecord(**json.loads(raw)))
                except (ValueError, TypeError):
                    continue
            return found

        return self._link.call(operation, lambda: []) + self._fallback.for_application(application_id)


def build_auth_stores(link: RedisLink, clock: Clock, audit_path: str | None) -> dict[str, Any]:
    return {
        "sessions": RedisSessionStore(link, clock),
        "audit": RedisAuditLog(link, clock, InMemoryAuditLog(clock, audit_path)),
        "ownership": RedisOwnershipRegistry(link),
        "overrides": RedisOverrideStore(link),
    }
