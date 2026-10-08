from __future__ import annotations

import ipaddress
import re
import time
from collections.abc import Callable
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from firewall.delivery import validate_destination_url
from firewall.intake_intel import RealIntelService
from firewall.intake_records import (
    FINDING_STATUSES,
    RECHECK_OUTCOMES,
    InMemoryIntakeRepository,
    IntakeRepository,
)
from firewall.redis_layer.counters import FixedWindowCounter


MAX_PDF_BYTES = 5 * 1024 * 1024
MAX_PDF_PAGES = 10
MAX_LIST_ITEMS = 20
MAX_NOTE_CHARS = 500
PROFILE_LIMIT = 30
DISPUTE_LIMIT = 60
LIMIT_WINDOW_S = 600
CANDIDATE_KEYS = ("source", "fact", "status", "evidence", "dispute_note", "source_url", "fetched_at")
RECRUITER_KEYS = ("source", "fact", "status", "evidence", "source_url", "fetched_at")

_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}\Z")
_APPLICATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_CERTIFICATE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{2,63}\Z")
_DOI_BARE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\Z")
_GITHUB_USER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\Z")
_BLOCKED_HOST_SUFFIXES = ("localhost", "local", "internal", "lan", "home", "corp", "intranet")


class IntelService(Protocol):
    def parse_linkedin_pdf(self, data: bytes) -> dict[str, Any]: ...

    def build_findings(self, resume_text: str, profile: dict[str, Any]) -> list[dict[str, Any]]: ...

    def record_consent(
        self, application_id: str | None, scopes: list[str], **extra: Any
    ) -> str: ...

    def dispute(self, consent_id: str, finding_index: int, note: str) -> bool: ...


def normalise_public_https_url(value: object, allowed_hosts: tuple[str, ...] | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("URL must be a non-empty string")
    raw = value.strip()
    if len(raw) > 2048:
        raise ValueError("URL is too long")
    if any(ord(char) < 33 or ord(char) > 126 for char in raw):
        raise ValueError("URL contains invalid characters")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() != "https":
        raise ValueError("URL must use https")
    validate_destination_url(raw)
    host = (parsed.hostname or "").rstrip(".").lower()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("IP literal hosts are not allowed")
    if not _HOST.fullmatch(host) or host.rsplit(".", 1)[-1].isdigit():
        raise ValueError("URL host is not acceptable")
    if host.endswith(_BLOCKED_HOST_SUFFIXES):
        raise ValueError("URL host is not acceptable")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("URL has an invalid port") from error
    if port not in (None, 443):
        raise ValueError("URL port is not allowed")
    if allowed_hosts is not None and host not in allowed_hosts:
        raise ValueError("URL host is not permitted for this field")
    path = parsed.path or ""
    return urlunsplit(("https", host, path, "", ""))


def _github_url(value: str) -> tuple[str, str]:
    url = normalise_public_https_url(value, ("github.com", "www.github.com"))
    segments = [part for part in urlsplit(url).path.split("/") if part]
    if not segments or not _GITHUB_USER.fullmatch(segments[0]):
        raise ValueError("GitHub URL must name a user")
    return url, segments[0]


def _doi_value(value: str) -> str:
    raw = value.strip()
    if _DOI_BARE.fullmatch(raw):
        return raw
    url = normalise_public_https_url(raw, ("doi.org", "dx.doi.org", "www.doi.org"))
    candidate = urlsplit(url).path.lstrip("/")
    if not _DOI_BARE.fullmatch(candidate):
        raise ValueError("DOI link is not valid")
    return candidate


def _bounded(values: list[str] | None, label: str) -> list[str]:
    items = [item for item in (values or []) if isinstance(item, str) and item.strip()]
    if len(items) > MAX_LIST_ITEMS:
        raise ValueError(f"too many {label}")
    return items


class DisputeRequest(BaseModel):
    consent_id: str = Field(min_length=1, max_length=128)
    finding_index: int = Field(ge=0)
    note: str = Field(default="", max_length=MAX_NOTE_CHARS)


class RecheckRequest(BaseModel):
    finding_index: int = Field(ge=0)
    outcome: Literal["upheld", "withdrawn"]
    note: str = Field(default="", max_length=MAX_NOTE_CHARS)


def _deny() -> None:
    raise HTTPException(status_code=403, detail="forbidden")


def _candidate_view(record: dict[str, Any]) -> dict[str, Any]:
    findings = []
    for item in record["findings"]:
        entry = {key: item.get(key) for key in CANDIDATE_KEYS if key in item}
        recheck = item.get("recheck")
        if recheck:
            entry["recheck"] = {key: recheck.get(key) for key in ("outcome", "note", "at")}
        findings.append(entry)
    view = {
        "consent_id": record["consent_id"],
        "application_id": record.get("application_id"),
        "parsed": record["parsed"],
        "findings": findings,
    }
    if record.get("lookup_report"):
        view["lookup_report"] = record["lookup_report"]
    return view


def _recruiter_view(record: dict[str, Any]) -> dict[str, Any]:
    visible = []
    waiting = []
    withheld = 0
    for index, item in enumerate(record["findings"]):
        if item["status"] != "disputed":
            entry = {"index": index, **{key: item[key] for key in RECRUITER_KEYS if key in item}}
            if item.get("recheck"):
                entry["recheck_outcome"] = item["recheck"]["outcome"]
            visible.append(entry)
            continue
        withheld += 1
        if not item.get("recheck"):
            waiting.append(
                {
                    "finding_index": index,
                    "source": item.get("source"),
                    "fact": item.get("fact"),
                    "previous_status": item.get("previous_status"),
                    "dispute_note": item.get("dispute_note"),
                }
            )
    return {
        "consent_id": record["consent_id"],
        "application_id": record.get("application_id"),
        "findings": visible,
        "withheld_count": withheld,
        "pending_rechecks": waiting,
    }


def _read_pdf(upload: UploadFile) -> bytes:
    data = upload.file.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="linkedin_pdf exceeds 5 MiB")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="linkedin_pdf is not a PDF")
    return data


