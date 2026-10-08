from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from firewall.connectors.mock_ats import MockATS
from firewall.delivery import Delivery
from firewall.delivery_routes import build_delivery_router, build_inbox_router


def make_delivery() -> Delivery:
    config = {
        "destinations": {
            "hook": {"type": "queue", "url": "https://hooks.example.com/in?key=SUPERSECRETVALUE", "secret_env": "HOOK_SECRET"},
            "ats": {"type": "mock"},
        },
        "routes": {"PASS_TO_ATS": ["hook", "ats"], "ADDITIONAL_VERIFICATION": ["ats"], "MANUAL_REVIEW": ["ats"]},
    }
    return Delivery(config, MockATS(), environ={"HOOK_SECRET": "TOPSECRETVALUE"})


def allow():
    return None


def test_status_has_no_secrets():
    app = FastAPI()
    app.include_router(build_inbox_router(make_delivery(), guard=allow))
    response = TestClient(app).get("/v1/delivery/status")
    assert response.status_code == 200
    text = json.dumps(response.json())
    assert "TOPSECRETVALUE" not in text
    assert "SUPERSECRETVALUE" not in text
    assert "HOOK_SECRET" not in text
    assert {"routes", "destinations", "pending", "dead_letters"} <= set(response.json())


def test_default_guard_denies_everything():
    app = FastAPI()
    app.include_router(build_delivery_router(make_delivery()))
    app.include_router(build_inbox_router(make_delivery()))
    client = TestClient(app)
    assert client.get("/v1/delivery/status").status_code == 403
    assert client.get("/v1/delivery/inbox").status_code == 403
    assert client.post("/v1/delivery/replay-dead-letters").status_code == 403


def test_replay_requires_support_or_uses_it():
    class Bare:
        def status(self):
            return {}

    app = FastAPI()
    app.include_router(build_delivery_router(Bare(), guard=allow))
    assert TestClient(app).post("/v1/delivery/replay-dead-letters").status_code == 501

    class Replaying:
        def status(self):
            return {"pending": 0, "api_key": "leak"}

        def replay_dead_letters(self):
            return 3

    app = FastAPI()
    app.include_router(build_delivery_router(Replaying(), guard=allow))
    app.include_router(build_inbox_router(Replaying(), guard=allow))
    client = TestClient(app)
    assert client.post("/v1/delivery/replay-dead-letters").json() == {"replayed": 3}
    assert client.get("/v1/delivery/status").json() == {"pending": 0}


def test_inbox_lists_each_waiting_application():
    from firewall.models import Route
    from tests.conftest import make_application

    delivery = Delivery(
        {
            "destinations": {"ats": {"type": "mock"}, "box": {"type": "queue"}},
            "routes": {"PASS_TO_ATS": ["ats"], "ADDITIONAL_VERIFICATION": ["box"], "MANUAL_REVIEW": ["box"]},
        },
        MockATS(),
        environ={},
    )
    delivery.deliver(Route.MANUAL_REVIEW, make_application("w-1"))
    assert delivery.wait(3.0)
    app = FastAPI()
    app.include_router(build_inbox_router(delivery, guard=allow))
    body = TestClient(app).get("/v1/delivery/inbox").json()
    assert body["inboxes"][0]["name"] == "box"
    assert body["inboxes"][0]["items"][0]["application_id"] == "w-1"
