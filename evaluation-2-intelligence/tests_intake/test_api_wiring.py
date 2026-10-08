from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from firewall.api import MOCK_ATS, STORE, app
from firewall.auth import get_service
from firewall.auth.records import build_override
from firewall.connectors.mock_ats import MockATS
from firewall.delivery import Delivery
from firewall.delivery_routes import build_delivery_router
from firewall.models import Application, Route
from tests.conftest import make_application, make_job


client = TestClient(app)


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    for name in ("FIREWALL_API_KEY", "FIREWALL_REQUIRE_AUTH", "FIREWALL_ADMIN_USER", "FIREWALL_ADMIN_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    STORE.clear()
    MOCK_ATS.clear()
    yield
    STORE.clear()
    MOCK_ATS.clear()


def evaluate(application_id: str):
    body = {
        "application": make_application(application_id).model_dump(mode="json"),
        "job": make_job().model_dump(mode="json"),
    }
    response = client.post("/v1/applications/evaluate", json=body)
    assert response.status_code == 200
    return response.json()


def test_overrides_history_and_decision_list_fields():
    decision = evaluate("app-ov")
    assert client.get("/v1/decisions/app-missing/overrides").status_code == 404
    assert client.get("/v1/decisions/app-ov/overrides").json() == []
    first = client.get("/v1/decisions").json()[0]
    assert first["override"] is None and first["consent_id"] is None
    service = get_service()
    for route in ("MANUAL_REVIEW", "PASS_TO_ATS"):
        service.overrides.add(
            build_override("app-ov", route, "recruiter judgement call", "rita", decision["route"], decision["score"], service.clock)
        )
    history = client.get("/v1/decisions/app-ov/overrides").json()
    assert [item["override_route"] for item in history] == ["MANUAL_REVIEW", "PASS_TO_ATS"]
    assert history[0]["original_score"] == decision["score"]
    listed = client.get("/v1/decisions").json()[0]
    assert listed["override"]["override_route"] == "PASS_TO_ATS"
    assert listed["decision"] == client.get("/v1/decisions/app-ov").json()
    assert listed["decision"]["route"] == decision["route"]


def test_consent_id_shows_in_decision_list():
    evaluate("app-ci")
    response = client.post("/v1/intake/profile", data={"consent": "true", "application_id": "app-ci", "github_url": "https://github.com/ada-real"})
    consent_id = response.json()["consent_id"]
    assert client.get("/v1/decisions").json()[0]["consent_id"] == consent_id
    view = client.get(f"/v1/intake/{consent_id}/recruiter").json()
    assert view["application_id"] == "app-ci"


def test_intake_uses_stored_resume_context():
    evaluate("app-ctx")
    consent_id = client.post("/v1/intake/profile", data={"consent": "true", "application_id": "app-ctx"}).json()["consent_id"]
    recheck = client.post(f"/v1/intake/{consent_id}/recheck", json={"finding_index": 0, "outcome": "upheld"})
    assert recheck.status_code == 404


def test_recheck_and_overrides_are_guarded_when_auth_enforced(monkeypatch):
    monkeypatch.setenv("FIREWALL_REQUIRE_AUTH", "1")
    anonymous = TestClient(app)
    assert anonymous.get("/v1/decisions/x/overrides").status_code == 401
    assert anonymous.post("/v1/intake/x/recheck", json={"finding_index": 0, "outcome": "upheld"}).status_code == 401
    assert anonymous.post("/v1/delivery/replay-dead-letters").status_code == 401


class FlakyATS(MockATS):
    def __init__(self) -> None:
        super().__init__()
        self.fail = True

    def receive(self, application: Application) -> None:
        if self.fail:
            raise RuntimeError("down")
        super().receive(application)


def make_flaky_delivery(**options) -> tuple[Delivery, FlakyATS]:
    ats = FlakyATS()
    config = {
        "destinations": {"ats": {"type": "mock"}},
        "routes": {"PASS_TO_ATS": ["ats"], "ADDITIONAL_VERIFICATION": [], "MANUAL_REVIEW": []},
    }
    return Delivery(config, ats, **options), ats


def test_replay_resends_dead_letters_in_process():
    delivery, ats = make_flaky_delivery()
    delivery.deliver(Route.PASS_TO_ATS, make_application("app-r1"))
    assert delivery.wait(2)
    assert [letter.application_id for letter in delivery.dead_letters] == ["app-r1"]
    assert delivery.replay_dead_letters() == {"replayed": 1, "skipped": 0}
    assert delivery.wait(2)
    assert [letter.application_id for letter in delivery.dead_letters] == ["app-r1"]
    ats.fail = False
    assert delivery.replay_dead_letters() == {"replayed": 1, "skipped": 0}
    assert delivery.wait(2)
    assert delivery.dead_letters == ()
    assert [item.application_id for item in ats.applications()] == ["app-r1"]
    assert delivery.replay_dead_letters() == {"replayed": 0, "skipped": 0}


def test_replay_skips_letters_that_cannot_be_resent():
    from firewall.delivery import DeliveryDeadLetter

    delivery, ats = make_flaky_delivery()
    delivery._dead_letters.append(DeliveryDeadLetter("a", "ats", 1, "X"))
    delivery._dead_letters.append(DeliveryDeadLetter("b", "gone", 1, "X", {"application_id": "b"}))
    assert delivery.replay_dead_letters() == {"replayed": 0, "skipped": 2}


def test_replay_route_is_audited_and_has_no_secrets():
    delivery, ats = make_flaky_delivery()
    delivery.deliver(Route.PASS_TO_ATS, make_application("app-r2"))
    delivery.wait(2)
    events = []

    class Log:
        def append(self, event, actor, target="", ip="", detail=None):
            events.append((event, actor, detail))

    test_app = FastAPI()
    ats.fail = False
    test_app.include_router(build_delivery_router(delivery, guard=lambda: type("P", (), {"username": "root"})(), audit=lambda: Log()))
    response = TestClient(test_app).post("/v1/delivery/replay-dead-letters")
    assert response.json() == {"replayed": 1, "skipped": 0}
    assert events == [("delivery_replay", "root", {"replayed": 1, "skipped": 0})]
    assert "ada@example.com" not in response.text


def test_real_app_replay_route_works_and_logs_audit():
    response = client.post("/v1/delivery/replay-dead-letters")
    assert response.status_code == 200
    assert set(response.json()) == {"replayed", "skipped"}
    assert get_service().audit.entries(1)[0].event == "delivery_replay"
