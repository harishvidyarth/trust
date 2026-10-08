from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from firewall.api import MOCK_ATS, STORE, app
from firewall.models import Project


client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_api_state(monkeypatch):
    monkeypatch.delenv("FIREWALL_WEBHOOK_SECRET", raising=False)
    STORE.clear()
    MOCK_ATS.clear()
    yield
    STORE.clear()
    MOCK_ATS.clear()


def payload(application_factory, job_factory, **application_overrides):
    return {
        "application": application_factory(**application_overrides).model_dump(mode="json"),
        "job": job_factory().model_dump(mode="json"),
    }


def greenhouse_payload():
    return {
        "id": "gh-1",
        "candidate": {
            "name": "Lin Chen",
            "email": "lin@example.com",
            "phone": "9876543210",
            "skills": ["Python"],
            "experience": [{"company": "Acme", "title": "Engineer", "start": "2020-01", "end": "2025-01"}],
            "projects": [{"name": "Queue", "description": "Built a durable distributed task queue"}],
        },
        "job": {"id": "job-python", "must_have_skills": ["Python"], "min_years": 2},
        "metadata": {
            "device_id": "gh-device",
            "ip": "203.0.113.8",
            "submitted_at": 1767225600,
            "session_seconds": 80,
            "paste_char_ratio": 0.1,
        },
    }


def test_health_and_missing_decision():
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/v1/decisions/missing").status_code == 404


def test_evaluate_get_stats_and_mock_ats(application_factory, job_factory):
    honest = client.post("/v1/applications/evaluate", json=payload(application_factory, job_factory))
    assert honest.status_code == 200
    assert honest.json()["route"] == "PASS_TO_ATS"

    weak = client.post(
        "/v1/applications/evaluate",
        json=payload(
            application_factory,
            job_factory,
            application_id="weak",
            name="Different Person",
            email="different@example.com",
            phone="8000000000",
            skills=[],
            projects=[Project(name="Accessibility Audit", description="Reviewed keyboard navigation across public forms")],
        ),
    )
    assert weak.status_code == 200
    assert weak.json()["route"] == "MANUAL_REVIEW"

    stored = client.get("/v1/decisions/app-1")
    assert stored.json() == honest.json()
    stats = client.get("/v1/stats").json()
    assert stats["total_received"] == 2
    assert stats["counts_per_route"]["PASS_TO_ATS"] == 1
    assert stats["top_reason_codes"][0]["code"] == "QUAL_MISSING_MUST_HAVE"

    ats_apps = client.get("/v1/mock-ats/applications").json()
    assert [item["application_id"] for item in ats_apps] == ["app-1"]


def test_greenhouse_webhook_adapter():
    response = client.post("/v1/webhooks/greenhouse", json=greenhouse_payload())
    assert response.status_code == 200
    assert response.json()["route"] == "PASS_TO_ATS"
    assert client.get("/v1/mock-ats/applications").json()[0]["application_id"] == "gh-1"


@pytest.mark.parametrize("signature", [None, "not-a-valid-digest"], ids=["missing", "invalid"])
def test_greenhouse_webhook_rejects_missing_or_bad_signature(monkeypatch, signature):
    monkeypatch.setenv("FIREWALL_WEBHOOK_SECRET", "test-webhook-secret")
    body = json.dumps(greenhouse_payload(), separators=(",", ":")).encode()
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["Signature"] = signature

    response = client.post("/v1/webhooks/greenhouse", content=body, headers=headers)

    assert response.status_code == 401


def test_greenhouse_webhook_accepts_valid_signature(monkeypatch):
    secret = "test-webhook-secret"
    monkeypatch.setenv("FIREWALL_WEBHOOK_SECRET", secret)
    body = json.dumps(greenhouse_payload(), separators=(",", ":")).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    response = client.post(
        "/v1/webhooks/greenhouse",
        content=body,
        headers={"Content-Type": "application/json", "Signature": signature},
    )

    assert response.status_code == 200


def test_greenhouse_webhook_treats_empty_configured_secret_as_enabled(monkeypatch):
    monkeypatch.setenv("FIREWALL_WEBHOOK_SECRET", "")
    body = json.dumps(greenhouse_payload(), separators=(",", ":")).encode()
    empty_key_signature = hmac.new(b"", body, hashlib.sha256).hexdigest()
    response = client.post(
        "/v1/webhooks/greenhouse",
        content=body,
        headers={"Content-Type": "application/json", "Signature": empty_key_signature},
    )
    assert response.status_code == 401
