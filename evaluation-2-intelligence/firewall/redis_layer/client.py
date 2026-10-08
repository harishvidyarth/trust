from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any, TypeVar
from urllib.parse import urlsplit

import redis


KEY_PREFIX = "trust:"
ENV_URL = "FIREWALL_REDIS_URL"
_SCHEMES = frozenset({"redis", "rediss"})
_FAILURES = (redis.RedisError, OSError)
_SOCKET_TIMEOUT = 0.5

T = TypeVar("T")
log = logging.getLogger("firewall.redis")


def validate_redis_url(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("redis url must be a non-empty string")
    url = value.strip()
    try:
        parsed = urlsplit(url)
        parsed.port
    except ValueError as error:
        raise ValueError("redis url is malformed") from None
    if parsed.scheme.lower() not in _SCHEMES:
        raise ValueError("redis url scheme must be redis or rediss")
    if not parsed.hostname:
        raise ValueError("redis url must include a host")
    return url


def redact_url(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port is not None else ""
        scheme = parsed.scheme.lower()
    except ValueError:
        return "redis://<invalid>"
    if scheme not in _SCHEMES or not host:
        return "redis://<invalid>"
    return f"{scheme}://{host}{port}{parsed.path}"


def configured_url(environ: Mapping[str, str] | None = None) -> str | None:
    source = os.environ if environ is None else environ
    raw = source.get(ENV_URL)
    if raw is None or not raw.strip():
        return None
    try:
        return validate_redis_url(raw)
    except ValueError as error:
        log.warning("%s ignored: %s", ENV_URL, error)
        return None


def make_client(url: str) -> redis.Redis:
    return redis.Redis.from_url(
        validate_redis_url(url),
        socket_timeout=_SOCKET_TIMEOUT,
        socket_connect_timeout=_SOCKET_TIMEOUT,
        decode_responses=True,
        retry_on_timeout=False,
    )


class RedisLink:
    def __init__(self, client: Any, *, cooldown_s: float = 5.0) -> None:
        self.client = client
        self._cooldown = cooldown_s
        self._down_until = 0.0
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return time.monotonic() >= self._down_until

    def mark_down(self, error: BaseException) -> None:
        with self._lock:
            self._down_until = time.monotonic() + self._cooldown
        log.warning("redis unavailable (%s); using in-memory fallback", type(error).__name__)

    def mark_up(self) -> None:
        with self._lock:
            self._down_until = 0.0

    def call(self, operation: Callable[[Any], T], fallback: Callable[[], T]) -> T:
        if not self.available:
            return fallback()
        try:
            return operation(self.client)
        except _FAILURES as error:
            self.mark_down(error)
            return fallback()


_LINKS: dict[str, RedisLink] = {}
_LINKS_LOCK = threading.Lock()


def link_from_env(environ: Mapping[str, str] | None = None) -> RedisLink | None:
    url = configured_url(environ)
    if url is None:
        return None
    with _LINKS_LOCK:
        link = _LINKS.get(url)
        if link is None:
            link = RedisLink(make_client(url))
            _LINKS[url] = link
        return link


def redis_status(
    environ: Mapping[str, str] | None = None,
    *,
    link: RedisLink | None = None,
) -> dict[str, Any]:
    active = link if link is not None else link_from_env(environ)
    if active is None:
        return {"configured": False, "reachable": False, "latency_ms": None}
    started = time.perf_counter()
    try:
        active.client.ping()
    except _FAILURES as error:
        active.mark_down(error)
        return {"configured": True, "reachable": False, "latency_ms": None}
    active.mark_up()
    return {
        "configured": True,
        "reachable": True,
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
    }
