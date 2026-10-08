from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from firewall.auth.deps import Principal
from firewall.auth.service import get_service
from firewall.models import Decision, Reason
from firewall.reasoning import explain_reason
from firewall.redis_layer import KEY_PREFIX, RedisLink, link_from_env


RECORD_TTL_S = 90 * 24 * 3600
FORBIDDEN_CHARS = "-()[]:;"

TITLES = {
    "DUP_EMAIL": "Email already used",
    "DUP_PHONE": "Phone number already used",
    "DUP_RESUME_EXACT": "Resume matches an earlier one",
    "DUP_RESUME_NEAR": "Resume is very close to an earlier one",
    "DUP_SAME_JOB": "Already applied for this job",
    "VELOCITY_HIGH": "Many applications in a short time",
    "NETWORK_BURST": "Busy network",
    "IDENTITY_DEVICE_ROTATION": "Several devices used",
    "FAST_SUBMIT": "Application finished very quickly",
    "PASTE_BULK": "Mostly pasted text",
    "TEMPLATE_REUSE": "Shared project wording",
    "QUAL_MISSING_MUST_HAVE": "Required skills not shown",
    "QUAL_UNDER_EXPERIENCE": "Less experience than the role asks for",
    "TIMELINE_INVALID": "Dates that do not make sense",
    "TIMELINE_OVERLAP": "Roles that overlap in time",
    "RESUME_HIDDEN_TEXT": "Hidden text in the resume",
    "RESUME_PROMPT_INJECTION": "Instructions aimed at screening tools",
    "RESUME_KEYWORD_STUFFING": "Too many job keywords",
    "RESUME_PARSE_DIVERGENCE": "Resume reads differently to a computer",
    "EMAIL_DISPOSABLE": "Throwaway email address",
    "PROBING_SUSPECTED": "Many changed copies sent",
    "SESSION_ELAPSED_MISMATCH": "Session timing does not match",
    "UNATTESTED_PATH": "Not sent through a verified session",
    "TOKEN_INVALID": "Session token not valid",
    "LINK_REUSE": "Profile link used by others",
    "FUZZY_IDENTITY": "Close match to another applicant",
    "EMAIL_ALIAS": "Email looks like a changed form of an earlier one",
    "CLUSTER_SAME_FACTBASE": "Details shared with other applications",
    "CERT_ID_INVALID_FORMAT": "Certificate number looks wrong",
    "REFERENCE_EMAIL_IS_CANDIDATE": "Reference email is your own",
    "REFERENCE_EMAIL_FREEMAIL": "Reference uses a free email address",
    "IDENTITY_NAME_MISMATCH": "Name does not match",
}

PREFIX_TITLES = (
    ("RESUME_HIDDEN_", "Hidden text in the resume"),
    ("RESUME_INJECTION_", "Instructions aimed at screening tools"),
    ("RESUME_STUFFING_", "Too many job keywords"),
    ("RESUME_DIVERGENCE_", "Resume reads differently to a computer"),
    ("DUP_", "Possible duplicate application"),
    ("GITHUB_", "Code project check"),
    ("DOI_", "Research paper check"),
    ("PAPER_", "Research paper check"),
    ("MEMBERSHIP_", "Membership check"),
    ("DIN_", "Company role check"),
    ("DIRECTORSHIP_", "Company role check"),
    ("REGULATOR_", "Professional registration check"),
    ("PATENT_", "Patent check"),
    ("ARXIV_", "Research paper check"),
    ("IEEE_", "Research paper check"),
    ("LINKEDIN_", "Profile details do not match"),
    ("PORTFOLIO_", "Portfolio check"),
    ("DOMAIN_", "Website check"),
    ("EMPLOYER_", "Employer check"),
    ("IDENTITY_", "Identity check"),
    ("FED_", "Report from a partner system"),
    ("TIMELINE_", "Work history dates"),
    ("QUAL_", "Match with the role"),
)

JOB_PRESETS = [
    {
        "id": "software",
        "title": "Backend Software Engineer",
        "must_have": ["Python", "FastAPI", "PostgreSQL", "Docker"],
        "nice_to_have": ["AWS", "Redis"],
        "min_years": 2,
    },
    {
        "id": "finance",
        "title": "Financial Analyst",
        "must_have": ["Financial modelling", "Excel", "Accounting", "Reporting"],
        "nice_to_have": ["SQL", "Power BI"],
        "min_years": 3,
    },
    {
        "id": "hardware",
        "title": "Embedded Hardware Engineer",
        "must_have": ["C", "Embedded systems", "Circuit design", "PCB layout"],
        "nice_to_have": ["RTOS", "Python"],
        "min_years": 3,
    },
    {
        "id": "design",
        "title": "Product Designer",
        "must_have": ["Figma", "User research", "Prototyping", "Design systems"],
        "nice_to_have": ["Accessibility", "Motion design"],
        "min_years": 2,
    },
    {
        "id": "operations",
        "title": "Operations Manager",
        "must_have": ["Process improvement", "Vendor management", "Budgeting", "Team leadership"],
        "nice_to_have": ["Six Sigma", "ERP tools"],
        "min_years": 5,
    },
]


def plain(text: str) -> str:
    cleaned = text
    for char in FORBIDDEN_CHARS:
        cleaned = cleaned.replace(char, ",") if char in ":;" else cleaned.replace(char, " ")
    return " ".join(cleaned.split()).replace(" ,", ",").replace(",,", ",")


def title_for(code: str) -> str:
    known = TITLES.get(code)
    if known is not None:
        return known
    for prefix, title in PREFIX_TITLES:
        if code.startswith(prefix):
            return title
    return "Another point to check"


def is_concern(reason: Reason) -> bool:
    return reason.weight > 0 and reason.severity.casefold() != "info"


