from __future__ import annotations

import hashlib
import hmac
import math
import os
import re
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
from firewall.auth.records import OUTCOME_TTL_S, OutcomeStore
from firewall.auth.deps import Principal
from firewall.auth.service import get_service
from firewall.auth_routes import build_auth_router
from firewall.applicant_routes import (
    ApplicantService,
    apply_typed,
    build_applicant_router,
    context_block,
    has_typed_values,
    parse_typed_form,
    require_identity,
    status_for,
    synthesize_resume,
)
from firewall.candidate_routes import build_candidate_router, build_replace_links
from firewall.config import load_config
from firewall.connectors.greenhouse import GreenhouseWebhook, adapt
from firewall.connectors.mock_ats import MockATS
from firewall.check_routes import build_check_router
from firewall.claim_routes import build_claim_router
from firewall.identity import IdentityService
from firewall.identity.service import RESULT_TTL_SECONDS as IDENTITY_TTL_S
from firewall.identity_routes import build_identity_router
from firewall.delivery import build_delivery_from_env
from firewall.delivery_routes import build_delivery_router, build_inbox_router
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
    link_from_env,
    build_cache,
    build_counter,
    build_intake_repository,
    build_llm_client,
    build_optional_queue,
    build_signal_cache,
    build_store,
    redis_status,
)
from firewall.redis_layer.cache import RedisJsonCache
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
UPLOAD_SUFFIXES = frozenset({".pdf", ".docx", ".txt"})
REPLACE_LINKS = build_replace_links()
UPLOAD_COUNTER = build_counter()
UPLOAD_LIMIT = 20
UPLOAD_WINDOW_S = 3600
FORM_TTL_S = 90 * 24 * 3600
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
    text = str(context["resume_text"]) if context else ""
    if text.strip():
        return text
    record = APPLICANTS.record(application_id)
    fields = record.get("fields") if record else None
    if not isinstance(fields, dict):
        return text
    try:
        return "\n".join(part for part in (synthesize_resume(fields), context_block(fields)) if part)
    except (KeyError, TypeError):
        return text


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


def _person_key(name: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", name.casefold()).split())


def _same_applicant(first: Application, second: Application) -> bool:
    if first.application_id == second.application_id or first.job_id != second.job_id:
        return False
    name = _person_key(first.candidate.name)
    if name and name == _person_key(second.candidate.name):
        return True
    same_email = first.candidate.email.casefold() == second.candidate.email.casefold()
    same_phone = re.sub(r"\D", "", first.candidate.phone) == re.sub(r"\D", "", second.candidate.phone)
    return bool(first.candidate.email and same_email) or bool(re.sub(r"\D", "", first.candidate.phone) and same_phone)


def _application_by_id(application_id: str) -> Application | None:
    return next((item for item in STORE.applications() if item.application_id == application_id), None)


def _same_person_ids(application_id: str) -> list[str]:
    application = _application_by_id(application_id)
    if application is None:
        return []
    return [item.application_id for item in STORE.by_job(application.job_id) if _same_applicant(application, item)]


def _close_if_person_rejected(application: Application) -> None:
    for item in STORE.by_job(application.job_id):
        if _same_applicant(application, item) and OUTCOMES.is_rejected(item.application_id):
            try:
                OUTCOMES.change(
                    application.application_id,
                    "rejected",
                    f"Closed together with application {item.application_id[:8]} for the same applicant and job.",
                    "linked to an earlier rejection",
                )
            except Exception:
                pass
            return


def _evaluate_and_forward(
    application: Application,
    job: JobRequirements,
    extra_reasons: list[Reason] | None = None,
    dry_run: bool = False,
    resume_text: str | None = None,
    ignore_application_ids: tuple[str, ...] = (),
    context_text: str | None = None,
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
        ignore_application_ids=ignore_application_ids,
    )
    if dry_run:
        return decision
    _remember(application, context_text if context_text is not None else resume_text)
    if not already_decided:
        _close_if_person_rejected(application)
        if not OUTCOMES.is_rejected(application.application_id):
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
        raise HTTPException(status_code=413, detail="That file is larger than 5 MB. Please choose a smaller file.")
    return data


def _check_upload(data: bytes, filename: str) -> None:
    if not data:
        raise HTTPException(status_code=400, detail="That file is empty. Please choose your resume and try again.")
    if os.path.splitext(filename.lower())[1] not in UPLOAD_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail="That file type is not supported. Please upload a PDF, a Word file or a plain text file.",
        )


def _check_replaces(principal: Principal, replaces: str | None, own_id: str | None) -> str | None:
    if not replaces:
        return None
    problem = HTTPException(status_code=400, detail="We could not find the earlier application you want to replace.")
    if replaces == own_id or STORE.get_decision(replaces) is None:
        raise problem
    if principal.role == "candidate" and get_service().ownership.owner(replaces) != principal.username:
        raise problem
    return replaces


