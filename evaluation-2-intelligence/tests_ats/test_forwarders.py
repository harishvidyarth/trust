from __future__ import annotations

import base64
import hashlib
import hmac
import json

import httpx
import pytest

from firewall.connectors.forwarders import (
    ATSForwarder,
    ForwardingError,
    GenericWebhookForwarder,
    GreenhouseHarvestForwarder,
    QueueForwarder,
    Router,
)
from firewall.models import Application, Candidate, Route, SubmissionSignals


def make_application(application_id: str = "app-1") -> Application:
    return Application(
        application_id=application_id,
        job_id="job-42",
        candidate=Candidate(
            name="Grace Hopper",
            email="grace@example.test",
            phone="+1-555-0142",
            skills=["Python"],
        ),
        signals=SubmissionSignals(
            device_id="device-1",
            ip="203.0.113.20",
            session_seconds=90,
            paste_char_ratio=0.1,
            submitted_at=1_800_000_000,
        ),
    )


def test_greenhouse_success_auth_headers_and_idempotency() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"id": 7})

    forwarder = GreenhouseHarvestForwarder(
        "secret-key",
        "12345",
        base_url="https://sandbox.test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    application = make_application()
    assert forwarder.forward(application) is True
    assert forwarder.forward(application) is False
    assert len(seen) == 1
    assert seen[0].headers["on-behalf-of"] == "12345"
    assert seen[0].headers["idempotency-key"] == "app-1"
    assert base64.b64decode(seen[0].headers["authorization"].split()[1]).decode() == "secret-key:"
    assert json.loads(seen[0].content)["applications"] == [{"job_id": "job-42"}]


def test_retry_uses_exponential_backoff_then_succeeds() -> None:
    attempts = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503 if attempts < 3 else 202)

    forwarder = GenericWebhookForwarder(
        "https://queue.test/intake",
        "signing-secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_attempts=3,
        backoff_seconds=0.1,
        sleep=delays.append,
    )
    assert forwarder.forward(make_application()) is True
    assert attempts == 3
    assert delays == [0.1, 0.2]
    assert forwarder.dead_letters == []


def test_exhausted_delivery_is_dead_lettered_and_raises() -> None:
    forwarder = GenericWebhookForwarder(
        "https://queue.test/intake",
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
        max_attempts=2,
        sleep=lambda _: None,
    )
    with pytest.raises(ForwardingError, match="added to dead letters"):
        forwarder.forward(make_application())
    assert len(forwarder.dead_letters) == 1
    assert forwarder.dead_letters[0].application.application_id == "app-1"
    assert forwarder.dead_letters[0].attempts == 2


def test_generic_webhook_hmac_covers_exact_body() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(204)

    forwarder = GenericWebhookForwarder(
        "https://receiver.test/webhook",
        "top-secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    forwarder.forward(make_application())
    request = captured[0]
    expected = hmac.new(b"top-secret", request.content, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(request.headers["x-firewall-signature"], expected)


class RecordingForwarder(ATSForwarder):
    def __init__(self) -> None:
        self.ids: list[str] = []

    def forward(self, application: Application) -> bool:
        self.ids.append(application.application_id)
        return True


def test_router_config_keeps_review_application_out_of_ats() -> None:
    ats = RecordingForwarder()
    review = QueueForwarder("manual-review")
    router = Router(
        {
            "routes": {
                "PASS_TO_ATS": ["ats"],
                "ADDITIONAL_VERIFICATION": ["verification"],
                "MANUAL_REVIEW": ["review"],
            }
        },
        {
            "ats": ats,
            "verification": QueueForwarder("verification"),
            "review": review,
        },
    )
    suspicious = make_application("stuffed-fraud")
    router.dispatch(Route.MANUAL_REVIEW, suspicious)
    assert ats.ids == []
    assert [item.application_id for item in review.items] == ["stuffed-fraud"]

    router.dispatch(Route.PASS_TO_ATS, make_application("honest"))
    assert ats.ids == ["honest"]
