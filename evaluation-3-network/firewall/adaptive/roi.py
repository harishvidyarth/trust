from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ROIAssumptions(BaseModel):
    applications_per_month: int = Field(ge=0)
    fraud_share: float = Field(ge=0, le=1)
    minutes_per_manual_screen: float = Field(ge=0)
    recruiter_hourly_cost: float = Field(ge=0)
    cost_of_bad_hire: float = Field(ge=0)
    firewall_catch_rate: float = Field(ge=0, le=1)
    false_positive_rate: float = Field(ge=0, le=1)


def calculate_roi(
    assumptions: ROIAssumptions | dict[str, Any] | None = None,
    **labelled_inputs: Any,
) -> dict[str, Any]:
    if assumptions is not None and labelled_inputs:
        raise ValueError("provide either assumptions or labelled keyword inputs, not both")
    if assumptions is None:
        model = ROIAssumptions(**labelled_inputs)
    elif isinstance(assumptions, ROIAssumptions):
        model = assumptions
    else:
        model = ROIAssumptions.model_validate(assumptions)
    values = model.model_dump()
    applications = model.applications_per_month
    fake = applications * model.fraud_share
    legitimate = applications - fake
    caught = fake * model.firewall_catch_rate
    false_positives = legitimate * model.false_positive_rate
    manual_with_firewall = caught + false_positives
    screens_avoided = max(0.0, applications - manual_with_firewall)
    hours_saved = screens_avoided * model.minutes_per_manual_screen / 60
    screening_cost = model.minutes_per_manual_screen / 60 * model.recruiter_hourly_cost
    cost_per_fake = screening_cost + (1 - model.firewall_catch_rate) * model.cost_of_bad_hire
    monthly_savings = hours_saved * model.recruiter_hourly_cost + caught * model.cost_of_bad_hire
    outputs = {
        "estimated_fake_applications": _rounded(fake),
        "estimated_bad_hires_avoided": _rounded(caught),
        "estimated_manual_screens_with_firewall": _rounded(manual_with_firewall),
        "hours_saved": _rounded(hours_saved),
        "cost_per_fake_application": _rounded(cost_per_fake),
        "monthly_savings": _rounded(monthly_savings),
    }
    return {
        "kind": "model_not_measurement",
        "assumptions": values,
        "outputs": outputs,
        "note": "Scenario model based entirely on the labelled assumptions; it is not an observed measurement.",
    }


def _rounded(value: float) -> float:
    return round(float(value), 2)
