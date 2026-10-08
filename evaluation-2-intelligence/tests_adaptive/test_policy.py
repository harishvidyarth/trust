from firewall.adaptive.policy import PolicySimulator


RECORDS = [
    {"reasons": [{"code": "A", "weight": 20}], "label": "legit"},
    {"reasons": [{"code": "A", "weight": 20}, {"code": "B", "weight": 25}], "label": "bad"},
    {"reasons": [{"code": "H", "weight": 5}], "label": "bad", "hard_escalation_codes": ["H"]},
]


def test_replay_counts_moves_and_metrics():
    result = PolicySimulator.replay(RECORDS, {"A": 40, "B": 30, "H": 5}, 70, 40)

    assert sum(result["route_counts"].values()) == 3
    assert result["moved"] == 2
    assert result["metrics"]["recall"] == 1.0
    assert result["metrics"]["false_positive_rate"] == 1.0


def test_shadow_mode_is_advisory_and_threshold_sweep_returns_points():
    report = PolicySimulator.shadow_mode_report(
        RECORDS,
        {"weights": {"A": 40, "B": 30, "H": 5}, "pass_min": 70, "review_max": 40},
    )
    points = PolicySimulator.threshold_sweep(RECORDS, {"A": 20, "B": 25, "H": 5}, [60, 70], [30, 40])

    assert report["effect"] == "none"
    assert report["mode"] == "shadow"
    assert len(points) == 4
    assert all(point["review_max"] < point["pass_min"] for point in points)
