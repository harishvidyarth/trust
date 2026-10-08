from __future__ import annotations

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any, Iterable

from firewall.enrichment.crossref import CrossrefConnector
from firewall.enrichment.domain import DomainConnector
from firewall.enrichment.github import GitHubConnector
from firewall.enrichment.identity import IdentityConnector
from firewall.enrichment.models import Claims, EnrichmentSignal, EnrichmentSummary
from firewall.enrichment.scholar import ScholarConnector


DEFAULT_WEIGHTS = {
    "GITHUB_DATES_MISMATCH": 25,
    "GITHUB_REPO_NOT_FOUND": 20,
    "DOI_NOT_FOUND": 25,
    "DOI_TITLE_MISMATCH": 20,
    "DOMAIN_AGE_RECENT": 8,
    "IDENTITY_NAME_MISMATCH": 30,
    "EMAIL_DISPOSABLE": 10,
    "GITHUB_ACCOUNT_NEW": 5,
    "PAPER_AUTHOR_MISMATCH": 18,
}

POSITIVE_BONUSES = {
    "GITHUB_CORROBORATED": 6,
    "DOI_VERIFIED": 6,
    "DOMAIN_REACHABLE": 2,
    "IDENTITY_OIDC_VERIFIED": 8,
    "PAPER_CORROBORATED": 4,
}


class TTLCache:
    def __init__(self, ttl_seconds: float = 300.0) -> None:
        self.ttl_seconds = max(0.0, ttl_seconds)
        self._values: dict[str, tuple[float, list[EnrichmentSignal]]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> list[EnrichmentSignal] | None:
        with self._lock:
            item = self._values.get(key)
            if item is None:
                return None
            expires_at, value = item
            if expires_at <= time.monotonic():
                self._values.pop(key, None)
                return None
            return [signal.model_copy(deep=True) for signal in value]

    def set(self, key: str, value: list[EnrichmentSignal]) -> None:
        with self._lock:
            self._values[key] = (
                time.monotonic() + self.ttl_seconds,
                [signal.model_copy(deep=True) for signal in value],
            )


_DEFAULT_CACHE = TTLCache()


def _connector_name(connector: Any) -> str:
    return str(getattr(connector, "name", connector.__class__.__name__.removesuffix("Connector").casefold()))


def _cache_key(connector: Any, claims: Claims) -> str:
    identity = f"{connector.__class__.__module__}.{connector.__class__.__qualname__}:{_connector_name(connector)}"
    digest = hashlib.sha256(claims.model_dump_json().encode("utf-8")).hexdigest()
    return f"{identity}:{digest}"


def _validated_signals(value: Any) -> list[EnrichmentSignal]:
    if not isinstance(value, list):
        raise TypeError("connector result must be a list")
    return [item if isinstance(item, EnrichmentSignal) else EnrichmentSignal.model_validate(item) for item in value]


def enrich(
    claims: Claims,
    connectors: Iterable[Any] | None = None,
    per_connector_timeout: float = 4.0,
    cache: TTLCache | None = None,
) -> tuple[list[EnrichmentSignal], EnrichmentSummary]:

    summary = EnrichmentSummary()
    try:
        claims = claims if isinstance(claims, Claims) else Claims.model_validate(claims)
    except Exception:
        return [], summary

    connector_list = list(connectors) if connectors is not None else [
        GitHubConnector(),
        CrossrefConnector(),
        DomainConnector(),
        IdentityConnector(),
        ScholarConnector(),
    ]
    active_cache = cache if cache is not None else _DEFAULT_CACHE
    results: dict[int, list[EnrichmentSignal]] = {}
    pending: list[tuple[int, Any, str, str]] = []
    for index, connector in enumerate(connector_list):
        name = _connector_name(connector)
        key = _cache_key(connector, claims)
        cached = active_cache.get(key)
        if cached is not None:
            results[index] = cached
            summary.cached.append(name)
        else:
            pending.append((index, connector, name, key))

    if pending:
        executor = ThreadPoolExecutor(max_workers=len(pending), thread_name_prefix="enrichment")
        future_meta = {
            executor.submit(connector.check, claims): (index, name, key)
            for index, connector, name, key in pending
        }
        done, not_done = wait(future_meta, timeout=max(0.0, per_connector_timeout))
        for future in done:
            index, name, key = future_meta[future]
            try:
                connector_signals = _validated_signals(future.result())
            except Exception:
                summary.failed.append(name)
                continue
            results[index] = connector_signals
            active_cache.set(key, connector_signals)
            summary.ran.append(name)
        for future in not_done:
            _, name, _ = future_meta[future]
            future.cancel()
            summary.timed_out.append(name)
        executor.shutdown(wait=False, cancel_futures=True)

    order = {_connector_name(connector): index for index, connector in enumerate(connector_list)}
    summary.ran.sort(key=lambda name: order.get(name, 10**9))
    summary.timed_out.sort(key=lambda name: order.get(name, 10**9))
    summary.failed.sort(key=lambda name: order.get(name, 10**9))
    summary.cached.sort(key=lambda name: order.get(name, 10**9))
    signals = [signal for index in sorted(results) for signal in results[index]]
    return signals, summary


def to_reasons(
    signals: Iterable[EnrichmentSignal],
    weights: dict[str, int] | None = None,
    bonus_cap: int = 15,
) -> tuple[list[dict[str, Any]], int]:

    configured = DEFAULT_WEIGHTS if weights is None else weights
    reasons: list[dict[str, Any]] = []
    trust = 0.0
    for signal in signals:
        if signal.polarity == "negative" and signal.code in configured:
            weight = int(max(0, configured[signal.code]) * signal.confidence)
            reasons.append(
                {"code": signal.code, "severity": signal.severity, "detail": signal.detail, "weight": weight}
            )
        elif signal.polarity == "positive":
            trust += POSITIVE_BONUSES.get(signal.code, 0) * signal.confidence
    return reasons, min(max(0, bonus_cap), int(round(trust)))
