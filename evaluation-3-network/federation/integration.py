
from __future__ import annotations

from typing import Any

from firewall.models import Application, Decision, Route

from .client import NodeClient
from .fingerprints import to_fingerprints


async def check_application(application: Application, nodeclient: NodeClient) -> list[dict[str, Any]]:
    response = await nodeclient.check(to_fingerprints(application, nodeclient.secret))
    allowed = {"code", "severity", "detail", "weight_hint", "matches"}
    return [{key: value for key, value in hit.items() if key in allowed} for hit in response.get("hits", [])]


def _classification(decision: Decision) -> str:
    codes = " ".join(reason.code.lower() for reason in decision.reasons)
    if "template_reuse" in codes or "cluster" in codes or "farm" in codes:
        return "farm"
    if "timeline" in codes or "fabricat" in codes or "claim" in codes:
        return "fabricated"
    if "duplicate" in codes or "dup_" in codes:
        return "duplicate"
    return "bot"


async def report_decision(
    application: Application,
    decision: Decision,
    nodeclient: NodeClient,
    *,
    ttl: int = 3600,
) -> dict[str, object] | None:
    is_manual = decision.route == Route.MANUAL_REVIEW
    has_strong_reason = any(
        reason.severity.lower() == "high" and reason.weight >= 30 for reason in decision.reasons
    )
    if not is_manual or decision.score > 40 or not has_strong_reason:
        return None
    confidence = min(0.99, max(0.70, (100 - decision.score) / 100))
    return await nodeclient.report_application(
        application, _classification(decision), confidence=confidence, ttl=ttl
    )
