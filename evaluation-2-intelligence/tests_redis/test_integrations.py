from __future__ import annotations

import time

import fakeredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from firewall.auth import AuthService, AuthSettings
from firewall.auth.audit import InMemoryAuditLog
from firewall.auth.records import OverrideRecord
from firewall.auth.users import ADMIN, CANDIDATE, InMemoryUserStore, make_user
from firewall.auth_routes import build_auth_router
from firewall.connectors.mock_ats import MockATS
from firewall.delivery import Delivery
from firewall.enrichment.models import EnrichmentSignal
from firewall.models import Application, Route
from firewall.redis_layer import (
    CachedLLMClient,
    JsonCache,
    RedisDeliveryQueue,
    RedisLink,
    SignalCache,
    build_cache,
    build_intake_repository,
    build_llm_client,
    build_optional_queue,
    build_signal_cache,
)
from firewall.redis_layer.auth_stores import (
    RedisAuditLog,
    RedisOverrideStore,
    RedisOwnershipRegistry,
    RedisRateLimiter,
    RedisSessionStore,
)
from firewall.redis_layer.intake_store import RedisIntakeRepository
from tests_redis.conftest import make_application

PASSWORD = "correct-horse-battery"


class Clock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("FIREWALL_ADMIN_USER", "FIREWALL_ADMIN_PASSWORD", "FIREWALL_USERS_FILE", "FIREWALL_AUDIT_FILE", "FIREWALL_REDIS_URL"):
        monkeypatch.delenv(name, raising=False)


def boot(link, users, clock, **settings):
    service = AuthService.from_env(clock=clock, settings=AuthSettings(**settings), users=users, link=link)
    app = FastAPI()
    app.include_router(build_auth_router({}.get, service))
    return service, app


def make_users(clock):
    users = InMemoryUserStore()
    users.create(make_user("alice", PASSWORD, CANDIDATE, clock()))
    users.create(make_user("root", PASSWORD, ADMIN, clock()))
    return users


