from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from firewall.models import Decision, Route


class AuditLog:
    GENESIS_HASH = "0" * 64

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self.anchor_path = Path(f"{self.path}.anchor") if self.path is not None else None
        self.entries: list[dict[str, Any]] = []
        if self.path is not None and self.path.exists():
            self.entries = self._read_file()
        self._sealed_count = len(self.entries)
        self._sealed_hash = self.entries[-1].get("hash", self.GENESIS_HASH) if self.entries else self.GENESIS_HASH
        if self.anchor_path is not None and self.anchor_path.exists():
            anchor = json.loads(self.anchor_path.read_text(encoding="utf-8"))
            self._sealed_count = int(anchor["count"])
            self._sealed_hash = str(anchor["head_hash"])

    def append(
        self,
        decision: Decision | Mapping[str, Any],
        *,
        version: str | int | None = None,
        config_version: str | int | None = None,
        weights_version: str | int | None = None,
        actor: str = "system",
    ) -> dict[str, Any]:
        if not actor:
            raise ValueError("actor cannot be empty")
        data = self._decision_data(decision)
        previous = self.entries[-1]["hash"] if self.entries else self.GENESIS_HASH
        entry: dict[str, Any] = {
            "prev_hash": previous,
            "application_id": data["application_id"],
            "route": data["route"],
            "score": data["score"],
            "reason_codes": data["reason_codes"],
            "version": version,
            "config_version": config_version,
            "weights_version": weights_version,
            "actor": actor,
        }
        entry["hash"] = self._hash(entry)
        self.entries.append(entry)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(self._canonical(entry) + "\n")
        self._sealed_count = len(self.entries)
        self._sealed_hash = entry["hash"]
        self._write_anchor()
        return dict(entry)

    def verify_chain(self) -> int | None:
        if self.path is not None:
            try:
                entries = self._read_file()
            except AuditParseError as error:
                return error.index
        else:
            entries = self.entries
        previous = self.GENESIS_HASH
        for index, entry in enumerate(entries):
            if entry.get("prev_hash") != previous or entry.get("hash") != self._hash(entry):
                return index
            previous = entry["hash"]
        if len(entries) != self._sealed_count:
            return min(len(entries), self._sealed_count)
        if previous != self._sealed_hash:
            return max(0, len(entries) - 1)
        return None

    @classmethod
    def _hash(cls, entry: Mapping[str, Any]) -> str:
        payload = {key: value for key, value in entry.items() if key != "hash"}
        return hashlib.sha256(cls._canonical(payload).encode("utf-8")).hexdigest()

    @staticmethod
    def _canonical(value: Mapping[str, Any]) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def _decision_data(decision: Decision | Mapping[str, Any]) -> dict[str, Any]:
        if isinstance(decision, Decision):
            return {
                "application_id": decision.application_id,
                "route": decision.route.value,
                "score": decision.score,
                "reason_codes": [reason.code for reason in decision.reasons],
            }
        route = decision["route"]
        route_value = route.value if isinstance(route, Route) else str(route)
        reasons = decision.get("reasons", decision.get("reason_codes", []))
        reason_codes = [reason.get("code") if isinstance(reason, Mapping) else str(reason) for reason in reasons]
        return {
            "application_id": str(decision["application_id"]),
            "route": route_value,
            "score": int(decision["score"]),
            "reason_codes": reason_codes,
        }

    def _read_file(self) -> list[dict[str, Any]]:
        if self.path is None or not self.path.exists():
            return []
        entries: list[dict[str, Any]] = []
        with self.path.open(encoding="utf-8") as handle:
            for index, line in enumerate(handle):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except (json.JSONDecodeError, TypeError) as error:
                    raise AuditParseError(index) from error
                if not isinstance(value, dict):
                    raise AuditParseError(index)
                entries.append(value)
        return entries

    def _write_anchor(self) -> None:
        if self.anchor_path is None:
            return
        anchor = {"count": self._sealed_count, "head_hash": self._sealed_hash}
        self.anchor_path.write_text(self._canonical(anchor) + "\n", encoding="utf-8")


class AuditParseError(ValueError):
    def __init__(self, index: int) -> None:
        super().__init__(f"invalid audit entry at index {index}")
        self.index = index
