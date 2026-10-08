from __future__ import annotations

import os
from collections.abc import Collection

from firewall.config import Config
from firewall.enrichment.claims import extract_claims
from firewall.enrichment.crossref import CrossrefConnector
from firewall.enrichment.domain import DomainConnector
from firewall.enrichment.github import GitHubConnector
from firewall.enrichment.identity import IdentityConnector
from firewall.enrichment.roles import RoleConnector, role_reasons, select_profile
from firewall.enrichment.runner import enrich
from firewall.enrichment.scholar import ScholarConnector
from firewall.llm.ollama_client import OllamaClient
from firewall.models import Application, Decision, JobRequirements, Reason, Route
from firewall.reasoning import annotate_reasons, generate_reasoning, plain_text_ok
from firewall.signals.automation import detect_automation
from firewall.signals.consistency import detect_consistency
from firewall.signals.duplicates import detect_duplicates
from firewall.signals.identity_links import detect_identity_links
from firewall.signals.qualification import evaluate_qualification
from firewall.store import ApplicationStore


HARD_ESCALATIONS = (
    frozenset({"DUP_SAME_JOB", "VELOCITY_HIGH"}),
    frozenset({"VELOCITY_HIGH", "FAST_SUBMIT"}),
    frozenset({"DUP_RESUME_NEAR", "TEMPLATE_REUSE"}),
    frozenset({"RESUME_HIDDEN_TEXT", "RESUME_PROMPT_INJECTION"}),
)


def _reason_family(code: str) -> str | None:
    if code.startswith("RESUME_HIDDEN_"):
        return "HIDDEN"
    if code.startswith("RESUME_INJECTION_"):
        return "INJECTION"
    if code.startswith("RESUME_STUFFING_"):
        return "STUFFING"
    if code.startswith("RESUME_DIVERGENCE_"):
        return "DIVERGENCE"
    return None


def _route(score: int, reasons: list[Reason], config: Config) -> Route:
    codes = {item.code for item in reasons}
    if "VELOCITY_HIGH" in codes:
        return Route.MANUAL_REVIEW
    if any(rule <= codes for rule in HARD_ESCALATIONS):
        return Route.MANUAL_REVIEW
    if "RESUME_PROMPT_INJECTION" in codes and score >= config.pass_min:
        return Route.ADDITIONAL_VERIFICATION
    if "IDENTITY_DEVICE_ROTATION" in codes and score >= config.pass_min:
        return Route.ADDITIONAL_VERIFICATION
    if score >= config.pass_min:
        return Route.PASS_TO_ATS
    if score <= config.review_max:
        return Route.MANUAL_REVIEW
    return Route.ADDITIONAL_VERIFICATION


def score_and_route(reasons: list[Reason], config: Config, bonus: int = 0) -> tuple[int, Route]:
    family_totals: dict[str, int] = {}
    uncapped = 0
    for item in reasons:
        family = _reason_family(item.code)
        if family is None:
            uncapped += item.weight
        else:
            family_totals[family] = family_totals.get(family, 0) + item.weight
    penalty = uncapped + sum(min(total, config.family_cap.get(family, total)) for family, total in family_totals.items())
    score = max(0, min(100, 100 - penalty + max(0, bonus)))
    return score, _route(score, reasons, config)


def _summary(score: int, route: Route, reasons: list[Reason]) -> str:
    opening = f"The trust score is {score} out of 100."
    if not reasons:
        return f"{opening} No concerns were found, so this application can move on to the hiring system."
    if route == Route.PASS_TO_ATS:
        return f"{opening} This application can move on to the hiring system, but a few small concerns are listed below."
    if route == Route.ADDITIONAL_VERIFICATION:
        return f"{opening} More checks are needed before this application moves on because of the concerns below."
    return f"{opening} A person should review this application because of the concerns below."