def _page_count(data: bytes) -> int:
    from io import BytesIO

    from pypdf import PdfReader

    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise ValueError("encrypted")
        return len(reader.pages)
    except Exception as error:
        raise HTTPException(status_code=400, detail="linkedin_pdf could not be read") from error


def _actor(principal: Any) -> str:
    return str(getattr(principal, "username", "unknown"))


def _owned_by_other(principal: Any, record: dict[str, Any]) -> bool:
    if getattr(principal, "role", None) != "candidate":
        return False
    return record.get("owner") != getattr(principal, "username", None)


def build_intake_router(
    intel: IntelService | None = None,
    repository: IntakeRepository | None = None,
    guard: Callable[..., Any] | None = None,
    resume_text_for: Callable[[str], str] | None = None,
    recruiter_guard: Callable[..., Any] | None = None,
    authorize: Callable[[Any, str], None] | None = None,
    counter: FixedWindowCounter | None = None,
    audit: Callable[[], Any] | None = None,
) -> APIRouter:
    service: IntelService = intel or RealIntelService()
    repo: IntakeRepository = repository or InMemoryIntakeRepository()
    limiter = counter or FixedWindowCounter()
    lookup = resume_text_for or (lambda application_id: "")
    dependencies = [Depends(guard)] if guard is not None else []
    principal_dependency = Depends(guard) if guard is not None else None
    recruiter_dependency = Depends(recruiter_guard or guard or _deny)
    router = APIRouter(dependencies=dependencies)

    def note_audit(event: str, principal: Any, target: str, request: Request, detail: dict[str, object]) -> None:
        if audit is None:
            return
        audit().append(
            event,
            _actor(principal),
            target,
            request.client.host if request.client is not None else "unknown",
            detail,
        )

    def throttle(request: Request, kind: str, limit: int) -> None:
        host = request.client.host if request.client is not None else "unknown"
        if limiter.hit(f"intake:{kind}:{host}", LIMIT_WINDOW_S) > limit:
            raise HTTPException(status_code=429, detail="too many requests, try again later")

    @router.post("/v1/intake/profile")
    def intake_profile(
        request: Request,
        consent: bool = Form(False),
        linkedin_pdf: UploadFile | None = File(None),
        github_url: str | None = Form(None),
        portfolio_url: str | None = Form(None),
        doi_links: list[str] | None = Form(None),
        certificate_ids: list[str] | None = Form(None),
        application_id: str | None = Form(None),
        principal: Any = principal_dependency,
    ) -> dict[str, Any]:
        throttle(request, "profile", PROFILE_LIMIT)
        if consent is not True:
            raise HTTPException(status_code=400, detail="consent is required")
        profile: dict[str, Any] = {}
        scopes: list[str] = []
        try:
            if application_id:
                if not _APPLICATION_ID.fullmatch(application_id):
                    raise ValueError("application_id is invalid")
            else:
                application_id = None
            if github_url and github_url.strip():
                profile["github_url"], profile["github_username"] = _github_url(github_url)
                scopes.append("github")
            if portfolio_url and portfolio_url.strip():
                profile["portfolio_url"] = normalise_public_https_url(portfolio_url)
                scopes.append("portfolio")
            dois = [_doi_value(item) for item in _bounded(doi_links, "doi_links")]
            if dois:
                profile["dois"] = list(dict.fromkeys(dois))
                scopes.append("doi")
            certificates = _bounded(certificate_ids, "certificate_ids")
            for item in certificates:
                if not _CERTIFICATE_ID.fullmatch(item.strip()):
                    raise ValueError("certificate id is invalid")
            if certificates:
                profile["certificate_ids"] = list(dict.fromkeys(item.strip() for item in certificates))
                scopes.append("certificate")
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if application_id and authorize is not None:
            authorize(principal, application_id)

        parsed: dict[str, Any] = {}
        if linkedin_pdf is not None and linkedin_pdf.filename:
            data = _read_pdf(linkedin_pdf)
            if _page_count(data) > MAX_PDF_PAGES:
                raise HTTPException(status_code=413, detail="linkedin_pdf has too many pages")
            try:
                parsed = dict(service.parse_linkedin_pdf(data))
            except Exception as error:
                raise HTTPException(status_code=400, detail="linkedin_pdf could not be parsed") from error
            profile["linkedin_text"] = str(parsed.pop("text", ""))
            profile["linkedin_profile"] = parsed.pop("profile", None)
            profile["linkedin_name"] = str(parsed.get("name") or "")
            if not profile.get("github_username") and parsed.get("github_username"):
                profile["github_username"] = parsed["github_username"]
            scopes.append("linkedin")

        resume_text = lookup(application_id) if application_id else ""
        profile["application_id"] = application_id
        findings = [dict(item) for item in service.build_findings(resume_text, profile)]
        scopes.extend(profile.pop("extra_scopes", []))
        extra = {key: profile.pop(key) for key in ("intel_result", "intel_index") if key in profile}
        report = profile.pop("lookup_report", None)
        consent_id = service.record_consent(application_id, scopes, **extra)
        record = {
            "consent_id": consent_id,
            "application_id": application_id,
            "owner": getattr(principal, "username", None),
            "parsed": parsed,
            "findings": findings,
            "lookup_report": report,
            "scopes": scopes,
            "created_at": time.time(),
        }
        repo.save(record)
        note_audit("intake_consent", principal, consent_id, request, {"scopes": scopes, "application_id": application_id})
        view = _candidate_view(record)
        body = {"consent_id": consent_id, "parsed": parsed, "findings": view["findings"]}
        if report:
            body["lookup_report"] = report
        return body

    @router.post("/v1/intake/dispute")
    def intake_dispute(body: DisputeRequest, request: Request, principal: Any = principal_dependency) -> dict[str, Any]:
        throttle(request, "dispute", DISPUTE_LIMIT)
        record = repo.get(body.consent_id)
        if record is None or _owned_by_other(principal, record):
            raise HTTPException(status_code=404, detail="unknown consent_id")
        if not repo.set_finding_status(body.consent_id, body.finding_index, "disputed", body.note):
            raise HTTPException(status_code=404, detail="unknown finding")
        service.dispute(body.consent_id, body.finding_index, body.note)
        note_audit("intake_dispute", principal, body.consent_id, request, {"finding_index": body.finding_index})
        return {"ok": True}

    @router.post("/v1/intake/{consent_id}/recheck")
    def intake_recheck(
        consent_id: str,
        body: RecheckRequest,
        request: Request,
        principal: Any = recruiter_dependency,
    ) -> dict[str, Any]:
        if body.outcome not in RECHECK_OUTCOMES:
            raise HTTPException(status_code=422, detail="outcome is invalid")
        outcome = repo.recheck_finding(
            consent_id, body.finding_index, body.outcome, _actor(principal), body.note.strip() or None, time.time()
        )
        if outcome == "unknown":
            raise HTTPException(status_code=404, detail="unknown finding")
        if outcome == "not_disputed":
            raise HTTPException(status_code=409, detail="finding is not disputed")
        if outcome == "already_rechecked":
            raise HTTPException(status_code=409, detail="finding was already rechecked")
        notify = getattr(service, "recheck", None)
        if notify is not None:
            notify(consent_id, body.finding_index, body.outcome)
        note_audit(
            "intake_recheck",
            principal,
            consent_id,
            request,
            {"finding_index": body.finding_index, "outcome": body.outcome},
        )
        return {"ok": True, "finding_index": body.finding_index, "outcome": body.outcome}

    @router.get("/v1/intake/{consent_id}/recruiter")
    def intake_recruiter(consent_id: str, _: Any = recruiter_dependency) -> dict[str, Any]:
        record = repo.get(consent_id)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown consent_id")
        return _recruiter_view(record)

    @router.get("/v1/intake/{consent_id}")
    def intake_record(consent_id: str, principal: Any = principal_dependency) -> dict[str, Any]:
        record = repo.get(consent_id)
        if record is None or _owned_by_other(principal, record):
            raise HTTPException(status_code=404, detail="unknown consent_id")
        return _candidate_view(record)

    return router
