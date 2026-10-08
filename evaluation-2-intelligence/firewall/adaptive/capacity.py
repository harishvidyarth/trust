from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from firewall.models import Route


def suggest_threshold_adjustments(
    review_capacity: int,
    recent_volume: int | Mapping[str, Any] | Sequence[Any],
    pass_min: int,
    review_max: int,
    *,
    max_adjustment: int = 5,
    tolerance: float = 0.1,
) -> dict[str, Any]:
    if review_capacity < 0:
        raise ValueError("review_capacity cannot be negative")
    if not 0 <= review_max < pass_min <= 100:
        raise ValueError("thresholds must satisfy 0 <= review_max < pass_min <= 100")
    if max_adjustment < 0 or not 0 <= tolerance < 1:
        raise ValueError("adjustment and tolerance bounds are invalid")
    total, review_load = _volume(recent_volume)
    if total == 0:
        return _result(pass_min, review_max, 0, "No recent volume; thresholds remain unchanged.", total, review_load, review_capacity)
    if review_load > review_capacity * (1 + tolerance):
        pressure = (review_load - review_capacity) / max(review_capacity, 1)
        adjustment = min(max_adjustment, max(1, round(max_adjustment * min(1.0, pressure))))
        new_review = max(0, review_max - adjustment)
        new_pass = max(new_review + 1, pass_min - adjustment)
        explanation = (
            f"Recent manual-review load {review_load} exceeds capacity {review_capacity}; "
            f"lower both thresholds by at most {max_adjustment} to reduce queue pressure."
        )
        return _result(new_pass, new_review, -adjustment, explanation, total, review_load, review_capacity)
    if review_load < review_capacity * (1 - tolerance):
        spare = (review_capacity - review_load) / max(review_capacity, 1)
        adjustment = min(max_adjustment, max(1, round(max_adjustment * min(1.0, spare))))
        new_pass = min(100, pass_min + adjustment)
        new_review = min(new_pass - 1, review_max + adjustment)
        explanation = (
            f"Recent manual-review load {review_load} is below capacity {review_capacity}; "
            f"raise thresholds by at most {max_adjustment} to use available review capacity."
        )
        return _result(new_pass, new_review, adjustment, explanation, total, review_load, review_capacity)
    return _result(
        pass_min,
        review_max,
        0,
        f"Recent manual-review load {review_load} is within the capacity band around {review_capacity}; thresholds remain unchanged.",
        total,
        review_load,
        review_capacity,
    )


class CapacityRouter:
    suggest = staticmethod(suggest_threshold_adjustments)


def _volume(recent_volume: int | Mapping[str, Any] | Sequence[Any]) -> tuple[int, int]:
    if isinstance(recent_volume, int):
        if recent_volume < 0:
            raise ValueError("recent_volume cannot be negative")
        return recent_volume, recent_volume
    if isinstance(recent_volume, Mapping):
        source = recent_volume.get("route_counts", recent_volume)
        review = int(source.get(Route.MANUAL_REVIEW.value, source.get("manual_review", 0)))
        total = int(recent_volume.get("total", sum(int(value) for value in source.values() if isinstance(value, (int, float)))))
        if total < 0 or review < 0 or review > total:
            raise ValueError("recent volume counts are invalid")
        return total, review
    routes = [item.route if hasattr(item, "route") else item for item in recent_volume]
    review = sum(str(route) == Route.MANUAL_REVIEW.value for route in routes)
    return len(routes), review


def _result(
    pass_min: int,
    review_max: int,
    adjustment: int,
    explanation: str,
    total: int,
    review_load: int,
    capacity: int,
) -> dict[str, Any]:
    return {
        "pass_min": pass_min,
        "review_max": review_max,
        "adjustment": adjustment,
        "recent_total": total,
        "recent_manual_reviews": review_load,
        "review_capacity": capacity,
        "explanation": explanation,
        "advisory_only": True,
    }