def _has_integrity_or_duplicate(reasons: list[Reason]) -> bool:
    return any(
        item.code.startswith(("DUP_", "RESUME_", "TIMELINE_")) or item.code == "TEMPLATE_REUSE"
        for item in reasons
    )


def evaluate(
    application: Application,
    job: JobRequirements,
    store: ApplicationStore,
    config: Config,
    llm_client: OllamaClient | None = None,
    extra_reasons: list[Reason] | None = None,
    persist: bool = True,
    excluded_reason_codes: Collection[str] | None = None,
    resume_text: str | None = None,
) -> Decision:
    existing = store.get_decision(application.application_id)
    if existing is not None:
        if all(item.explanation for item in existing.reasons):
            return existing
        return existing.model_copy(update={"reasons": annotate_reasons(existing.reasons)})

    reasons = detect_duplicates(application, store, config)
    reasons.extend(detect_identity_links(application, store, config))
    if extra_reasons:
        reasons.extend(extra_reasons)
    reasons.extend(detect_automation(application, store, config))
    _, qualification_reasons = evaluate_qualification(application, job, config)
    reasons.extend(qualification_reasons)
    consistency_application = application
    if application.candidate._qualification_only_experience:
        candidate = application.candidate.model_copy(update={"experience": []})
        consistency_application = application.model_copy(update={"candidate": candidate})
    reasons.extend(detect_consistency(consistency_application, config))
    if excluded_reason_codes:
        reasons = [item for item in reasons if item.code not in excluded_reason_codes]
    reasons.sort(key=lambda item: (-item.weight, item.code, item.detail))

    score, route = score_and_route(reasons, config)
    if (
        config.corroboration_enabled
        and os.getenv("FIREWALL_ENRICH") == "1"
        and config.corroboration_min <= score <= config.corroboration_max
        and resume_text is not None
    ):
        try:
            claims = extract_claims(resume_text, application.candidate)
            profile = select_profile("", job.must_have_skills, job.nice_to_have)
            connectors = [
                GitHubConnector(),
                CrossrefConnector(),
                DomainConnector(),
                IdentityConnector(),
                ScholarConnector(),
                RoleConnector(profile, resume_text, application.candidate),
            ]
            signals, _ = enrich(claims, connectors=connectors, per_connector_timeout=4.0)
            reason_values, trust_bonus = role_reasons(signals, bonus_cap=15)
            enrichment_reasons = [Reason.model_validate(item) for item in reason_values]
            has_negative = any(item.polarity == "negative" for item in signals)
            bonus = 0
            if not has_negative and not _has_integrity_or_duplicate(reasons):
                bonus = trust_bonus
            reasons.extend(enrichment_reasons)
            reasons.sort(key=lambda item: (-item.weight, item.code, item.detail))
            score, route = score_and_route(reasons, config, bonus=bonus)
        except Exception:
            pass
    summary = _summary(score, route, reasons)
    llm_used = False
    active_client = llm_client
    if os.getenv("FIREWALL_LLM") == "1":
        try:
            active_client = active_client or OllamaClient()
            rewritten = active_client.rewrite_summary(
                [item.code for item in reasons],
                list(application.candidate.skills),
            )
            if not isinstance(rewritten, str) or not rewritten.strip() or not plain_text_ok(rewritten):
                raise ValueError("LLM summary was empty or not plain")
            summary = rewritten.strip()
            llm_used = True
        except Exception:
            pass
    recruiter_summary, candidate_fixes, reasoning_llm_used = generate_reasoning(
        reasons,
        route,
        llm_client=active_client,
    )
    if reasoning_llm_used:
        summary = recruiter_summary
        llm_used = True
    decision = Decision(
        application_id=application.application_id,
        score=score,
        route=route,
        reasons=annotate_reasons(reasons),
        summary=summary,
        recruiter_summary=recruiter_summary,
        candidate_fixes=candidate_fixes,
        llm_used=llm_used,
    )
    if persist:
        store.save(application, decision)
    return decision
