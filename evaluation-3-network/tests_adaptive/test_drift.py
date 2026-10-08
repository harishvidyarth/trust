from firewall.adaptive.drift import (
    detect_campaign,
    drift_report,
    ks_statistic,
    population_stability_index,
)


def test_distribution_statistics_are_zero_for_identical_samples():
    values = [10, 20, 30, 40, 50]
    assert population_stability_index(values, values) == 0.0
    assert ks_statistic(values, values) == 0.0


def test_ks_detects_separated_distributions():
    assert ks_statistic([1, 2, 3], [10, 11, 12]) == 1.0


def test_campaign_detection_uses_rates_and_minimum_counts():
    baseline = {"total": 1000, "reason_counts": {"STUFFING": 10, "NOISE": 1}}
    window = {"total": 500, "reason_counts": {"STUFFING": 31, "NOISE": 4}}

    alerts = detect_campaign(window, baseline, ratio_threshold=3, min_count=5)

    assert alerts[0]["code"] == "STUFFING"
    assert alerts[0]["ratio"] == 6.2
    assert "6.2x baseline" in alerts[0]["message"]
    assert all(alert["code"] != "NOISE" for alert in alerts)


def test_drift_report_includes_scores_and_reason_frequencies():
    report = drift_report(
        [80, 81, 82],
        [20, 21, 22],
        {"A": 30},
        {"A": 5},
        window_total=100,
        baseline_total=100,
    )
    assert report["score_ks"] == 1.0
    assert report["reason_frequency_psi"] > 0
