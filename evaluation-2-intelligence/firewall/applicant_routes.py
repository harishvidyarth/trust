from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from firewall.auth.deps import Principal
from firewall.claim_check import analyze_application
from firewall.claim_routes import (
    MAX_ATTEMPTS,
    RECEIVED_MESSAGE,
    application_text_for,
    claim_record,
    submit_claim_answer,
)
from firewall.intake_routes import normalise_public_https_url
from firewall.models import Candidate, Decision, Route

FORM_TTL_S = 90 * 24 * 3600
IDENTITY_ITEM = "identity-check"
IDENTITY_LABEL = "Quick identity check"
IDENTITY_HELP = "About one minute. You use your camera and microphone to follow three simple prompts. Nothing is recorded or kept."
FORBIDDEN_CHARS = "-()[]:;"
MAX_DETAIL_NOTES = 5
MAX_REQUESTS = 20
MAX_ANSWER_CHARS = 4000
CLAIM_ITEM = "claim-question"
DETAILS_ITEM = "more-details"
REQUEST_PREFIX = "request-"
NEUTRAL_QUESTION = "Please describe one project you worked on. Say what your role was, what you did yourself and what the result was."
DETAILS_LABEL = "Anything else you would like to add"
DETAILS_HELP = "Optional. You can type more about your work, links or dates."
REQUEST_HELP = "Please type your answer below."
QUESTION_HELP = "Type your answer in your own words. You can try up to three times."
DETAILS_MESSAGE = "Thank you. Your details were sent to the hiring team."

STATUS_BY_ROUTE = {
    Route.PASS_TO_ATS.value: (
        "sent",
        "Your application was sent to the hiring team.",
        "Thank you for applying. The hiring team will contact you if they need anything else.",
    ),
    Route.ADDITIONAL_VERIFICATION.value: (
        "more_details",
        "The hiring team may need a few more details.",
        "You can type them in below whenever you are ready. This helps the hiring team move faster.",
    ),
    Route.MANUAL_REVIEW.value: (
        "in_review",
        "A person on the hiring team is reviewing your application.",
        "Thank you for your patience. You can add more details below if you wish.",
    ),
}

CLOSED_STATUS = {
    "status": "closed",
    "title": "Thank you for applying.",
    "message": "The hiring team has decided not to move forward with your application at this time. We appreciate the time you took.",
}
CLOSED_DETAIL = "This application is closed."

LIMITS = {
    "applicant_name": 120,
    "applicant_email": 254,
    "applicant_phone": 32,
    "role_title": 120,
    "current_employer": 120,
    "education": 300,
    "about_project": 2000,
}
MAX_PAPERS = 10
MAX_PAPER_CHARS = 300
MAX_CERTIFICATES = 10
MAX_CERTIFICATE_CHARS = 64
MAX_SKILLS = 30
MAX_SKILL_CHARS = 60

LABELS = {
    "applicant_name": "name",
    "applicant_email": "email address",
    "applicant_phone": "phone number",
    "role_title": "role",
    "current_employer": "current employer",
    "education": "education",
    "about_project": "project description",
}

EMAIL_RE = re.compile(r"[^@\s]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}\Z")
PHONE_RE = re.compile(r"\+?[0-9][0-9 ().-]{5,30}[0-9]\Z")
CERTIFICATE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{2,63}\Z")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
GITHUB_HOSTS = ("github.com", "www.github.com")
LINKEDIN_HOSTS = ("linkedin.com", "www.linkedin.com")

FIELD_KEYS = (
    "applicant_name",
    "applicant_email",
    "applicant_phone",
    "role_title",
    "current_employer",
    "education",
    "github_url",
    "linkedin_url",
    "portfolio_url",
    "papers",
    "certificate_ids",
    "years_experience",
    "extra_skills",
    "about_project",
)


def plain(text: str) -> str:
    cleaned = text
    for char in FORBIDDEN_CHARS:
        cleaned = cleaned.replace(char, ",") if char in ":;" else cleaned.replace(char, " ")
    return " ".join(cleaned.split()).replace(" ,", ",").replace(",,", ",")


