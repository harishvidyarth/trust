from __future__ import annotations

from firewall import engine
from firewall.config import Config
from firewall.enrichment.models import EnrichmentSignal, EnrichmentSummary
from firewall.models import Route
from firewall.store import InMemoryApplicationStore

from tests.conftest import make_application, make_job


def positive(code: str) -> EnrichmentSignal:
    return EnrichmentSignal(
        code=code, polarity="positive", severity="low", confidence=1.0, source="test", detail="Verified."
    )


def negative(code: str) -> EnrichmentSignal:
    return EnrichmentSignal(
        code=code, polarity="negative", severity="medium", confidence=1.0, source="test", detail="Mismatch."
    )


class Recorder:
    def __init__(self, signals: list[EnrichmentSignal], error: Exception | None = None) -> None:
        self.signals = signals
        self.error = error
        self.calls = 0

    def __call__(self, claims, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.signals, EnrichmentSummary()


def uncertain_application(application_id: str = "app-u"):
    return make_application(application_id, skills=[])


def run(application, recorder, monkeypatch, enabled: bool = True, resume_text: str | None = "github.com/ada"):
    monkeypatch.setattr(engine, "enrich", recorder)
    if enabled:
        monkeypatch.setenv("FIREWALL_ENRICH", "1")
    else:
        monkeypatch.delenv("FIREWALL_ENRICH", raising=False)
    return engine.evaluate(
        application, make_job(), InMemoryApplicationStore(), Config(), resume_text=resume_text
    )


def test_uncertain_score_without_enrichment_is_in_band(monkeypatch):
    recorder = Recorder([])
    decision = run(uncertain_application(), recorder, monkeypatch, enabled=False)
    assert 41 <= decision.score <= 69
    assert recorder.calls == 0


def test_clean_application_out_of_band_never_calls_enrichment(monkeypatch):
    recorder = Recorder([positive("GITHUB_CORROBORATED")])
    decision = run(make_application(), recorder, monkeypatch)
    assert decision.score >= 70
    assert recorder.calls == 0


def test_no_resume_text_never_calls_enrichment(monkeypatch):
    recorder = Recorder([positive("GITHUB_CORROBORATED")])
    run(uncertain_application(), recorder, monkeypatch, resume_text=None)
    assert recorder.calls == 0


def test_positive_corroboration_lifts_uncertain_case(monkeypatch):
    base = run(uncertain_application(), Recorder([]), monkeypatch, enabled=False)
    recorder = Recorder([positive("IDENTITY_OIDC_VERIFIED"), positive("DOI_VERIFIED"), positive("GITHUB_CORROBORATED")])
    decision = run(uncertain_application(), recorder, monkeypatch)
    assert recorder.calls == 1
    assert decision.score == min(100, base.score + 15)
    assert decision.score > base.score


def test_negative_corroboration_adds_reason_and_blocks_bonus(monkeypatch):
    base = run(uncertain_application(), Recorder([]), monkeypatch, enabled=False)
    recorder = Recorder([negative("GITHUB_DATES_MISMATCH"), positive("GITHUB_CORROBORATED")])
    decision = run(uncertain_application(), recorder, monkeypatch)
    assert "GITHUB_DATES_MISMATCH" in {item.code for item in decision.reasons}
    assert decision.score < base.score
    assert decision.route != Route.PASS_TO_ATS


def test_connector_failure_keeps_original_decision(monkeypatch):
    base = run(uncertain_application(), Recorder([]), monkeypatch, enabled=False)
    decision = run(uncertain_application(), Recorder([], error=RuntimeError("down")), monkeypatch)
    assert decision.score == base.score
    assert decision.route == base.route
    assert [item.code for item in decision.reasons] == [item.code for item in base.reasons]


def test_integrity_reason_blocks_bonus(monkeypatch):
    from firewall.models import Reason

    extra = [Reason(code="RESUME_HIDDEN_TEXT_WHITE", severity="high", detail="x", weight=1)]
    store = InMemoryApplicationStore()
    recorder = Recorder([positive("IDENTITY_OIDC_VERIFIED"), positive("DOI_VERIFIED")])
    monkeypatch.setattr(engine, "enrich", recorder)
    monkeypatch.setenv("FIREWALL_ENRICH", "1")
    without = engine.evaluate(
        uncertain_application("a1"), make_job(), InMemoryApplicationStore(), Config(),
        extra_reasons=extra, resume_text="x", persist=False,
    )
    assert recorder.calls == 1
    base_score = 100 - 35 - 1
    assert without.score == base_score
