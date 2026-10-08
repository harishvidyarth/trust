from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

from firewall.auth.audit import AuditLog, InMemoryAuditLog
from firewall.auth.limits import Clock, InMemoryRateLimiter, LoginThrottle, RateLimiter
from firewall.auth.records import (
    InMemoryOverrideStore,
    InMemoryOwnershipRegistry,
    OverrideRecord,
    OverrideStore,
    OwnershipRegistry,
)
from firewall.auth.sessions import InMemorySessionStore, SessionStore
from firewall.auth.users import UserStore, bootstrap_admin, build_user_store_from_env


def _redis_link() -> Any | None:
    from firewall.redis_layer.client import link_from_env

    return link_from_env()


COOKIE_NAME = "trust_session"
CSRF_HEADER = "X-CSRF-Token"


@dataclass(frozen=True)
class AuthSettings:
    cookie_secure: bool = False
    allow_signup: bool = True
    user_lockout_attempts: int = 5
    ip_lockout_attempts: int = 20
    lockout_window_seconds: float = 900.0
    lockout_seconds: float = 900.0
    signup_limit: int = 5
    signup_window_seconds: float = 3600.0

    @classmethod
    def from_env(cls) -> "AuthSettings":
        return cls(
            cookie_secure=os.getenv("FIREWALL_COOKIE_SECURE", "0") == "1",
            allow_signup=os.getenv("FIREWALL_ALLOW_SIGNUP", "1") != "0",
        )


class AuthService:
    def __init__(
        self,
        users: UserStore,
        sessions: SessionStore,
        audit: AuditLog,
        throttle: LoginThrottle,
        signup_limiter: RateLimiter,
        ownership: OwnershipRegistry,
        overrides: OverrideStore,
        settings: AuthSettings,
        clock: Clock,
    ) -> None:
        self.users = users
        self.sessions = sessions
        self.audit = audit
        self.throttle = throttle
        self.signup_limiter = signup_limiter
        self.ownership = ownership
        self.overrides = overrides
        self.settings = settings
        self.clock = clock

    @classmethod
    def from_env(
        cls,
        clock: Clock = time.time,
        settings: AuthSettings | None = None,
        users: UserStore | None = None,
        link: Any | None = None,
    ) -> "AuthService":
        settings = settings or AuthSettings.from_env()
        users = users if users is not None else build_user_store_from_env()
        active_link = link if link is not None else _redis_link()
        if active_link is not None:
            return cls._with_redis(active_link, clock, settings, users)
        service = cls(
            users=users,
            sessions=InMemorySessionStore(clock),
            audit=InMemoryAuditLog(clock, os.getenv("FIREWALL_AUDIT_FILE") or None),
            throttle=LoginThrottle(
                InMemoryRateLimiter(
                    settings.user_lockout_attempts,
                    settings.lockout_window_seconds,
                    settings.lockout_seconds,
                    clock,
                ),
                InMemoryRateLimiter(
                    settings.ip_lockout_attempts,
                    settings.lockout_window_seconds,
                    settings.lockout_seconds,
                    clock,
                ),
            ),
            signup_limiter=InMemoryRateLimiter(
                settings.signup_limit,
                settings.signup_window_seconds,
                settings.signup_window_seconds,
                clock,
            ),
            ownership=InMemoryOwnershipRegistry(),
            overrides=InMemoryOverrideStore(),
            settings=settings,
            clock=clock,
        )
        if bootstrap_admin(users, clock()):
            service.audit.append("admin_bootstrapped", "system")
        return service

    @classmethod
    def _with_redis(
        cls,
        link: Any,
        clock: Clock,
        settings: AuthSettings,
        users: UserStore,
    ) -> "AuthService":
        from firewall.redis_layer.auth_stores import RedisRateLimiter, build_auth_stores

        stores = build_auth_stores(link, clock, os.getenv("FIREWALL_AUDIT_FILE") or None)
        service = cls(
            users=users,
            sessions=stores["sessions"],
            audit=stores["audit"],
            throttle=LoginThrottle(
                RedisRateLimiter(
                    link,
                    "login:user",
                    settings.user_lockout_attempts,
                    settings.lockout_window_seconds,
                    settings.lockout_seconds,
                    clock,
                ),
                RedisRateLimiter(
                    link,
                    "login:ip",
                    settings.ip_lockout_attempts,
                    settings.lockout_window_seconds,
                    settings.lockout_seconds,
                    clock,
                ),
            ),
            signup_limiter=RedisRateLimiter(
                link,
                "signup",
                settings.signup_limit,
                settings.signup_window_seconds,
                settings.signup_window_seconds,
                clock,
            ),
            ownership=stores["ownership"],
            overrides=stores["overrides"],
            settings=settings,
            clock=clock,
        )
        if bootstrap_admin(users, clock()):
            service.audit.append("admin_bootstrapped", "system")
        return service


_ACTIVE: AuthService | None = None


def set_service(service: AuthService) -> None:
    global _ACTIVE
    _ACTIVE = service


def get_service() -> AuthService:
    if _ACTIVE is None:
        raise RuntimeError("auth service is not configured")
    return _ACTIVE


def bind_application(application_id: str, username: str) -> None:
    get_service().ownership.bind(application_id, username)


def overrides_for(application_id: str) -> list[OverrideRecord]:
    return get_service().overrides.for_application(application_id)
