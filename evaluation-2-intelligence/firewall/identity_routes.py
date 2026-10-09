from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from python_multipart.multipart import MultipartParser, parse_options_header

from firewall.auth.deps import Principal
from firewall.identity import IdentityError, IdentityService
from firewall.models import Decision

MAX_BODY_BYTES = 3 * 1024 * 1024
MAX_PARTS = 4
REQUESTS_PER_MINUTE = 40
FACE_RECEIVED = "Thank you. Your face check was received."
VOICE_RECEIVED = "Thank you. Your voice check was received."
PHOTO_RECEIVED = "Thank you. Your photo check was received."
PHOTO_BODY_BYTES = 4 * 1024 * 1024
ID_PHOTO_BYTES = 1536 * 1024
LIVE_PHOTO_BYTES = 700 * 1024
PHOTO_PARTS = 3
NOT_FOUND = "We could not find that application."
TOO_BIG = "That upload is too large."
BAD_UPLOAD = "We could not read that upload. Please try again."


class SessionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: str = Field(min_length=1, max_length=128)
    consent: StrictBool
    photo_consent: StrictBool = False


async def read_capped(request: Request, limit: int = MAX_BODY_BYTES) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            length = int(declared)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=BAD_UPLOAD) from error
        if length < 0:
            raise HTTPException(status_code=400, detail=BAD_UPLOAD)
        if length > limit:
            raise HTTPException(status_code=413, detail=TOO_BIG)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise HTTPException(status_code=413, detail=TOO_BIG)
    return bytes(body)


def parse_form(body: bytes, content_type: str, max_parts: int = MAX_PARTS, max_size: int = MAX_BODY_BYTES) -> dict[str, bytes]:
    kind, options = parse_options_header(content_type.encode("latin-1", "ignore"))
    boundary = options.get(b"boundary")
    if kind != b"multipart/form-data" or not boundary:
        raise HTTPException(status_code=400, detail=BAD_UPLOAD)
    parts: dict[str, bytearray] = {}
    state: dict[str, Any] = {"name": None, "field": b"", "value": b"", "count": 0}

    def on_part_begin() -> None:
        state["name"] = None
        state["count"] += 1
        if state["count"] > max_parts:
            raise ValueError("too many parts")

    def on_header_field(data: bytes, start: int, end: int) -> None:
        state["field"] += data[start:end]

    def on_header_value(data: bytes, start: int, end: int) -> None:
        state["value"] += data[start:end]

    def on_header_end() -> None:
        if state["field"].lower() == b"content-disposition":
            _, disposition = parse_options_header(state["value"])
            name = disposition.get(b"name")
            if name is not None:
                state["name"] = name.decode("utf-8", "ignore")
                parts.setdefault(state["name"], bytearray())
        state["field"] = b""
        state["value"] = b""

    def on_part_data(data: bytes, start: int, end: int) -> None:
        if state["name"] is not None:
            parts[state["name"]].extend(data[start:end])

    parser = MultipartParser(
        boundary,
        {
            "on_part_begin": on_part_begin,
            "on_header_field": on_header_field,
            "on_header_value": on_header_value,
            "on_header_end": on_header_end,
            "on_part_data": on_part_data,
        },
        max_size=max_size,
    )
    try:
        parser.write(body)
        parser.finalize()
    except Exception as error:
        raise HTTPException(status_code=400, detail=BAD_UPLOAD) from error
    return {name: bytes(value) for name, value in parts.items()}


