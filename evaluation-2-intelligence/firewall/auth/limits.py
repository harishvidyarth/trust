from __future__ import annotations

import threading
from collections import defaultdict
from typing import Callable, Protocol


Clock = Callable[[], float]


class RateLimiter(Protocol):
    def blocked(self, key: str) -> bool: ...

    def hit(self, key: str) -> None: ...

    def reset(self, key: str) -> None: ...


class InMemoryRateLimiter:
    def __init__(self, limit: int, window_seconds: float, lockout_seconds: float, clock: Clock) -> None:
        self._limit = limit
        self._window = window_seconds
        self._lockout = lockout_seconds
        self._clock = clock
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def blocked(self, key: str) -> bool:
        with self._lock:
            until = self._locked_until.get(key)
            if until is None:
                return False
            if self._clock() >= until:
                del self._locked_until[key]
                self._hits.pop(key, None)
                return False
            return True

    def hit(self, key: str) -> None:
        with self._lock:
            now = self._clock()
            recent = [stamp for stamp in self._hits[key] if now - stamp < self._window]
            recent.append(now)
            self._hits[key] = recent
            if len(recent) >= self._limit:
                self._locked_until[key] = now + self._lockout

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)
            self._locked_until.pop(key, None)


class LoginThrottle:
    def __init__(self, per_user: RateLimiter, per_ip: RateLimiter) -> None:
        self._per_user = per_user
        self._per_ip = per_ip

    def locked(self, username: str, ip: str) -> bool:
        return self._per_user.blocked(username) or self._per_ip.blocked(ip)

    def failure(self, username: str, ip: str) -> None:
        self._per_user.hit(username)
        self._per_ip.hit(ip)

    def success(self, username: str) -> None:
        self._per_user.reset(username)
