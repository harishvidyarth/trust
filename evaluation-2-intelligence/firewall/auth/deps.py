from __future__ import annotations

import hmac
import os
from dataclasses import dataclass
from typing import Callable

from fastapi import Depends, HTTPException, Request

from firewall.auth.service import COOKIE_NAME, CSRF_HEADER, AuthService, get_service
from firewall.auth.users import ADMIN, CANDIDATE, SERVICE


SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(frozen=True)
class Principal:
    username: str
    role: str
    via: str
    session_id: str = ""
    csrf_token: str = ""


def client_ip(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def _api_key_principal(request: Request) -> Principal | None:
    supplied = request.headers.get("x-api-key")
    if supplied is None:
        return None
    expected = os.getenv("FIREWALL_API_KEY", "")
    if not expected or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="authentication required")
    return Principal(username="service", role=SERVICE, via="api_key")


def _session_principal(request: Request, service: AuthService) -> Principal | None:
    session_id = request.cookies.get(COOKIE_NAME)
    if not session_id:
        return None
    session = service.sessions.get(session_id)
    if session is None:
        return None
    user = service.users.get(session.username)
    if user is None or user.disabled:
        service.sessions.delete(session_id)
        return None
    if request.method not in SAFE_METHODS:
        supplied = request.headers.get(CSRF_HEADER, "")
        if not hmac.compare_digest(supplied.encode(), session.csrf_token.encode()):
            raise HTTPException(status_code=403, detail="csrf token missing or invalid")
    return Principal(
        username=user.username,
        role=user.role,
        via="session",
        session_id=session.session_id,
        csrf_token=session.csrf_token,
    )


def current_user(request: Request) -> Principal:
    service = get_service()
    principal = _api_key_principal(request) or _session_principal(request, service)
    if principal is None:
        if request.cookies.get(COOKIE_NAME):
            raise HTTPException(
                status_code=401,
                detail="session expired, please sign in again",
                headers={"X-Auth-Reason": "session_expired"},
            )
        raise HTTPException(status_code=401, detail="authentication required")
    return principal


def require_role(*roles: str) -> Callable[[Principal], Principal]:
    allowed = frozenset(roles)

    def dependency(principal: Principal = Depends(current_user)) -> Principal:
        if principal.role not in allowed:
            raise HTTPException(status_code=403, detail="forbidden")
        return principal

    return dependency


def require_application_access(application_id: str, principal: Principal = Depends(current_user)) -> Principal:
    if principal.role == CANDIDATE:
        if get_service().ownership.owner(application_id) != principal.username:
            raise HTTPException(status_code=404, detail="decision not found")
        return principal
    if principal.role in {ADMIN, "recruiter"}:
        return principal
    raise HTTPException(status_code=403, detail="forbidden")
