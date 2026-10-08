from __future__ import annotations

import asyncio

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from firewall.models import Decision, Route

from federation.client import NodeClient
from federation.fingerprints import to_fingerprints
from federation.integration import report_decision
from federation.node import NodeConfig, create_app, public_key_text
from federation.tests.test_fingerprints import SECRET as FP_SECRET
from federation.tests.test_fingerprints import application

SECRET = "consortium-secret-for-tests"


def request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async def run() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(run())


@pytest.fixture
def network(tmp_path):
    keys = {node: Ed25519PrivateKey.generate() for node in ("a", "b")}
    public_keys = {node: public_key_text(key.public_key()) for node, key in keys.items()}
    config = NodeConfig(
        node_id="receiver",
        peers=[],
        secret=SECRET,
        key_file=tmp_path / "receiver.pem",
        peers_file=None,
        confirmation_k=2,
        score_threshold=1.5,
        public_keys=public_keys,
    )
    app = create_app(config)
    clients = {
        node: NodeClient("http://test", secret=SECRET, node_id=node, private_key=key)
        for node, key in keys.items()
    }
    return app, clients


def submit(app, client, fingerprints, confidence=0.9):
    report = client.build_report(fingerprints, "bot", confidence, 300)
    assert request(app, "POST", "/fed/report", json=report.model_dump(by_alias=True)).status_code == 202


def hits(app, fingerprints):
    return request(app, "POST", "/fed/check", json={"fingerprints": fingerprints}).json()["hits"]


def test_network_prefix_alone_never_matches(network) -> None:
    app, clients = network
    shared_campus = {"ip24": "c" * 64}
    submit(app, clients["a"], shared_campus)
    submit(app, clients["b"], shared_campus)
    assert hits(app, shared_campus) == []


def test_device_alone_from_two_nodes_stays_advisory(network) -> None:
    app, clients = network
    lab_machine = {"device": "d" * 64}
    submit(app, clients["a"], lab_machine)
    submit(app, clients["b"], lab_machine)
    result = hits(app, lab_machine)
    assert [hit["code"] for hit in result] == ["FED_ADVISORY"]


def test_device_plus_network_does_not_confirm(network) -> None:
    app, clients = network
    both = {"device": "d" * 64, "ip24": "c" * 64}
    submit(app, clients["a"], both)
    submit(app, clients["b"], both)
    assert [hit["code"] for hit in hits(app, both)] == ["FED_ADVISORY"]


def test_email_from_two_nodes_confirms(network) -> None:
    app, clients = network
    identity = {"email": "e" * 64}
    submit(app, clients["a"], identity)
    submit(app, clients["b"], identity)
    assert [hit["code"] for hit in hits(app, identity)] == ["FED_CONFIRMED"]


@pytest.mark.parametrize("ip", ["", "unknown", "not-an-ip", "203.0.113.5, 10.0.0.1"])
def test_odd_ip_values_do_not_crash_fingerprinting(ip) -> None:
    base = application()
    odd = base.model_copy(update={"signals": base.signals.model_copy(update={"ip": ip})})
    prints = to_fingerprints(odd, FP_SECRET)
    assert "email" in prints
    if ip.startswith("203.0.113.5"):
        assert "ip24" in prints
    else:
        assert "ip24" not in prints


def test_secret_is_required_unless_dev_flag_is_set(monkeypatch) -> None:
    monkeypatch.delenv("FED_SECRET", raising=False)
    monkeypatch.delenv("FED_ALLOW_INSECURE_DEV", raising=False)
    with pytest.raises(RuntimeError):
        NodeConfig.from_env()
    monkeypatch.setenv("FED_ALLOW_INSECURE_DEV", "1")
    assert NodeConfig.from_env().secret
    monkeypatch.setenv("FED_SECRET", "a-real-secret-value")
    monkeypatch.delenv("FED_ALLOW_INSECURE_DEV")
    assert NodeConfig.from_env().secret == "a-real-secret-value"


class RecordingClient:
    secret = FP_SECRET

    def __init__(self):
        self.calls = []

    async def report_application(self, application, classification, confidence, ttl):
        self.calls.append((classification, confidence))
        return {"accepted": True}


def decision(score, route, reasons):
    return Decision(application_id="x", score=score, route=route, reasons=reasons, summary="s")


def test_report_uses_fraud_confidence_not_trust_score() -> None:
    reasons = [{"code": "RESUME_PROMPT_INJECTION", "severity": "high", "detail": "d", "weight": 45}]
    client = RecordingClient()
    asyncio.run(report_decision(application(), decision(5, Route.MANUAL_REVIEW, reasons), client))
    assert client.calls and client.calls[0][1] >= 0.95


def test_template_reuse_is_reported_as_farm() -> None:
    reasons = [
        {"code": "TEMPLATE_REUSE", "severity": "high", "detail": "d", "weight": 35},
        {"code": "DUP_RESUME_NEAR", "severity": "high", "detail": "d", "weight": 32},
    ]
    client = RecordingClient()
    asyncio.run(report_decision(application(), decision(20, Route.MANUAL_REVIEW, reasons), client))
    assert client.calls[0][0] == "farm"


def test_review_with_only_weak_reasons_stays_local() -> None:
    reasons = [{"code": "FAST_SUBMIT", "severity": "medium", "detail": "d", "weight": 12}]
    client = RecordingClient()
    assert asyncio.run(report_decision(application(), decision(30, Route.MANUAL_REVIEW, reasons), client)) is None
    assert client.calls == []