def _same_person(previous_id: str, application: Application) -> bool:
    candidates = STORE.by_email(application.candidate.email) + STORE.by_phone(application.candidate.phone)
    return any(item.application_id == previous_id for item in candidates)


def _parse_job(job_json: str) -> JobRequirements:
    try:
        return JobRequirements.model_validate_json(job_json)
    except ValidationError as error:
        raise RequestValidationError(error.errors()) from error


def _form_cache() -> JsonCache:
    link = link_from_env()
    if link is not None:
        return RedisJsonCache(link, "applicant_form", default_ttl_s=FORM_TTL_S)
    return JsonCache(max_entries=100_000, default_ttl_s=FORM_TTL_S)


FORM_CACHE = _form_cache()
CLAIM_CACHE = build_cache("claimcheck")
OUTCOMES = OutcomeStore(build_cache("outcomes", default_ttl_s=OUTCOME_TTL_S), time.time)
IDENTITY_COUNTER = build_counter()
IDENTITY = IdentityService(
    results=build_cache("identity", default_ttl_s=IDENTITY_TTL_S),
    sessions=build_cache("identity_sessions", default_ttl_s=1800),
    counter=IDENTITY_COUNTER,
    audit=lambda: get_service().audit,
)
APPLICANTS = ApplicantService(
    form_cache=FORM_CACHE,
    claim_cache=CLAIM_CACHE,
    decision_for=STORE.get_decision,
    text_for=_resume_text_for,
    candidate_for=_candidate_for,
    owner_of=lambda application_id: get_service().ownership.owner(application_id),
    audit=lambda: get_service().audit,
    closed=OUTCOMES.is_rejected,
    identity_done=IDENTITY.is_complete,
)


def _candidate_reply(application_id: str, decision: Decision) -> dict[str, object]:
    return {
        "application_id": application_id,
        **status_for(decision.route.value),
        "follow_up": APPLICANTS.follow_up(application_id, decision),
    }


