from __future__ import annotations

import socket
from datetime import datetime, timezone
from urllib.parse import urlsplit

import fakeredis
import pytest
import redis

from firewall.models import Application, Candidate, Decision, Experience, JobRequirements, Project, SubmissionSignals, Route
from firewall.redis_layer.client import RedisLink
from firewall.store import InMemoryApplicationStore


BASE_TS = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
LIVE_URL = "redis://127.0.0.1:6379/15"


def make_application(
    application_id: str = "app-1",
    job_id: str = "job-1",
    *,
    name: str = "Ada Lovelace",
    email: str = "ada@example.com",
    phone: str = "+91 98765 43210",
    projects: list[Project] | None = None,
    device_id: str = "device-1",
    ip: str = "203.0.113.1",
    submitted_at: float = BASE_TS,
) -> Application:
    return Application(
        application_id=application_id,
        job_id=job_id,
        candidate=Candidate(
            name=name,
            email=email,
            phone=phone,
            skills=["Python", "K8s"],
            experience=[Experience(company="Analytical Engines", title="Engineer", start="2020-01", end="2025-01")],
            projects=projects
            if projects is not None
            else [Project(name="Scheduler", description="Built a reliable distributed job scheduling service in modern Python")],
        ),
        signals=SubmissionSignals(
            device_id=device_id,
            ip=ip,
            session_seconds=120,
            paste_char_ratio=0.2,
            submitted_at=submitted_at,
        ),
    )


def make_job() -> JobRequirements:
    return JobRequirements(must_have_skills=["Python", "Kubernetes"], nice_to_have=[], min_years=3)


def stored_decision(application_id: str, score: int = 100) -> Decision:
    return Decision(application_id=application_id, score=score, route=Route.PASS_TO_ATS, summary="stored")


def live_available() -> bool:
    parsed = urlsplit(LIVE_URL)
    try:
        with socket.create_connection((parsed.hostname, parsed.port), timeout=0.3):
            pass
        probe = redis.Redis.from_url(LIVE_URL, socket_timeout=0.5, socket_connect_timeout=0.5)
        return bool(probe.ping())
    except (OSError, redis.RedisError):
        return False


@pytest.fixture
def application_factory():
    return make_application


@pytest.fixture
def job_factory():
    return make_job


@pytest.fixture
def fake_link():
    server = fakeredis.FakeServer()
    return RedisLink(fakeredis.FakeRedis(server=server, decode_responses=True))


@pytest.fixture
def live_link():
    if not live_available():
        pytest.skip("no live redis on 127.0.0.1:6379")
    client = redis.Redis.from_url(LIVE_URL, socket_timeout=0.5, socket_connect_timeout=0.5, decode_responses=True)
    for key in client.scan_iter(match="trust:*"):
        client.delete(key)
    yield RedisLink(client)
    for key in client.scan_iter(match="trust:*"):
        client.delete(key)


@pytest.fixture(params=["memory", "fakeredis", "live"])
def backend(request):
    if request.param == "memory":
        return None
    if request.param == "fakeredis":
        return request.getfixturevalue("fake_link")
    return request.getfixturevalue("live_link")


@pytest.fixture
def store(backend):
    if backend is None:
        return InMemoryApplicationStore()
    from firewall.redis_layer import RedisApplicationStore

    return RedisApplicationStore(backend)
