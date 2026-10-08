from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Protocol

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


OUTCOME_TTL_S = 400 * 24 * 3600
MAX_OUTCOME_HISTORY = 100


class OutcomeConflict(Exception):
    pass


class OutcomeStore:
    def __init__(self, cache: Any, clock: Clock) -> None:
        self._cache = cache
        self._clock = clock
        self._lock = threading.Lock()

    @staticmethod
    def _key(application_id: str) -> str:
        return f"outcome:{application_id}"

    def get(self, application_id: str) -> dict[str, Any] | None:
        value = self._cache.get(self._key(application_id))
        return value if isinstance(value, dict) else None

    def is_rejected(self, application_id: str) -> bool:
        record = self.get(application_id)
        return record is not None and record.get("state") == "rejected"

    def last_rejection(self, application_id: str) -> dict[str, Any] | None:
        record = self.get(application_id)
        if record is None or record.get("state") != "rejected":
            return None
        return record["history"][-1]

    def change(self, application_id: str, target: str, reason: str, by: str) -> dict[str, Any]:
        with self._lock:
            record = self.get(application_id) or {"state": "open", "history": []}
            if (target == "rejected") == (record["state"] == "rejected"):
                raise OutcomeConflict(target)
            entry = {
                "outcome": "REJECTED" if target == "rejected" else "REOPENED",
                "reason": reason,
                "by": by,
                "at": self._clock(),
            }
            history = (record["history"] + [entry])[-MAX_OUTCOME_HISTORY:]
            self._cache.set(self._key(application_id), {"state": target, "history": history}, OUTCOME_TTL_S)
            return entry
