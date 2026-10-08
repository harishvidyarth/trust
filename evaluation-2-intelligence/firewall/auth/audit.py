from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from firewall.auth.limits import Clock


@dataclass(frozen=True)
class AuditEntry:
    seq: int
    at: float
    event: str
    actor: str
    target: str
    ip: str
    detail: dict[str, object]


class AuditLog(Protocol):
    def append(
        self,
        event: str,
        actor: str,
        target: str = "",
        ip: str = "",
        detail: dict[str, object] | None = None,
    ) -> AuditEntry: ...

    def entries(self, limit: int = 200) -> list[AuditEntry]: ...


class InMemoryAuditLog:
    def __init__(self, clock: Clock, path: str | Path | None = None) -> None:
        self._clock = clock
        self._path = Path(path) if path else None
        self._entries: list[AuditEntry] = []
        self._lock = threading.Lock()

    def append(
        self,
        event: str,
        actor: str,
        target: str = "",
        ip: str = "",
        detail: dict[str, object] | None = None,
    ) -> AuditEntry:
        with self._lock:
            entry = AuditEntry(
                seq=len(self._entries) + 1,
                at=self._clock(),
                event=event,
                actor=actor,
                target=target,
                ip=ip,
                detail=dict(detail or {}),
            )
            self._entries.append(entry)
            if self._path is not None:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(asdict(entry), sort_keys=True) + "\n")
            return entry

    def entries(self, limit: int = 200) -> list[AuditEntry]:
        with self._lock:
            return list(reversed(self._entries[-limit:]))
