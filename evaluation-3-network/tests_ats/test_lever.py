from __future__ import annotations

from firewall.connectors.lever import LeverWebhook, adapt


def test_lever_posting_payload_adapts_to_firewall_models() -> None:
    payload = LeverWebhook.model_validate(
        {
            "application_id": "lever-app-1",
            "candidate": {
                "name": "Ada Lovelace",
                "email": "ada@example.test",
                "phone": "+1-555-0100",
                "skills": ["Python"],
            },
            "posting": {
                "id": "posting-1",
                "must_have_skills": ["Python"],
                "nice_to_have": ["AWS"],
                "min_years": 2,
            },
            "metadata": {
                "device_id": "device-1",
                "ip": "203.0.113.10",
                "submitted_at": 1_800_000_000,
            },
        }
    )
    application, job = adapt(payload)
    assert application.application_id == "lever-app-1"
    assert application.job_id == "posting-1"
    assert application.candidate.skills == ["Python"]
    assert job.nice_to_have == ["AWS"]
    assert job.min_years == 2
