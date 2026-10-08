from __future__ import annotations

import logging
import time

import fakeredis
import pytest
import redis

from firewall.redis_layer import (
    DeliveryQueue,
    FixedWindowCounter,
    JsonCache,
    QueueItem,
    RedisApplicationStore,
    RedisDeliveryQueue,
    RedisFixedWindowCounter,
    RedisJsonCache,
    RedisLink,
    build_cache,
    build_counter,
    build_queue,
    build_store,
    cache_digest,
    configured_url,
    redact_url,
    redis_status,
    validate_redis_url,
)
from firewall.store import InMemoryApplicationStore
from tests_redis.conftest import stored_decision


class DeadClient:
    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise redis.ConnectionError("down")

        return fail

    def pipeline(self, *args, **kwargs):
        raise redis.ConnectionError("down")


@pytest.fixture
def dead_link():
    return RedisLink(DeadClient(), cooldown_s=60)


@pytest.fixture(params=["memory", "fakeredis", "live"])
def counter(request):
    if request.param == "memory":
        return FixedWindowCounter()
    return RedisFixedWindowCounter(request.getfixturevalue("fake_link" if request.param == "fakeredis" else "live_link"))


def test_counter_counts_and_resets(counter):
    assert [counter.hit("login:a", 60) for _ in range(3)] == [1, 2, 3]
    assert counter.hit("login:b", 60) == 1
    counter.reset("login:a")
    assert counter.hit("login:a", 60) == 1
    with pytest.raises(ValueError):
        counter.hit("x", 0)


def test_counter_window_expires(counter):
    assert counter.hit("short", 1) == 1
    assert counter.hit("short", 1) == 2
    time.sleep(1.2)
    assert counter.hit("short", 1) == 1


def test_redis_counter_sets_ttl_once(fake_link):
    counter = RedisFixedWindowCounter(fake_link)
    counter.hit("k", 100)
    first = fake_link.client.ttl("trust:ctr:k")
    counter.hit("k", 5000)
    assert 0 < fake_link.client.ttl("trust:ctr:k") <= first


@pytest.fixture(params=["memory", "fakeredis", "live"])
def cache(request):
    if request.param == "memory":
        return JsonCache()
    return RedisJsonCache(request.getfixturevalue("fake_link" if request.param == "fakeredis" else "live_link"), "t")


def test_cache_roundtrip_and_digest_keying(cache):
    assert cache.get({"q": 1, "r": 2}) is None
    assert cache.set({"q": 1, "r": 2}, {"score": 7}) is True
    assert cache.get({"r": 2, "q": 1}) == {"score": 7}
    assert cache.get("other") is None
    assert len(cache_digest("x")) == 64


def test_cache_ttl_and_size_cap(cache):
    assert cache.set("short", [1], ttl_s=1)
    time.sleep(1.2)
    assert cache.get("short") is None
    assert cache.set("big", "x" * 300_000) is False
    assert cache.get("big") is None
    assert cache.set("bad", object()) is False
    assert cache.set("zero", 1, ttl_s=0) is False


def test_memory_cache_evicts_oldest():
    cache = JsonCache(max_entries=2)
    for name in ("a", "b", "c"):
        cache.set(name, name)
    assert cache.get("a") is None and cache.get("c") == "c"


def test_redis_cache_keys_hold_digest_only(fake_link):
    RedisJsonCache(fake_link, "enrich").set("secret-input@example.com", {"a": 1})
    key = next(iter(fake_link.client.scan_iter()))
    assert key.startswith("trust:cache:enrich:") and "secret-input" not in key


@pytest.fixture(params=["memory", "fakeredis", "live"])
def queue(request):
    if request.param == "memory":
        return DeliveryQueue()
    return RedisDeliveryQueue(request.getfixturevalue("fake_link" if request.param == "fakeredis" else "live_link"), "t")


def test_queue_fifo_requeue_and_dead_letter(queue):
    assert queue.pop(0) is None
    first = QueueItem("a1", "ats", {"x": 1})
    assert queue.push(first) and queue.push(QueueItem("a2", "ats"))
    assert queue.depth() == 2
    popped = queue.pop(0.2)
    assert popped == first
    assert queue.requeue(popped)
    assert queue.pop(0).application_id == "a2"
    again = queue.pop(0)
    assert again.application_id == "a1" and again.attempts == 1
    letter = queue.dead_letter(again, "TimeoutError")
    assert (letter.application_id, letter.destination, letter.attempts, letter.error) == ("a1", "ats", 1, "TimeoutError")
    assert queue.dead_letters() == (letter,)
    assert queue.depth() == 0


def test_queue_pop_timeout_returns_none(queue):
    started = time.monotonic()
    assert queue.pop(1) is None
    assert 0.8 < time.monotonic() - started < 3


