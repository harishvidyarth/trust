from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

from firewall.delivery import DeliveryDeadLetter
from firewall.redis_layer.client import KEY_PREFIX, RedisLink


DEAD_LETTER_LIMIT = 1_000
KEY_TTL_S = 7 * 24 * 3600
MAX_ITEM_BYTES = 262_144
_BLOCK_SLICE_S = 0.25


@dataclass(frozen=True)
class QueueItem:
    application_id: str
    destination: str
    payload: dict[str, Any] = field(default_factory=dict)
    attempts: int = 0

    def encode(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

    @classmethod
    def decode(cls, raw: str) -> "QueueItem | None":
        try:
            data = json.loads(raw)
            return cls(
                application_id=str(data["application_id"]),
                destination=str(data["destination"]),
                payload=dict(data.get("payload") or {}),
                attempts=int(data.get("attempts", 0)),
            )
        except (ValueError, KeyError, TypeError):
            return None


class DeliveryQueue:
    def __init__(self, *, dead_letter_limit: int = DEAD_LETTER_LIMIT) -> None:
        self._queue: deque[QueueItem] = deque()
        self._dead_letters: deque[DeliveryDeadLetter] = deque(maxlen=dead_letter_limit)
        self._condition = threading.Condition()

    def push(self, item: QueueItem) -> bool:
        try:
            if len(item.encode().encode()) > MAX_ITEM_BYTES:
                return False
        except (TypeError, ValueError):
            return False
        with self._condition:
            self._queue.append(item)
            self._condition.notify()
        return True

    def pop(self, timeout_s: float = 0.0) -> QueueItem | None:
        with self._condition:
            if not self._queue and timeout_s > 0:
                self._condition.wait(timeout_s)
            return self._queue.popleft() if self._queue else None

    def requeue(self, item: QueueItem) -> bool:
        return self.push(QueueItem(item.application_id, item.destination, item.payload, item.attempts + 1))

    def dead_letter(self, item: QueueItem, error: str) -> DeliveryDeadLetter:
        letter = DeliveryDeadLetter(
            application_id=item.application_id,
            destination=item.destination,
            attempts=max(item.attempts, 1),
            error=error,
            payload=item.payload or None,
        )
        with self._condition:
            self._dead_letters.append(letter)
        return letter

    def dead_letters(self) -> tuple[DeliveryDeadLetter, ...]:
        with self._condition:
            return tuple(self._dead_letters)

    def take_dead_letters(self) -> tuple[DeliveryDeadLetter, ...]:
        with self._condition:
            letters = tuple(self._dead_letters)
            self._dead_letters.clear()
            return letters

    def depth(self) -> int:
        with self._condition:
            return len(self._queue)


class RedisDeliveryQueue(DeliveryQueue):
    def __init__(
        self,
        link: RedisLink,
        name: str = "delivery",
        *,
        fallback: DeliveryQueue | None = None,
        dead_letter_limit: int = DEAD_LETTER_LIMIT,
    ) -> None:
        super().__init__(dead_letter_limit=dead_letter_limit)
        self._link = link
        self._queue_key = f"{KEY_PREFIX}queue:{name}"
        self._dead_key = f"{KEY_PREFIX}queue:{name}:dead"
        self._limit = dead_letter_limit
        self._fallback = fallback or DeliveryQueue(dead_letter_limit=dead_letter_limit)

    def push(self, item: QueueItem) -> bool:
        try:
            raw = item.encode()
        except (TypeError, ValueError):
            return False
        if len(raw.encode()) > MAX_ITEM_BYTES:
            return False

        def operation(client):
            pipe = client.pipeline(transaction=True)
            pipe.lpush(self._queue_key, raw)
            pipe.expire(self._queue_key, KEY_TTL_S)
            pipe.execute()
            return True

        return self._link.call(operation, lambda: self._fallback.push(item))

    def pop(self, timeout_s: float = 0.0) -> QueueItem | None:
        def operation(client):
            deadline = time.monotonic() + timeout_s
            raw = client.rpop(self._queue_key)
            while raw is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                popped = client.brpop(self._queue_key, timeout=min(remaining, _BLOCK_SLICE_S))
                raw = popped[1] if popped else None
            return QueueItem.decode(raw)

        found = self._link.call(operation, lambda: None)
        return found if found is not None else self._fallback.pop(0.0)

    def requeue(self, item: QueueItem) -> bool:
        return self.push(QueueItem(item.application_id, item.destination, item.payload, item.attempts + 1))

    def dead_letter(self, item: QueueItem, error: str) -> DeliveryDeadLetter:
        letter = DeliveryDeadLetter(
            application_id=item.application_id,
            destination=item.destination,
            attempts=max(item.attempts, 1),
            error=error,
            payload=item.payload or None,
        )
        raw = json.dumps(asdict(letter), separators=(",", ":"))

        def operation(client):
            pipe = client.pipeline(transaction=True)
            pipe.lpush(self._dead_key, raw)
            pipe.ltrim(self._dead_key, 0, self._limit - 1)
            pipe.expire(self._dead_key, KEY_TTL_S)
            pipe.execute()
            return letter

        return self._link.call(operation, lambda: self._fallback.dead_letter(item, error))

    def dead_letters(self) -> tuple[DeliveryDeadLetter, ...]:
        def operation(client):
            letters = []
            for raw in reversed(client.lrange(self._dead_key, 0, -1)):
                try:
                    letters.append(DeliveryDeadLetter(**json.loads(raw)))
                except (ValueError, TypeError):
                    continue
            return tuple(letters)

        remote = self._link.call(operation, lambda: ())
        return remote + self._fallback.dead_letters()

    def take_dead_letters(self) -> tuple[DeliveryDeadLetter, ...]:
        def operation(client):
            pipe = client.pipeline(transaction=True)
            pipe.lrange(self._dead_key, 0, -1)
            pipe.delete(self._dead_key)
            letters = []
            for raw in reversed(pipe.execute()[0]):
                try:
                    letters.append(DeliveryDeadLetter(**json.loads(raw)))
                except (ValueError, TypeError):
                    continue
            return tuple(letters)

        remote = self._link.call(operation, lambda: ())
        return remote + self._fallback.take_dead_letters()

    def depth(self) -> int:
        remote = self._link.call(lambda client: int(client.llen(self._queue_key)), lambda: 0)
        return remote + self._fallback.depth()
