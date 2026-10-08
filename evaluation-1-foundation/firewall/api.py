from __future__ import annotations

import hashlib
import hmac
import math
import os
import time
import uuid
from collections import Counter
from typing import Annotated

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from firewall.config import load_config
from firewall.connectors.greenhouse import GreenhouseWebhook, adapt
from firewall.connectors.mock_ats import MockATS
from firewall.engine import evaluate
from firewall.models import (
    Application,
    Decision,
    EvaluationRequest,
    JobRequirements,
    Reason,
    Route,
    SubmissionSignals,
)
from firewall.resume.service import analyze_resume, naive_ats_rank
from firewall.resume.style import analyze_style
from firewall.store import InMemoryApplicationStore


app = FastAPI(title="AI Application Firewall", version="0.1.0")
STORE = InMemoryApplicationStore()
MOCK_ATS = MockATS()
CONFIG = load_config()
MAX_WEBHOOK_BYTES = 1_048_576
MAX_UPLOAD_BYTES = 5_242_880
OPEN_PATHS = frozenset({"/healthz"})
UNKNOWN_IPS = frozenset({"", "0.0.0.0", "unknown"})


@app.middleware("http")
async def protect(request: Request, call_next):
    api_key = os.getenv("FIREWALL_API_KEY")
    if api_key and request.method != "OPTIONS" and request.url.path not in OPEN_PATHS:
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
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "Signature"],
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


def _evaluate_and_forward(
    application: Application,
    job: JobRequirements,
    extra_reasons: list[Reason] | None = None,
    dry_run: bool = False,
) -> Decision:
    already_decided = STORE.get_decision(application.application_id) is not None
    decision = evaluate(application, job, STORE, CONFIG, extra_reasons=extra_reasons, persist=not dry_run)
    if dry_run:
        return decision
    if decision.route == Route.PASS_TO_ATS and not already_decided:
        MOCK_ATS.receive(application)
    return decision


def _mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


@app.post("/v1/applications/evaluate", response_model=Decision)
def evaluate_application(payload: EvaluationRequest, request: Request) -> Decision:
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
) -> Decision:
    data = await _read_upload(file)
    job = _parse_job(job_json)
    analysis = analyze_resume(data, file.filename or "resume", job)
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
    return _evaluate_and_forward(application, job, extra, dry_run=dry_run)


@app.post("/v1/resume/inspect")
async def inspect_resume(
    file: Annotated[UploadFile, File()],
    job_json: Annotated[str, Form()],
) -> dict[str, object]:
    data = await _read_upload(file)
    job = _parse_job(job_json)
    analysis = analyze_resume(data, file.filename or "resume", job)
    return {
        "filename": file.filename,
        "parsed_ok": analysis.parsed_ok,
        "human_view": analysis.visible_text,
        "ats_view": analysis.ats_view_text,
        "hidden_spans": [span.model_dump() for span in analysis.hidden_spans],
        "reasons": analysis.reasons,
        "naive_ats_score": naive_ats_rank(analysis.ats_view_text, job),
        "human_view_ats_score": naive_ats_rank(analysis.visible_text, job),
        "ai_writing": analyze_style(analysis.visible_text),
    }


@app.get("/v1/decisions")
def list_decisions(limit: Annotated[int, Query(ge=1, le=1000)] = 200) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for application in reversed(STORE.applications()[-limit:]):
        decision = STORE.get_decision(application.application_id)
        if decision is None:
            continue
        items.append(
            {
                "application_id": application.application_id,
                "job_id": application.job_id,
                "candidate_name": application.candidate.name,
                "candidate_email_masked": _mask_email(application.candidate.email),
                "submitted_at": application.signals.submitted_at,
                "decision": decision.model_dump(mode="json"),
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


@app.get("/v1/decisions/{application_id}", response_model=Decision)
def get_decision(application_id: str) -> Decision:
    decision = STORE.get_decision(application_id)
    if decision is None:
        raise HTTPException(status_code=404, detail="decision not found")
    return decision


@app.get("/v1/stats")
def get_stats() -> dict[str, object]:
    decisions = STORE.decisions()
    route_counts = Counter(decision.route.value for decision in decisions)
    reason_counts = Counter(reason.code for decision in decisions for reason in decision.reasons)
    return {
        "total_received": len(decisions),
        "counts_per_route": {route.value: route_counts[route.value] for route in Route},
        "top_reason_codes": [
            {"code": code, "count": count}
            for code, count in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
    }


@app.get("/v1/mock-ats/applications", response_model=list[Application])
def mock_ats_applications() -> tuple[Application, ...]:
    return MOCK_ATS.applications()


@app.get("/healthz")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}
