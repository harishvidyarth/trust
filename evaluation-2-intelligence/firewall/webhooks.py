from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Callable, Mapping
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from firewall.connectors.lever import LeverWebhook, adapt
from firewall.delivery import Delivery
from firewall.models import Application, Decision, JobRequirements


MAX_WEBHOOK_BYTES = 1_048_576


async def _read_body(request: Request) -> bytes:
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


def build_router(
    evaluate_and_forward: Callable[[Application, JobRequirements], Decision],
    sanitize: Callable[[Application, Request], Application] | None = None,
    delivery: Delivery | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> APIRouter:
    router = APIRouter()
    active_environ = os.environ if environ is None else environ

    @router.post("/v1/webhooks/lever", response_model=Decision)
    async def lever_webhook(
        request: Request,
        signature: Annotated[str | None, Header(alias="Signature")] = None,
    ) -> Decision:
        body = await _read_body(request)
        secret = active_environ.get("FIREWALL_LEVER_WEBHOOK_SECRET")
        if not secret:
            raise HTTPException(status_code=401, detail="invalid webhook signature configuration")
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        supplied = "" if signature is None else signature.strip().casefold()
        if not hmac.compare_digest(expected, supplied):
            raise HTTPException(status_code=401, detail="invalid webhook signature")
        try:
            payload = LeverWebhook.model_validate_json(body)
        except ValidationError as error:
            raise RequestValidationError(error.errors()) from error
        application, job = adapt(payload)
        if sanitize is not None:
            application = sanitize(application, request)
        decision = evaluate_and_forward(application, job)
        if delivery is not None:
            try:
                delivery.deliver(decision.route, application)
            except Exception:
                pass
        return decision

    return router
