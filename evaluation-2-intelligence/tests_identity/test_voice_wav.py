from __future__ import annotations

import os
import struct
import time

import numpy as np
import pytest

from firewall.identity import voice
from firewall.identity.voice import WavError, analyze_wav, parse_wav
from tests_identity.audio import RATE, chunk, fmt_body, noise, pcm, riff, sine, speech_like, wav_bytes

RESULT_KEYS = {
    "verdict",
    "human_score",
    "synthetic_risk",
    "replay_risk",
    "confidence",
    "indicators",
    "signals",
    "reason_code",
    "reason",
    "duration_s",
    "model",
    "is_real_model",
}


def code_of(data) -> str:
    return analyze_wav(data)["reason_code"]


def test_valid_file_returns_full_shape():
    result = analyze_wav(wav_bytes(speech_like()))
    assert set(result) == RESULT_KEYS
    assert result["model"] == "heuristic-v1" and result["is_real_model"] is False
    assert result["verdict"] in {"HUMAN", "SYNTHETIC", "REPLAY", "UNKNOWN"}


def test_odd_sized_chunk_before_data_is_word_aligned():
    samples = sine(1.0)
    data = riff(chunk(b"fmt ", fmt_body()), chunk(b"LIST", b"abc"), chunk(b"data", pcm(samples)))
    parsed, rate = parse_wav(data)
    assert rate == RATE and len(parsed) == len(samples)


def test_missing_pad_byte_after_odd_chunk_does_not_raise():
    data = riff(chunk(b"fmt ", fmt_body()), chunk(b"LIST", b"abc", pad=False), chunk(b"data", pcm(sine(1.0))))
    assert isinstance(analyze_wav(data), dict)


def test_every_truncation_of_a_header_is_safe():
    data = wav_bytes(sine(0.5))
    for cut in range(0, 80):
        result = analyze_wav(data[:cut])
        assert result["verdict"] == "UNKNOWN"
        assert result["reason_code"] in {"invalid_wav", "too_short"}


def test_huge_declared_riff_and_data_sizes_are_clipped_to_real_length():
    samples = sine(1.0)
    data = riff(chunk(b"fmt ", fmt_body()), chunk(b"data", pcm(samples), declared=0x7FFFFFF0), declared=0xFFFFFFFF)
    parsed, _ = parse_wav(data)
    assert len(parsed) == len(samples)


def test_streaming_data_size_is_clipped():
    samples = sine(1.0)
    data = riff(chunk(b"fmt ", fmt_body()), chunk(b"data", pcm(samples), declared=0xFFFFFFFF))
    parsed, _ = parse_wav(data)
    assert len(parsed) == len(samples)
    assert analyze_wav(data)["reason_code"] != "error"


def test_odd_byte_length_is_trimmed():
    body = pcm(sine(1.0)) + b"\x01"
    data = riff(chunk(b"fmt ", fmt_body()), chunk(b"data", body, declared=len(body)))
    parsed, _ = parse_wav(data)
    assert len(parsed) == RATE


def test_stereo_is_averaged_to_mono():
    left = sine(1.0, freq=200)
    stereo = np.column_stack([left, left]).reshape(-1)
    data = riff(chunk(b"fmt ", fmt_body(channels=2)), chunk(b"data", pcm(stereo)))
    parsed, _ = parse_wav(data)
    assert len(parsed) == RATE
    assert np.allclose(parsed, left, atol=1e-3)