def build_identity_router(
    *,
    candidate_guard: Callable[..., Any],
    reader_guard: Callable[..., Any],
    staff_guard: Callable[..., Any],
    service: IdentityService,
    decision_for: Callable[[str], Decision | None],
    owner_of: Callable[[str], str | None],
    counter: Any,
    closed: Callable[[str], bool] | None = None,
) -> APIRouter:
    router = APIRouter()

    def throttle(principal: Principal) -> None:
        if counter.hit(f"identity:req:{principal.username}", 60) > REQUESTS_PER_MINUTE:
            raise HTTPException(status_code=429, detail="Too many requests. Please wait a moment and try again.")

    def own_session(principal: Principal, session_id: str) -> None:
        try:
            record = service.load(session_id)
        except IdentityError as error:
            raise HTTPException(status_code=error.status, detail=error.detail) from error
        if principal.via != "open" and record["username"] != principal.username:
            raise HTTPException(status_code=404, detail="We could not find that check.")

    def fail(error: IdentityError) -> HTTPException:
        return HTTPException(status_code=error.status, detail=error.detail)

    @router.post("/v1/verify/session")
    async def start(request: Request, principal: Principal = Depends(candidate_guard)) -> dict[str, Any]:
        throttle(principal)
        body = await read_capped(request)
        try:
            data = SessionIn.model_validate(json.loads(body or b"{}"))
        except (ValueError, RecursionError) as error:
            raise HTTPException(status_code=400, detail="We could not read that request.") from error
        application_id = data.application_id
        if decision_for(application_id) is None or (principal.via != "open" and owner_of(application_id) != principal.username):
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        if closed is not None and closed(application_id):
            raise HTTPException(status_code=409, detail="This application is closed.")
        try:
            return service.create_session(application_id, principal.username, data.consent, data.photo_consent)
        except IdentityError as error:
            raise fail(error) from error

    @router.post("/v1/verify/{session_id}/face")
    async def face(session_id: str, request: Request, principal: Principal = Depends(candidate_guard)) -> dict[str, Any]:
        throttle(principal)
        own_session(principal, session_id)
        body = await read_capped(request)
        try:
            report = json.loads(body)
        except (ValueError, RecursionError) as error:
            raise HTTPException(status_code=400, detail="We could not read the face check. Please try again.") from error
        try:
            service.record_face(session_id, report)
        except IdentityError as error:
            raise fail(error) from error
        return {"received": True, "message": FACE_RECEIVED}

    @router.post("/v1/verify/{session_id}/voice")
    async def voice(session_id: str, request: Request, principal: Principal = Depends(candidate_guard)) -> dict[str, Any]:
        throttle(principal)
        own_session(principal, session_id)
        body = await read_capped(request)
        form = parse_form(body, request.headers.get("content-type", ""))
        del body
        audio = form.get("audio")
        if not audio:
            raise HTTPException(status_code=400, detail="Please include a recording.")
        transcript = form.get("transcript", b"")
        if len(transcript) > 1200:
            raise HTTPException(status_code=400, detail="Please keep the transcript under 300 characters.")
        try:
            service.record_voice(session_id, audio, transcript.decode("utf-8", "ignore"))
        except IdentityError as error:
            raise fail(error) from error
        finally:
            del audio
            form.clear()
        return {"received": True, "message": VOICE_RECEIVED}

    @router.post("/v1/verify/{session_id}/photos")
    async def photos(session_id: str, request: Request, principal: Principal = Depends(candidate_guard)) -> dict[str, Any]:
        throttle(principal)
        own_session(principal, session_id)
        body = await read_capped(request, PHOTO_BODY_BYTES)
        form = parse_form(body, request.headers.get("content-type", ""), PHOTO_PARTS, PHOTO_BODY_BYTES)
        del body
        try:
            id_photo = form.get("id_photo")
            live = [form[name] for name in ("live_1", "live_2") if form.get(name)]
            if not id_photo or not live:
                raise HTTPException(status_code=400, detail="Please include your ID photo and a camera frame.")
            if len(id_photo) > ID_PHOTO_BYTES or any(len(item) > LIVE_PHOTO_BYTES for item in live):
                raise HTTPException(status_code=413, detail=TOO_BIG)
            try:
                service.record_photos(session_id, id_photo, live)
            except IdentityError as error:
                raise fail(error) from error
        finally:
            form.clear()
        return {"received": True, "message": PHOTO_RECEIVED}

    @router.get("/v1/verify/capabilities")
    def capabilities(principal: Principal = Depends(candidate_guard)) -> dict[str, Any]:
        return {"face_match_available": service.face_match_available()}

    @router.get("/v1/verify/status")
    def status(principal: Principal = Depends(staff_guard)) -> dict[str, Any]:
        return service.status()

    @router.get("/v1/verify/session/{session_id}")
    def session_state(session_id: str, principal: Principal = Depends(candidate_guard)) -> dict[str, Any]:
        own_session(principal, session_id)
        try:
            return service.session_view(session_id)
        except IdentityError as error:
            raise fail(error) from error

    @router.get("/v1/verify/application/{application_id}")
    def stored(application_id: str, principal: Principal = Depends(reader_guard)) -> dict[str, Any]:
        if principal.role not in {"recruiter", "admin"}:
            raise HTTPException(status_code=404, detail="No identity check has been done.")
        result = service.result(application_id)
        if result is None:
            raise HTTPException(status_code=404, detail="No identity check has been done.")
        return result

    return router
