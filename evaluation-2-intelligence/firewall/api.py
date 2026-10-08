from __future__ import annotations

import hashlib
import hmac
import math
import os
import time
import uuid
from collections import Counter
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from firewall.auth import bind_application, overrides_for
from firewall.auth.deps import Principal
from firewall.auth.service import get_service
from firewall.auth_routes import build_auth_router
from firewall.config import load_config
from firewall.connectors.greenhouse import GreenhouseWebhook, adapt
from firewall.connectors.mock_ats import MockATS
from firewall.delivery import build_delivery_from_env
from firewall.delivery_routes import build_delivery_router
from firewall.guards import application_access, auth_enforced, guard, session_cookie_present
from firewall.intake_intel import RealIntelService
from firewall.intake_routes import build_intake_router
from firewall.llm.ollama_client import OllamaClient
from firewall.engine import evaluate
from firewall.models import (
    Application,
    Candidate,
    Decision,
    EvaluationRequest,
    JobRequirements,
    Reason,
    Route,
    SubmissionSignals,
)
from firewall.resume.intent import classify_hidden_intents
from firewall.resume.service import analyze_resume, naive_ats_rank
from firewall.resume.style import analyze_style
from firewall.enrichment import runner as enrichment_runner
from firewall.redis_layer import (
    JsonCache,
    build_counter,
    build_intake_repository,
    build_llm_client,
    build_optional_queue,
    build_signal_cache,
    build_store,
    redis_status,
)
from firewall.webhooks import build_router


app = FastAPI(title="AI Application Firewall", version="0.1.0")
STORE = build_store()
MOCK_ATS = MockATS()
CONFIG = load_config()
DELIVERY = build_delivery_from_env(MOCK_ATS, queue=build_optional_queue())
INTAKE_REPO = build_intake_repository()
CONTEXT = JsonCache(default_ttl_s=86_400)
MAX_CONTEXT_TEXT = 200_000
_SIGNAL_CACHE = build_signal_cache()
if _SIGNAL_CACHE is not None:
    enrichment_runner._DEFAULT_CACHE = _SIGNAL_CACHE
MAX_WEBHOOK_BYTES = 1_048_576
MAX_UPLOAD_BYTES = 5_242_880
OPEN_PATHS = frozenset({"/healthz"})
UNKNOWN_IPS = frozenset({"", "0.0.0.0", "unknown"})


@app.middleware("http")
async def protect(request: Request, call_next):
    api_key = os.getenv("FIREWALL_API_KEY")
    session_path = auth_enforced() and (request.url.path.startswith("/v1/auth/") or session_cookie_present(request))
    if api_key and request.method != "OPTIONS" and request.url.path not in OPEN_PATHS and not session_path:
        supplied = request.headers.get("x-api-key", "")
        if not hmac.compare_digest(supplied.encode(), api_key.encode()):
            return JSONResponse({"detail": "missing or invalid API key"}, status_code=401)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cache-Control"] = "no-store"
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv("FIREWALL_CORS_ORIGINS", "http://localhost:8080,http://127.0.0.1:8080").split(",")
        if origin.strip()
    ],
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "Signature", "X-CSRF-Token"],
    allow_credentials=True,
)


def _clean_timestamp(value: float) -> float:
    now = time.time()
    if not math.isfinite(value) or value <= 0:
        return now
    if value > 1e11:
        value = value / 1000.0
    if value > now + 300:
        return now
    return value


def _sanitize(application: Application, request: Request) -> Application:
    signals = application.signals
    update: dict[str, object] = {"submitted_at": _clean_timestamp(signals.submitted_at)}
    if signals.ip in UNKNOWN_IPS and request.client is not None:
        update["ip"] = request.client.host
    return application.model_copy(update={"signals": signals.model_copy(update=update)})


def _llm_client() -> object | None:
    if os.getenv("FIREWALL_LLM") != "1":
        return None
    return build_llm_client(OllamaClient())


def _remember(application: Application, resume_text: str | None) -> None:
    key = f"ctx:{application.application_id}"
    previous = CONTEXT.get(key) or {}
    text = resume_text if resume_text is not None else previous.get("resume_text", "")
    CONTEXT.set(
        key,
        {"resume_text": text[:MAX_CONTEXT_TEXT], "candidate": application.candidate.model_dump(mode="json")},
    )


def _resume_text_for(application_id: str) -> str:
    context = CONTEXT.get(f"ctx:{application_id}")
    return str(context["resume_text"]) if context else ""


