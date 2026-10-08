from __future__ import annotations

import os

from fastapi import HTTPException, Request

from firewall.auth.deps import Principal, current_user
from firewall.auth.service import COOKIE_NAME, get_service
from firewall.auth.users import ADMIN, CANDIDATE, RECRUITER


OPEN_PRINCIPAL = Principal(username="open", role=ADMIN, via="open")


def auth_enforced() -> bool:
    if os.getenv("FIREWALL_REQUIRE_AUTH") == "1":
        return True
    return bool(os.getenv("FIREWALL_ADMIN_USER") and os.getenv("FIREWALL_ADMIN_PASSWORD"))


def session_cookie_present(request: Request) -> bool:
    return COOKIE_NAME in request.cookies


def guard(*roles: str):
    allowed = frozenset(roles)

    def dependency(request: Request) -> Principal:
        if not auth_enforced():
            return OPEN_PRINCIPAL
        principal = current_user(request)
        if principal.role not in allowed:
            raise HTTPException(status_code=403, detail="forbidden")
        return principal

    return dependency


def application_access(request: Request) -> Principal:
    if not auth_enforced():
        return OPEN_PRINCIPAL
    principal = current_user(request)
    application_id = request.path_params.get("application_id", "")
    if principal.role == CANDIDATE:
        if get_service().ownership.owner(application_id) != principal.username:
            raise HTTPException(status_code=404, detail="decision not found")
        return principal
    if principal.role in {ADMIN, RECRUITER}:
        return principal
    raise HTTPException(status_code=403, detail="forbidden")
