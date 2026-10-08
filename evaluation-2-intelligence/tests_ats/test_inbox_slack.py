from __future__ import annotations

import json

import httpx
import pytest

from firewall.connectors.mock_ats import MockATS
from firewall.delivery import Delivery, default_delivery_config, validate_delivery_config
from firewall.models import Route

from tests.conftest import make_application


def build(config=None, environ=None, client=None):
    return Delivery(config or default_delivery_config(), MockATS(), environ=environ or {}, client=client)


def test_default_routes_land_in_three_places():
    config = validate_delivery_config(default_delivery_config())
    assert config["routes"]["PASS_TO_ATS"] == ["mock_ats"]
    assert config["routes"]["ADDITIONAL_VERIFICATION"] == ["verification_inbox"]
    assert config["routes"]["MANUAL_REVIEW"] == ["review_inbox"]


def test_inboxes_collect_each_route():
    delivery = build()
    delivery.deliver(Route.ADDITIONAL_VERIFICATION, make_application("a-1"))
    delivery.deliver(Route.MANUAL_REVIEW, make_application("a-2"))
    delivery.deliver(Route.PASS_TO_ATS, make_application("a-3"))
    assert delivery.wait(3.0)
    boxes = {box["name"]: box for box in delivery.inboxes()}
    assert [item["application_id"] for item in boxes["verification_inbox"]["items"]] == ["a-1"]
    assert [item["application_id"] for item in boxes["review_inbox"]["items"]] == ["a-2"]
    assert "mock_ats" not in boxes


def test_slack_destination_posts_plain_text_without_personal_data():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, text="ok")

    config = {
        "destinations": {"mock_ats": {"type": "mock_ats"}, "ping": {"type": "slack", "url_env": "SLACK_HOOK"}},
        "routes": {"PASS_TO_ATS": ["mock_ats"], "ADDITIONAL_VERIFICATION": [], "MANUAL_REVIEW": ["ping"]},
    }
    delivery = build(config, {"SLACK_HOOK": "https://hooks.slack.com/services/T000/B000/XXXX"}, httpx.Client(transport=httpx.MockTransport(handler)))
    application = make_application("a-9")
    delivery.deliver(Route.MANUAL_REVIEW, application)
    assert delivery.wait(3.0)
    assert len(seen) == 1
    assert "a-9" in seen[0]["text"]
    assert application.candidate.email not in seen[0]["text"]
    assert application.candidate.name not in seen[0]["text"]


def test_slack_rejects_other_hosts_and_missing_secret():
    config = {
        "destinations": {"mock_ats": {"type": "mock_ats"}, "ping": {"type": "slack", "url_env": "SLACK_HOOK"}},
        "routes": {"PASS_TO_ATS": ["mock_ats"], "ADDITIONAL_VERIFICATION": [], "MANUAL_REVIEW": ["ping"]},
    }
    with pytest.raises(ValueError):
        build(config, {"SLACK_HOOK": "https://example.com/hook"})
    with pytest.raises(ValueError):
        build(config, {})
    with pytest.raises(ValueError):
        validate_delivery_config({**config, "destinations": {**config["destinations"], "ping": {"type": "slack"}}})