@app.post("/v1/applications/upload", response_model=None)
async def upload_application(
    request: Request,
    job_json: Annotated[str, Form()],
    device_id: Annotated[str, Form(min_length=1, max_length=128)],
    file: Annotated[UploadFile | None, File()] = None,
    application_id: Annotated[str | None, Form(max_length=128)] = None,
    job_id: Annotated[str, Form(max_length=128)] = "job",
    session_seconds: Annotated[float, Form(ge=0)] = 60.0,
    paste_char_ratio: Annotated[float, Form(ge=0, le=1)] = 0.0,
    dry_run: Annotated[bool, Form()] = False,
    replaces: Annotated[str | None, Form(max_length=128)] = None,
    applicant_name: Annotated[str | None, Form()] = None,
    applicant_email: Annotated[str | None, Form()] = None,
    applicant_phone: Annotated[str | None, Form()] = None,
    role_title: Annotated[str | None, Form()] = None,
    current_employer: Annotated[str | None, Form()] = None,
    education: Annotated[str | None, Form()] = None,
    github_url: Annotated[str | None, Form()] = None,
    linkedin_url: Annotated[str | None, Form()] = None,
    portfolio_url: Annotated[str | None, Form()] = None,
    papers: Annotated[str | None, Form()] = None,
    certificate_ids: Annotated[str | None, Form()] = None,
    years_experience: Annotated[str | None, Form()] = None,
    extra_skills: Annotated[str | None, Form()] = None,
    about_project: Annotated[str | None, Form()] = None,
    consent: Annotated[bool, Form()] = False,
    principal: Principal = Depends(guard("service", "candidate", "recruiter", "admin")),
) -> dict[str, object] | Decision:
    is_candidate = principal.role == "candidate"
    if is_candidate:
        if UPLOAD_COUNTER.hit(f"upload:{principal.username}", UPLOAD_WINDOW_S) > UPLOAD_LIMIT:
            raise HTTPException(status_code=429, detail="You have sent a lot of applications in the last hour. Please try again later.")
        if not consent:
            raise HTTPException(status_code=400, detail="Please agree to share your details with the hiring team before you send your application.")
        dry_run = False
    fields = parse_typed_form(
        {
            "applicant_name": applicant_name,
            "applicant_email": applicant_email,
            "applicant_phone": applicant_phone,
            "role_title": role_title,
            "current_employer": current_employer,
            "education": education,
            "github_url": github_url,
            "linkedin_url": linkedin_url,
            "portfolio_url": portfolio_url,
            "papers": papers,
            "certificate_ids": certificate_ids,
            "years_experience": years_experience,
            "extra_skills": extra_skills,
            "about_project": about_project,
        }
    )
    data = b""
    filename = "resume"
    if file is not None and (file.filename or ""):
        data = await _read_upload(file)
        filename = file.filename or "resume"
        _check_upload(data, filename)
    if not data:
        require_identity(fields)
        data = synthesize_resume(fields).encode()
        filename = "application.txt"
        typed_only = True
    else:
        typed_only = False
    previous_id = _check_replaces(principal, replaces, application_id)
    job = _parse_job(job_json)
    analysis = analyze_resume(data, filename, job)
    if not analysis.parsed_ok:
        raise HTTPException(
            status_code=400,
            detail="We could not read that file. Please save it again as a normal PDF, Word file or text file and try again.",
        )
    hidden_intent = classify_hidden_intents(analysis.hidden_spans, job)
    candidate = apply_typed(analysis.candidate, fields)
    context_text = analysis.visible_text
    extra_block = context_block(fields)
    if extra_block and not typed_only:
        context_text = f"{analysis.visible_text}\n\n{extra_block}"
    application = Application(
        application_id=application_id or uuid.uuid4().hex,
        job_id=job_id,
        candidate=candidate,
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
    decision = _evaluate_and_forward(
        application,
        job,
        extra,
        dry_run=dry_run,
        resume_text=analysis.visible_text,
        ignore_application_ids=(previous_id,) if previous_id and _same_person(previous_id, application) else (),
        context_text=context_text,
    )
    if (is_candidate or principal.via == "open") and not dry_run:
        bind_application(application.application_id, principal.username)
        if previous_id is not None:
            REPLACE_LINKS.link(application.application_id, previous_id)
    if not dry_run and (is_candidate or has_typed_values(fields)):
        APPLICANTS.store_submission(application.application_id, {**fields, "consent": consent}, application.signals.submitted_at)
    if is_candidate:
        return _candidate_reply(application.application_id, decision)
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
        rejection = OUTCOMES.last_rejection(application.application_id)
        items.append(
            {
                "outcome": "rejected" if rejection else None,
                "outcome_by": rejection["by"] if rejection else None,
                "outcome_at": rejection["at"] if rejection else None,
                "application_id": application.application_id,
                "job_id": application.job_id,
                "candidate_name": application.candidate.name,
                "candidate_email_masked": _mask_email(application.candidate.email),
                "submitted_at": application.signals.submitted_at,
                "decision": decision.model_dump(mode="json"),
                "override": history[-1].public() if history else None,
                "consent_id": INTAKE_REPO.consent_for_application(application.application_id),
                "applicant_form_present": APPLICANTS.record(application.application_id) is not None,
                "follow_up_open": APPLICANTS.open_count(application.application_id),
                **IDENTITY.summary(application.application_id),
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
        "rejected": sum(1 for decision in decisions if OUTCOMES.is_rejected(decision.application_id)),
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
app.include_router(build_auth_router(STORE.get_decision, outcomes=OUTCOMES, siblings_of=_same_person_ids))
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
app.include_router(build_candidate_router(STORE, REPLACE_LINKS, guard("candidate"), guard("candidate", "recruiter", "admin"), APPLICANTS))
app.include_router(build_delivery_router(DELIVERY, guard=guard("admin"), audit=lambda: get_service().audit))
def _restored_inboxes() -> dict[str, list[dict[str, object]]]:
    boxes: dict[str, list[dict[str, object]]] = {}
    for application in STORE.applications():
        decision = STORE.get_decision(application.application_id)
        if decision is None or decision.route == Route.PASS_TO_ATS:
            continue
        for name in DELIVERY.route_names.get(decision.route.value, []):
            boxes.setdefault(name, []).insert(
                0,
                {
                    "application_id": application.application_id,
                    "job_id": application.job_id,
                    "candidate_name": application.candidate.name,
                },
            )
    return boxes


app.include_router(
    build_check_router(
        runner_guard=guard("recruiter", "admin"),
        reader_guard=guard("recruiter", "admin"),
        decision_for=STORE.get_decision,
        text_for=_resume_text_for,
        candidate_for=_candidate_for,
        owner_of=lambda application_id: get_service().ownership.owner(application_id),
        cache=build_cache("checks"),
        audit=lambda: get_service().audit,
    )
)
app.include_router(
    build_claim_router(
        candidate_guard=guard("candidate"),
        reader_guard=guard("candidate", "recruiter", "admin"),
        decision_for=STORE.get_decision,
        text_for=_resume_text_for,
        candidate_for=_candidate_for,
        owner_of=lambda application_id: get_service().ownership.owner(application_id),
        cache=CLAIM_CACHE,
        audit=lambda: get_service().audit,
        closed=OUTCOMES.is_rejected,
    )
)
app.include_router(
    build_identity_router(
        candidate_guard=guard("candidate"),
        reader_guard=guard("candidate", "recruiter", "admin"),
        staff_guard=guard("recruiter", "admin"),
        service=IDENTITY,
        decision_for=STORE.get_decision,
        owner_of=lambda application_id: get_service().ownership.owner(application_id),
        counter=IDENTITY_COUNTER,
        closed=OUTCOMES.is_rejected,
    )
)
app.include_router(build_inbox_router(DELIVERY, guard=guard("recruiter", "admin"), restore=_restored_inboxes, exclude=OUTCOMES.is_rejected))
app.include_router(
    build_applicant_router(APPLICANTS, candidate_guard=guard("candidate"), recruiter_guard=guard("recruiter", "admin"))
)
