from __future__ import annotations

from firewall.config import Config
from firewall.models import Application, Reason
from firewall.signals.common import reason
from firewall.signals.dates import parse_month, reference_month


def detect_consistency(application: Application, config: Config) -> list[Reason]:
    present = reference_month(application.signals.submitted_at)
    valid_roles: list[tuple[str, int, int]] = []
    invalid_details: list[str] = []

    for item in application.candidate.experience:
        label = f"{item.company}/{item.title}"
        try:
            start = parse_month(item.start, present)
            end = parse_month(item.end, present)
        except ValueError:
            invalid_details.append(f"{label} has an invalid YYYY-MM date")
            continue
        if start > present:
            invalid_details.append(f"{label} contains a future start date")
        elif end < start:
            invalid_details.append(f"{label} ends before it starts")
        elif end > present + config.max_future_end_months:
            invalid_details.append(f"{label} ends too far in the future")
        else:
            valid_roles.append((label, start, min(end, present)))

    overlaps: list[str] = []
    for index, (first_label, first_start, first_end) in enumerate(valid_roles):
        for second_label, second_start, second_end in valid_roles[index + 1 :]:
            overlap = min(first_end, second_end) - max(first_start, second_start)
            if overlap > config.overlap_months:
                overlaps.append(f"{first_label} and {second_label} overlap by {overlap} months")

    claimed = application.candidate.claimed_experience_years
    total_years = sum(end - start for _, start, end in valid_roles) / 12
    if claimed is not None and claimed > total_years + config.claimed_experience_tolerance_years:
        invalid_details.append(f"claimed {claimed:.1f} years but timeline supports {total_years:.1f}")

    found: list[Reason] = []
    if invalid_details:
        found.append(reason(config, "TIMELINE_INVALID", "medium", "; ".join(invalid_details) + "."))
    if overlaps:
        found.append(reason(config, "TIMELINE_OVERLAP", "medium", "; ".join(overlaps) + "."))
    return found
