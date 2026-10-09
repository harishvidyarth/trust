from __future__ import annotations

import re
from typing import Any

from firewall.identity.errors import IdentityError

REPORT_KEYS = frozenset({"steps", "frames_with_face", "total_frames", "duration_ms", "model"})
STEP_KEYS = frozenset({"id", "passed", "ms"})
MODEL = "mediapipe-facemesh"
MIN_STEP_MS = 300
MAX_STEP_MS = 40_000
MIN_TOTAL_MS = 2_000
MAX_TOTAL_MS = 120_000
TOLERANCE_S = 5
SUM_SLACK_MS = 1_000
MIN_FACE_RATIO = 0.6
MIN_FRAMES = 10
MAX_STEPS = 8
MAX_COUNT = 1_000_000
STEP_ID = re.compile(r"^[a-z_]{1,32}$")
BAD_REPORT = "We could not read the face check. Please try again."


def whole(value: Any, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > high:
        raise IdentityError(400, BAD_REPORT)
    return value


def parse(report: Any) -> dict[str, Any]:
    if not isinstance(report, dict) or set(report) != REPORT_KEYS or report["model"] != MODEL:
        raise IdentityError(400, BAD_REPORT)
    steps = report["steps"]
    if not isinstance(steps, list) or len(steps) > MAX_STEPS:
        raise IdentityError(400, BAD_REPORT)
    parsed = []
    for step in steps:
        if not isinstance(step, dict) or set(step) != STEP_KEYS:
            raise IdentityError(400, BAD_REPORT)
        if not isinstance(step["id"], str) or not STEP_ID.match(step["id"]) or not isinstance(step["passed"], bool):
            raise IdentityError(400, BAD_REPORT)
        parsed.append({"id": step["id"], "passed": step["passed"], "ms": whole(step["ms"], MAX_COUNT)})
    seen = whole(report["frames_with_face"], MAX_COUNT)
    total = whole(report["total_frames"], MAX_COUNT)
    if seen > total:
        raise IdentityError(400, BAD_REPORT)
    return {"steps": parsed, "seen": seen, "total": total, "duration_ms": whole(report["duration_ms"], MAX_COUNT)}


def judge(report: Any, issued: list[str], elapsed_s: float) -> dict[str, Any]:
    data = parse(report)
    steps = data["steps"]
    outcome = {"state": "", "steps_done": 0, "steps_total": len(issued), "client_measured": True}
    shape_ok = [step["id"] for step in steps] == issued
    times_ok = all(MIN_STEP_MS <= step["ms"] <= MAX_STEP_MS for step in steps)
    total_ok = MIN_TOTAL_MS <= data["duration_ms"] <= MAX_TOTAL_MS
    clock_ok = data["duration_ms"] <= (elapsed_s + TOLERANCE_S) * 1000
    sum_ok = sum(step["ms"] for step in steps) <= data["duration_ms"] + SUM_SLACK_MS
    if not (shape_ok and times_ok and total_ok and clock_ok and sum_ok):
        outcome["state"] = "implausible"
        return outcome
    outcome["steps_done"] = sum(1 for step in steps if step["passed"])
    if data["total"] < MIN_FRAMES or data["seen"] / data["total"] < MIN_FACE_RATIO:
        outcome["state"] = "not_seen"
    elif outcome["steps_done"] < len(issued):
        outcome["state"] = "steps_incomplete"
    else:
        outcome["state"] = "passed"
    return outcome
