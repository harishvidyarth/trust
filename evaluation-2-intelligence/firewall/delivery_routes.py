from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request


_SECRET_KEY = re.compile(r"secret|token|password|api_?key|authorization|credential", re.IGNORECASE)


def _deny() -> None:
    raise HTTPException(status_code=403, detail="forbidden")


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _scrub(item) for key, item in value.items() if not _SECRET_KEY.search(str(key))}
    if isinstance(value, (list, tuple)):
        return [_scrub(item) for item in value]
    return value


def build_delivery_router(
    delivery: Any,
    guard: Callable[..., Any] | None = None,
    audit: Callable[[], Any] | None = None,
) -> APIRouter:
    dependency = Depends(guard or _deny)
    router = APIRouter(dependencies=[dependency])

    @router.post("/v1/delivery/replay-dead-letters")
    def replay_dead_letters(request: Request, principal: Any = dependency) -> dict[str, Any]:
        replay = getattr(delivery, "replay_dead_letters", None)
        if replay is None:
            raise HTTPException(status_code=501, detail="replay is not supported by this delivery")
        result = replay()
        body = {"replayed": result} if isinstance(result, int) else _scrub(dict(result)) if isinstance(result, dict) else {"replayed": 0}
        if audit is not None:
            audit().append(
                "delivery_replay",
                str(getattr(principal, "username", "unknown")),
                ip=request.client.host if request.client is not None else "unknown",
                detail={key: value for key, value in body.items() if isinstance(value, int)},
            )
        return body

    return router


def build_inbox_router(delivery: Any, guard: Callable[..., Any] | None = None) -> APIRouter:
    router = APIRouter(dependencies=[Depends(guard or _deny)])

    @router.get("/v1/delivery/status")
    def delivery_status() -> dict[str, Any]:
        return _scrub(delivery.status())

    @router.get("/v1/delivery/inbox")
    def delivery_inbox() -> dict[str, Any]:
        reader = getattr(delivery, "inboxes", None)
        return {"inboxes": reader() if reader is not None else []}

    return router