def _candidate_for(application_id: str) -> Candidate | None:
    context = CONTEXT.get(f"ctx:{application_id}")
    if context:
        return Candidate.model_validate(context["candidate"])
    for application in STORE.applications():
        if application.application_id == application_id:
            return application.candidate
    return None


def _score_for(application_id: str) -> int | None:
    decision = STORE.get_decision(application_id)
    return decision.score if decision is not None else None


def _authorize_application(principal: Principal | None, application_id: str) -> None:
    if principal is None or principal.role != "candidate":
        return
    if get_service().ownership.owner(application_id) != principal.username:
        raise HTTPException(status_code=404, detail="application not found")


def _evaluate_and_forward(
    application: Application,
    job: JobRequirements,
    extra_reasons: list[Reason] | None = None,
    dry_run: bool = False,
    resume_text: str | None = None,
) -> Decision:
    already_decided = STORE.get_decision(application.application_id) is not None
    decision = evaluate(
        application,
        job,
        STORE,
        CONFIG,
        extra_reasons=extra_reasons,
        persist=not dry_run,
        resume_text=resume_text,
        llm_client=_llm_client(),
    )
    if dry_run:
        return decision
    _remember(application, resume_text)
    if not already_decided:
        try:
            DELIVERY.deliver(decision.route, application)
        except Exception:
            pass
    return decision


def _mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


@app.post("/v1/applications/evaluate", response_model=Decision)
def evaluate_application(
    payload: EvaluationRequest,
    request: Request,
    principal: Principal = Depends(guard("service", "recruiter", "admin")),
) -> Decision:
    return _evaluate_and_forward(_sanitize(payload.application, request), payload.job)


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="resume file too large")
    return data


def _parse_job(job_json: str) -> JobRequirements:
    try:
        return JobRequirements.model_validate_json(job_json)
    except ValidationError as error:
        raise RequestValidationError(error.errors()) from error


@app.post("/v1/applications/upload", response_model=Decision)
async def upload_application(
    request: Request,
    file: Annotated[UploadFile, File()],
    job_json: Annotated[str, Form()],
    device_id: Annotated[str, Form(min_length=1, max_length=128)],
    application_id: Annotated[str | None, Form(max_length=128)] = None,
    job_id: Annotated[str, Form(max_length=128)] = "job",
    session_seconds: Annotated[float, Form(ge=0)] = 60.0,
    paste_char_ratio: Annotated[float, Form(ge=0, le=1)] = 0.0,
    dry_run: Annotated[bool, Form()] = False,
    principal: Principal = Depends(guard("service", "candidate", "recruiter", "admin")),
) -> Decision:
    data = await _read_upload(file)
    job = _parse_job(job_json)
    analysis = analyze_resume(data, file.filename or "resume", job)
    hidden_intent = classify_hidden_intents(analysis.hidden_spans, job)
    application = Application(
        application_id=application_id or uuid.uuid4().hex,
        job_id=job_id,
        candidate=analysis.candidate,
        signals=SubmissionSignals(
            device_id=device_id,
            ip=request.client.host if request.client is not None else "unknown",
            session_seconds=session_seconds,
            paste_char_ratio=paste_char_ratio,
            submitted_at=time.time(),
        ),
    )
    extra = [
        Reason(code=str(item["code"]), severity=str(item["severity"]), detail=str(item["detail"]), weight=int(item["weight"]))
        for item in analysis.reasons
    ]
    decision = _evaluate_and_forward(application, job, extra, dry_run=dry_run, resume_text=analysis.visible_text)
    if principal.role == "candidate" and not dry_run:
        bind_application(application.application_id, principal.username)
    return decision.model_copy(update={"hidden_intent": hidden_intent, "agreement": analysis.agreement})


@app.post("/v1/resume/inspect")
async def inspect_resume(
    file: Annotated[UploadFile, File()],
    job_json: Annotated[str, Form()],
    principal: Principal = Depends(guard("service", "candidate", "recruiter", "admin")),
) -> dict[str, object]:
    data = await _read_upload(file)
    job = _parse_job(job_json)
    analysis = analyze_resume(data, file.filename or "resume", job)
    hidden_intent = classify_hidden_intents(analysis.hidden_spans, job)
    return {
        "filename": file.filename,
        "parsed_ok": analysis.parsed_ok,
        "human_view": analysis.visible_text,
        "ats_view": analysis.ats_view_text,
        "agreement": analysis.agreement.model_dump(),
        "hidden_spans": [span.model_dump() for span in analysis.hidden_spans],
        "hidden_intent": [item.model_dump() for item in hidden_intent],
        "reasons": analysis.reasons,
        "naive_ats_score": naive_ats_rank(analysis.ats_view_text, job),
        "human_view_ats_score": naive_ats_rank(analysis.visible_text, job),
        "ai_writing": analyze_style(analysis.visible_text),
    }


