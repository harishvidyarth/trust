from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from firewall.auth import (
    AuthService,
    AuthSettings,
    Principal,
    bind_application,
    require_application_access,
    require_role,
)
from firewall.auth.users import ADMIN, CANDIDATE, RECRUITER, InMemoryUserStore, make_user
from firewall.auth_routes import build_auth_router
from firewall.models import Decision, Route


PASSWORD = "correct-horse-battery"


class FakeClock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Env:
    def __init__(self, service: AuthService, app: FastAPI, clock: FakeClock, decisions: dict[str, Decision]) -> None:
        self.service = service
        self.app = app
        self.clock = clock
        self.decisions = decisions

    def client(self) -> TestClient:
        return TestClient(self.app)

    def login(self, username: str, password: str | None = None) -> tuple[TestClient, str]:
        client = self.client()
        response = client.post("/v1/auth/login", json={"username": username, "password": PASSWORD if password is None else password})
        assert response.status_code == 200, response.text
        return client, response.json()["csrf_token"]


def make_env(monkeypatch: pytest.MonkeyPatch, **settings: object) -> Env:
    monkeypatch.delenv("FIREWALL_API_KEY", raising=False)
    monkeypatch.delenv("FIREWALL_ADMIN_USER", raising=False)
    monkeypatch.delenv("FIREWALL_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("FIREWALL_USERS_FILE", raising=False)
    monkeypatch.delenv("FIREWALL_AUDIT_FILE", raising=False)
    clock = FakeClock()
    store = InMemoryUserStore()
    for name, role in (("alice", CANDIDATE), ("bob", CANDIDATE), ("rita", RECRUITER), ("root", ADMIN)):
        store.create(make_user(name, PASSWORD, role, clock()))
    service = AuthService.from_env(clock=clock, settings=AuthSettings(**settings), users=store)
    decisions = {
        "app-alice": Decision(application_id="app-alice", score=82, route=Route.PASS_TO_ATS, summary="ok"),
        "app-bob": Decision(application_id="app-bob", score=35, route=Route.MANUAL_REVIEW, summary="check"),
    }
    app = FastAPI()
    app.include_router(build_auth_router(decisions.get, service))
    bind_application("app-alice", "alice")
    bind_application("app-bob", "bob")

    @app.get("/v1/decisions/{application_id}")
    def read_decision(application_id: str, principal: Principal = Depends(require_application_access)) -> dict[str, object]:
        return decisions[application_id].model_dump(mode="json")

    @app.post("/v1/applications/evaluate")
    def ingest(principal: Principal = Depends(require_role("service", "recruiter", "admin"))) -> dict[str, str]:
        return {"by": principal.role}

    @app.get("/v1/stats")
    def stats(principal: Principal = Depends(require_role("recruiter", "admin"))) -> dict[str, str]:
        return {"by": principal.role}

    return Env(service, app, clock, decisions)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Env:
    return make_env(monkeypatch)
