from __future__ import annotations

from eval.generate_style_set import build_style_set
from eval.run_style_eval import evaluate, metrics, predicted_label, report_markdown


def test_style_set_is_deterministic_balanced_and_template_labeled():
    first = build_style_set(seed=42, count_per_class=10)
    second = build_style_set(seed=42, count_per_class=10)

    assert first == second
    assert first["metadata"]["count"] == 30
    assert "no LLM" in first["metadata"]["construction"]
    labels = [item["label"] for item in first["records"]]
    assert labels.count("human") == 10
    assert labels.count("ai_polished") == 10
    assert labels.count("ai_generated") == 10
    assert len({item["text"] for item in first["records"]}) == 30


def test_binary_metrics_count_human_false_positives():
    records = [
        {"label": "human"},
        {"label": "human"},
        {"label": "ai_polished"},
        {"label": "ai_generated"},
    ]
    result = metrics(records, ["human", "ai_generated", "ai_polished", "human"])

    assert result["precision"] == 0.5
    assert result["recall"] == 0.5
    assert result["false_positives"] == 1
    assert result["false_positive_rate"] == 0.5


def test_score_mapping_and_offline_report_limits():
    assert predicted_label(34) == "human"
    assert predicted_label(35) == "ai_polished"
    assert predicted_label(64) == "ai_polished"
    assert predicted_label(65) == "ai_generated"
    result = evaluate(build_style_set(seed=9, count_per_class=10), allow_live=False)
    report = report_markdown(result)

    assert result["modes"]["heuristic"]["status"] == "measured"
    assert result["modes"]["hybrid"]["status"] == "not measured"
    assert "## LIMITS" in report
    assert "production accuracy claim" in report