@app.get("/v1/decisions")
def list_decisions(
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    principal: Principal = Depends(guard("recruiter", "admin")),
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for application in reversed(STORE.applications()[-limit:]):
        decision = STORE.get_decision(application.application_id)
        if decision is None:
            continue
        history = overrides_for(application.application_id)
        items.append(
            {
                "application_id": application.application_id,
                "job_id": application.job_id,
                "candidate_name": application.candidate.name,
                "candidate_email_masked": _mask_email(application.candidate.email),
                "submitted_at": application.signals.submitted_at,
                "decision": decision.model_dump(mode="json"),
                "override": history[-1].public() if history else None,
                "consent_id": INTAKE_REPO.consent_for_application(application.application_id),
            }
        )
    return items


async def _read_webhook_body(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            parsed_length = int(content_length)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="invalid content length") from error
        if parsed_length < 0:
            raise HTTPException(status_code=400, detail="invalid content length")
        if parsed_length > MAX_WEBHOOK_BYTES:
            raise HTTPException(status_code=413, detail="webhook body too large")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_WEBHOOK_BYTES:
            raise HTTPException(status_code=413, detail="webhook body too large")
    return bytes(body)


@app.post("/v1/webhooks/greenhouse", response_model=Decision)
async def greenhouse_webhook(
    request: Request,
    signature: Annotated[str | None, Header(alias="Signature")] = None,
) -> Decision:
    body = await _read_webhook_body(request)
    secret = os.getenv("FIREWALL_WEBHOOK_SECRET")
    if secret is not None:
        if not secret:
            raise HTTPException(status_code=401, detail="invalid webhook signature configuration")
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if signature is None or not hmac.compare_digest(expected, signature.strip().lower()):
            raise HTTPException(status_code=401, detail="invalid webhook signature")
    try:
        payload = GreenhouseWebhook.model_validate_json(body)
    except ValidationError as error:
        raise RequestValidationError(error.errors()) from error
    application, job = adapt(payload)
    return _evaluate_and_forward(_sanitize(application, request), job)


@app.get("/v1/decisions/{application_id}", response_model=Decision, dependencies=[Depends(application_access)])
def get_decision(application_id: str) -> Decision:
    decision = STORE.get_decision(application_id)
    if decision is None:
        raise HTTPException(status_code=404, detail="decision not found")
    return decision


@app.get("/v1/decisions/{application_id}/overrides")
def get_overrides(
    application_id: str,
    principal: Principal = Depends(guard("recruiter", "admin")),
) -> list[dict[str, object]]:
    if STORE.get_decision(application_id) is None:
        raise HTTPException(status_code=404, detail="decision not found")
    return [record.public() for record in overrides_for(application_id)]


@app.get("/v1/stats")
def get_stats(principal: Principal = Depends(guard("recruiter", "admin"))) -> dict[str, object]:
    decisions = STORE.decisions()
    route_counts = Counter(decision.route.value for decision in decisions)
    reason_counts = Counter(reason.code for decision in decisions for reason in decision.reasons)
    return {
        "redis": redis_status(),
        "language_model": {"enabled": os.getenv("FIREWALL_LLM") == "1", "model": os.getenv("FIREWALL_LLM_MODEL", "qwen2.5:7b-instruct")},
        "total_received": len(decisions),
        "counts_per_route": {route.value: route_counts[route.value] for route in Route},
        "top_reason_codes": [
            {"code": code, "count": count}
            for code, count in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
    }


@app.get("/v1/mock-ats/applications", response_model=list[Application])
def mock_ats_applications(principal: Principal = Depends(guard("recruiter", "admin"))) -> tuple[Application, ...]:
    return MOCK_ATS.applications()


@app.get("/healthz")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(build_router(_evaluate_and_forward, _sanitize))
app.include_router(build_auth_router(STORE.get_decision))
app.include_router(
    build_intake_router(
        intel=RealIntelService(_candidate_for, _score_for, CONFIG),
        repository=INTAKE_REPO,
        guard=guard("candidate", "recruiter", "admin"),
        recruiter_guard=guard("recruiter", "admin"),
        resume_text_for=_resume_text_for,
        authorize=_authorize_application,
        counter=build_counter(),
        audit=lambda: get_service().audit,
    )
)
app.include_router(build_delivery_router(DELIVERY, guard=guard("admin"), audit=lambda: get_service().audit))