def test_session_survives_restart_and_logout_is_shared(fake_link):
    clock = Clock()
    users = make_users(clock)
    first, app_one = boot(fake_link, users, clock)
    assert isinstance(first.sessions, RedisSessionStore)
    browser = TestClient(app_one)
    login = browser.post("/v1/auth/login", json={"username": "alice", "password": PASSWORD})
    assert login.status_code == 200
    csrf = login.json()["csrf_token"]

    second, app_two = boot(fake_link, users, clock)
    assert second is not first
    restarted = TestClient(app_two)
    restarted.cookies.update(browser.cookies)
    me = restarted.get("/v1/auth/me")
    assert me.status_code == 200 and me.json()["username"] == "alice" and me.json()["csrf_token"] == csrf
    assert restarted.post("/v1/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200

    third, app_three = boot(fake_link, users, clock)
    again = TestClient(app_three)
    again.cookies.update(browser.cookies)
    expired = again.get("/v1/auth/me")
    assert expired.status_code == 401
    assert expired.json() == {"detail": "session expired, please sign in again"}
    assert expired.headers["X-Auth-Reason"] == "session_expired"


def test_without_redis_restart_signs_everyone_out(monkeypatch):
    clock = Clock()
    users = make_users(clock)
    first, app_one = boot(None, users, clock)
    browser = TestClient(app_one)
    browser.post("/v1/auth/login", json={"username": "alice", "password": PASSWORD})
    second, app_two = boot(None, users, clock)
    restarted = TestClient(app_two)
    restarted.cookies.update(browser.cookies)
    assert restarted.get("/v1/auth/me").status_code == 401


def test_sessions_expire_by_idle_and_absolute_time_and_keys_are_safe(fake_link):
    clock = Clock()
    store = RedisSessionStore(fake_link, clock, idle_seconds=100, absolute_seconds=500)
    session = store.create("alice")
    keys = fake_link.client.keys("*")
    assert keys and all(key.startswith("trust:") for key in keys)
    assert all(session.session_id not in key for key in keys)
    assert all(fake_link.client.ttl(key) > 0 for key in keys)
    clock.now += 90
    assert store.get(session.session_id).last_seen == clock.now
    clock.now += 90
    assert store.get(session.session_id) is not None
    clock.now += 101
    assert store.get(session.session_id) is None
    other = store.create("alice")
    for _ in range(8):
        clock.now += 60
        assert store.get(other.session_id) is not None
    clock.now += 60
    assert store.get(other.session_id) is None


def test_delete_for_user_removes_all_sessions(fake_link):
    clock = Clock()
    store = RedisSessionStore(fake_link, clock)
    kept = store.create("bob")
    ids = [store.create("alice").session_id for _ in range(2)]
    assert store.delete_for_user("alice") == 2
    assert all(store.get(item) is None for item in ids)
    assert store.get(kept.session_id) is not None


def test_login_lockout_is_shared_across_restarts(fake_link):
    clock = Clock()
    users = make_users(clock)
    _, app_one = boot(fake_link, users, clock, user_lockout_attempts=3)
    client = TestClient(app_one)
    for _ in range(3):
        assert client.post("/v1/auth/login", json={"username": "alice", "password": "wrong-password-1"}).status_code == 401
    _, app_two = boot(fake_link, users, clock, user_lockout_attempts=3)
    blocked = TestClient(app_two).post("/v1/auth/login", json={"username": "alice", "password": PASSWORD})
    assert blocked.status_code == 429
    assert all(key.startswith("trust:") and fake_link.client.ttl(key) > 0 for key in fake_link.client.keys("*ctr*") + fake_link.client.keys("*lock*"))


def test_rate_limiter_blocks_resets_and_degrades(fake_link):
    clock = Clock()
    limiter = RedisRateLimiter(fake_link, "t", 2, 60, 60, clock)
    assert not limiter.blocked("k")
    limiter.hit("k")
    assert not limiter.blocked("k")
    limiter.hit("k")
    assert limiter.blocked("k")
    assert RedisRateLimiter(fake_link, "t", 2, 60, 60, clock).blocked("k")
    limiter.reset("k")
    assert not limiter.blocked("k")

    server = fakeredis.FakeServer()
    local = RedisRateLimiter(RedisLink(fakeredis.FakeRedis(server=server, decode_responses=True), cooldown_s=60), "t", 2, 60, 60, clock)
    server.connected = False
    local.hit("z")
    local.hit("z")
    assert local.blocked("z")
    clock.now += 61
    assert not local.blocked("z")


def test_audit_log_is_shared_ordered_and_bounded(fake_link):
    clock = Clock()
    first = RedisAuditLog(fake_link, clock, InMemoryAuditLog(clock))
    second = RedisAuditLog(fake_link, clock, InMemoryAuditLog(clock))
    first.append("a", "x", ip="1.1.1.1", detail={"n": 1})
    second.append("b", "y", target="t")
    entries = first.entries(10)
    assert [item.event for item in entries] == ["b", "a"]
    assert [item.seq for item in entries] == [2, 1]
    assert entries[1].detail == {"n": 1}
    assert len(first.entries(1)) == 1
    assert fake_link.client.ttl("trust:audit") > 0


def test_ownership_and_overrides_persist(fake_link):
    owners = RedisOwnershipRegistry(fake_link)
    owners.bind("app", "alice")
    owners.bind("app", "mallory")
    assert RedisOwnershipRegistry(fake_link).owner("app") == "alice"
    assert owners.owner("none") is None
    store = RedisOverrideStore(fake_link)
    record = OverrideRecord("app", "MANUAL_REVIEW", "good reason here", "rita", 5.0, "PASS_TO_ATS", 80)
    store.add(record)
    store.add(OverrideRecord("app", "PASS_TO_ATS", "second reason ok", "rita", 6.0, "PASS_TO_ATS", 80))
    found = RedisOverrideStore(fake_link).for_application("app")
    assert [item.override_route for item in found] == ["MANUAL_REVIEW", "PASS_TO_ATS"]
    assert found[0] == record
    assert all(fake_link.client.ttl(key) > 0 for key in fake_link.client.keys("trust:o*"))


def test_intake_repository_roundtrip_recheck_and_factory(fake_link):
    repo = build_intake_repository(link=fake_link)
    assert isinstance(repo, RedisIntakeRepository)
    record = {
        "consent_id": "c1",
        "application_id": "app-1",
        "parsed": {},
        "findings": [{"source": "github", "fact": "x", "status": "mismatch", "evidence": "e"}],
    }
    repo.save(record)
    other = RedisIntakeRepository(fake_link)
    assert other.get("c1")["findings"][0]["fact"] == "x"
    assert other.consent_for_application("app-1") == "c1"
    assert other.recheck_finding("c1", 0, "upheld", "rita", None, 1.0) == "not_disputed"
    assert other.set_finding_status("c1", 0, "disputed", "no")
    assert not other.set_finding_status("c1", 4, "disputed", "no")
    assert other.recheck_finding("c1", 0, "withdrawn", "rita", "fine", 2.0) == "ok"
    saved = other.get("c1")["findings"][0]
    assert saved["status"] == "mismatch" and saved["recheck"]["by"] == "rita"
    assert other.recheck_finding("c1", 0, "upheld", "rita", None, 3.0) == "not_disputed"
    assert other.set_finding_status("c1", 0, "disputed", "again")
    assert "recheck" not in other.get("c1")["findings"][0]
    assert other.recheck_finding("c1", 0, "upheld", "rita", None, 4.0) == "ok"
    assert other.recheck_finding("c1", 0, "withdrawn", "rita", None, 5.0) == "already_rechecked"
    assert other.recheck_finding("nope", 0, "upheld", "rita", None, 3.0) == "unknown"
    assert all(key.startswith("trust:") and fake_link.client.ttl(key) > 0 for key in fake_link.client.keys("*"))


def test_factories_without_redis_return_plain_variants(monkeypatch):
    from firewall.intake_records import InMemoryIntakeRepository

    assert isinstance(build_intake_repository(environ={}), InMemoryIntakeRepository)
    assert build_optional_queue(environ={}) is None
    assert build_signal_cache(environ={}) is None
    assert build_llm_client(object(), environ={}) is None
    assert type(build_cache("x", environ={})) is JsonCache


def test_llm_cache_calls_inner_once_and_signal_cache_roundtrips(fake_link):
    class Inner:
        calls = 0

        def rewrite_summary(self, codes, skills):
            Inner.calls += 1
            return "plain words"

        def embed(self, text):
            Inner.calls += 1
            return (0.5, 1.0)

    client = build_llm_client(Inner(), link=fake_link)
    assert isinstance(client, CachedLLMClient)
    assert client.rewrite_summary(["A"], ["py"]) == "plain words"
    assert client.rewrite_summary(["A"], ["py"]) == "plain words"
    assert client.embed("t") == (0.5, 1.0) and client.embed("t") == (0.5, 1.0)
    assert Inner.calls == 2
    assert all(key.startswith("trust:cache:llm:") for key in fake_link.client.keys("*"))

    cache = build_signal_cache(link=fake_link)
    assert isinstance(cache, SignalCache)
    signal = EnrichmentSignal(code="DOI_VERIFIED", polarity="positive", severity="low", confidence=0.9, detail="d", source="crossref")
    cache.set("k", [signal])
    assert cache.get("k") == [signal]
    assert cache.get("missing") is None


class FlakyATS(MockATS):
    fail = True

    def receive(self, application: Application) -> None:
        if self.fail:
            raise RuntimeError("down")
        super().receive(application)


def test_delivery_over_redis_queue_dead_letters_and_replays(fake_link):
    queue = RedisDeliveryQueue(fake_link, "delivery")
    ats = FlakyATS()
    config = {
        "destinations": {"ats": {"type": "mock"}},
        "routes": {"PASS_TO_ATS": ["ats"], "ADDITIONAL_VERIFICATION": [], "MANUAL_REVIEW": []},
    }
    delivery = Delivery(config, ats, queue=queue, workers=1)
    delivery.deliver(Route.PASS_TO_ATS, make_application("app-q"))
    deadline = time.monotonic() + 3
    while not delivery.dead_letters and time.monotonic() < deadline:
        time.sleep(0.05)
    assert [letter.application_id for letter in delivery.dead_letters] == ["app-q"]
    assert delivery.status()["dead_letters"] == 1
    assert all(key.startswith("trust:") and fake_link.client.ttl(key) > 0 for key in fake_link.client.keys("*"))
    FlakyATS.fail = False
    try:
        assert delivery.replay_dead_letters() == {"replayed": 1, "skipped": 0}
        assert delivery.wait(3)
        deadline = time.monotonic() + 3
        while not ats.applications() and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        FlakyATS.fail = True
    assert [item.application_id for item in ats.applications()] == ["app-q"]
    assert delivery.dead_letters == ()


def test_ownership_lists_applications_per_user_newest_first(fake_link):
    from firewall.auth.records import InMemoryOwnershipRegistry

    owners = RedisOwnershipRegistry(fake_link)
    owners.bind("a1", "alice")
    owners.bind("b1", "bob")
    owners.bind("a2", "alice")
    owners.bind("a2", "bob")
    assert owners.owned_by("alice") == ["a2", "a1"]
    assert owners.owned_by("bob") == ["b1"]
    assert RedisOwnershipRegistry(fake_link).owned_by("alice") == ["a2", "a1"]
    assert owners.owned_by("nobody") == []
    memory = InMemoryOwnershipRegistry()
    memory.bind("x1", "alice")
    memory.bind("y1", "bob")
    memory.bind("x2", "alice")
    assert memory.owned_by("alice") == ["x2", "x1"]
