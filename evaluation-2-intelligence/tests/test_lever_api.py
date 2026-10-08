from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from firewall.api import DELIVERY, MOCK_ATS, STORE, app


client = TestClient(app)
SECRET = "unit-test-secret"


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.delenv("FIREWALL_API_KEY", raising=False)
    monkeypatch.setenv("FIREWALL_LEVER_WEBHOOK_SECRET", SECRET)
    STORE.clear()
    MOCK_ATS.clear()
    yield
    STORE.clear()
    MOCK_ATS.clear()


def body(application_id: str = "lever-1") -> bytes:
    return json.dumps(
        {
            "id": application_id,
            "candidate": {
                "name": "Grace Hopper",
                "email": "grace@example.com",
                "phone": "+91 90000 11111",
                "skills": ["Python", "Kubernetes"],
                "experience": [{"company": "Navy Labs", "title": "Engineer", "start": "2018-01", "end": "2024-01"}],
                "projects": [{"name": "Compiler", "description": "Built a compiler front end with a typed intermediate form"}],
            },
            "posting": {"id": "job-9", "must_have_skills": ["Python"], "nice_to_have": [], "min_years": 2},
            "metadata": {
                "device_id": "dev-lever",
                "ip": "203.0.113.9",
                "submitted_at": time.time() - 60,
                "session_seconds": 120,
                "paste_char_ratio": 0.1,
            },
        }
    ).encode()


def sign(payload: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def test_lever_rejects_missing_and_bad_signature():
    payload = body()
    assert client.post("/v1/webhooks/lever", content=payload).status_code == 401
    assert client.post("/v1/webhooks/lever", content=payload, headers={"Signature": "0" * 64}).status_code == 401


def test_lever_rejects_when_secret_not_configured(monkeypatch):
    monkeypatch.delenv("FIREWALL_LEVER_WEBHOOK_SECRET")
    payload = body()
    assert client.post("/v1/webhooks/lever", content=payload, headers={"Signature": sign(payload)}).status_code == 401


def test_lever_valid_signature_returns_decision_and_delivers_pass():
    payload = body()
    response = client.post("/v1/webhooks/lever", content=payload, headers={"Signature": sign(payload)})
    assert response.status_code == 200
    decision = response.json()
    assert decision["application_id"] == "lever-1"
    assert decision["route"] == "PASS_TO_ATS"
    assert DELIVERY.wait(2.0)
    assert [item.application_id for item in MOCK_ATS.applications()] == ["lever-1"]


def test_lever_rejects_oversized_body():
    payload = b"x" * 1_048_577
    response = client.post("/v1/webhooks/lever", content=payload, headers={"Signature": sign(payload)})
    assert response.status_code == 413
