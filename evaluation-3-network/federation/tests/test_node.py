from __future__ import annotations

import asyncio
import base64
import time

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from federation.client import NodeClient
from federation.node import NodeConfig, create_app, public_key_text


SECRET = "consortium-secret-for-tests"
FP = {"email": "a" * 64, "device": "b" * 64}


def request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async def run() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(run())


@pytest.fixture
def network(tmp_path):
    keys = {node: Ed25519PrivateKey.generate() for node in ("a", "b", "rogue")}
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


def test_signature_verification_and_replay_rejection(network) -> None:
    app, clients = network
    report = clients["a"].build_report(FP, "bot", 0.95, 300)
    first = request(app, "POST", "/fed/report", json=report.model_dump(by_alias=True))
    assert first.status_code == 202

    replay = request(app, "POST", "/fed/report", json=report.model_dump(by_alias=True))
    assert replay.status_code == 409

    tampered = report.model_copy(update={"confidence": 0.99, "report_id": "f" * 64})
    invalid = request(app, "POST", "/fed/report", json=tampered.model_dump(by_alias=True))
    assert invalid.status_code == 401


def test_unknown_signer_is_rejected(network) -> None:
    app, _ = network
    unknown = NodeClient(
        "http://test", secret=SECRET, node_id="unknown", private_key=Ed25519PrivateKey.generate()
    )
    response = request(
        app,
        "POST",
        "/fed/report",
        json=unknown.build_report(FP, "farm", 0.9, 300).model_dump(by_alias=True),
    )
    assert response.status_code == 403


def test_k_node_confirmation_and_single_node_advisory(network) -> None:
    app, clients = network
    one = clients["a"].build_report(FP, "bot", 0.99, 300)
    assert request(app, "POST", "/fed/report", json=one.model_dump(by_alias=True)).status_code == 202
    advisory = request(app, "POST", "/fed/check", json={"fingerprints": FP}).json()["hits"]
    assert advisory[0]["code"] == "FED_ADVISORY"

    two = clients["b"].build_report(FP, "bot", 0.75, 300)
    assert request(app, "POST", "/fed/report", json=two.model_dump(by_alias=True)).status_code == 202
    confirmed = request(app, "POST", "/fed/check", json={"fingerprints": FP}).json()["hits"]
    assert confirmed[0]["code"] == "FED_CONFIRMED"
    assert confirmed[0]["independent_nodes"] == 2


def test_configured_weighted_threshold_can_confirm(network) -> None:
    app, clients = network
    app.state.federation.config.score_threshold = 0.9
    report = clients["a"].build_report(FP, "farm", 0.94, 300)
    request(app, "POST", "/fed/report", json=report.model_dump(by_alias=True))
    hits = request(app, "POST", "/fed/check", json={"fingerprints": FP}).json()["hits"]
    assert hits[0]["code"] == "FED_CONFIRMED"
    assert hits[0]["independent_nodes"] == 1


def test_ttl_expiry(network, monkeypatch) -> None:
    app, clients = network
    now = 2_000_000_000.0
    monkeypatch.setattr("federation.node.time.time", lambda: now)
    monkeypatch.setattr("federation.client.time.time", lambda: now)
    report = clients["a"].build_report(FP, "duplicate", 0.8, 10)
    assert request(app, "POST", "/fed/report", json=report.model_dump(by_alias=True)).status_code == 202
    monkeypatch.setattr("federation.node.time.time", lambda: now + 11)
    assert request(app, "POST", "/fed/check", json={"fingerprints": FP}).json()["hits"] == []


def test_revocation_by_origin(network) -> None:
    app, clients = network
    report = clients["a"].build_report(FP, "fabricated", 0.9, 300)
    request(app, "POST", "/fed/report", json=report.model_dump(by_alias=True))
    revoke = clients["a"].build_revocation(report.report_id)
    response = request(app, "POST", "/fed/revoke", json=revoke.model_dump())
    assert response.status_code == 202
    assert request(app, "POST", "/fed/check", json={"fingerprints": FP}).json()["hits"] == []

    wrong = clients["b"].build_revocation(report.report_id)
    assert request(app, "POST", "/fed/revoke", json=wrong.model_dump()).status_code == 403


def test_gossip_loop_safety_counts_one_stored_report(network) -> None:
    app, clients = network
    report = clients["a"].build_report(FP, "farm", 0.8, 300)
    payload = report.model_dump(by_alias=True)
    assert request(app, "POST", "/fed/report", json=payload).status_code == 202
    for _ in range(4):
        assert request(app, "POST", "/fed/report", json=payload).status_code == 409
    status = request(app, "GET", "/fed/status").json()
    assert status["reports_total"] == 1
    assert status["metrics"]["replays_rejected"] == 4


def test_malformed_signature_is_rejected(network) -> None:
    app, clients = network
    report = clients["a"].build_report(FP, "bot", 0.95, 300)
    payload = report.model_dump(by_alias=True)
    payload["signature"] = base64.b64encode(b"not-a-signature").decode()
    assert request(app, "POST", "/fed/report", json=payload).status_code == 401
