from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from firewall.auth.deps import Principal, client_ip, require_role
from firewall.auth.passwords import PasswordPolicyError, validate_password, verify_password
from firewall.auth.records import build_override
from firewall.auth.service import COOKIE_NAME, AuthService, set_service
from firewall.auth.users import (
    ADMIN,
    CANDIDATE,
    RECRUITER,
    UserExistsError,
    make_user,
    normalize_username,
    valid_username,
    with_changes,
)
from firewall.models import Route


DecisionLookup = Callable[[str], Any]
AnyUser = (CANDIDATE, RECRUITER, ADMIN)


class LoginBody(BaseModel):
    username: str = Field(max_length=128)
    password: str = Field(max_length=512)


class RegisterBody(BaseModel):
    username: str = Field(max_length=128)
    password: str = Field(max_length=512)


class CreateUserBody(BaseModel):
    username: str = Field(max_length=128)
    password: str = Field(max_length=512)
    role: Literal["candidate", "recruiter", "admin"]


class PatchUserBody(BaseModel):
    role: Literal["candidate", "recruiter", "admin"] | None = None
    disabled: bool | None = None


class ResetPasswordBody(BaseModel):
    new_password: str = Field(max_length=512)


class OverrideBody(BaseModel):
    route: Route
    reason: Annotated[str, Field(min_length=10, max_length=2000)]


def _decision_fields(decision: Any) -> tuple[str, int]:
    if isinstance(decision, dict):
        route, score = decision["route"], decision["score"]
    else:
        route, score = decision.route, decision.score
    return str(getattr(route, "value", route)), int(score)