@pytest.mark.parametrize("bits", [8, 24, 32])
def test_other_bit_depths_are_refused_plainly(bits):
    data = riff(chunk(b"fmt ", fmt_body(bits=bits)), chunk(b"data", bytes(RATE * bits // 8)))
    result = analyze_wav(data)
    assert result["reason_code"] == "unsupported_format" and result["verdict"] == "UNKNOWN"


def test_float_and_three_channels_are_refused():
    float_data = riff(chunk(b"fmt ", fmt_body(tag=3, bits=32)), chunk(b"data", bytes(4000)))
    assert code_of(float_data) == "unsupported_format"
    three = riff(chunk(b"fmt ", fmt_body(channels=3)), chunk(b"data", bytes(60000)))
    assert code_of(three) == "unsupported_format"


def test_size_limit():
    big = wav_bytes(np.zeros(1_400_000))
    assert len(big) > voice.MAX_AUDIO_BYTES
    assert code_of(big) == "too_large"
    assert len(wav_bytes(np.zeros(1_200_000))) < voice.MAX_AUDIO_BYTES + 1


def test_duration_limit():
    assert code_of(wav_bytes(np.zeros(RATE * 16))) == "too_long"
    assert code_of(wav_bytes(np.zeros(int(RATE * 14.9)))) != "too_long"


@pytest.mark.parametrize("rate", [7999, 48001, 100, 0])
def test_sample_rate_limits(rate):
    data = riff(chunk(b"fmt ", fmt_body(rate=rate)), chunk(b"data", bytes(32000)))
    assert code_of(data) == "bad_rate"


@pytest.mark.parametrize("rate", [8000, 44100, 48000])
def test_edge_rates_are_accepted(rate):
    assert code_of(wav_bytes(speech_like(2.0, rate=rate), rate=rate)) not in {"bad_rate", "error", "invalid_wav"}


def test_short_audio_is_too_short_not_error():
    assert code_of(wav_bytes(sine(0.05))) == "too_short"


def test_silence_is_unknown():
    result = analyze_wav(wav_bytes(np.zeros(RATE * 2)))
    assert result["verdict"] == "UNKNOWN"


def test_many_zero_chunks_do_not_loop_forever():
    junk = b"".join(chunk(b"JUNK", b"") for _ in range(5000))
    data = riff(chunk(b"fmt ", fmt_body()), junk, chunk(b"data", pcm(sine(1.0))))
    start = time.monotonic()
    assert isinstance(analyze_wav(data), dict)
    assert time.monotonic() - start < 5


def test_fmt_too_small_or_cut_off():
    assert code_of(riff(chunk(b"fmt ", b"\x01\x00"), chunk(b"data", bytes(4000)))) == "invalid_wav"
    assert code_of(b"RIFF" + struct.pack("<I", 100) + b"WAVEfmt " + struct.pack("<I", 16) + b"\x01\x00") == "invalid_wav"


def test_data_before_fmt_is_found():
    samples = sine(1.0)
    data = riff(chunk(b"data", pcm(samples)), chunk(b"fmt ", fmt_body()))
    assert len(parse_wav(data)[0]) == len(samples)


def test_missing_data_chunk():
    assert code_of(riff(chunk(b"fmt ", fmt_body()))) == "invalid_wav"


@pytest.mark.parametrize("seed", range(12))
def test_garbage_never_raises(seed):
    rng = np.random.default_rng(seed)
    blob = bytes(rng.integers(0, 256, size=int(rng.integers(0, 4000)), dtype=np.uint8))
    assert analyze_wav(blob)["verdict"] == "UNKNOWN"
    assert analyze_wav(b"RIFF" + blob)["verdict"] == "UNKNOWN"
    assert analyze_wav(b"RIFF\x00\x00\x00\x00WAVE" + blob)["verdict"] == "UNKNOWN"
    assert analyze_wav(os.urandom(64))["verdict"] == "UNKNOWN"


@pytest.mark.parametrize("value", [None, "text", 5, b"", bytearray(b"RIFF"), [1, 2]])
def test_wrong_types_never_raise(value):
    assert analyze_wav(value)["verdict"] == "UNKNOWN"


def test_parse_wav_raises_only_wav_errors():
    with pytest.raises(WavError):
        parse_wav(b"nope")


def test_analysis_is_deterministic():
    data = wav_bytes(speech_like())
    assert analyze_wav(data) == analyze_wav(data)


def test_long_audio_is_decimated_and_fast():
    data = wav_bytes(speech_like(14.5))
    start = time.monotonic()
    result = analyze_wav(data)
    assert time.monotonic() - start < 10
    assert result["reason_code"] != "error"


def test_thresholds_are_exposed_and_overridable():
    assert voice.THRESHOLDS["human_score_target"] == 0.70
    assert voice.THRESHOLDS["risk_decision_threshold"] == 0.50
    assert voice.THRESHOLDS["replay_decision_threshold"] == 0.70
    data = wav_bytes(speech_like())
    base = analyze_wav(data)
    strict = analyze_wav(data, {"human_score_target": 1.5})
    assert base["verdict"] == "HUMAN" and strict["verdict"] != "HUMAN"
    assert voice.THRESHOLDS["human_score_target"] == 0.70


def test_noise_does_not_crash_without_pitch():
    result = analyze_wav(wav_bytes(noise()))
    assert result["indicators"]["jitter"] is None
