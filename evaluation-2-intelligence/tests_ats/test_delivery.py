from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from firewall.connectors.forwarders import QueueForwarder
from firewall.connectors.mock_ats import MockATS
from firewall.delivery import Delivery, build_delivery_from_env, load_delivery_config, validate_delivery_config
from firewall.models import Route
from tests_ats.test_forwarders import make_application


def configured_routes() -> dict[str, object]:
    return {
        "destinations": {
            "greenhouse": {
                "type": "greenhouse",
                "api_key_env": "GREENHOUSE_KEY",
                "on_behalf_of": "42",
                "base_url": "https://greenhouse.test",
            },
            "lever": {
                "type": "lever",
                "api_key_env": "LEVER_KEY",
                "base_url": "https://lever.test/v1",
            },
            "webhook": {
                "type": "webhook",
                "url": "https://webhook.test/intake",
                "secret_env": "WEBHOOK_SECRET",
            },
            "verification": {
                "type": "queue",
                "url": "https://queue.test/verify",
                "secret_env": "QUEUE_SECRET",
            },
            "review": {"type": "queue"},
            "mock": {"type": "mock_ats"},
        },
        "routes": {
            "PASS_TO_ATS": ["greenhouse", "lever", "webhook", "mock"],
            "ADDITIONAL_VERIFICATION": ["verification"],
            "MANUAL_REVIEW": ["review"],
        },
    }


def test_delivery_routes_every_destination_with_fake_http() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(202)

    ats = MockATS()
    delivery = Delivery(
        configured_routes(),
        ats,
        environ={
            "GREENHOUSE_KEY": "greenhouse-secret",
            "LEVER_KEY": "lever-secret",
            "WEBHOOK_SECRET": "webhook-secret",
            "QUEUE_SECRET": "queue-secret",
        },
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    delivery.deliver(Route.PASS_TO_ATS, make_application("pass"))
    delivery.deliver(Route.ADDITIONAL_VERIFICATION, make_application("verify"))
    delivery.deliver(Route.MANUAL_REVIEW, make_application("review"))

    assert delivery.wait(2)
    assert sorted(request.url.host for request in requests) == [
        "greenhouse.test",
        "lever.test",
        "queue.test",
        "webhook.test",
    ]
    assert [item.application_id for item in ats.applications()] == ["pass"]
    review = delivery.forwarders["review"]
    assert isinstance(review, QueueForwarder)
    assert [item.application_id for item in review.items] == ["review"]


def test_delivery_retries_then_dead_letters_without_raising_to_caller() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503)

    config = {
        "destinations": {
            "failed": {
                "type": "webhook",
                "url": "https://failed.test/intake",
                "secret_env": "FAILED_SECRET",
                "max_attempts": 2,
                "backoff_seconds": 0,
            }
        },
        "routes": {
            "PASS_TO_ATS": ["failed"],
            "ADDITIONAL_VERIFICATION": [],
            "MANUAL_REVIEW": [],
        },
    }
    delivery = Delivery(
        config,
        MockATS(),
        environ={"FAILED_SECRET": "synthetic-secret"},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    delivery.deliver(Route.PASS_TO_ATS, make_application())

    assert delivery.wait(2)
    assert attempts == 2
    assert len(delivery.dead_letters) == 1
    assert delivery.dead_letters[0].application_id == "app-1"
    assert delivery.dead_letters[0].attempts == 2
    assert delivery.dead_letters[0].error == "ForwardingError"


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test/hook",
        "https://169.254.169.254/latest/meta-data",
        "https://10.0.0.8/hook",
        "https://metadata.google.internal/computeMetadata/v1",
        "https://user:password@example.test/hook",
    ],
)
def test_ssrf_guard_blocks_unsafe_destinations(url: str) -> None:
    config = {
        "destinations": {
            "hook": {"type": "webhook", "url": url, "secret_env": "HOOK_SECRET"}
        },
        "routes": {
            "PASS_TO_ATS": ["hook"],
            "ADDITIONAL_VERIFICATION": [],
            "MANUAL_REVIEW": [],
        },
    }

    with pytest.raises(ValueError):
        validate_delivery_config(config)


def test_private_destination_requires_explicit_opt_in() -> None:
    config = {
        "destinations": {
            "hook": {
                "type": "webhook",
                "url": "https://10.0.0.8/hook",
                "secret_env": "HOOK_SECRET",
            }
        },
        "routes": {
            "PASS_TO_ATS": ["hook"],
            "ADDITIONAL_VERIFICATION": [],
            "MANUAL_REVIEW": [],
        },
    }

    assert validate_delivery_config(config, allow_private=True)["destinations"]["hook"]["url"] == "https://10.0.0.8/hook"


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda config: config["routes"].update({"UNKNOWN": []}), "unknown route"),
        (lambda config: config["routes"].pop("MANUAL_REVIEW"), "missing route"),
        (lambda config: config["destinations"]["webhook"].update({"secret": "inline"}), "unknown keys"),
        (lambda config: config["routes"].update({"PASS_TO_ATS": ["missing"]}), "unknown destinations"),
    ],
)
def test_delivery_config_validation_errors(mutate, match: str) -> None:
    config = configured_routes()
    mutate(config)

    with pytest.raises(ValueError, match=match):
        validate_delivery_config(config)


def test_routes_load_from_json_string_and_file(tmp_path: Path) -> None:
    config = configured_routes()
    inline = load_delivery_config(json.dumps(config))
    path = tmp_path / "routes.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    stored = load_delivery_config(str(path))

    assert inline == stored


def test_default_delivery_uses_mock_ats_for_pass_route() -> None:
    ats = MockATS()
    delivery = build_delivery_from_env(ats, environ={})

    delivery.deliver(Route.PASS_TO_ATS, make_application("default-pass"))

    assert delivery.wait(2)
    assert [item.application_id for item in ats.applications()] == ["default-pass"]
