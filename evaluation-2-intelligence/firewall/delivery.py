from __future__ import annotations

import ipaddress
import json
import os
import re
import threading
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from firewall.connectors.forwarders import (
    ATSForwarder,
    GenericWebhookForwarder,
    GreenhouseHarvestForwarder,
    LeverForwarder,
    QueueForwarder,
    Router,
)
from firewall.connectors.mock_ats import MockATS
from firewall.models import Application, Route


_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_DESTINATION_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_METADATA_HOSTS = frozenset(
    {
        "instance-data",
        "metadata",
        "metadata.aws.internal",
        "metadata.google.internal",
    }
)
_ROOT_KEYS = frozenset({"destinations", "routes"})
_ROUTE_NAMES = frozenset(route.value for route in Route)
_COMMON_KEYS = frozenset({"type", "timeout", "max_attempts", "backoff_seconds"})
_TYPE_KEYS = {
    "mock": frozenset(),
    "mock_ats": frozenset(),
    "queue": frozenset({"url", "secret_env"}),
    "greenhouse": frozenset({"api_key_env", "on_behalf_of", "base_url", "candidate_path"}),
    "lever": frozenset({"api_key_env", "base_url", "opportunity_path"}),
    "webhook": frozenset({"url", "secret_env"}),
}


@dataclass(frozen=True)
class DeliveryDeadLetter:
    application_id: str
    destination: str
    attempts: int
    error: str
    payload: dict[str, Any] | None = field(default=None, repr=False, compare=False)


class _MockATSForwarder(ATSForwarder):
    def __init__(self, ats: MockATS) -> None:
        self.ats = ats
        self.destination = "memory://mock-ats"

    def forward(self, application: Application) -> bool:
        before = {item.application_id for item in self.ats.applications()}
        self.ats.receive(application)
        return application.application_id not in before


def _is_localhost(host: str) -> bool:
    lowered = host.rstrip(".").casefold()
    if lowered == "localhost" or lowered.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(lowered).is_loopback
    except ValueError:
        return False


def _validate_ip(host: str, allow_private: bool) -> None:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*", host, re.IGNORECASE):
            raise ValueError("destination URL uses a non-standard numeric host")
        return
    if address.is_loopback:
        return
    if address.is_link_local or address.is_unspecified or address.is_multicast:
        raise ValueError("destination URL resolves to a blocked address range")
    if address.is_private and not allow_private:
        raise ValueError("private destination URLs require FIREWALL_ALLOW_PRIVATE_DESTINATIONS=1")


