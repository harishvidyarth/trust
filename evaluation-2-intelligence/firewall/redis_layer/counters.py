from __future__ import annotations

import threading
import time

from firewall.redis_layer.client import KEY_PREFIX, RedisLink


class FixedWindowCounter:
    def __init__(self, *, max_keys: int = 100_000) -> None:
        self._entries: dict[str, tuple[float, int]] = {}
        self._max_keys = max_keys
        self._lock = threading.Lock()

    def hit(self, key: str, window_s: int) -> int:
        if window_s < 1:
            raise ValueError("window_s must be positive")
        now = time.monotonic()
        with self._lock:
            expires, count = self._entries.get(key, (0.0, 0))
            if expires <= now:
                if len(self._entries) >= self._max_keys:
                    self._entries = {name: item for name, item in self._entries.items() if item[0] > now}
                expires, count = now + window_s, 0
            count += 1
            self._entries[key] = (expires, count)
            return count

    def reset(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)


class RedisFixedWindowCounter(FixedWindowCounter):
    def __init__(self, link: RedisLink, *, fallback: FixedWindowCounter | None = None) -> None:
        super().__init__()
        self._link = link
        self._fallback = fallback or FixedWindowCounter()

    @staticmethod
    def _key(key: str) -> str:
        return f"{KEY_PREFIX}ctr:{key}"

    def hit(self, key: str, window_s: int) -> int:
        if window_s < 1:
            raise ValueError("window_s must be positive")

        def operation(client):
            pipe = client.pipeline(transaction=True)
            pipe.incr(self._key(key))
            pipe.expire(self._key(key), int(window_s), nx=True)
            return int(pipe.execute()[0])

        return self._link.call(operation, lambda: self._fallback.hit(key, window_s))

    def reset(self, key: str) -> None:
        self._link.call(lambda client: client.delete(self._key(key)), lambda: None)
        self._fallback.reset(key)