def concern_map(decision: Decision) -> dict[str, Reason]:
    found: dict[str, Reason] = {}
    for reason in decision.reasons:
        if is_concern(reason) and reason.code not in found:
            found[reason.code] = reason
    return found


def describe(reason: Reason) -> dict[str, str]:
    return {"title": plain(title_for(reason.code)), "explanation": plain(reason.explanation or explain_reason(reason))}


def unique_by_title(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    result = []
    for item in items:
        if item["title"] not in seen:
            seen.add(item["title"])
            result.append(item)
    return result


def delta_message(before: int, after: int, cleared: int, new: int) -> str:
    change = after - before
    if change > 0:
        parts = [f"Your score went up by {change} {'point' if change == 1 else 'points'}."]
    elif change < 0:
        parts = [f"Your score went down by {-change} {'point' if change == -1 else 'points'}."]
    else:
        parts = ["Your score stayed the same."]
    if cleared:
        parts.append(f"{cleared} {'concern is' if cleared == 1 else 'concerns are'} gone.")
    if new:
        parts.append(f"{new} new {'concern' if new == 1 else 'concerns'} appeared.")
    if not cleared and not new:
        parts.append("No concerns were added or removed.")
    return " ".join(parts)


class ReplaceLinks:
    def __init__(self, link: RedisLink | None = None) -> None:
        self._link = link
        self._local: dict[str, str] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(application_id: str) -> str:
        return f"{KEY_PREFIX}repl:{application_id}"

    def _remember(self, application_id: str, previous_id: str) -> None:
        with self._lock:
            self._local[application_id] = previous_id

    def link(self, application_id: str, previous_id: str) -> None:
        self._remember(application_id, previous_id)
        if self._link is not None:
            self._link.call(
                lambda client: client.set(self._key(application_id), previous_id, ex=RECORD_TTL_S),
                lambda: None,
            )

    def previous(self, application_id: str) -> str | None:
        if self._link is not None:
            remote = self._link.call(lambda client: client.get(self._key(application_id)), lambda: None)
            if remote is not None:
                return remote.decode() if isinstance(remote, bytes) else str(remote)
        with self._lock:
            return self._local.get(application_id)


def build_replace_links() -> ReplaceLinks:
    return ReplaceLinks(link_from_env())


def build_candidate_router(
    store: Any,
    links: ReplaceLinks,
    candidate_guard: Callable[..., Principal],
    any_guard: Callable[..., Principal],
) -> APIRouter:
    router = APIRouter()

    def submitted_at_map(ids: set[str]) -> dict[str, tuple[str, float]]:
        found: dict[str, tuple[str, float]] = {}
        for application in store.applications():
            if application.application_id in ids:
                found[application.application_id] = (application.job_id, application.signals.submitted_at)
        return found

    def own_items(username: str) -> list[dict[str, object]]:
        ids = get_service().ownership.owned_by(username)
        meta = submitted_at_map(set(ids))
        items: list[dict[str, object]] = []
        for application_id in ids:
            decision = store.get_decision(application_id)
            if decision is None or application_id not in meta:
                continue
            job_id, submitted_at = meta[application_id]
            items.append(
                {
                    "application_id": application_id,
                    "job_id": job_id,
                    "submitted_at": submitted_at,
                    "score": decision.score,
                    "route": decision.route.value,
                    "summary": decision.summary,
                    "concern_count": len(concern_map(decision)),
                    "fix_count": len(decision.candidate_fixes or []),
                    "replaces": links.previous(application_id),
                    "is_test": False,
                }
            )
        items.sort(key=lambda item: float(item["submitted_at"]), reverse=True)
        return items

    def own_decision(username: str, application_id: str) -> Decision:
        decision = store.get_decision(application_id)
        if decision is None or get_service().ownership.owner(application_id) != username:
            raise HTTPException(status_code=404, detail="We could not find that application.")
        return decision

    @router.get("/v1/me/applications")
    def my_applications(principal: Principal = Depends(candidate_guard)) -> list[dict[str, object]]:
        return own_items(principal.username)

    @router.get("/v1/me/summary")
    def my_summary(principal: Principal = Depends(candidate_guard)) -> dict[str, object]:
        items = own_items(principal.username)
        return {
            "applications": len(items),
            "best_score": max((int(item["score"]) for item in items), default=None),
            "latest_route": items[0]["route"] if items else None,
        }

    @router.get("/v1/me/applications/{application_id}", response_model=Decision)
    def my_application(application_id: str, principal: Principal = Depends(candidate_guard)) -> Decision:
        return own_decision(principal.username, application_id)

    @router.get("/v1/me/applications/{application_id}/delta")
    def my_delta(application_id: str, principal: Principal = Depends(candidate_guard)) -> dict[str, object]:
        current = own_decision(principal.username, application_id)
        previous_id = links.previous(application_id)
        previous = own_decision(principal.username, previous_id) if previous_id else None
        if previous_id is None or previous is None:
            raise HTTPException(status_code=404, detail="This application does not replace an earlier one.")
        before, after = concern_map(previous), concern_map(current)
        cleared = unique_by_title([describe(before[code]) for code in before if code not in after])
        added = unique_by_title([describe(after[code]) for code in after if code not in before])
        return {
            "previous_id": previous_id,
            "score_before": previous.score,
            "score_after": current.score,
            "score_change": current.score - previous.score,
            "cleared": cleared,
            "new": added,
            "unchanged_count": len([code for code in before if code in after]),
            "message": delta_message(previous.score, current.score, len(cleared), len(added)),
        }

    @router.get("/v1/job-presets")
    def job_presets(principal: Principal = Depends(any_guard)) -> list[dict[str, object]]:
        return [dict(item) for item in JOB_PRESETS]

    return router