def _check_password(password: str) -> None:
    try:
        validate_password(password)
    except PasswordPolicyError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def build_auth_router(decision_lookup: DecisionLookup, service: AuthService | None = None) -> APIRouter:
    service = service or AuthService.from_env()
    set_service(service)
    router = APIRouter(prefix="/v1")
    audit = service.audit

    @router.post("/auth/login")
    def login(body: LoginBody, request: Request, response: Response) -> dict[str, object]:
        username = normalize_username(body.username)
        ip = client_ip(request)
        locked = service.throttle.locked(username, ip)
        user = service.users.get(username)
        verified = verify_password(user.password_hash if user else None, body.password)
        if locked:
            audit.append("login_blocked", username, ip=ip)
            raise HTTPException(status_code=429, detail="too many attempts, try again later")
        if not verified or user is None or user.disabled:
            service.throttle.failure(username, ip)
            reason = "disabled" if user is not None and user.disabled and verified else "bad_credentials"
            audit.append("login_failed", username, ip=ip, detail={"reason": reason})
            raise HTTPException(status_code=401, detail="invalid credentials")
        service.throttle.success(username)
        session = service.sessions.create(user.username)
        response.set_cookie(
            COOKIE_NAME,
            session.session_id,
            max_age=int(getattr(service.sessions, "absolute_seconds", 28800)),
            httponly=True,
            samesite="lax",
            secure=service.settings.cookie_secure,
            path="/",
        )
        audit.append("login_ok", user.username, ip=ip)
        return {"username": user.username, "role": user.role, "csrf_token": session.csrf_token}

    @router.post("/auth/logout")
    def logout(
        request: Request,
        response: Response,
        principal: Principal = Depends(require_role(*AnyUser)),
    ) -> dict[str, str]:
        service.sessions.delete(principal.session_id)
        response.delete_cookie(COOKIE_NAME, path="/")
        audit.append("logout", principal.username, ip=client_ip(request))
        return {"status": "logged_out"}

    @router.get("/auth/me")
    def me(principal: Principal = Depends(require_role(*AnyUser))) -> dict[str, object]:
        return {"username": principal.username, "role": principal.role, "csrf_token": principal.csrf_token}

    @router.post("/auth/register", status_code=201)
    def register(body: RegisterBody, request: Request) -> dict[str, object]:
        ip = client_ip(request)
        if not service.settings.allow_signup:
            raise HTTPException(status_code=403, detail="signup is disabled")
        key = f"signup:{ip}"
        if service.signup_limiter.blocked(key):
            raise HTTPException(status_code=429, detail="too many attempts, try again later")
        service.signup_limiter.hit(key)
        username = normalize_username(body.username)
        if not valid_username(username):
            raise HTTPException(status_code=422, detail="username must be 3-64 characters: a-z, 0-9, . _ -")
        _check_password(body.password)
        try:
            user = make_user(username, body.password, CANDIDATE, service.clock())
            service.users.create(user)
        except UserExistsError as error:
            raise HTTPException(status_code=409, detail="username unavailable") from error
        audit.append("user_registered", user.username, ip=ip)
        return user.public()

    @router.get("/admin/users")
    def list_users(principal: Principal = Depends(require_role(ADMIN))) -> list[dict[str, object]]:
        return [user.public() for user in service.users.list()]

    @router.post("/admin/users", status_code=201)
    def create_user(
        body: CreateUserBody,
        request: Request,
        principal: Principal = Depends(require_role(ADMIN)),
    ) -> dict[str, object]:
        username = normalize_username(body.username)
        if not valid_username(username):
            raise HTTPException(status_code=422, detail="username must be 3-64 characters: a-z, 0-9, . _ -")
        _check_password(body.password)
        try:
            user = make_user(username, body.password, body.role, service.clock())
            service.users.create(user)
        except UserExistsError as error:
            raise HTTPException(status_code=409, detail="username unavailable") from error
        audit.append("user_created", principal.username, user.username, client_ip(request), {"role": user.role})
        return user.public()

    @router.patch("/admin/users/{username}")
    def patch_user(
        username: str,
        body: PatchUserBody,
        request: Request,
        principal: Principal = Depends(require_role(ADMIN)),
    ) -> dict[str, object]:
        user = service.users.get(username)
        if user is None:
            raise HTTPException(status_code=404, detail="user not found")
        if user.username == principal.username:
            raise HTTPException(status_code=400, detail="cannot change your own role or status")
        changes: dict[str, object] = {}
        if body.role is not None:
            changes["role"] = body.role
        if body.disabled is not None:
            changes["disabled"] = body.disabled
        if not changes:
            raise HTTPException(status_code=422, detail="nothing to change")
        updated = with_changes(user, **changes)
        service.users.update(updated)
        service.sessions.delete_for_user(updated.username)
        audit.append("user_updated", principal.username, updated.username, client_ip(request), changes)
        return updated.public()

    @router.post("/admin/users/{username}/reset-password")
    def reset_password(
        username: str,
        body: ResetPasswordBody,
        request: Request,
        principal: Principal = Depends(require_role(ADMIN)),
    ) -> dict[str, str]:
        user = service.users.get(username)
        if user is None:
            raise HTTPException(status_code=404, detail="user not found")
        _check_password(body.new_password)
        fresh = make_user(user.username, body.new_password, user.role, user.created_at)
        service.users.update(with_changes(user, password_hash=fresh.password_hash))
        service.sessions.delete_for_user(user.username)
        audit.append("password_reset", principal.username, user.username, client_ip(request))
        return {"status": "password_reset"}

    @router.get("/admin/audit")
    def read_audit(
        limit: Annotated[int, Query(ge=1, le=1000)] = 200,
        principal: Principal = Depends(require_role(ADMIN)),
    ) -> list[dict[str, object]]:
        return [asdict(entry) for entry in audit.entries(limit)]

    @router.post("/decisions/{application_id}/override")
    def override_decision(
        application_id: str,
        body: OverrideBody,
        request: Request,
        principal: Principal = Depends(require_role(RECRUITER, ADMIN)),
    ) -> dict[str, object]:
        decision = decision_lookup(application_id)
        if decision is None:
            raise HTTPException(status_code=404, detail="decision not found")
        original_route, original_score = _decision_fields(decision)
        record = build_override(
            application_id,
            body.route.value,
            body.reason.strip(),
            principal.username,
            original_route,
            original_score,
            service.clock,
        )
        service.overrides.add(record)
        audit.append(
            "decision_override",
            principal.username,
            application_id,
            client_ip(request),
            {"from": original_route, "to": record.override_route, "score": original_score, "reason": record.reason},
        )
        return {
            "application_id": application_id,
            "original": {"route": original_route, "score": original_score},
            "override": record.public(),
        }

    return router
