from __future__ import annotations

import os

from firewall.config import Config
from firewall.llm.ollama_client import OllamaClient
from firewall.models import Application, Decision, JobRequirements, Reason, Route
from firewall.signals.automation import detect_automation
from firewall.signals.consistency import detect_consistency
from firewall.signals.duplicates import detect_duplicates
from firewall.signals.qualification import evaluate_qualification
from firewall.store import ApplicationStore


HARD_ESCALATIONS = (
    frozenset({"DUP_SAME_JOB", "VELOCITY_HIGH"}),
    frozenset({"VELOCITY_HIGH", "FAST_SUBMIT"}),
    frozenset({"DUP_RESUME_NEAR", "TEMPLATE_REUSE"}),
)


def _route(score: int, reasons: list[Reason], config: Config) -> Route:
    codes = {item.code for item in reasons}
    if "VELOCITY_HIGH" in codes:
        return Route.MANUAL_REVIEW
    if any(rule <= codes for rule in HARD_ESCALATIONS):
        return Route.MANUAL_REVIEW
    if score >= config.pass_min:
        return Route.PASS_TO_ATS
    if score <= config.review_max:
        return Route.MANUAL_REVIEW
    return Route.ADDITIONAL_VERIFICATION


def _summary(score: int, route: Route, reasons: list[Reason]) -> str:
    if not reasons:
        return f"Trust score {score}: no configured risk or qualification concerns; route is {route.value}."
    codes = ", ".join(item.code for item in reasons)
    return f"Trust score {score}: {len(reasons)} concern(s) found ({codes}); route is {route.value}."


def evaluate(
    application: Application,
    job: JobRequirements,
    store: ApplicationStore,
    config: Config,
    llm_client: OllamaClient | None = None,
    extra_reasons: list[Reason] | None = None,
    persist: bool = True,
) -> Decision:
    existing = store.get_decision(application.application_id)
    if existing is not None:
        return existing

    reasons = detect_duplicates(application, store, config)
    if extra_reasons:
        reasons.extend(extra_reasons)
    reasons.extend(detect_automation(application, store, config))
    _, qualification_reasons = evaluate_qualification(application, job, config)
    reasons.extend(qualification_reasons)
    reasons.extend(detect_consistency(application, config))
    reasons.sort(key=lambda item: (-item.weight, item.code, item.detail))

    score = max(0, min(100, 100 - sum(item.weight for item in reasons)))
    route = _route(score, reasons, config)
    summary = _summary(score, route, reasons)
    llm_used = False
    if os.getenv("FIREWALL_LLM") == "1":
        try:
            client = llm_client or OllamaClient()
            rewritten = client.rewrite_summary(
                [item.code for item in reasons],
                list(application.candidate.skills),
            )
            if not isinstance(rewritten, str) or not rewritten.strip():
                raise ValueError("LLM summary was empty")
            summary = rewritten.strip()
            llm_used = True
        except Exception:
            pass
    decision = Decision(
        application_id=application.application_id,
        score=score,
        route=route,
        reasons=reasons,
        summary=summary,
        llm_used=llm_used,
    )
    if persist:
        store.save(application, decision)
    return decision
