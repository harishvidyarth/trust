from firewall.adaptive.capacity import suggest_threshold_adjustments


def test_over_capacity_suggests_bounded_lower_thresholds():
    suggestion = suggest_threshold_adjustments(
        review_capacity=20,
        recent_volume={"total": 100, "MANUAL_REVIEW": 50},
        pass_min=70,
        review_max=40,
        max_adjustment=5,
    )
    assert suggestion["pass_min"] == 65
    assert suggestion["review_max"] == 35
    assert "exceeds" in suggestion["explanation"]


def test_balanced_capacity_keeps_thresholds():
    suggestion = suggest_threshold_adjustments(20, {"total": 100, "MANUAL_REVIEW": 20}, 70, 40)
    assert suggestion["adjustment"] == 0
