from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Protocol

from firewall.auth.limits import Clock


@dataclass(frozen=True)
class OverrideRecord:
    application_id: str
    override_route: str
    reason: str
    actor: str
    at: float
    original_route: str
    original_score: int

    def public(self) -> dict[str, object]:
        return {
            "application_id": self.application_id,
            "override_route": self.override_route,
            "reason": self.reason,
            "actor": self.actor,
            "at": self.at,
            "original_route": self.original_route,
            "original_score": self.original_score,
        }


class OwnershipRegistry(Protocol):
    def bind(self, application_id: str, username: str) -> None: ...

    def owner(self, application_id: str) -> str | None: ...

    def owned_by(self, username: str) -> list[str]: ...


class OverrideStore(Protocol):
    def add(self, record: OverrideRecord) -> None: ...

    def for_application(self, application_id: str) -> list[OverrideRecord]: ...


class InMemoryOwnershipRegistry:
    def __init__(self) -> None:
        self._owners: dict[str, str] = {}
        self._lock = threading.Lock()

    def bind(self, application_id: str, username: str) -> None:
        with self._lock:
            self._owners.setdefault(application_id, username)

    def owner(self, application_id: str) -> str | None:
        with self._lock:
            return self._owners.get(application_id)

    def owned_by(self, username: str) -> list[str]:
        with self._lock:
            return [key for key, value in reversed(self._owners.items()) if value == username]


class InMemoryOverrideStore:
    def __init__(self) -> None:
        self._records: dict[str, list[OverrideRecord]] = {}
        self._lock = threading.Lock()

    def add(self, record: OverrideRecord) -> None:
        with self._lock:
            self._records.setdefault(record.application_id, []).append(record)

    def for_application(self, application_id: str) -> list[OverrideRecord]:
        with self._lock:
            return list(self._records.get(application_id, []))


def build_override(
    application_id: str,
    route: str,
    reason: str,
    actor: str,
    original_route: str,
    original_score: int,
    clock: Clock,
) -> OverrideRecord:
    return OverrideRecord(
        application_id=application_id,
        override_route=route,
        reason=reason,
        actor=actor,
        at=clock(),
        original_route=original_route,
        original_score=original_score,
    )