def bad(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail=message)


def one_line(value: str | None, field: str) -> str:
    cleaned = " ".join(CONTROL_RE.sub(" ", value or "").split())
    limit = LIMITS.get(field, 200)
    if len(cleaned) > limit:
        raise bad(f"Your {LABELS.get(field, 'entry')} is too long. Please keep it under {limit} characters.")
    return cleaned


def many_lines(value: str | None, field: str) -> str:
    cleaned = CONTROL_RE.sub(" ", (value or "").replace("\r", "\n"))
    lines = [" ".join(line.split()) for line in cleaned.split("\n")]
    joined = "\n".join(line for line in lines if line)
    if len(joined) > LIMITS[field]:
        raise bad(f"Your {LABELS[field]} is too long. Please keep it under {LIMITS[field]} characters.")
    return joined


def link(value: str | None, hosts: tuple[str, ...] | None, label: str) -> str:
    raw = " ".join((value or "").split())
    if not raw:
        return ""
    try:
        return normalise_public_https_url(raw, hosts)
    except ValueError as error:
        where = f" on {hosts[0]}" if hosts is not None else ""
        raise bad(f"The {label} link is not valid. Please use a full https link{where} without a user name or password.") from error


def split_items(value: str | None, pattern: str, limit: int, item_chars: int, label: str, seen_fold: bool = True) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for part in re.split(pattern, CONTROL_RE.sub(" ", value or "")):
        item = " ".join(part.split())
        if not item:
            continue
        if len(item) > item_chars:
            raise bad(f"One of your {label} is too long. Please keep each one under {item_chars} characters.")
        key = item.casefold() if seen_fold else item
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
    if len(items) > limit:
        raise bad(f"Please list at most {limit} {label}.")
    return items


def parse_typed_form(raw: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "applicant_name": one_line(raw.get("applicant_name"), "applicant_name"),
        "applicant_email": one_line(raw.get("applicant_email"), "applicant_email"),
        "applicant_phone": one_line(raw.get("applicant_phone"), "applicant_phone"),
        "role_title": one_line(raw.get("role_title"), "role_title"),
        "current_employer": one_line(raw.get("current_employer"), "current_employer"),
        "education": one_line(raw.get("education"), "education"),
        "github_url": link(raw.get("github_url"), GITHUB_HOSTS, "GitHub"),
        "linkedin_url": link(raw.get("linkedin_url"), LINKEDIN_HOSTS, "LinkedIn"),
        "portfolio_url": link(raw.get("portfolio_url"), None, "portfolio"),
        "papers": split_items(raw.get("papers"), r"\n|\r", MAX_PAPERS, MAX_PAPER_CHARS, "papers"),
        "certificate_ids": split_items(raw.get("certificate_ids"), r"[,\n\r]", MAX_CERTIFICATES, MAX_CERTIFICATE_CHARS, "certificate numbers", False),
        "years_experience": None,
        "extra_skills": split_items(raw.get("extra_skills"), r"[,\n\r]", MAX_SKILLS, MAX_SKILL_CHARS, "skills"),
        "about_project": many_lines(raw.get("about_project"), "about_project"),
    }
    if fields["applicant_email"] and not EMAIL_RE.fullmatch(fields["applicant_email"]):
        raise bad("Please type a valid email address.")
    if fields["applicant_phone"] and not PHONE_RE.fullmatch(fields["applicant_phone"]):
        raise bad("Please type a valid phone number using digits and spaces, with an optional plus sign at the start.")
    for code in fields["certificate_ids"]:
        if not CERTIFICATE_RE.fullmatch(code):
            raise bad("One of your certificate numbers does not look right. Please check it and try again.")
    years = " ".join(str(raw.get("years_experience") or "").split())
    if years:
        try:
            value = float(years)
        except ValueError as error:
            raise bad("Please type your years of experience as a number between 0 and 60.") from error
        if not 0 <= value <= 60:
            raise bad("Please type your years of experience as a number between 0 and 60.")
        fields["years_experience"] = value
    return fields


