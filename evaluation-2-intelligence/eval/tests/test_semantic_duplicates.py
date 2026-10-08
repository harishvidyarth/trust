from __future__ import annotations

from eval import measure_semantic_duplicates as measurement


def test_measure_honest_filters_abuse_and_compares_switch(monkeypatch):
    seen = []

    def replay(records, config):
        seen.append((records, config))
        route = "ADDITIONAL_VERIFICATION" if config.semantic_dup_enabled else "PASS_TO_ATS"
        return [
            {"route": route, "reasons": ["DUP_RESUME_NEAR"] if config.semantic_dup_enabled else []}
            for _ in records
        ]

    monkeypatch.setattr(measurement, "replay", replay)
    records = [
        {"label": "LEGIT", "application": {"application_id": "honest-1"}},
        {"label": "ABUSE", "application": {"application_id": "attack-1"}},
        {"label": "LEGIT", "application": {"application_id": "honest-2"}},
    ]

    result = measurement.measure_honest(records, 0.95)

    assert result["before"]["flagged"] == 0
    assert result["after"]["flagged"] == 2
    assert result["before"]["count"] == result["after"]["count"] == 2
    assert all(len(items) == 2 for items, _ in seen)
    assert seen[0][1].semantic_dup_enabled is False
    assert seen[1][1].semantic_dup_enabled is True
    assert seen[1][1].semantic_dup_similarity_threshold == 0.95
