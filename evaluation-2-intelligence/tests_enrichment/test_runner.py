from __future__ import annotations

import time

from firewall.enrichment.models import Claims, EnrichmentSignal
from firewall.enrichment.runner import TTLCache, enrich, to_reasons


def signal(code: str, polarity: str = "negative", confidence: float = 1.0) -> EnrichmentSignal:
    return EnrichmentSignal(
        code=code,
        polarity=polarity,
        severity="medium",
        confidence=confidence,
        source="test",
        detail="Generic verification result.",
    )


class Connector:
    def __init__(self, name: str, delay: float = 0, result=None, error: Exception | None = None):
        self.name = name
        self.delay = delay
        self.result = result if result is not None else []
        self.error = error
        self.calls = 0

    def check(self, claims):
        self.calls += 1
        time.sleep(self.delay)
        if self.error:
            raise self.error
        return self.result


def test_runner_is_concurrent() -> None:
    connectors = [Connector("one", 0.08), Connector("two", 0.08), Connector("three", 0.08)]
    started = time.monotonic()
    _, summary = enrich(Claims(), connectors=connectors, per_connector_timeout=0.5, cache=TTLCache())
    elapsed = time.monotonic() - started
    assert elapsed < 0.18
    assert summary.ran == ["one", "two", "three"]


def test_runner_timeout_isolated_and_returns_promptly() -> None:
    fast = Connector("fast", result=[signal("DOI_NOT_FOUND")])
    slow = Connector("slow", delay=0.2)
    started = time.monotonic()
    signals, summary = enrich(Claims(), connectors=[fast, slow], per_connector_timeout=0.03, cache=TTLCache())
    assert time.monotonic() - started < 0.12
    assert [item.code for item in signals] == ["DOI_NOT_FOUND"]
    assert summary.ran == ["fast"]
    assert summary.timed_out == ["slow"]


def test_runner_failure_isolated_and_never_raises() -> None:
    broken = Connector("broken", error=RuntimeError("boom"))
    good = Connector("good", result=[signal("EMAIL_DISPOSABLE")])
    signals, summary = enrich(Claims(), connectors=[broken, good], cache=TTLCache())
    assert [item.code for item in signals] == ["EMAIL_DISPOSABLE"]
    assert summary.failed == ["broken"]
    assert summary.ran == ["good"]


def test_runner_caches_per_connector_results() -> None:
    connector = Connector("cached", result=[signal("DOI_NOT_FOUND")])
    cache = TTLCache(ttl_seconds=60)
    first, _ = enrich(Claims(), connectors=[connector], cache=cache)
    second, summary = enrich(Claims(), connectors=[connector], cache=cache)
    assert first == second
    assert connector.calls == 1
    assert summary.cached == ["cached"]


def test_expired_cache_runs_again() -> None:
    connector = Connector("short-cache")
    cache = TTLCache(ttl_seconds=0.001)
    enrich(Claims(), connectors=[connector], cache=cache)
    time.sleep(0.005)
    enrich(Claims(), connectors=[connector], cache=cache)
    assert connector.calls == 2


def test_weight_scaling_unknown_codes_and_bonus_cap() -> None:
    reasons, bonus = to_reasons(
        [
            signal("GITHUB_DATES_MISMATCH", confidence=0.5),
            signal("UNKNOWN_NEGATIVE"),
            signal("IDENTITY_OIDC_VERIFIED", polarity="positive"),
            signal("DOI_VERIFIED", polarity="positive"),
            signal("GITHUB_CORROBORATED", polarity="positive"),
        ]
    )
    assert len(reasons) == 1
    assert reasons[0]["code"] == "GITHUB_DATES_MISMATCH"
    assert reasons[0]["weight"] == 12
    assert reasons[0]["detail"].startswith("Generic verification result. Provenance: source=test;")
    assert bonus == 15
