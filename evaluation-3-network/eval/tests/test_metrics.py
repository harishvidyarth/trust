from __future__ import annotations

import pytest

from eval.run_eval import compute_metrics


def test_metric_function_on_tiny_handmade_case():
    rows = [
        {"label": "ABUSE", "route": "MANUAL_REVIEW"},
        {"label": "ABUSE", "route": "PASS_TO_ATS"},
        {"label": "LEGIT", "route": "ADDITIONAL_VERIFICATION"},
        {"label": "LEGIT", "route": "PASS_TO_ATS"},
    ]

    metrics = compute_metrics(rows)

    assert metrics["tp"] == 1
    assert metrics["fp"] == 1
    assert metrics["fn"] == 1
    assert metrics["tn"] == 1
    assert metrics["precision"] == pytest.approx(0.5)
    assert metrics["recall"] == pytest.approx(0.5)
    assert metrics["f1"] == pytest.approx(0.5)
    assert metrics["false_positive_rate"] == pytest.approx(0.5)

