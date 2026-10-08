from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from firewall.redis_layer.app_store import RedisApplicationStore
from firewall.redis_layer.cache import CachedLLMClient, JsonCache, RedisJsonCache, SignalCache, cache_digest
from firewall.redis_layer.client import (
    ENV_URL,
    KEY_PREFIX,
    RedisLink,
    configured_url,
    link_from_env,
    redact_url,
    redis_status,
    validate_redis_url,
)
from firewall.redis_layer.counters import FixedWindowCounter, RedisFixedWindowCounter
from firewall.redis_layer.delivery_queue import DeliveryQueue, QueueItem, RedisDeliveryQueue
from firewall.store import ApplicationStore, InMemoryApplicationStore


def build_store(environ: Mapping[str, str] | None = None, *, link: RedisLink | None = None) -> ApplicationStore:
    active = link or link_from_env(environ)
    return RedisApplicationStore(active) if active is not None else InMemoryApplicationStore()


def build_counter(environ: Mapping[str, str] | None = None, *, link: RedisLink | None = None) -> FixedWindowCounter:
    active = link or link_from_env(environ)
    return RedisFixedWindowCounter(active) if active is not None else FixedWindowCounter()


def build_cache(
    namespace: str = "default",
    environ: Mapping[str, str] | None = None,
    *,
    link: RedisLink | None = None,
    **options: Any,
) -> JsonCache:
    active = link or link_from_env(environ)
    return RedisJsonCache(active, namespace, **options) if active is not None else JsonCache(**options)


def build_queue(
    name: str = "delivery",
    environ: Mapping[str, str] | None = None,
    *,
    link: RedisLink | None = None,
) -> DeliveryQueue:
    active = link or link_from_env(environ)
    return RedisDeliveryQueue(active, name) if active is not None else DeliveryQueue()


def build_optional_queue(
    name: str = "delivery",
    environ: Mapping[str, str] | None = None,
    *,
    link: RedisLink | None = None,
) -> DeliveryQueue | None:
    active = link or link_from_env(environ)
    return RedisDeliveryQueue(active, name) if active is not None else None


def build_signal_cache(
    environ: Mapping[str, str] | None = None,
    *,
    link: RedisLink | None = None,
) -> SignalCache | None:
    active = link or link_from_env(environ)
    return SignalCache(build_cache("enrichment", link=active)) if active is not None else None


def build_llm_client(
    inner: Any,
    environ: Mapping[str, str] | None = None,
    *,
    link: RedisLink | None = None,
) -> Any | None:
    active = link or link_from_env(environ)
    return CachedLLMClient(inner, build_cache("llm", link=active)) if active is not None else None


def build_intake_repository(environ: Mapping[str, str] | None = None, *, link: RedisLink | None = None) -> Any:
    from firewall.intake_records import InMemoryIntakeRepository
    from firewall.redis_layer.intake_store import RedisIntakeRepository

    active = link or link_from_env(environ)
    return RedisIntakeRepository(active) if active is not None else InMemoryIntakeRepository()


__all__ = [
    "ENV_URL",
    "KEY_PREFIX",
    "ApplicationStore",
    "DeliveryQueue",
    "FixedWindowCounter",
    "JsonCache",
    "QueueItem",
    "RedisApplicationStore",
    "RedisDeliveryQueue",
    "RedisFixedWindowCounter",
    "RedisJsonCache",
    "RedisLink",
    "CachedLLMClient",
    "SignalCache",
    "build_cache",
    "build_counter",
    "build_intake_repository",
    "build_llm_client",
    "build_optional_queue",
    "build_queue",
    "build_signal_cache",
    "build_store",
    "cache_digest",
    "configured_url",
    "link_from_env",
    "redact_url",
    "redis_status",
    "validate_redis_url",
]
