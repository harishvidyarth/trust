from __future__ import annotations

import json
from typing import Any

import redis

from firewall.intake_records import (
    InMemoryIntakeRepository,
    apply_recheck,
    apply_status,
    copy_record,
)
from firewall.redis_layer.client import KEY_PREFIX, RedisLink


RECORD_TTL_S = 30 * 24 * 3600
MAX_RECORD_BYTES = 262_144
_RETRIES = 5


class RedisIntakeRepository:
    def __init__(self, link: RedisLink, fallback: InMemoryIntakeRepository | None = None) -> None:
        self._link = link
        self._fallback = fallback or InMemoryIntakeRepository()

    @staticmethod
    def _key(consent_id: str) -> str:
        return f"{KEY_PREFIX}intake:{consent_id}"

    @staticmethod
    def _app_key(application_id: str) -> str:
        return f"{KEY_PREFIX}intake:app:{application_id}"

    def save(self, record: dict[str, Any]) -> None:
        raw = json.dumps(record, separators=(",", ":"), default=str)
        if len(raw.encode()) > MAX_RECORD_BYTES:
            raise ValueError("intake record is too large")
        consent_id = str(record["consent_id"])

        def operation(client):
            pipe = client.pipeline(transaction=True)
            pipe.set(self._key(consent_id), raw, ex=RECORD_TTL_S)
            if record.get("application_id"):
                pipe.set(self._app_key(str(record["application_id"])), consent_id, ex=RECORD_TTL_S)
            pipe.execute()

        self._link.call(operation, lambda: self._fallback.save(record))

    def get(self, consent_id: str) -> dict[str, Any] | None:
        def operation(client):
            raw = client.get(self._key(consent_id))
            if raw is None:
                return None
            try:
                return copy_record(json.loads(raw))
            except ValueError:
                return None

        found = self._link.call(operation, lambda: None)
        return found if found is not None else self._fallback.get(consent_id)

    def _mutate(self, consent_id: str, finding_index: int, change, missing):
        def operation(client):
            key = self._key(consent_id)
            for _ in range(_RETRIES):
                with client.pipeline() as pipe:
                    try:
                        pipe.watch(key)
                        raw = pipe.get(key)
                        if raw is None:
                            return missing
                        record = json.loads(raw)
                        if not 0 <= finding_index < len(record["findings"]):
                            return missing
                        result = change(record["findings"][finding_index])
                        pipe.multi()
                        pipe.set(key, json.dumps(record, separators=(",", ":"), default=str), ex=RECORD_TTL_S)
                        pipe.execute()
                        return result
                    except redis.WatchError:
                        continue
            return missing

        return self._link.call(operation, lambda: None)

    def set_finding_status(self, consent_id: str, finding_index: int, status: str, note: str | None) -> bool:
        result = self._mutate(consent_id, finding_index, lambda item: apply_status(item, status, note), False)
        if result is None:
            return self._fallback.set_finding_status(consent_id, finding_index, status, note)
        return bool(result)

    def recheck_finding(
        self, consent_id: str, finding_index: int, outcome: str, actor: str, note: str | None, at: float
    ) -> str:
        result = self._mutate(
            consent_id, finding_index, lambda item: apply_recheck(item, outcome, actor, note, at), "unknown"
        )
        if result is None:
            return self._fallback.recheck_finding(consent_id, finding_index, outcome, actor, note, at)
        return str(result)

    def consent_for_application(self, application_id: str) -> str | None:
        remote = self._link.call(lambda client: client.get(self._app_key(application_id)), lambda: None)
        return remote if remote is not None else self._fallback.consent_for_application(application_id)