def require_identity(fields: dict[str, Any]) -> None:
    if not fields["applicant_name"]:
        raise bad("Please type your full name.")
    if not fields["applicant_email"]:
        raise bad("Please type your email address.")
    if not fields["applicant_phone"]:
        raise bad("Please type your phone number.")


def has_typed_values(fields: dict[str, Any]) -> bool:
    return any(fields.get(key) not in ("", [], None) for key in FIELD_KEYS)


def synthesize_resume(fields: dict[str, Any]) -> str:
    lines = [fields["applicant_name"], fields["applicant_email"], fields["applicant_phone"]]
    if fields["role_title"]:
        lines.append(f"Applying for the role of {fields['role_title']}")
    if fields["current_employer"]:
        lines.append(f"Current employer {fields['current_employer']}")
    if fields["years_experience"] is not None:
        years = fields["years_experience"]
        lines.append(f"{int(years) if years == int(years) else years} years of experience")
    for label, key in (("GitHub", "github_url"), ("LinkedIn", "linkedin_url"), ("Portfolio", "portfolio_url")):
        if fields[key]:
            lines.append(f"{label}: {fields[key]}")
    lines.extend(f"Paper: {paper}" for paper in fields["papers"])
    if fields["certificate_ids"]:
        lines.append("Certificate numbers: " + ", ".join(fields["certificate_ids"]))
    if fields["education"]:
        lines.extend(["Education", fields["education"]])
    if fields["extra_skills"]:
        lines.extend(["Skills", ", ".join(fields["extra_skills"])])
    if fields["about_project"]:
        lines.extend(["Projects", "Project work", " ".join(fields["about_project"].split())])
    return "\n".join(lines) + "\n"


def context_block(fields: dict[str, Any]) -> str:
    lines: list[str] = []
    for label, key in (("GitHub", "github_url"), ("LinkedIn", "linkedin_url"), ("Portfolio", "portfolio_url")):
        if fields[key]:
            lines.append(f"{label}: {fields[key]}")
    lines.extend(f"Paper: {paper}" for paper in fields["papers"])
    if fields["certificate_ids"]:
        lines.append("Certificate numbers: " + ", ".join(fields["certificate_ids"]))
    return "\n".join(lines)


def merge_skills(existing: list[str], extra: list[str]) -> list[str]:
    merged = list(existing)
    seen = {item.casefold() for item in merged}
    for item in extra:
        if item.casefold() not in seen:
            seen.add(item.casefold())
            merged.append(item)
    return merged


def apply_typed(candidate: Candidate, fields: dict[str, Any]) -> Candidate:
    update: dict[str, Any] = {}
    for source, target in (("applicant_name", "name"), ("applicant_email", "email"), ("applicant_phone", "phone")):
        if fields[source]:
            update[target] = fields[source]
    if fields["extra_skills"]:
        update["skills"] = merge_skills(candidate.skills, fields["extra_skills"])
    return candidate.model_copy(update=update) if update else candidate


def status_for(route: str) -> dict[str, str]:
    status, title, message = STATUS_BY_ROUTE.get(route, STATUS_BY_ROUTE[Route.MANUAL_REVIEW.value])
    return {"status": status, "title": title, "message": message}


class AnswerBody(BaseModel):
    item_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=MAX_ANSWER_CHARS)


class RequestBody(BaseModel):
    label: str = Field(min_length=3, max_length=200)
    help: str | None = Field(default=None, max_length=300)


