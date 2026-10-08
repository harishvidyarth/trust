from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


def population_stability_index(
    baseline: Sequence[float],
    current: Sequence[float],
    bins: int = 10,
    epsilon: float = 1e-6,
) -> float:
    if not baseline or not current:
        raise ValueError("both score distributions must be non-empty")
    if bins < 2:
        raise ValueError("bins must be at least 2")
    ordered = sorted(float(value) for value in baseline)
    boundaries: list[float] = []
    for index in range(1, bins):
        position = math.ceil(index * len(ordered) / bins) - 1
        boundary = ordered[max(0, min(position, len(ordered) - 1))]
        if not boundaries or boundary > boundaries[-1]:
            boundaries.append(boundary)
    expected = _bucket_proportions(baseline, boundaries)
    actual = _bucket_proportions(current, boundaries)
    value = 0.0
    for expected_share, actual_share in zip(expected, actual, strict=True):
        if expected_share == actual_share:
            continue
        expected_safe = max(expected_share, epsilon)
        actual_safe = max(actual_share, epsilon)
        value += (actual_safe - expected_safe) * math.log(actual_safe / expected_safe)
    return round(value, 12)


def ks_statistic(first: Sequence[float], second: Sequence[float]) -> float:
    if not first or not second:
        raise ValueError("both score distributions must be non-empty")
    left = sorted(float(value) for value in first)
    right = sorted(float(value) for value in second)
    points = sorted(set(left + right))
    left_index = 0
    right_index = 0
    maximum = 0.0
    for point in points:
        while left_index < len(left) and left[left_index] <= point:
            left_index += 1
        while right_index < len(right) and right[right_index] <= point:
            right_index += 1
        maximum = max(maximum, abs(left_index / len(left) - right_index / len(right)))
    return round(maximum, 12)


def detect_campaign(
    window_stats: Mapping[str, Any],
    baseline_stats: Mapping[str, Any],
    *,
    ratio_threshold: float = 3.0,
    min_count: int = 5,
) -> list[dict[str, Any]]:
    if ratio_threshold <= 1 or min_count < 1:
        raise ValueError("ratio_threshold must exceed 1 and min_count must be positive")
    window_counts, window_total = _stats(window_stats)
    baseline_counts, baseline_total = _stats(baseline_stats)
    alerts: list[dict[str, Any]] = []
    for code in sorted(set(window_counts) | set(baseline_counts)):
        count = window_counts.get(code, 0)
        baseline_count = baseline_counts.get(code, 0)
        if count < min_count:
            continue
        current_rate = count / window_total
        baseline_rate = baseline_count / baseline_total
        comparison_rate = baseline_rate if baseline_rate > 0 else 1 / baseline_total
        ratio = current_rate / comparison_rate
        if ratio >= ratio_threshold:
            rounded = round(ratio, 1)
            name = code.lower().replace("_", " ")
            alerts.append(
                {
                    "code": code,
                    "ratio": rounded,
                    "window_count": count,
                    "baseline_count": baseline_count,
                    "message": f"{name} rate {rounded:.1f}x baseline",
                }
            )
    return sorted(alerts, key=lambda item: (-item["ratio"], item["code"]))


def reason_frequency_psi(
    window_counts: Mapping[str, int],
    baseline_counts: Mapping[str, int],
    *,
    window_total: int | None = None,
    baseline_total: int | None = None,
    epsilon: float = 1e-6,
) -> float:
    current_total = window_total if window_total is not None else sum(window_counts.values())
    expected_total = baseline_total if baseline_total is not None else sum(baseline_counts.values())
    if current_total <= 0 or expected_total <= 0:
        raise ValueError("reason-frequency totals must be positive")
    categories = sorted(set(window_counts) | set(baseline_counts))
    value = 0.0
    for code in categories:
        actual = max(window_counts.get(code, 0) / current_total, epsilon)
        expected = max(baseline_counts.get(code, 0) / expected_total, epsilon)
        if actual != expected:
            value += (actual - expected) * math.log(actual / expected)
    current_other = max((current_total - sum(window_counts.values())) / current_total, epsilon)
    expected_other = max((expected_total - sum(baseline_counts.values())) / expected_total, epsilon)
    if current_other != expected_other:
        value += (current_other - expected_other) * math.log(current_other / expected_other)
    return round(value, 12)


def drift_report(
    window_scores: Sequence[float],
    baseline_scores: Sequence[float],
    window_reason_counts: Mapping[str, int],
    baseline_reason_counts: Mapping[str, int],
    *,
    window_total: int | None = None,
    baseline_total: int | None = None,
) -> dict[str, float]:
    return {
        "score_psi": population_stability_index(baseline_scores, window_scores),
        "score_ks": ks_statistic(baseline_scores, window_scores),
        "reason_frequency_psi": reason_frequency_psi(
            window_reason_counts,
            baseline_reason_counts,
            window_total=window_total,
            baseline_total=baseline_total,
        ),
    }


def _bucket_proportions(values: Sequence[float], boundaries: Sequence[float]) -> list[float]:
    counts = [0] * (len(boundaries) + 1)
    for raw in values:
        value = float(raw)
        index = 0
        while index < len(boundaries) and value > boundaries[index]:
            index += 1
        counts[index] += 1
    return [count / len(values) for count in counts]


def _stats(stats: Mapping[str, Any]) -> tuple[dict[str, int], int]:
    counts_raw = stats.get("reason_counts", stats)
    counts = {
        str(code): int(count)
        for code, count in counts_raw.items()
        if code not in {"total", "reason_counts"}
    }
    total = int(stats.get("total", sum(counts.values())))
    if total <= 0 or any(count < 0 for count in counts.values()):
        raise ValueError("window statistics must have non-negative counts and a positive total")
    return counts, total
