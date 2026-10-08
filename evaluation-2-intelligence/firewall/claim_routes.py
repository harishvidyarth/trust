from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from firewall.auth.deps import Principal
from firewall.claim_check import MAX_RESPONSE, analyze_application, evaluate_response
from firewall.models import Candidate, Decision

MAX_ATTEMPTS = 3
RECORD_TTL_SECONDS = 90 * 24 * 3600
CANDIDATE_HIDDEN = {"evidence_terms_found", "base_score", "answer_score", "verification_score", "reason_codes"}


RECEIVED_MESSAGE = "Thank you. Your answer was sent to the hiring team."


class QuestionIn(BaseModel):
    application_id: str = Field(min_length=1, max_length=128)


class AnswerIn(QuestionIn):
    response: str = Field(min_length=1, max_length=20000)


def candidate_text(candidate: Candidate) -> str:
    parts = list(candidate.skills)
    for item in candidate.experience:
        parts.append(f"{item.title} {item.company}")
    for project in candidate.projects:
        parts.append(f"{project.name} {project.description}")
    if candidate.claimed_experience_years:
        parts.append(f"{int(candidate.claimed_experience_years)} years")
    return ". ".join(part for part in parts if part)


def application_text_for(
    text_for: Callable[[str], str], candidate_for: Callable[[str], Candidate | None], application_id: str
) -> str:
    text = text_for(application_id)
    if text.strip():
        return text
    candidate = candidate_for(application_id)
    return candidate_text(candidate) if candidate is not None else ""


def claim_record(cache: Any, application_id: str) -> dict[str, Any]:
    stored = cache.get(f"claimcheck:{application_id}")
    return stored if isinstance(stored, dict) else {}


def submit_claim_answer(
    *,
    cache: Any,
    decision: Decision,
    application_id: str,
    text: str,
    answer: str,
    actor: str,
    audit: Callable[[], Any] | None,
) -> dict[str, Any]:
    answer = answer.strip()
    if not answer:
        raise HTTPException(status_code=400, detail="Please write an answer before you send it.")
    if len(answer) > MAX_RESPONSE:
        raise HTTPException(status_code=400, detail=f"Please keep your answer under {MAX_RESPONSE} characters.")
    record = claim_record(cache, application_id)
    attempts = int(record.get("attempts", 0))
    if attempts >= MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="You have used all your tries for this question.")
    analysis = analyze_application(text)
    if analysis["claim"] is None:
        raise HTTPException(status_code=400, detail="Nothing needs an answer for this application.")
    result = evaluate_response(text, analysis["role"], analysis["claim"], answer, decision.score)
    result["question"] = analysis["question"]
    result["attempts_left"] = max(0, MAX_ATTEMPTS - attempts - 1)
    result["answered_at"] = time.time()
    cache.set(
        f"claimcheck:{application_id}",
        {"attempts": attempts + 1, "result": result, "claim": analysis["claim"]},
        RECORD_TTL_SECONDS,
    )
    if audit is not None:
        try:
            audit().append("claim_answered", actor, application_id)
        except Exception:
            pass
    return result


def build_claim_router(
    *,
    candidate_guard: Callable[..., Any],
    reader_guard: Callable[..., Any],
    decision_for: Callable[[str], Decision | None],
    text_for: Callable[[str], str],
    candidate_for: Callable[[str], Candidate | None],
    owner_of: Callable[[str], str | None],
    cache: Any,
    audit: Callable[[], Any] | None = None,
    closed: Callable[[str], bool] | None = None,
) -> APIRouter:
    router = APIRouter()

    def can_touch(principal: Principal, application_id: str) -> bool:
        if principal.role in {"recruiter", "admin"} and principal.via != "open":
            return True
        if principal.via == "open":
            return True
        return owner_of(application_id) == principal.username

    def application_text(application_id: str) -> str:
        return application_text_for(text_for, candidate_for, application_id)

    def record_for(application_id: str) -> dict[str, Any]:
        return claim_record(cache, application_id)

    def shape(principal: Principal, value: dict[str, Any]) -> dict[str, Any]:
        if principal.role in {"recruiter", "admin"}:
            return value
        return {key: item for key, item in value.items() if key not in CANDIDATE_HIDDEN}

    def check_access(principal: Principal, application_id: str) -> Decision:
        decision = decision_for(application_id)
        if decision is None or not can_touch(principal, application_id):
            raise HTTPException(status_code=404, detail="We could not find that application.")
        return decision

    @router.post("/v1/claims/question")
    def question(body: QuestionIn, principal: Principal = Depends(reader_guard)) -> dict[str, Any]:
        check_access(principal, body.application_id)
        analysis = analyze_application(application_text(body.application_id))
        record = record_for(body.application_id)
        attempts = int(record.get("attempts", 0))
        left = max(0, MAX_ATTEMPTS - attempts)
        if principal.role == "candidate":
            return {"question": analysis["question"], "attempts_left": left, "answered": bool(record.get("result"))}
        return {
            "role": analysis["role"],
            "claims": analysis["claims"],
            "claim": analysis["claim"],
            "question": analysis["question"],
            "attempts_left": left,
            "answered": bool(record.get("result")),
            "result": shape(principal, record["result"]) if record.get("result") else None,
        }

    @router.post("/v1/claims/verify")
    def verify(body: AnswerIn, principal: Principal = Depends(candidate_guard)) -> dict[str, Any]:
        decision = check_access(principal, body.application_id)
        if principal.role in {"recruiter", "admin"} and principal.via != "open":
            raise HTTPException(status_code=403, detail="Only the candidate can answer this question.")
        if closed is not None and closed(body.application_id):
            raise HTTPException(status_code=409, detail="This application is closed.")
        result = submit_claim_answer(
            cache=cache,
            decision=decision,
            application_id=body.application_id,
            text=application_text(body.application_id),
            answer=body.response,
            actor=principal.username,
            audit=audit,
        )
        if principal.role == "candidate":
            return {"received": True, "message": RECEIVED_MESSAGE, "attempts_left": result["attempts_left"]}
        return shape(principal, result)

    @router.get("/v1/claims/application/{application_id}")
    def stored(application_id: str, principal: Principal = Depends(reader_guard)) -> dict[str, Any]:
        if principal.role == "candidate":
            raise HTTPException(status_code=404, detail="We could not find that application.")
        check_access(principal, application_id)
        record = record_for(application_id)
        if not record.get("result"):
            raise HTTPException(status_code=404, detail="No answer has been sent yet.")
        return shape(principal, record["result"])

    return router
