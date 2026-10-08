from datetime import UTC, datetime, timedelta

from firewall.adaptive.feedback import FeedbackEvent, WeightLearner


DEFAULTS = {"DUP_EMAIL": 20, "FAST_SUBMIT": 12}


def event(recruiter: str, verdict: str, minute: int) -> FeedbackEvent:
    return FeedbackEvent(
        application_id=f"app-{recruiter}-{minute}",
        verdict=verdict,
        recruiter_id=recruiter,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=minute),
    )


def test_single_recruiter_cannot_move_weights():
    learner = WeightLearner(recruiter_quorum=3, event_quorum=6)
    learner.record(event("r1", "fraud", 0), ["DUP_EMAIL"])
    learner.record(event("r1", "fraud", 1), ["DUP_EMAIL"])

    assert learner.apply(DEFAULTS) == DEFAULTS


def test_quorum_moves_weight_and_explains_change():
    learner = WeightLearner(recruiter_quorum=3, event_quorum=6, learning_rate=8)
    for index in range(3):
        learner.record(event(f"r{index}", "fraud", index), ["DUP_EMAIL"])

    adjusted = learner.apply(DEFAULTS)

    assert adjusted["DUP_EMAIL"] == 24
    assert "20->24" in learner.explain_change("DUP_EMAIL")
    assert "3 recruiter confirmations" in learner.explain_change("DUP_EMAIL")


def test_bounds_rollback_and_determinism():
    def run() -> tuple[dict[str, int], WeightLearner]:
        learner = WeightLearner(recruiter_quorum=2, learning_rate=100, per_update_cap=50)
        for batch in range(5):
            for recruiter in ("r1", "r2"):
                learner.record(event(recruiter, "fraud", batch * 10), ["FAST_SUBMIT"])
            learner.apply(DEFAULTS)
        return learner.apply(DEFAULTS), learner

    first, learner = run()
    second, _ = run()
    assert first == second
    assert first["FAST_SUBMIT"] <= 18
    assert learner.rollback(0) == DEFAULTS
    assert learner.apply(DEFAULTS) == DEFAULTS


def test_rate_limit_rejects_excess_feedback():
    learner = WeightLearner(max_events_per_recruiter=1, rate_window_seconds=3600)
    assert learner.record(event("r1", "fraud", 0), ["DUP_EMAIL"])
    assert not learner.record(event("r1", "fraud", 1), ["DUP_EMAIL"])
    assert learner.change_log[-1]["action"] == "rate_limited"
