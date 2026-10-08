from __future__ import annotations

import math
from collections import defaultdict, deque
from datetime import datetime
from typing import Literal, Mapping, Sequence

from pydantic import BaseModel


class FeedbackEvent(BaseModel):
    application_id: str
    verdict: Literal["fine", "fraud"]
    recruiter_id: str
    timestamp: datetime


class WeightLearner:
    def __init__(
        self,
        *,
        recruiter_quorum: int = 3,
        event_quorum: int = 6,
        max_events_per_recruiter: int = 5,
        rate_window_seconds: int = 3600,
        learning_rate: float = 8.0,
        per_update_cap: float = 4.0,
    ) -> None:
        if recruiter_quorum < 2:
            raise ValueError("recruiter_quorum must be at least 2")
        if event_quorum < 2:
            raise ValueError("event_quorum must be at least 2")
        if max_events_per_recruiter < 1 or rate_window_seconds < 1:
            raise ValueError("rate limits must be positive")
        if learning_rate <= 0 or per_update_cap <= 0:
            raise ValueError("learning parameters must be positive")
        self.recruiter_quorum = recruiter_quorum
        self.event_quorum = event_quorum
        self.max_events_per_recruiter = max_events_per_recruiter
        self.rate_window_seconds = rate_window_seconds
        self.learning_rate = float(learning_rate)
        self.per_update_cap = float(per_update_cap)
        self._rate_windows: dict[str, deque[float]] = defaultdict(deque)
        self._pending: list[tuple[FeedbackEvent, tuple[str, ...]]] = []
        self._seen: set[tuple[str, str]] = set()
        self._offsets: dict[str, float] = defaultdict(float)
        self._weights: dict[str, int] | None = None
        self._defaults: dict[str, int] | None = None
        self._version = 0
        self._snapshots: dict[int, dict[str, object]] = {}
        self._last_change: dict[str, dict[str, object]] = {}
        self.change_log: list[dict[str, object]] = []

    @property
    def version(self) -> int:
        return self._version

    @property
    def snapshots(self) -> dict[int, dict[str, int]]:
        return {
            version: dict(snapshot["weights"])
            for version, snapshot in self._snapshots.items()
        }

    def record(self, event: FeedbackEvent | Mapping[str, object], reason_codes: Sequence[str]) -> bool:
        item = event if isinstance(event, FeedbackEvent) else FeedbackEvent.model_validate(event)
        codes = tuple(sorted({str(code) for code in reason_codes if str(code)}))
        if not codes:
            raise ValueError("at least one reason code is required")
        duplicate_key = (item.application_id, item.recruiter_id)
        if duplicate_key in self._seen:
            self.change_log.append(
                {"action": "duplicate_ignored", "application_id": item.application_id, "recruiter_id": item.recruiter_id}
            )
            return False
        moment = item.timestamp.timestamp()
        window = self._rate_windows[item.recruiter_id]
        cutoff = moment - self.rate_window_seconds
        while window and window[0] <= cutoff:
            window.popleft()
        if len(window) >= self.max_events_per_recruiter:
            self.change_log.append(
                {"action": "rate_limited", "application_id": item.application_id, "recruiter_id": item.recruiter_id}
            )
            return False
        window.append(moment)
        self._seen.add(duplicate_key)
        self._pending.append((item, codes))
        self.change_log.append(
            {
                "action": "feedback_accepted",
                "application_id": item.application_id,
                "recruiter_id": item.recruiter_id,
                "verdict": item.verdict,
                "reason_codes": list(codes),
            }
        )
        return True

    add_feedback = record
    observe = record

    def apply(self, default_weights: Mapping[str, int]) -> dict[str, int]:
        defaults = self._validate_defaults(default_weights)
        if self._defaults is None:
            self._defaults = defaults
            self._weights = dict(defaults)
            self._snapshots[0] = {"weights": dict(defaults), "offsets": {}}
        elif defaults != self._defaults:
            raise ValueError("default_weights cannot change within a learner history")

        grouped: dict[tuple[str, str], list[FeedbackEvent]] = defaultdict(list)
        for event, codes in self._pending:
            for code in codes:
                if code in defaults:
                    grouped[(code, event.verdict)].append(event)

        consumed: set[tuple[str, str]] = set()
        updates: list[dict[str, object]] = []
        for code, verdict in sorted(grouped):
            events = grouped[(code, verdict)]
            recruiters = {event.recruiter_id for event in events}
            quorum_met = len(recruiters) >= self.recruiter_quorum or len(events) >= self.event_quorum
            if not quorum_met:
                continue
            target = 1.0 if verdict == "fraud" else 0.0
            prediction = self._sigmoid(self._offsets[code] / max(defaults[code], 1))
            raw_delta = self.learning_rate * (target - prediction)
            delta = max(-self.per_update_cap, min(self.per_update_cap, raw_delta))
            old_weight = self._weight_for(code, defaults[code])
            self._offsets[code] += delta
            new_weight = self._weight_for(code, defaults[code])
            for event in events:
                consumed.add((event.application_id, event.recruiter_id))
            if new_weight != old_weight:
                update = {
                    "action": "weight_updated",
                    "code": code,
                    "old_weight": old_weight,
                    "new_weight": new_weight,
                    "verdict": verdict,
                    "events": len(events),
                    "recruiters": len(recruiters),
                }
                updates.append(update)
                self._last_change[code] = update

        if consumed:
            self._pending = [
                (event, codes)
                for event, codes in self._pending
                if (event.application_id, event.recruiter_id) not in consumed
            ]
        if updates:
            self._weights = {code: self._weight_for(code, default) for code, default in defaults.items()}
            self._version += 1
            for update in updates:
                update["version"] = self._version
                self.change_log.append(update)
            self._snapshots[self._version] = {
                "weights": dict(self._weights),
                "offsets": dict(self._offsets),
            }
        return dict(self._weights or defaults)

    def rollback(self, version: int) -> dict[str, int]:
        if version not in self._snapshots:
            raise ValueError(f"unknown snapshot version: {version}")
        snapshot = self._snapshots[version]
        previous = self._version
        self._version = version
        self._weights = dict(snapshot["weights"])
        self._offsets = defaultdict(float, snapshot["offsets"])
        self.change_log.append({"action": "rollback", "from_version": previous, "to_version": version})
        return dict(self._weights)

    def explain_change(self, code: str) -> str:
        change = self._last_change.get(code)
        if not change:
            current = (self._weights or {}).get(code)
            if current is None:
                return f"weight of {code} has no confirmed change"
            return f"weight of {code} remains {current}; no confirmed quorum changed it"
        return (
            f"weight of {code} moved {change['old_weight']}->{change['new_weight']} "
            f"after {change['recruiters']} recruiter confirmations "
            f"across {change['events']} feedback events"
        )

    def _weight_for(self, code: str, default: int) -> int:
        candidate = default + self._offsets[code]
        bounded = max(default * 0.5, min(default * 1.5, candidate))
        return int(math.floor(bounded + 0.5))

    @staticmethod
    def _sigmoid(value: float) -> float:
        return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, value))))

    @staticmethod
    def _validate_defaults(default_weights: Mapping[str, int]) -> dict[str, int]:
        weights = dict(default_weights)
        if not weights:
            raise ValueError("default_weights cannot be empty")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in weights.values()):
            raise ValueError("default weights must be non-negative integers")
        return weights
