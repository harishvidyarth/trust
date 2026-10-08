from __future__ import annotations

from datetime import datetime, timezone


def parse_datetime(value: object, *, end: bool = False) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        if len(text) == 4 and text.isdigit():
            return datetime(int(text), 12 if end else 1, 31 if end else 1, tzinfo=timezone.utc)
        if len(text) == 7:
            year, month = (int(part) for part in text.split("-"))
            if end:
                if month == 12:
                    return datetime(year + 1, 1, 1, tzinfo=timezone.utc)
                return datetime(year, month + 1, 1, tzinfo=timezone.utc)
            return datetime(year, month, 1, tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None
