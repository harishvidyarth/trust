from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends

from firewall.auth.deps import Principal
from firewall.auth.service import get_service
from firewall.applicant_routes import CLOSED_STATUS, ApplicantService, status_for
from firewall.models import Decision
from firewall.redis_layer import KEY_PREFIX, RedisLink, link_from_env


RECORD_TTL_S = 90 * 24 * 3600

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
    service: ApplicantService,
) -> APIRouter:
    router = APIRouter()

    def application_meta(ids: set[str]) -> dict[str, tuple[str, float]]:
        found: dict[str, tuple[str, float]] = {}
        for application in store.applications():
            if application.application_id in ids:
                found[application.application_id] = (application.job_id, application.signals.submitted_at)
        return found

    def view(application_id: str, decision: Decision, job_id: str, submitted_at: float, detailed: bool) -> dict[str, object]:
        follow_up = service.follow_up(application_id, decision)
        record = service.record(application_id) or {}
        fields = record.get("fields") or {}
        item: dict[str, object] = {
            "application_id": application_id,
            "job_id": job_id,
            "role_title": fields.get("role_title") or None,
            "submitted_at": submitted_at,
            **(CLOSED_STATUS if service.closed(application_id) else status_for(decision.route.value)),
            "follow_up_count": service.unanswered(follow_up),
            "replaces": links.previous(application_id),
        }
        if detailed:
            item["follow_up"] = follow_up
        return item

    def own_items(username: str) -> list[dict[str, object]]:
        ids = get_service().ownership.owned_by(username)
        meta = application_meta(set(ids))
        items: list[dict[str, object]] = []
        for application_id in ids:
            decision = store.get_decision(application_id)
            if decision is None or application_id not in meta:
                continue
            job_id, submitted_at = meta[application_id]
            items.append(view(application_id, decision, job_id, submitted_at, False))
        items.sort(key=lambda item: float(item["submitted_at"]), reverse=True)
        return items

    @router.get("/v1/me/applications")
    def my_applications(principal: Principal = Depends(candidate_guard)) -> list[dict[str, object]]:
        return own_items(principal.username)

    @router.get("/v1/me/summary")
    def my_summary(principal: Principal = Depends(candidate_guard)) -> dict[str, object]:
        items = own_items(principal.username)
        return {"applications": len(items), "latest_status": items[0]["status"] if items else None}

    @router.get("/v1/me/applications/{application_id}")
    def my_application(application_id: str, principal: Principal = Depends(candidate_guard)) -> dict[str, object]:
        decision = service.own_decision(principal, application_id)
        job_id, submitted_at = application_meta({application_id}).get(application_id, ("job", 0.0))
        return view(application_id, decision, job_id, submitted_at, True)

    @router.get("/v1/job-presets")
    def job_presets(principal: Principal = Depends(any_guard)) -> list[dict[str, object]]:
        return [dict(item) for item in JOB_PRESETS]

    return router
