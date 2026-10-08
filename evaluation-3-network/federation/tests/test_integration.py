from __future__ import annotations

import asyncio

from firewall.models import Decision, Route

from federation.integration import check_application, report_decision
from federation.tests.test_fingerprints import SECRET, application


class FakeClient:
    secret = SECRET

    async def check(self, fingerprints):
        return {
            "hits": [
                {
                    "code": "FED_CONFIRMED",
                    "severity": "high",
                    "detail": "Matched two independent consortium nodes.",
                    "weight_hint": 80,
                    "matches": ["email", "resume_band_2"],
                    "independent_nodes": 2,
                }
            ]
        }

    async def report_application(self, application, classification, confidence, ttl):
        return {"accepted": True, "class": classification}


def test_check_application_returns_reason_shaped_dicts() -> None:
    hits = asyncio.run(check_application(application(), FakeClient()))
    assert hits == [
        {
            "code": "FED_CONFIRMED",
            "severity": "high",
            "detail": "Matched two independent consortium nodes.",
            "weight_hint": 80,
            "matches": ["email", "resume_band_2"],
        }
    ]


def test_report_decision_only_reports_strong_manual_review() -> None:
    strong = Decision(
        application_id="app-1",
        score=10,
        route=Route.MANUAL_REVIEW,
        reasons=[{"code": "BOT_AUTOFILL", "severity": "high", "detail": "Automated", "weight": 80}],
        summary="Strong bot evidence",
    )
    result = asyncio.run(report_decision(application(), strong, FakeClient()))
    assert result == {"accepted": True, "class": "bot"}

    weak = strong.model_copy(update={"score": 60})
    assert asyncio.run(report_decision(application(), weak, FakeClient())) is None

    passed = strong.model_copy(update={"route": Route.PASS_TO_ATS})
    assert asyncio.run(report_decision(application(), passed, FakeClient())) is None
