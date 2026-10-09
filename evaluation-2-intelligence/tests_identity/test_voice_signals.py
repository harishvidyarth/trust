from __future__ import annotations

import numpy as np

from firewall.identity.voice import analyze_wav
from tests_identity.audio import RATE, noise, seconds, sine, speech_like, wav_bytes


def verdict(samples, rate=RATE) -> str:
    return analyze_wav(wav_bytes(samples, rate=rate))["verdict"]


def test_pure_sine_is_not_human():
    assert verdict(sine(3.0)) != "HUMAN"


def test_constant_amplitude_tone_with_vibrato_is_not_human():
    t = seconds(3.0)
    tone = 0.4 * np.sin(2 * np.pi * (150 * t + 3 * np.sin(2 * np.pi * 0.3 * t)))
    assert verdict(tone) != "HUMAN"


def test_sine_is_flagged_synthetic_with_even_signals():
    result = analyze_wav(wav_bytes(sine(3.0)))
    assert result["verdict"] == "SYNTHETIC"
    assert "constant_energy_envelope" in result["signals"]["synthetic"]


def test_speech_like_noise_burst_is_not_synthetic():
    assert verdict(speech_like(4.0)) != "SYNTHETIC"
    assert verdict(speech_like(4.0, seed=7)) != "SYNTHETIC"


def test_plain_noise_modulated_like_speech_is_not_synthetic():
    t = seconds(4.0)
    envelope = np.abs(np.sin(2 * np.pi * 3 * t)) ** 2 * (0.5 + 0.5 * np.sin(2 * np.pi * 0.7 * t))
    assert verdict(envelope * noise(4.0) * 3) != "SYNTHETIC"


def test_loudness_does_not_change_the_verdict():
    quiet = analyze_wav(wav_bytes(speech_like(4.0) * 0.3))
    loud = analyze_wav(wav_bytes(speech_like(4.0) * 1.0))
    assert quiet["verdict"] == loud["verdict"]


def test_scores_are_bounded_and_fixed_wording():
    result = analyze_wav(wav_bytes(speech_like()))
    for key in ("human_score", "synthetic_risk", "replay_risk", "confidence"):
        assert 0.0 <= result[key] <= 1.0
    assert not any(char in result["reason"] for char in "-()[]:;")
