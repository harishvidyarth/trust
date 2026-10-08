from __future__ import annotations

import json

from firewall.models import Application

from federation.client import NodeClient
from federation.fingerprints import to_fingerprints
from federation.node import generate_private_key, public_key_text


SECRET = "a-long-test-consortium-secret"


def application(
    *,
    name: str = "Alice Example",
    email: str = "Alice.Example+jobs@gmail.com",
    phone: str = "+91 98765 43210",
    device: str = "browser-device-001",
    ip: str = "203.0.113.47",
    description: str = "Built resilient payment services using Python FastAPI PostgreSQL Redis Docker",
) -> Application:
    return Application.model_validate(
        {
            "application_id": "app-1",
            "job_id": "job-1",
            "candidate": {
                "name": name,
                "email": email,
                "phone": phone,
                "skills": ["Python"],
                "experience": [
                    {"company": "Acme Labs", "title": "Engineer", "start": "2024", "end": "2026"}
                ],
                "projects": [{"name": "Payments", "description": description}],
            },
            "signals": {
                "device_id": device,
                "ip": ip,
                "session_seconds": 12,
                "paste_char_ratio": 0.8,
                "submitted_at": 1_700_000_000,
            },
        }
    )


def test_email_alias_normalisation_collides() -> None:
    first = to_fingerprints(application(), SECRET)
    alias = application(email="aliceexample@googlemail.com")
    assert first["email"] == to_fingerprints(alias, SECRET)["email"]


def test_near_copy_resume_has_at_least_one_band_collision() -> None:
    first = to_fingerprints(application(), SECRET)
    near_copy = application(
        email="other@example.net",
        phone="1111111111",
        device="other-device",
        ip="198.51.100.4",
        description="Built resilient payment services using Python FastAPI PostgreSQL Redis Docker Kubernetes",
    )
    second = to_fingerprints(near_copy, SECRET)
    bands = [f"resume_band_{index}" for index in range(4)]
    assert any(first[kind] == second[kind] for kind in bands)


def test_honest_candidate_with_different_identity_does_not_collide() -> None:
    first = to_fingerprints(application(), SECRET)
    honest = application(
        name="Bob Honest",
        email="bob@elsewhere.test",
        phone="5550001212",
        device="fresh-device",
        ip="198.51.100.200",
        description="Designed accessible mobile interfaces and offline synchronization for health workers",
    )
    second = to_fingerprints(honest, SECRET)
    assert not set(first.items()) & set(second.items())


def test_every_outbound_message_contains_no_raw_pii(tmp_path) -> None:
    source = application()
    fingerprints = to_fingerprints(source, SECRET)
    key_path = tmp_path / "node.pem"
    private_key = generate_private_key(key_path)
    client = NodeClient(
        "http://node.invalid",
        secret=SECRET,
        node_id="employer-a",
        private_key=private_key,
    )
    report = client.build_report(fingerprints, "bot", confidence=0.96, ttl=300)
    check = {"fingerprints": fingerprints}
    revocation = client.build_revocation(report.report_id)
    bloom = client.export_bloom(fingerprints.values()).to_dict()

    raw_fragments = [
        source.candidate.name,
        source.candidate.email,
        source.candidate.phone,
        source.signals.ip,
        source.signals.device_id,
        source.candidate.projects[0].description,
        "aliceexample@gmail.com",
        "9876543210",
        "203.0.113.0/24",
    ]
    for message in (fingerprints, report.model_dump(by_alias=True), check, revocation.model_dump(), bloom):
        serialized = json.dumps(message, sort_keys=True)
        assert all(fragment not in serialized for fragment in raw_fragments)
        assert "Alice" not in serialized

    assert len(public_key_text(private_key.public_key())) == 44


def test_bloom_export_import_round_trip() -> None:
    fingerprints = to_fingerprints(application(), SECRET)
    exported = NodeClient.export_bloom(fingerprints.values(), capacity=100)
    imported = NodeClient.import_bloom(exported.to_dict())
    assert all(value in imported for value in fingerprints.values())
    assert "f" * 64 not in imported
