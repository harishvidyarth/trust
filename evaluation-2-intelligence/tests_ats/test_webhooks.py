from __future__ import annotations

import hashlib
import hmac
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from firewall.models import Application, Decision, JobRequirements, Route
from firewall.webhooks import build_router


def payload() -> dict[str, object]:
    return {
        "id": "lever-app-1",
        "candidate": {
            "name": "Synthetic Candidate",
            "email": "candidate@example.test",
            "phone": "+1-555-0100",
            "skills": ["Python"],
        },
        "posting": {
            "id": "job-1",
            "must_have_skills": ["Python"],
            "nice_to_have": [],
            "min_years": 0,
        },
        "metadata": {
            "device_id": "device-1",
            "ip": "203.0.113.8",
            "submitted_at": 1_800_000_000,
            "session_seconds": 60,
            "paste_char_ratio": 0,
        },
    }


def client(secret: str | None) -> tuple[TestClient, list[Application]]:
    seen: list[Application] = []

    def evaluate(application: Application, job: JobRequirements) -> Decision:
        seen.append(application)
        return Decision(
            application_id=application.application_id,
            score=100,
            route=Route.PASS_TO_ATS,
            summary="Synthetic decision.",
        )

    app = FastAPI()
    environ = {} if secret is None else {"FIREWALL_LEVER_WEBHOOK_SECRET": secret}
    app.include_router(build_router(evaluate, environ=environ))
    return TestClient(app), seen


def test_lever_webhook_accepts_valid_signature_and_returns_decision() -> None:
    secret = "synthetic-secret"
    test_client, seen = client(secret)
    body = json.dumps(payload(), separators=(",", ":")).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    response = test_client.post(
        "/v1/webhooks/lever",
        content=body,
        headers={"Content-Type": "application/json", "Signature": signature},
    )

    assert response.status_code == 200
    assert response.json()["application_id"] == "lever-app-1"
    assert [application.application_id for application in seen] == ["lever-app-1"]


def test_lever_webhook_rejects_bad_signature() -> None:
    test_client, seen = client("synthetic-secret")

    response = test_client.post(
        "/v1/webhooks/lever",
        json=payload(),
        headers={"Signature": "bad"},
    )

    assert response.status_code == 401
    assert seen == []


def test_lever_webhook_rejects_missing_or_empty_secret() -> None:
    missing_client, _ = client(None)
    empty_client, _ = client("")

    assert missing_client.post("/v1/webhooks/lever", json=payload()).status_code == 401
    assert empty_client.post("/v1/webhooks/lever", json=payload()).status_code == 401


def test_lever_webhook_caps_body_at_one_mibibyte() -> None:
    test_client, _ = client("synthetic-secret")

    response = test_client.post(
        "/v1/webhooks/lever",
        content=b"x" * 1_048_577,
        headers={"Content-Length": "1048577", "Signature": "bad"},
    )

    assert response.status_code == 413
