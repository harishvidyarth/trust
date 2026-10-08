from __future__ import annotations

from datetime import datetime, timezone


def reference_month(submitted_at: float) -> int:
    submitted = datetime.fromtimestamp(submitted_at, tz=timezone.utc)
    return submitted.year * 12 + submitted.month - 1


def parse_month(value: str, present_month: int) -> int:
    cleaned = value.strip().lower()
    if cleaned == "present":
        return present_month
    parsed = datetime.strptime(cleaned, "%Y-%m")
    return parsed.year * 12 + parsed.month - 1
