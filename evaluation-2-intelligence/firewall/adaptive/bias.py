from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from firewall.models import Route


def impact_ratio_report(
    records: Iterable[Mapping[str, Any]],
    group_attribute: str = "group",
    *,
    threshold: float = 0.80,
) -> dict[str, Any]:
    if not 0 < threshold <= 1:
        raise ValueError("threshold must be in (0, 1]")
    totals: dict[str, int] = defaultdict(int)
    selected: dict[str, int] = defaultdict(int)
    for record in records:
        group = str(record.get(group_attribute, "(missing)"))
        totals[group] += 1
        route = record.get("route")
        route_value = route.value if isinstance(route, Route) else str(route)
        if route_value == Route.PASS_TO_ATS.value:
            selected[group] += 1
    rates = {group: selected[group] / total for group, total in totals.items()}
    highest_rate = max(rates.values(), default=0.0)
    reference_group = min((group for group, rate in rates.items() if rate == highest_rate), default=None)
    rows: list[dict[str, Any]] = []
    for group in sorted(totals):
        rate = rates[group]
        ratio = rate / highest_rate if highest_rate else 1.0
        rows.append(
            {
                "group": group,
                "total": totals[group],
                "selected": selected[group],
                "selection_rate": round(rate, 6),
                "impact_ratio": round(ratio, 6),
                "flagged": ratio < threshold,
            }
        )
    return {
        "group_attribute": group_attribute,
        "reference_group": reference_group,
        "reference_rate": round(highest_rate, 6),
        "threshold": threshold,
        "groups": rows,
    }


def cohort_slices(
    records: Iterable[Mapping[str, Any]],
    cohort_attributes: Sequence[str],
    *,
    threshold: float = 0.80,
) -> list[dict[str, Any]]:
    rows = list(records)
    slices: list[dict[str, Any]] = []
    for attribute in cohort_attributes:
        report = impact_ratio_report(rows, attribute, threshold=threshold)
        for group in report["groups"]:
            slices.append({"cohort": attribute, "reference_group": report["reference_group"], **group})
    return slices


def cohort_markdown_table(
    records: Iterable[Mapping[str, Any]],
    cohort_attributes: Sequence[str],
    *,
    threshold: float = 0.80,
) -> str:
    lines = [
        "| Cohort | Group | Total | PASS_TO_ATS | Selection rate | Impact ratio | Flagged < 0.80 |",
        "|---|---|---:|---:|---:|---:|:---:|",
    ]
    for row in cohort_slices(records, cohort_attributes, threshold=threshold):
        lines.append(
            f"| {row['cohort']} | {row['group']} | {row['total']} | {row['selected']} | "
            f"{row['selection_rate']:.3f} | {row['impact_ratio']:.3f} | {'yes' if row['flagged'] else 'no'} |"
        )
    return "\n".join(lines)
