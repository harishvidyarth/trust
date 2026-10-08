from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Any

from firewall.redis_layer.client import KEY_PREFIX, RedisLink


DEFAULT_TTL_S = 3600
MAX_VALUE_BYTES = 262_144


def cache_digest(value: object) -> str:
    if isinstance(value, str):
        raw = value
    else:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


class JsonCache:
    def __init__(
        self,
        *,
        max_entries: int = 1024,
        max_value_bytes: int = MAX_VALUE_BYTES,
        default_ttl_s: int = DEFAULT_TTL_S,
    ) -> None:
        self._entries: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._max_entries = max_entries
        self.max_value_bytes = max_value_bytes
        self.default_ttl_s = default_ttl_s
        self._lock = threading.Lock()

    def _encode(self, value: Any) -> str | None:
        try:
            payload = json.dumps(value, separators=(",", ":"))
        except (TypeError, ValueError):
            return None
        return payload if len(payload.encode()) <= self.max_value_bytes else None

    def get(self, source: object) -> Any | None:
        digest = cache_digest(source)
        with self._lock:
            entry = self._entries.get(digest)
            if entry is None:
                return None
            if entry[0] <= time.monotonic():
                self._entries.pop(digest, None)
                return None
            self._entries.move_to_end(digest)
            return json.loads(entry[1])

    def set(self, source: object, value: Any, ttl_s: int | None = None) -> bool:
        payload = self._encode(value)
        ttl = self.default_ttl_s if ttl_s is None else ttl_s
        if payload is None or ttl < 1:
            return False
        digest = cache_digest(source)
        with self._lock:
            self._entries[digest] = (time.monotonic() + ttl, payload)
            self._entries.move_to_end(digest)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
        return True

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


class RedisJsonCache(JsonCache):
    def __init__(
        self,
        link: RedisLink,
        namespace: str = "default",
        *,
        fallback: JsonCache | None = None,
        max_value_bytes: int = MAX_VALUE_BYTES,
        default_ttl_s: int = DEFAULT_TTL_S,
    ) -> None:
        super().__init__(max_value_bytes=max_value_bytes, default_ttl_s=default_ttl_s)
        self._link = link
        self._namespace = namespace
        self._fallback = fallback or JsonCache(max_value_bytes=max_value_bytes, default_ttl_s=default_ttl_s)

    def _key(self, source: object) -> str:
        return f"{KEY_PREFIX}cache:{self._namespace}:{cache_digest(source)}"

    def get(self, source: object) -> Any | None:
        def operation(client):
            raw = client.get(self._key(source))
            if raw is None:
                return None
            try:
                return json.loads(raw)
            except ValueError:
                return None

        return self._link.call(operation, lambda: self._fallback.get(source))

    def set(self, source: object, value: Any, ttl_s: int | None = None) -> bool:
        payload = self._encode(value)
        ttl = self.default_ttl_s if ttl_s is None else ttl_s
        if payload is None or ttl < 1:
            return False

        def operation(client):
            client.set(self._key(source), payload, ex=int(ttl))
            return True

        return self._link.call(operation, lambda: self._fallback.set(source, value, ttl))

    def clear(self) -> None:
        def operation(client):
            keys = list(client.scan_iter(match=f"{KEY_PREFIX}cache:{self._namespace}:*", count=500))
            if keys:
                client.delete(*keys)

        self._link.call(operation, lambda: None)
        self._fallback.clear()


class CachedLLMClient:
    def __init__(self, inner: Any, cache: JsonCache, ttl_s: int = DEFAULT_TTL_S) -> None:
        self._inner = inner
        self._cache = cache
        self._ttl = ttl_s

    def _cached(self, method: str, source: object, produce):
        key = {"method": method, "source": source}
        hit = self._cache.get(key)
        if hit is not None:
            return hit["value"]
        value = produce()
        self._cache.set(key, {"value": value}, self._ttl)
        return value

    def rewrite_summary(self, reason_codes: list[str], skill_names: list[str]) -> str:
        return self._cached(
            "rewrite_summary",
            [list(reason_codes), list(skill_names)],
            lambda: self._inner.rewrite_summary(reason_codes, skill_names),
        )

    def generate_json(self, prompt: str, schema: dict[str, Any]) -> Any:
        return self._cached("generate_json", [prompt, schema], lambda: self._inner.generate_json(prompt, schema))

    def embed(self, text: str) -> tuple[float, ...] | None:
        value = self._cached("embed", text, lambda: self._inner.embed(text))
        return tuple(value) if value is not None else None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class SignalCache:
    def __init__(self, cache: JsonCache, ttl_s: int = 300) -> None:
        self._cache = cache
        self._ttl = ttl_s

    def get(self, key: str):
        from firewall.enrichment.models import EnrichmentSignal

        raw = self._cache.get(key)
        if raw is None:
            return None
        try:
            return [EnrichmentSignal.model_validate(item) for item in raw]
        except ValueError:
            return None

    def set(self, key: str, value) -> None:
        self._cache.set(key, [signal.model_dump(mode="json") for signal in value], self._ttl)
