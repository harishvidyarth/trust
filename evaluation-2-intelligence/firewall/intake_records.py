from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, Protocol


FINDING_STATUSES = ("verified", "unverified", "mismatch", "disputed")
RECHECK_OUTCOMES = ("upheld", "withdrawn")

Change = Callable[[dict[str, Any]], bool]


class IntakeRepository(Protocol):
    def save(self, record: dict[str, Any]) -> None: ...

    def get(self, consent_id: str) -> dict[str, Any] | None: ...

    def set_finding_status(
        self, consent_id: str, finding_index: int, status: str, note: str | None
    ) -> bool: ...

    def recheck_finding(
        self, consent_id: str, finding_index: int, outcome: str, actor: str, note: str | None, at: float
    ) -> str: ...

    def consent_for_application(self, application_id: str) -> str | None: ...


def copy_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        **record,
        "parsed": dict(record.get("parsed", {})),
        "findings": [dict(item) for item in record.get("findings", [])],
    }


def apply_status(finding: dict[str, Any], status: str, note: str | None) -> bool:
    if status == "disputed":
        if finding["status"] != "disputed":
            finding["previous_status"] = finding["status"]
        finding["dispute_note"] = note
        finding.pop("recheck", None)
    finding["status"] = status
    return True


def apply_recheck(
    finding: dict[str, Any], outcome: str, actor: str, note: str | None, at: float
) -> str:
    if finding["status"] != "disputed":
        return "not_disputed"
    if "recheck" in finding:
        return "already_rechecked"
    if outcome == "withdrawn":
        finding["status"] = finding.get("previous_status", "unverified")
    finding["recheck"] = {"outcome": outcome, "by": actor, "note": note, "at": at}
    return "ok"


class InMemoryIntakeRepository:
    def __init__(self, max_records: int = 10_000) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._by_application: dict[str, str] = {}
        self._lock = threading.Lock()
        self._max = max_records

    def save(self, record: dict[str, Any]) -> None:
        with self._lock:
            if len(self._records) >= self._max:
                oldest = next(iter(self._records))
                dropped = self._records.pop(oldest)
                if self._by_application.get(str(dropped.get("application_id"))) == oldest:
                    self._by_application.pop(str(dropped.get("application_id")), None)
            consent_id = str(record["consent_id"])
            self._records[consent_id] = copy_record(record)
            if record.get("application_id"):
                self._by_application[str(record["application_id"])] = consent_id

    def get(self, consent_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._records.get(consent_id)
            return copy_record(record) if record is not None else None

    def set_finding_status(
        self, consent_id: str, finding_index: int, status: str, note: str | None
    ) -> bool:
        with self._lock:
            record = self._records.get(consent_id)
            if record is None or not 0 <= finding_index < len(record["findings"]):
                return False
            return apply_status(record["findings"][finding_index], status, note)

    def recheck_finding(
        self, consent_id: str, finding_index: int, outcome: str, actor: str, note: str | None, at: float
    ) -> str:
        with self._lock:
            record = self._records.get(consent_id)
            if record is None or not 0 <= finding_index < len(record["findings"]):
                return "unknown"
            return apply_recheck(record["findings"][finding_index], outcome, actor, note, at)

    def consent_for_application(self, application_id: str) -> str | None:
        with self._lock:
            return self._by_application.get(application_id)
