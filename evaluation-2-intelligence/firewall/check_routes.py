from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from firewall.auth.deps import Principal
from firewall.enrichment.claims import extract_claims
from firewall.enrichment.crossref import CrossrefConnector
from firewall.enrichment.domain import DomainConnector
from firewall.enrichment.github import GitHubConnector
from firewall.enrichment.github_profile import GitHubProfileConnector
from firewall.enrichment.identity import IdentityConnector
from firewall.claim_check import analyze_application
from firewall.enrichment.roles import RoleConnector, select_profile
from firewall.enrichment.runner import enrich
from firewall.enrichment.scholar import ScholarConnector
from firewall.models import Candidate, Decision, Reason
from firewall.reasoning import explain_reason

MIN_SECONDS_BETWEEN_RUNS = 30
RECORD_TTL_SECONDS = 14 * 24 * 3600
PROFILE_LABELS = {
    "general": "General role",
    "finance": "Finance and accounting role",
    "hardware": "Hardware and engineering role",
    "sales_ops_design": "Sales, operations or design role",
}
SOURCE_LABELS = (
    ("github", "GitHub"),
    ("crossref", "Research papers"),
    ("scholar", "Research papers"),
    ("domain", "Employer website"),
    ("identity", "Name and email"),
    ("role", "Role specific checks"),
)
CANNOT_CHECK = {
    "finance": "Professional membership numbers such as ICAI, ACCA or CFA cannot be checked automatically yet. A person can ask for the certificate.",
    "hardware": "Patent numbers can be checked only when a patent lookup key is set up. A person can ask for the patent link.",
    "sales_ops_design": "Awards and case studies are checked only when they appear on the portfolio page you list.",
    "general": "Past employers and degrees cannot be confirmed automatically. A person can ask for a reference.",
}


class RunIn(BaseModel):
    application_id: str = Field(min_length=1, max_length=128)


def source_label(source: str) -> str:
    lowered = source.lower()
    for prefix, label in SOURCE_LABELS:
        if lowered.startswith(prefix):
            return label
    return "Other check"


def build_check_router(
    *,
    runner_guard: Callable[..., Any],
    reader_guard: Callable[..., Any],
    decision_for: Callable[[str], Decision | None],
    text_for: Callable[[str], str],
    candidate_for: Callable[[str], Candidate | None],
    owner_of: Callable[[str], str | None],
    cache: Any,
    enrich_function: Callable[..., Any] = enrich,
    connector_factory: Callable[[str, str, Candidate], list[Any]] | None = None,
    audit: Callable[[], Any] | None = None,
) -> APIRouter:
    router = APIRouter()

    def allowed(principal: Principal, application_id: str) -> bool:
        if principal.via == "open" or principal.role in {"recruiter", "admin"}:
            return True
        return owner_of(application_id) == principal.username

    def guard_access(principal: Principal, application_id: str) -> None:
        if decision_for(application_id) is None or not allowed(principal, application_id):
            raise HTTPException(status_code=404, detail="We could not find that application.")

    def default_connectors(profile: str, text: str, candidate: Candidate) -> list[Any]:
        return [
            GitHubConnector(),
            GitHubProfileConnector(skills=list(candidate.skills)),
            CrossrefConnector(),
            DomainConnector(),
            IdentityConnector(),
            ScholarConnector(),
            RoleConnector(profile, text, candidate),
        ]

    def not_checked(claims: Any, profile: str) -> list[dict[str, Any]]:
        items = []
        if not claims.github_username and not claims.project_repos and not claims.invalid_code_links:
            items.append(("GitHub", "No GitHub link or project was found in the resume. Add your GitHub link so your projects can be checked."))
        if not claims.dois and not claims.papers:
            items.append(("Research papers", "No research paper or DOI was found in the resume. This only matters if you list papers."))
        if not claims.employer_domains:
            items.append(("Employer website", "No employer website was found in the resume, so the employer could not be looked up."))
        items.append(("Role specific checks", CANNOT_CHECK.get(profile, CANNOT_CHECK["general"])))
        return [{"source": label, "status": "not_checked", "title": "Could not be checked", "explanation": text, "evidence_url": None, "checked_at": None} for label, text in items]

    def shape(signals: list[Any]) -> list[dict[str, Any]]:
        checks = []
        for signal in signals:
            reason = Reason(code=signal.code, severity=str(signal.severity), detail="", weight=0)
            checks.append(
                {
                    "source": source_label(str(signal.source)),
                    "status": "confirmed" if signal.polarity == "positive" else "problem",
                    "title": "Confirmed" if signal.polarity == "positive" else "Needs a closer look",
                    "explanation": explain_reason(reason),
                    "evidence_url": signal.evidence_url,
                    "checked_at": signal.fetched_at.isoformat() if signal.fetched_at else None,
                    "claim": signal.matched_claim,
                }
            )
        return checks

    def stored(application_id: str) -> dict[str, Any] | None:
        value = cache.get(f"checks:{application_id}")
        return value if isinstance(value, dict) else None

    @router.post("/v1/checks/run")
    def run_checks(body: RunIn, principal: Principal = Depends(runner_guard)) -> dict[str, Any]:
        guard_access(principal, body.application_id)
        previous = stored(body.application_id)
        if previous and time.time() - float(previous.get("ran_at", 0)) < MIN_SECONDS_BETWEEN_RUNS:
            raise HTTPException(status_code=429, detail="These checks just ran. Please wait a little before running them again.")
        candidate = candidate_for(body.application_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="We could not find that application.")
        text = text_for(body.application_id) or ""
        claims = extract_claims(text, candidate)
        detected = analyze_application(text or ". ".join(candidate.skills))["role"]
        profile = select_profile("" if detected == "Unknown" else detected, candidate.skills, [])
        factory = connector_factory or default_connectors
        try:
            signals, summary = enrich_function(claims, connectors=factory(profile, text, candidate), per_connector_timeout=25.0)
        except Exception:
            signals, summary = [], None
        checks = shape(list(signals)) + not_checked(claims, profile)
        counts = {name: sum(item["status"] == name for item in checks) for name in ("confirmed", "problem", "not_checked")}
        result = {
            "application_id": body.application_id,
            "ran_at": time.time(),
            "role_profile": profile,
            "role_label": PROFILE_LABELS.get(profile, "General role"),
            "github_found": bool(claims.github_username),
            "github_invalid_links": list(claims.invalid_code_links),
            "github": GitHubProfileConnector.summary_for(claims.github_username),
            "checks": checks,
            "counts": counts,
            "slow_sources": list(getattr(summary, "timed_out", []) or []) + list(getattr(summary, "failed", []) or []),
            "note": "These checks are advice for a person. They never change the decision by themselves and a missing result is never held against you.",
        }
        cache.set(f"checks:{body.application_id}", result, RECORD_TTL_SECONDS)
        if audit is not None:
            try:
                audit().append("claim_checks_run", principal.username, body.application_id)
            except Exception:
                pass
        return result

    @router.get("/v1/checks/application/{application_id}")
    def read_checks(application_id: str, principal: Principal = Depends(reader_guard)) -> dict[str, Any]:
        guard_access(principal, application_id)
        value = stored(application_id)
        if value is None:
            raise HTTPException(status_code=404, detail="These checks have not been run yet.")
        return value

    return router