class ApplicantService:
    def __init__(
        self,
        *,
        form_cache: Any,
        claim_cache: Any,
        decision_for: Callable[[str], Decision | None],
        text_for: Callable[[str], str],
        candidate_for: Callable[[str], Candidate | None],
        owner_of: Callable[[str], str | None],
        audit: Callable[[], Any] | None = None,
        closed: Callable[[str], bool] | None = None,
        identity_done: Callable[[str], bool] | None = None,
    ) -> None:
        self.closed = closed or (lambda application_id: False)
        self.identity_done = identity_done or (lambda application_id: False)
        self.form_cache = form_cache
        self.claim_cache = claim_cache
        self.decision_for = decision_for
        self.text_for = text_for
        self.candidate_for = candidate_for
        self.owner_of = owner_of
        self.audit = audit
        self._lock = threading.Lock()

    @staticmethod
    def _key(application_id: str) -> str:
        return f"applicant_form:{application_id}"

    def record(self, application_id: str) -> dict[str, Any] | None:
        value = self.form_cache.get(self._key(application_id))
        return value if isinstance(value, dict) else None

    def _save(self, application_id: str, record: dict[str, Any]) -> None:
        if not self.form_cache.set(self._key(application_id), record, FORM_TTL_S):
            raise HTTPException(status_code=400, detail="That is too much text to save. Please shorten it and try again.")

    def store_submission(self, application_id: str, fields: dict[str, Any], submitted_at: float) -> None:
        with self._lock:
            previous = self.record(application_id) or {}
            record = {
                "fields": fields,
                "submitted_at": submitted_at,
                "requests": previous.get("requests", []),
                "details": previous.get("details", []),
            }
            self._save(application_id, record)

    def _note(self, event: str, actor: str, application_id: str) -> None:
        if self.audit is None:
            return
        try:
            self.audit().append(event, actor, application_id)
        except Exception:
            pass

    def open_count(self, application_id: str) -> int:
        record = self.record(application_id)
        if record is None:
            return 0
        return sum(1 for item in record.get("requests", []) if item.get("answer") is None)

    def claim_item(self, application_id: str, decision: Decision) -> dict[str, Any] | None:
        if decision.route == Route.PASS_TO_ATS:
            return None
        text = application_text_for(self.text_for, self.candidate_for, application_id)
        analysis = analyze_application(text)
        if analysis["claim"] is None or not analysis["question"]:
            return None
        label = NEUTRAL_QUESTION if analysis["claim"] == "Unsubstantiated Expertise Claim" else plain(str(analysis["question"]))
        record = claim_record(self.claim_cache, application_id)
        return {
            "id": CLAIM_ITEM,
            "kind": "question",
            "label": label,
            "help": QUESTION_HELP,
            "required": decision.route == Route.ADDITIONAL_VERIFICATION,
            "answered": bool(record.get("result")),
            "attempts_left": max(0, MAX_ATTEMPTS - int(record.get("attempts", 0))),
        }

    def follow_up(self, application_id: str, decision: Decision) -> list[dict[str, Any]]:
        if self.closed(application_id):
            return []
        items: list[dict[str, Any]] = []
        question = self.claim_item(application_id, decision)
        if question is not None:
            items.append(question)
        record = self.record(application_id) or {}
        for request in record.get("requests", []):
            items.append(
                {
                    "id": request["id"],
                    "kind": "request",
                    "label": plain(request["label"]),
                    "help": plain(request.get("help") or "") or REQUEST_HELP,
                    "required": True,
                    "answered": request.get("answer") is not None,
                }
            )
        notes = record.get("details", [])
        items.append(self.identity_item(application_id))
        items.append(
            {
                "id": DETAILS_ITEM,
                "kind": "details",
                "label": DETAILS_LABEL,
                "help": DETAILS_HELP,
                "required": False,
                "answered": bool(notes),
                "attempts_left": max(0, MAX_DETAIL_NOTES - len(notes)),
            }
        )
        return items

    def identity_item(self, application_id: str) -> dict[str, Any]:
        return {
            "id": IDENTITY_ITEM,
            "kind": "identity",
            "label": IDENTITY_LABEL,
            "help": IDENTITY_HELP,
            "required": False,
            "answered": bool(self.identity_done(application_id)),
        }

    @staticmethod
    def unanswered(items: list[dict[str, Any]]) -> int:
        return sum(1 for item in items if item["kind"] not in {"details", "identity"} and not item["answered"])

    def own_decision(self, principal: Principal, application_id: str) -> Decision:
        decision = self.decision_for(application_id)
        if decision is None or self.owner_of(application_id) != principal.username:
            raise HTTPException(status_code=404, detail="We could not find that application.")
        return decision

    def answer(self, principal: Principal, application_id: str, item_id: str, text: str) -> dict[str, Any]:
        decision = self.own_decision(principal, application_id)
        if self.closed(application_id):
            raise HTTPException(status_code=409, detail=CLOSED_DETAIL)
        typed = text.strip()
        if not typed:
            raise bad("Please type your answer before you send it.")
        if item_id == CLAIM_ITEM:
            if self.claim_item(application_id, decision) is None:
                raise HTTPException(status_code=404, detail="We could not find that question.")
            result = submit_claim_answer(
                cache=self.claim_cache,
                decision=decision,
                application_id=application_id,
                text=application_text_for(self.text_for, self.candidate_for, application_id),
                answer=typed,
                actor=principal.username,
                audit=self.audit,
            )
            return {"received": True, "message": RECEIVED_MESSAGE, "attempts_left": result["attempts_left"]}
        with self._lock:
            record = self.record(application_id) or {
                "fields": {},
                "submitted_at": time.time(),
                "requests": [],
                "details": [],
            }
            if item_id == DETAILS_ITEM:
                if len(record["details"]) >= MAX_DETAIL_NOTES:
                    raise HTTPException(status_code=429, detail="You have already added the most notes we can take.")
                record["details"].append({"text": typed, "at": time.time()})
                left: int | None = MAX_DETAIL_NOTES - len(record["details"])
                message = DETAILS_MESSAGE
            else:
                target = next((item for item in record["requests"] if item["id"] == item_id), None)
                if target is None or not item_id.startswith(REQUEST_PREFIX):
                    raise HTTPException(status_code=404, detail="We could not find that question.")
                target["answer"] = typed
                target["answered_at"] = time.time()
                left = None
                message = RECEIVED_MESSAGE
            self._save(application_id, record)
        self._note("details_added", principal.username, application_id)
        return {"received": True, "message": message, "attempts_left": left}

    def add_request(self, principal: Principal, application_id: str, label: str, help_text: str | None) -> dict[str, Any]:
        if self.decision_for(application_id) is None:
            raise HTTPException(status_code=404, detail="We could not find that application.")
        clean_label = " ".join(label.split())
        if len(clean_label) < 3:
            raise bad("Please type what you would like the applicant to tell you.")
        with self._lock:
            record = self.record(application_id) or {
                "fields": {},
                "submitted_at": time.time(),
                "requests": [],
                "details": [],
            }
            if len(record["requests"]) >= MAX_REQUESTS:
                raise HTTPException(status_code=429, detail="This application already has the most requests allowed.")
            item = {
                "id": f"{REQUEST_PREFIX}{len(record['requests']) + 1}",
                "label": clean_label,
                "help": " ".join((help_text or "").split()),
                "asked_by": principal.username,
                "asked_at": time.time(),
                "answer": None,
                "answered_at": None,
            }
            record["requests"].append(item)
            self._save(application_id, record)
        self._note("details_requested", principal.username, application_id)
        return item

    def recruiter_form(self, application_id: str) -> dict[str, Any]:
        record = self.record(application_id)
        if record is None:
            raise HTTPException(status_code=404, detail="No form was found for this application.")
        return {
            "fields": record.get("fields", {}),
            "submitted_at": record.get("submitted_at"),
            "requests": record.get("requests", []),
            "details": record.get("details", []),
        }


def build_applicant_router(
    service: ApplicantService,
    *,
    candidate_guard: Callable[..., Principal],
    recruiter_guard: Callable[..., Principal],
) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/me/applications/{application_id}/answers")
    def answer(application_id: str, body: AnswerBody, principal: Principal = Depends(candidate_guard)) -> dict[str, Any]:
        return service.answer(principal, application_id, body.item_id, body.text)

    @router.get("/v1/applications/{application_id}/form")
    def form(application_id: str, principal: Principal = Depends(recruiter_guard)) -> dict[str, Any]:
        return service.recruiter_form(application_id)

    @router.post("/v1/applications/{application_id}/requests")
    def request_details(application_id: str, body: RequestBody, principal: Principal = Depends(recruiter_guard)) -> dict[str, Any]:
        return service.add_request(principal, application_id, body.label, body.help)

    return router