def test_queue_dead_letter_cap_and_oversize_rejected(fake_link):
    queue = RedisDeliveryQueue(fake_link, "cap", dead_letter_limit=3)
    for index in range(5):
        queue.dead_letter(QueueItem(f"a{index}", "d"), "E")
    assert [letter.application_id for letter in queue.dead_letters()] == ["a2", "a3", "a4"]
    assert queue.push(QueueItem("big", "d", {"blob": "x" * 300_000})) is False


def test_degradation_store_counter_cache_queue(dead_link, application_factory, caplog):
    store = RedisApplicationStore(dead_link)
    store.save(application_factory(application_id="m"), stored_decision("m"))
    assert [item.application_id for item in store.by_email("ada@example.com")] == ["m"]
    assert store.get_decision("m").score == 100
    store.clear()
    assert store.applications() == ()
    counter = RedisFixedWindowCounter(dead_link)
    assert (counter.hit("k", 10), counter.hit("k", 10)) == (1, 2)
    counter.reset("k")
    assert counter.hit("k", 10) == 1
    cache = RedisJsonCache(dead_link, "n")
    assert cache.set("k", {"v": 1}) and cache.get("k") == {"v": 1}
    queue = RedisDeliveryQueue(dead_link, "q")
    assert queue.push(QueueItem("a", "d")) and queue.depth() == 1
    assert queue.pop(0).application_id == "a"
    assert queue.pop(0.3) is None


def test_recovery_after_cooldown(application_factory):
    server = fakeredis.FakeServer()
    link = RedisLink(fakeredis.FakeRedis(server=server, decode_responses=True), cooldown_s=0.05)
    server.connected = False
    store = RedisApplicationStore(link)
    store.save(application_factory(application_id="outage"), stored_decision("outage"))
    server.connected = True
    time.sleep(0.1)
    store.save(application_factory(application_id="back", email="b@example.com", phone="9555555555"), stored_decision("back"))
    assert [item.application_id for item in store.applications()] == ["back"]


def test_url_validation_and_redaction():
    assert validate_redis_url("redis://127.0.0.1:6379/0")
    assert validate_redis_url("rediss://user:pw@cache.example.com:6380/1")
    for bad in ("http://x", "ftp://h", "redis://", "", "unix:///tmp/s", "redis://h:notaport", 5):
        with pytest.raises(ValueError) as caught:
            validate_redis_url(bad)
        assert "pw" not in str(caught.value)
    shown = redact_url("rediss://user:hunter2@cache.example.com:6380/1")
    assert shown == "rediss://cache.example.com:6380/1" and "hunter2" not in shown
    assert redact_url("garbage") == "redis://<invalid>"


def test_env_configuration_and_no_password_in_logs(caplog):
    assert configured_url({}) is None
    assert configured_url({"FIREWALL_REDIS_URL": "  "}) is None
    with caplog.at_level(logging.WARNING):
        assert configured_url({"FIREWALL_REDIS_URL": "http://user:hunter2@host:1"}) is None
    assert "hunter2" not in caplog.text
    assert isinstance(build_store({}), InMemoryApplicationStore)
    assert type(build_counter({})) is FixedWindowCounter
    assert type(build_cache("n", {})) is JsonCache
    assert type(build_queue("q", {})) is DeliveryQueue


def test_factories_return_redis_variants_with_link(fake_link):
    assert isinstance(build_store(link=fake_link), RedisApplicationStore)
    assert isinstance(build_counter(link=fake_link), RedisFixedWindowCounter)
    assert isinstance(build_cache("n", link=fake_link), RedisJsonCache)
    assert isinstance(build_queue("q", link=fake_link), RedisDeliveryQueue)


def test_unreachable_url_never_raises_and_log_has_no_secret(caplog):
    url = "redis://user:hunter2@127.0.0.1:1/0"
    env = {"FIREWALL_REDIS_URL": url}
    with caplog.at_level(logging.WARNING):
        store = build_store(env)
        store.save(__import__("tests_redis.conftest", fromlist=["x"]).make_application(), stored_decision("app-1"))
        assert store.applications()[0].application_id == "app-1"
        assert redis_status(env) == {"configured": True, "reachable": False, "latency_ms": None}
    assert "hunter2" not in caplog.text


def test_status_shapes(fake_link):
    assert redis_status({}) == {"configured": False, "reachable": False, "latency_ms": None}
    result = redis_status(link=fake_link)
    assert result["configured"] and result["reachable"] and result["latency_ms"] >= 0
    assert set(result) == {"configured", "reachable", "latency_ms"}


def test_live_status_and_env_factory(live_link):
    env = {"FIREWALL_REDIS_URL": "redis://127.0.0.1:6379/15"}
    assert redis_status(env)["reachable"] is True
    assert isinstance(build_store(env), RedisApplicationStore)
