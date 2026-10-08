from firewall.adaptive.roi import ROIAssumptions, calculate_roi


def test_roi_returns_inputs_and_computed_outputs():
    assumptions = ROIAssumptions(
        applications_per_month=1000,
        fraud_share=0.1,
        minutes_per_manual_screen=6,
        recruiter_hourly_cost=30,
        cost_of_bad_hire=1000,
        firewall_catch_rate=0.8,
        false_positive_rate=0.1,
    )
    result = calculate_roi(assumptions)

    assert result["assumptions"]["applications_per_month"] == 1000
    assert result["outputs"]["hours_saved"] == 83.0
    assert result["outputs"]["cost_per_fake_application"] == 203.0
    assert result["outputs"]["monthly_savings"] == 82490.0
    assert result["kind"] == "model_not_measurement"
