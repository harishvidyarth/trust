from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

import httpx

from firewall.models import Application, Route


class ForwardingError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeadLetter:
    application: Application
    destination: str
    attempts: int
    error: str


class ATSForwarder(ABC):
    @abstractmethod
    def forward(self, application: Application) -> bool:
        pass


class _RetryingHTTPForwarder(ATSForwarder):
    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
        max_attempts: int = 3,
        backoff_seconds: float = 0.25,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.client = client or httpx.Client()
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self.sleep = sleep
        self.dead_letters: list[DeadLetter] = []
        self._delivered: set[str] = set()
        self._lock = threading.RLock()

    @property
    @abstractmethod
    def destination(self) -> str: ...

    @abstractmethod
    def _request(self, application: Application) -> httpx.Response: ...

    def _on_success(self, application: Application) -> None:
        return None

    def forward(self, application: Application) -> bool:
        with self._lock:
            if application.application_id in self._delivered:
                return False

            last_error: Exception | None = None
            for attempt in range(1, self.max_attempts + 1):
                try:
                    response = self._request(application)
                    response.raise_for_status()
                    self._on_success(application)
                    self._delivered.add(application.application_id)
                    return True
                except Exception as error:
                    last_error = error
                    if attempt < self.max_attempts:
                        self.sleep(self.backoff_seconds * (2 ** (attempt - 1)))

            assert last_error is not None
            self.dead_letters.append(
                DeadLetter(
                    application=application,
                    destination=self.destination,
                    attempts=self.max_attempts,
                    error=f"{type(last_error).__name__}: {last_error}",
                )
            )
            raise ForwardingError(
                f"failed to forward {application.application_id} to {self.destination} "
                f"after {self.max_attempts} attempts; application added to dead letters"
            ) from last_error


def _common_headers(application: Application) -> dict[str, str]:
    return {"Idempotency-Key": application.application_id}


class GreenhouseHarvestForwarder(_RetryingHTTPForwarder):

    def __init__(
        self,
        api_key: str,
        on_behalf_of: str,
        *,
        base_url: str = "https://harvest.greenhouse.io",
        candidate_path: str = "/v1/candidates",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.api_key = api_key
        self.on_behalf_of = on_behalf_of
        self.url = f"{base_url.rstrip('/')}/{candidate_path.lstrip('/')}"

    @property
    def destination(self) -> str:
        return self.url

    def _request(self, application: Application) -> httpx.Response:
        names = application.candidate.name.strip().split(maxsplit=1)
        payload = {
            "first_name": names[0] if names else "",
            "last_name": names[1] if len(names) > 1 else "",
            "email_addresses": [{"value": application.candidate.email, "type": "personal"}],
            "phone_numbers": [{"value": application.candidate.phone, "type": "mobile"}],
            "applications": [{"job_id": application.job_id}],
            "firewall_application_id": application.application_id,
        }
        headers = {
            **_common_headers(application),
            "On-Behalf-Of": self.on_behalf_of,
        }
        return self.client.post(
            self.url,
            json=payload,
            headers=headers,
            auth=httpx.BasicAuth(self.api_key, ""),
            timeout=self.timeout,
        )


class LeverForwarder(_RetryingHTTPForwarder):

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.lever.co/v1",
        opportunity_path: str = "/opportunities",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.api_key = api_key
        self.url = f"{base_url.rstrip('/')}/{opportunity_path.lstrip('/')}"

    @property
    def destination(self) -> str:
        return self.url

    def _request(self, application: Application) -> httpx.Response:
        payload = {
            "name": application.candidate.name,
            "emails": [application.candidate.email],
            "phones": [{"value": application.candidate.phone}],
            "posting": application.job_id,
            "firewall_application_id": application.application_id,
            "candidate": application.candidate.model_dump(mode="json"),
        }
        return self.client.post(
            self.url,
            json=payload,
            headers=_common_headers(application),
            auth=httpx.BasicAuth(self.api_key, ""),
            timeout=self.timeout,
        )


class GenericWebhookForwarder(_RetryingHTTPForwarder):
    def __init__(self, url: str, secret: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.url = url
        self.secret = secret.encode("utf-8")

    @property
    def destination(self) -> str:
        return self.url

    def _request(self, application: Application) -> httpx.Response:
        body = json.dumps(
            application.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        signature = hmac.new(self.secret, body, hashlib.sha256).hexdigest()
        headers = {
            **_common_headers(application),
            "Content-Type": "application/json",
            "X-Firewall-Signature": signature,
        }
        return self.client.post(self.url, content=body, headers=headers, timeout=self.timeout)


class QueueForwarder(_RetryingHTTPForwarder):

    def __init__(
        self,
        name: str,
        *,
        url: str | None = None,
        secret: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.name = name
        self.url = url
        self.secret = secret.encode("utf-8") if secret is not None else None
        self.items: list[Application] = []

    @property
    def destination(self) -> str:
        return self.url or f"memory://{self.name}"

    def _request(self, application: Application) -> httpx.Response:
        if self.url is None:
            return httpx.Response(202, request=httpx.Request("POST", self.destination))

        body = json.dumps(
            {
                "queue": self.name,
                "application": application.model_dump(mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = {**_common_headers(application), "Content-Type": "application/json"}
        if self.secret is not None:
            headers["X-Firewall-Signature"] = hmac.new(
                self.secret, body, hashlib.sha256
            ).hexdigest()
        return self.client.post(self.url, content=body, headers=headers, timeout=self.timeout)

    def _on_success(self, application: Application) -> None:
        self.items.append(application)


class Router:

    def __init__(
        self,
        config: Mapping[str | Route, Sequence[str | ATSForwarder] | str | ATSForwarder],
        forwarders: Mapping[str, ATSForwarder] | None = None,
    ) -> None:
        raw_routes: Any = config.get("routes", config)
        if not isinstance(raw_routes, Mapping):
            raise ValueError("router config must contain a route mapping")
        registry = dict(forwarders or {})
        self._routes: dict[Route, tuple[ATSForwarder, ...]] = {}
        for key, configured in raw_routes.items():
            route = key if isinstance(key, Route) else Route(str(key))
            values = configured if isinstance(configured, Sequence) and not isinstance(configured, str) else [configured]
            resolved: list[ATSForwarder] = []
            for value in values:
                if isinstance(value, str):
                    try:
                        resolved.append(registry[value])
                    except KeyError as error:
                        raise ValueError(f"unknown forwarder {value!r} for route {route.value}") from error
                elif isinstance(value, ATSForwarder):
                    resolved.append(value)
                else:
                    raise TypeError(f"invalid forwarder for route {route.value}: {value!r}")
            self._routes[route] = tuple(resolved)

    @classmethod
    def from_config(
        cls,
        config: Mapping[str | Route, Any],
        forwarders: Mapping[str, ATSForwarder],
    ) -> "Router":
        return cls(config, forwarders)

    def dispatch(self, route: Route | str, application: Application) -> list[bool]:
        resolved_route = route if isinstance(route, Route) else Route(route)
        try:
            destinations = self._routes[resolved_route]
        except KeyError as error:
            raise ForwardingError(f"no forwarder configured for route {resolved_route.value}") from error
        return [forwarder.forward(application) for forwarder in destinations]

    def route(self, route: Route | str, application: Application) -> list[bool]:
        return self.dispatch(route, application)

    def forward(self, route: Route | str, application: Application) -> list[bool]:
        return self.dispatch(route, application)