def validate_destination_url(value: object, allow_private: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("destination URL must be a non-empty string")
    url = value.strip()
    parsed = urlsplit(url)
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("destination URL must not contain credentials")
    host = parsed.hostname
    if host is None:
        raise ValueError("destination URL must include a host")
    lowered = host.rstrip(".").casefold()
    if lowered in _METADATA_HOSTS or lowered.endswith(".metadata.google.internal"):
        raise ValueError("destination URL uses a blocked metadata host")
    if lowered.endswith((".local", ".internal", ".lan")) and not _is_localhost(lowered):
        raise ValueError("destination URL uses a blocked internal host")
    _validate_ip(lowered, allow_private)
    if parsed.scheme != "https" and not (_is_localhost(lowered) and parsed.scheme == "http"):
        raise ValueError("destination URLs must use https unless the host is localhost")
    if parsed.fragment:
        raise ValueError("destination URL must not contain a fragment")
    try:
        parsed.port
    except ValueError as error:
        raise ValueError("destination URL has an invalid port") from error
    return url


def _public_destination(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme in {"http", "https"} and parsed.hostname:
        port = f":{parsed.port}" if parsed.port is not None else ""
        return urlunsplit((parsed.scheme, f"{parsed.hostname}{port}", "", "", ""))
    return value


def _expect_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _validate_options(spec: Mapping[str, Any], name: str) -> None:
    timeout = spec.get("timeout", 10.0)
    attempts = spec.get("max_attempts", 3)
    backoff = spec.get("backoff_seconds", 0.25)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError(f"destination {name!r} timeout must be positive")
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
        raise ValueError(f"destination {name!r} max_attempts must be a positive integer")
    if isinstance(backoff, bool) or not isinstance(backoff, (int, float)) or backoff < 0:
        raise ValueError(f"destination {name!r} backoff_seconds must be non-negative")


def _env_secret(spec: Mapping[str, Any], key: str, environ: Mapping[str, str]) -> str:
    env_key = f"{key}_env"
    env_name = spec.get(env_key)
    if not isinstance(env_name, str) or _ENV_NAME.fullmatch(env_name) is None:
        raise ValueError(f"{env_key} must name an environment variable")
    value = environ.get(env_name)
    if not value:
        raise ValueError(f"environment variable {env_name} is not set")
    return value


def _validate_path(value: object, key: str) -> str:
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//"):
        raise ValueError(f"{key} must be an absolute URL path")
    return value


def validate_delivery_config(
    config: Mapping[str, Any],
    *,
    allow_private: bool = False,
) -> dict[str, Any]:
    unknown_root = set(config) - _ROOT_KEYS
    missing_root = _ROOT_KEYS - set(config)
    if unknown_root:
        raise ValueError(f"unknown delivery config keys: {', '.join(sorted(unknown_root))}")
    if missing_root:
        raise ValueError(f"missing delivery config keys: {', '.join(sorted(missing_root))}")
    destinations = _expect_mapping(config["destinations"], "destinations")
    routes = _expect_mapping(config["routes"], "routes")
    unknown_routes = set(routes) - _ROUTE_NAMES
    missing_routes = _ROUTE_NAMES - set(routes)
    if unknown_routes:
        raise ValueError(f"unknown route names: {', '.join(sorted(unknown_routes))}")
    if missing_routes:
        raise ValueError(f"missing route names: {', '.join(sorted(missing_routes))}")
    clean_destinations: dict[str, dict[str, Any]] = {}
    for name, raw_spec in destinations.items():
        if not isinstance(name, str) or _DESTINATION_NAME.fullmatch(name) is None:
            raise ValueError("destination names must contain only letters, digits, dots, dashes, and underscores")
        spec = dict(_expect_mapping(raw_spec, f"destination {name!r}"))
        kind_value = spec.get("type")
        if not isinstance(kind_value, str):
            raise ValueError(f"destination {name!r} must include a type")
        kind = kind_value.casefold()
        if kind not in _TYPE_KEYS:
            raise ValueError(f"unknown destination type {kind!r} for {name}")
        unknown = set(spec) - _COMMON_KEYS - _TYPE_KEYS[kind]
        if unknown:
            raise ValueError(f"unknown keys for destination {name!r}: {', '.join(sorted(unknown))}")
        spec["type"] = kind
        _validate_options(spec, name)
        for key in ("url", "base_url"):
            if key in spec:
                spec[key] = validate_destination_url(spec[key], allow_private)
        if kind == "queue" and "secret_env" in spec and "url" not in spec:
            raise ValueError(f"destination {name!r} cannot set secret_env without url")
        if kind in {"webhook", "queue"} and kind == "webhook" and "url" not in spec:
            raise ValueError(f"destination {name!r} requires url")
        if kind in {"greenhouse", "lever"} and "api_key_env" not in spec:
            raise ValueError(f"destination {name!r} requires api_key_env")
        if kind == "webhook" and "secret_env" not in spec:
            raise ValueError(f"destination {name!r} requires secret_env")
        if kind == "greenhouse":
            if not isinstance(spec.get("on_behalf_of"), str) or not spec["on_behalf_of"].strip():
                raise ValueError(f"destination {name!r} requires on_behalf_of")
            if "candidate_path" in spec:
                spec["candidate_path"] = _validate_path(spec["candidate_path"], "candidate_path")
        if kind == "lever" and "opportunity_path" in spec:
            spec["opportunity_path"] = _validate_path(spec["opportunity_path"], "opportunity_path")
        for env_key in ("api_key_env", "secret_env"):
            if env_key in spec and (
                not isinstance(spec[env_key], str) or _ENV_NAME.fullmatch(spec[env_key]) is None
            ):
                raise ValueError(f"destination {name!r} {env_key} must name an environment variable")
        clean_destinations[name] = spec
    clean_routes: dict[str, list[str]] = {}
    for route_name, raw_names in routes.items():
        if not isinstance(raw_names, list) or any(not isinstance(name, str) for name in raw_names):
            raise ValueError(f"route {route_name} must contain a list of destination names")
        if len(raw_names) != len(set(raw_names)):
            raise ValueError(f"route {route_name} contains duplicate destinations")
        missing = set(raw_names) - set(clean_destinations)
        if missing:
            raise ValueError(f"route {route_name} references unknown destinations: {', '.join(sorted(missing))}")
        clean_routes[route_name] = list(raw_names)
    return {"destinations": clean_destinations, "routes": clean_routes}


def default_delivery_config() -> dict[str, Any]:
    return {
        "destinations": {"mock_ats": {"type": "mock_ats"}},
        "routes": {
            Route.PASS_TO_ATS.value: ["mock_ats"],
            Route.ADDITIONAL_VERIFICATION.value: [],
            Route.MANUAL_REVIEW.value: [],
        },
    }


def load_delivery_config(
    value: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    active_environ = os.environ if environ is None else environ
    source = active_environ.get("FIREWALL_ROUTES") if value is None else value
    allow_private = active_environ.get("FIREWALL_ALLOW_PRIVATE_DESTINATIONS") == "1"
    if source is None or not source.strip():
        return validate_delivery_config(default_delivery_config(), allow_private=allow_private)
    raw = source.strip()
    if raw.startswith("{"):
        loaded = json.loads(raw)
    else:
        try:
            loaded = json.loads(Path(raw).read_text(encoding="utf-8"))
        except OSError as error:
            raise ValueError("FIREWALL_ROUTES must be JSON or a readable JSON file path") from error
    config = _expect_mapping(loaded, "FIREWALL_ROUTES")
    return validate_delivery_config(config, allow_private=allow_private)


def _build_forwarder(
    name: str,
    spec: Mapping[str, Any],
    ats: MockATS,
    environ: Mapping[str, str],
    client: httpx.Client | None,
) -> ATSForwarder:
    kind = str(spec["type"])
    options = {
        key: spec[key]
        for key in ("timeout", "max_attempts", "backoff_seconds")
        if key in spec
    }
    if client is not None:
        options["client"] = client
    if kind in {"mock", "mock_ats"}:
        return _MockATSForwarder(ats)
    if kind == "queue":
        secret = _env_secret(spec, "secret", environ) if "secret_env" in spec else None
        return QueueForwarder(name, url=spec.get("url"), secret=secret, **options)
    if kind == "greenhouse":
        extra = {key: spec[key] for key in ("base_url", "candidate_path") if key in spec}
        return GreenhouseHarvestForwarder(
            _env_secret(spec, "api_key", environ),
            str(spec["on_behalf_of"]),
            **extra,
            **options,
        )
    if kind == "lever":
        extra = {key: spec[key] for key in ("base_url", "opportunity_path") if key in spec}
        return LeverForwarder(_env_secret(spec, "api_key", environ), **extra, **options)
    if kind == "webhook":
        return GenericWebhookForwarder(
            str(spec["url"]),
            _env_secret(spec, "secret", environ),
            **options,
        )
    raise ValueError(f"unknown destination type {kind!r} for {name}")


class Delivery:
    def __init__(
        self,
        config: Mapping[str, Any],
        ats: MockATS,
        *,
        environ: Mapping[str, str] | None = None,
        client: httpx.Client | None = None,
        workers: int = 4,
        queue: Any | None = None,
    ) -> None:
        if workers < 1:
            raise ValueError("workers must be positive")
        active_environ = os.environ if environ is None else environ
        allow_private = active_environ.get("FIREWALL_ALLOW_PRIVATE_DESTINATIONS") == "1"
        clean = validate_delivery_config(config, allow_private=allow_private)
        self.forwarders = {
            name: _build_forwarder(name, spec, ats, active_environ, client)
            for name, spec in clean["destinations"].items()
        }
        self.router = Router.from_config({"routes": clean["routes"]}, self.forwarders)
        self.route_names = clean["routes"]
        self._shared = queue
        self._queue: Queue[tuple[str, ATSForwarder, Application]] = Queue()
        self._dead_letters: deque[DeliveryDeadLetter] = deque(maxlen=1_000)
        self._condition = threading.Condition()
        self._pending = 0
        self._threads = tuple(
            threading.Thread(target=self._work, name=f"firewall-delivery-{index}", daemon=True)
            for index in range(workers)
        )
        for thread in self._threads:
            thread.start()

    def deliver(self, route: Route | str, application: Application) -> None:
        destinations = self.router.destinations(route)
        names = self.route_names[(route if isinstance(route, Route) else Route(route)).value]
        if self._shared is not None:
            self._deliver_shared(names, application)
            return
        with self._condition:
            self._pending += len(destinations)
        for name, forwarder in zip(names, destinations):
            self._queue.put_nowait((name, forwarder, application))

    def _deliver_shared(self, names: list[str], application: Application) -> None:
        from firewall.redis_layer.delivery_queue import QueueItem

        payload = application.model_dump(mode="json")
        for name in names:
            item = QueueItem(application.application_id, name, payload)
            if not self._shared.push(item):
                self._record_dead_letter(application.application_id, name, 0, "QueueRejected", payload)

    def _record_dead_letter(
        self,
        application_id: str,
        destination: str,
        attempts: int,
        error: str,
        payload: dict[str, Any] | None,
    ) -> None:
        letter = DeliveryDeadLetter(application_id, destination, attempts, error, payload)
        with self._condition:
            self._dead_letters.append(letter)

    def _work(self) -> None:
        if self._shared is not None:
            self._work_shared()
            return
        while True:
            name, forwarder, application = self._queue.get()
            try:
                forwarder.forward(application)
            except Exception as error:
                self._record_dead_letter(
                    application.application_id,
                    name,
                    int(getattr(forwarder, "max_attempts", 1)),
                    type(error).__name__,
                    application.model_dump(mode="json"),
                )
            finally:
                with self._condition:
                    self._pending -= 1
                    self._condition.notify_all()
                self._queue.task_done()

    def _work_shared(self) -> None:
        while True:
            item = self._shared.pop(0.5)
            if item is None:
                time.sleep(0.1)
                continue
            with self._condition:
                self._pending += 1
            try:
                forwarder = self.forwarders[item.destination]
                forwarder.forward(Application.model_validate(item.payload))
            except Exception as error:
                self._shared.dead_letter(item, type(error).__name__)
            finally:
                with self._condition:
                    self._pending -= 1
                    self._condition.notify_all()

    def _backlog(self) -> int:
        return int(self._shared.depth()) if self._shared is not None else 0

    def wait(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while self._pending or self._backlog():
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                step = 0.05 if remaining is None else min(0.05, remaining)
                self._condition.wait(step if self._shared is not None else remaining)
            return True

    @property
    def dead_letters(self) -> tuple[DeliveryDeadLetter, ...]:
        with self._condition:
            local = tuple(self._dead_letters)
        shared = tuple(self._shared.dead_letters()) if self._shared is not None else ()
        return local + shared

    def replay_dead_letters(self) -> dict[str, int]:
        with self._condition:
            letters = list(self._dead_letters)
            self._dead_letters.clear()
        if self._shared is not None:
            letters.extend(self._shared.take_dead_letters())
        replayed = 0
        skipped = 0
        for letter in letters:
            if letter.payload is None or letter.destination not in self.forwarders:
                skipped += 1
                continue
            try:
                application = Application.model_validate(letter.payload)
            except ValueError:
                skipped += 1
                continue
            self._requeue(letter.destination, application)
            replayed += 1
        return {"replayed": replayed, "skipped": skipped}

    def _requeue(self, destination: str, application: Application) -> None:
        if self._shared is not None:
            self._deliver_shared([destination], application)
            return
        with self._condition:
            self._pending += 1
        self._queue.put_nowait((destination, self.forwarders[destination], application))

    def status(self) -> dict[str, Any]:
        with self._condition:
            pending = self._pending
            dead_letters = len(self._dead_letters)
        if self._shared is not None:
            pending += self._backlog()
            dead_letters += len(self._shared.dead_letters())
        destinations = []
        for name, forwarder in self.forwarders.items():
            target = str(getattr(forwarder, "destination", ""))
            destinations.append(
                {
                    "name": name,
                    "kind": type(forwarder).__name__,
                    "target": _public_destination(target),
                    "dead_letters": len(getattr(forwarder, "dead_letters", ())),
                }
            )
        return {
            "routes": {name: list(values) for name, values in self.route_names.items()},
            "destinations": destinations,
            "pending": pending,
            "dead_letters": dead_letters,
        }


def build_delivery_from_env(
    ats: MockATS | None = None,
    *,
    client: httpx.Client | None = None,
    environ: Mapping[str, str] | None = None,
    queue: Any | None = None,
) -> Delivery:
    active_environ = os.environ if environ is None else environ
    config = load_delivery_config(environ=active_environ)
    return Delivery(config, ats or MockATS(), environ=active_environ, client=client, queue=queue)
