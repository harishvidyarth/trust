from __future__ import annotations

import math
import struct
from typing import Any, Protocol

MODEL_NAME = "heuristic-v1"
MAX_AUDIO_BYTES = 2_621_440
MAX_SECONDS = 15.0
MIN_RATE = 8000
MAX_RATE = 48000
MIN_SAMPLES = 1600
MAX_CHUNKS = 256
MAX_PITCH_FRAMES = 400
FRAME_SIZE = 512
VOICING_THRESHOLD = 0.30
MIN_VOICED_FRAMES = 8
WAVE_FORMAT_PCM = 1
WAVE_FORMAT_EXTENSIBLE = 0xFFFE

THRESHOLDS: dict[str, float] = {
    "human_score_target": 0.70,
    "human_min_rms_variation": 0.20,
    "human_min_jitter": 0.004,
    "human_min_centroid_std": 40.0,
    "human_min_temporal_variation": 0.008,
    "human_max_spectral_repetition": 0.92,
    "human_min_duration": 0.40,
    "human_min_shimmer": 0.10,
    "human_w_rms_variation": 0.20,
    "human_w_jitter": 0.20,
    "human_w_centroid": 0.15,
    "human_w_temporal": 0.15,
    "human_w_spectral": 0.10,
    "human_w_duration": 0.10,
    "human_w_shimmer": 0.10,
    "risk_decision_threshold": 0.50,
    "risk_jitter_critical": 0.0025,
    "risk_rms_variation_critical": 0.12,
    "risk_spectral_repetition_critical": 0.955,
    "risk_temporal_variation_critical": 0.004,
    "risk_shimmer_critical": 0.04,
    "synthetic_w_pitch": 0.30,
    "synthetic_w_envelope": 0.25,
    "synthetic_w_spectrum": 0.20,
    "synthetic_w_temporal": 0.15,
    "synthetic_w_shimmer": 0.10,
    "replay_decision_threshold": 0.70,
    "replay_w_abrupt": 0.40,
    "replay_w_envelope": 0.25,
    "replay_w_spectrum": 0.20,
    "replay_w_margin": 0.15,
    "risk_playback_margin_critical": 0.15,
}

MESSAGES = {
    "too_large": "The recording is too large. Please keep it under 2.5 megabytes.",
    "too_long": "The recording is too long. Please keep it under 15 seconds.",
    "bad_rate": "The recording uses an unsupported sample rate.",
    "unsupported_format": "The recording must be a 16 bit WAV file with one or two channels.",
    "invalid_wav": "We could not read that recording.",
    "too_short": "The recording is too short to check.",
    "no_speech": "No speech was found in the recording.",
    "error": "The recording could not be checked.",
    "unclear": "The recording did not give clear evidence either way.",
    "human": "The recording shows natural variation in loudness and pitch.",
    "synthetic": "The recording has very even pitch and loudness which is typical of synthetic speech.",
    "replay": "The recording starts abruptly and stays very even which can happen when audio is played back.",
}

REJECT_CODES = frozenset({"too_large", "too_long", "bad_rate", "unsupported_format", "invalid_wav"})


class WavError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AsrAdapter(Protocol):
    def transcribe(self, samples: Any, sample_rate: int) -> str: ...


def _read(fmt: str, data: bytes, offset: int) -> tuple[Any, ...]:
    size = struct.calcsize(fmt)
    if offset < 0 or offset + size > len(data):
        raise WavError("invalid_wav")
    return struct.unpack_from(fmt, data, offset)


def parse_wav(data: bytes) -> tuple[Any, int]:
    import numpy as np

    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise WavError("invalid_wav")
    data = bytes(data)
    if len(data) > MAX_AUDIO_BYTES:
        raise WavError("too_large")
    if len(data) < 12 or data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise WavError("invalid_wav")
    position = 12
    fmt: tuple[int, int, int, int] | None = None
    span: tuple[int, int] | None = None
    for _ in range(MAX_CHUNKS):
        if position + 8 > len(data):
            break
        chunk_id = data[position : position + 4]
        (size,) = _read("<I", data, position + 4)
        body = position + 8
        if chunk_id == b"fmt " and fmt is None:
            if size < 16:
                raise WavError("invalid_wav")
            tag, channels, rate, _byte_rate, _align, bits = _read("<HHIIHH", data, body)
            if tag == WAVE_FORMAT_EXTENSIBLE:
                if size < 40:
                    raise WavError("invalid_wav")
                (tag,) = _read("<H", data, body + 24)
            fmt = (tag, channels, rate, bits)
        elif chunk_id == b"data" and span is None:
            available = len(data) - body
            length = min(size, available)
            span = (body, length)
        position = body + size + (size & 1)
        if fmt is not None and span is not None:
            break
    if fmt is None or span is None:
        raise WavError("invalid_wav")
    tag, channels, rate, bits = fmt
    if tag != WAVE_FORMAT_PCM or bits != 16 or channels not in (1, 2):
        raise WavError("unsupported_format")
    if rate < MIN_RATE or rate > MAX_RATE:
        raise WavError("bad_rate")
    offset, length = span
    length -= length % (2 * channels)
    frames = length // (2 * channels)
    if frames / rate > MAX_SECONDS:
        raise WavError("too_long")
    samples = np.frombuffer(data[offset : offset + length], dtype="<i2").astype(np.float64) / 32768.0
    if channels == 2:
        samples = samples.reshape(-1, 2).mean(axis=1)
    return samples, rate


def _frames(audio: Any, size: int) -> Any:
    count = max(1, len(audio) // size)
    return audio[: count * size].reshape(count, size)


def _pitch(audio: Any, rate: int, rms_floor: float) -> dict[str, Any]:
    import numpy as np

    frame_len = max(1024, int(rate * 0.064))
    min_lag = max(2, int(rate / 400))
    max_lag = min(int(rate / 60), frame_len - 1)
    result: dict[str, Any] = {"jitter": None, "shimmer": None, "voiced": 0, "tracked": 0}
    if len(audio) <= frame_len or max_lag <= min_lag:
        return result
    base_hop = max(1, int(rate * 0.008))
    span = len(audio) - frame_len
    hop = max(base_hop, math.ceil(span / MAX_PITCH_FRAMES))
    starts = np.arange(0, span, hop)[:MAX_PITCH_FRAMES]
    index = starts[:, None] + np.arange(frame_len)[None, :]
    frames = audio[index]
    frame_rms = np.sqrt(np.mean(frames**2, axis=1))
    loud = frame_rms >= rms_floor
    frames = frames[loud]
    result["tracked"] = int(frames.shape[0])
    if frames.shape[0] == 0:
        return result
    frames = frames - frames.mean(axis=1, keepdims=True)
    energy = np.einsum("ij,ij->i", frames, frames)
    keep = energy > 0
    peaks_all = np.max(np.abs(frames), axis=1)
    frames = frames[keep]
    energy = energy[keep]
    if frames.shape[0] == 0:
        return result
    size = 1 << (2 * frame_len - 1).bit_length()
    spectrum = np.fft.rfft(frames, n=size, axis=1)
    corr = np.fft.irfft(spectrum * np.conj(spectrum), n=size, axis=1)[:, : max_lag + 1]
    search = corr[:, min_lag:max_lag]
    offsets = np.argmax(search, axis=1)
    rows = np.arange(search.shape[0])
    peak_values = search[rows, offsets]
    voiced = (peak_values / energy) >= VOICING_THRESHOLD
    periods: list[float] = []
    for row in np.nonzero(voiced)[0]:
        offset = int(offsets[row])
        lag = float(min_lag + offset)
        line = search[row]
        if 0 < offset < line.shape[0] - 1:
            left, mid, right = line[offset - 1], line[offset], line[offset + 1]
            denom = left - 2 * mid + right
            if abs(denom) > 1e-12:
                lag += float(0.5 * (left - right) / denom)
        periods.append(lag)
    result["voiced"] = len(periods)
    if len(periods) >= MIN_VOICED_FRAMES:
        values = np.array(periods)
        mean_period = float(np.mean(values))
        inliers = values[(values > 0.6 * mean_period) & (values < 1.6 * mean_period)]
        if len(inliers) >= 3:
            result["jitter"] = float(np.mean(np.abs(np.diff(inliers))) / max(float(np.mean(inliers)), 1e-9))
        else:
            result["jitter"] = float(np.mean(np.abs(np.diff(values))) / max(mean_period, 1e-9))
        if peaks_all.shape[0] >= MIN_VOICED_FRAMES:
            result["shimmer"] = float(np.std(peaks_all) / max(float(np.mean(peaks_all)), 1e-9))
    return result


def extract_features(audio: Any, rate: int) -> dict[str, Any]:
    import numpy as np
    from scipy.fft import dct

    duration = len(audio) / rate
    frames = _frames(audio, FRAME_SIZE)
    rms = np.sqrt(np.mean(frames**2, axis=1))
    rms_floor = max(0.005, float(np.percentile(rms, 20)))
    active = rms > rms_floor
    if not np.any(active):
        return {"duration": duration, "has_speech": False}
    count = frames.shape[0]
    magnitudes = np.abs(np.fft.rfft(frames * np.hanning(FRAME_SIZE), axis=1))
    freqs = np.fft.rfftfreq(FRAME_SIZE, 1.0 / rate)
    total = magnitudes.sum(axis=1)
    total[total == 0] = 1e-10
    centroids = (magnitudes * freqs).sum(axis=1) / total
    log_magnitudes = np.log(magnitudes + 1e-10)
    mfccs = dct(log_magnitudes, type=2, axis=1)[:, :13]
    rms_mean = float(np.mean(rms))
    rms_variation = float(np.std(rms) / max(rms_mean, 1e-10))
    if count > 1:
        norms = np.linalg.norm(magnitudes, axis=1, keepdims=True)
        norms[norms == 0] = 1
        unit = magnitudes / norms
        repetition = float(np.mean(np.sum(unit[1:] * unit[:-1], axis=1)))
    else:
        repetition = 0.0
    quarter = max(1, count // 4)
    quarters = []
    for step in range(4):
        start = step * quarter
        end = start + quarter if step < 3 else count
        if start < count:
            quarters.append(float(np.mean(rms[start:end])))
    temporal = float(np.std(quarters)) if quarters else 0.0
    first = int(np.argmax(active))
    last = count - int(np.argmax(active[::-1])) - 1
    start_ratio = first / max(count, 1)
    end_ratio = (count - last) / max(count, 1)
    hf = freqs > 4000
    hf_ratio = float(np.mean(magnitudes[:, hf].sum(axis=1) / (total + 1e-10))) if hf.any() else 0.0
    pitch = _pitch(audio, rate, rms_floor)
    voiced_ratio = min(1.0, pitch["voiced"] / max(pitch["tracked"], 1))
    features = {
        "duration": duration,
        "active_duration": int(np.sum(active)) * FRAME_SIZE / rate,
        "has_speech": True,
        "rms_variation": rms_variation,
        "centroid_std": float(np.std(centroids)),
        "spectral_repetition": repetition,
        "temporal_variation": temporal,
        "start_ratio": float(start_ratio),
        "end_ratio": float(end_ratio),
        "hf_ratio": hf_ratio,
        "jitter": pitch["jitter"],
        "shimmer": pitch["shimmer"],
        "voiced_ratio": float(voiced_ratio),
        "mfcc_spread": float(np.mean(np.std(mfccs[:, 1:], axis=0))),
    }
    for key, value in features.items():
        if isinstance(value, float) and not math.isfinite(value):
            features[key] = 0.0
    return features


def _blank(code: str, reason_code: str | None = None) -> dict[str, Any]:
    return {
        "verdict": "UNKNOWN",
        "human_score": 0.0,
        "synthetic_risk": 0.0,
        "replay_risk": 0.0,
        "confidence": 0.0,
        "indicators": {},
        "signals": {"human": [], "synthetic": [], "replay": []},
        "reason_code": reason_code or code,
        "reason": MESSAGES[code],
        "duration_s": 0.0,
        "model": MODEL_NAME,
        "is_real_model": False,
    }


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(float(value), digits)


def classify(features: dict[str, Any], thresholds: dict[str, float] | None = None) -> dict[str, Any]:
    t = {**THRESHOLDS, **(thresholds or {})}
    if not features.get("has_speech", False):
        result = _blank("no_speech")
        result["duration_s"] = round(float(features.get("duration", 0.0)), 2)
        return result
    jitter = features["jitter"]
    shimmer = features["shimmer"]
    human = {
        "natural_amplitude_variation": (features["rms_variation"] > t["human_min_rms_variation"], t["human_w_rms_variation"]),
        "natural_pitch_variation": (jitter is not None and jitter > t["human_min_jitter"], t["human_w_jitter"]),
        "formant_movement": (features["centroid_std"] > t["human_min_centroid_std"], t["human_w_centroid"]),
        "temporal_dynamics": (features["temporal_variation"] > t["human_min_temporal_variation"], t["human_w_temporal"]),
        "spectral_change": (features["spectral_repetition"] < t["human_max_spectral_repetition"], t["human_w_spectral"]),
        "sufficient_duration": (features["active_duration"] > t["human_min_duration"], t["human_w_duration"]),
        "amplitude_irregularity": (shimmer is not None and shimmer > t["human_min_shimmer"], t["human_w_shimmer"]),
    }
    synthetic = {
        "machine_like_pitch_periodicity": (jitter is not None and jitter < t["risk_jitter_critical"], t["synthetic_w_pitch"]),
        "constant_energy_envelope": (features["rms_variation"] < t["risk_rms_variation_critical"], t["synthetic_w_envelope"]),
        "static_spectrum": (features["spectral_repetition"] > t["risk_spectral_repetition_critical"], t["synthetic_w_spectrum"]),
        "flat_temporal_dynamics": (features["temporal_variation"] < t["risk_temporal_variation_critical"], t["synthetic_w_temporal"]),
        "uniform_waveform_amplitude": (shimmer is not None and shimmer < t["risk_shimmer_critical"], t["synthetic_w_shimmer"]),
    }
    margin = min(features["start_ratio"], features["end_ratio"])
    replay = {
        "abrupt_playback_start": (margin < t["risk_playback_margin_critical"], t["replay_w_abrupt"]),
        "constant_energy_envelope": (features["rms_variation"] < t["risk_rms_variation_critical"], t["replay_w_envelope"]),
        "static_spectrum": (features["spectral_repetition"] > t["risk_spectral_repetition_critical"], t["replay_w_spectrum"]),
        "no_acoustic_margin": (margin <= 0.0, t["replay_w_margin"]),
    }

    def total(group: dict[str, tuple[bool, float]]) -> float:
        return sum(weight for fired, weight in group.values() if fired)

    def weight_sum(group: dict[str, tuple[bool, float]]) -> float:
        return sum(weight for _, weight in group.values())

    human_ratio = total(human) / weight_sum(human)
    synthetic_ratio = total(synthetic) / weight_sum(synthetic)
    replay_risk = total(replay)
    human_ok = human_ratio >= t["human_score_target"]
    synthetic_hit = synthetic_ratio >= t["risk_decision_threshold"]
    replay_hit = replay_risk >= t["replay_decision_threshold"]
    if human_ok and not synthetic_hit:
        verdict, confidence, code = "HUMAN", min(0.99, 0.50 + 0.50 * human_ratio), "human"
    elif synthetic_hit and not human_ok:
        verdict, confidence, code = "SYNTHETIC", min(0.95, 0.55 + 0.45 * synthetic_ratio), "synthetic"
    elif replay_hit and not human_ok:
        verdict, confidence, code = "REPLAY", min(0.95, 0.55 + 0.45 * replay_risk), "replay"
    else:
        verdict, code = "UNKNOWN", "unclear"
        confidence = min(0.60, max(human_ratio, synthetic_ratio, replay_risk))
    indicators = {
        "rms_variation": _round(features["rms_variation"], 4),
        "jitter": _round(jitter, 5),
        "shimmer": _round(shimmer, 4),
        "centroid_std_hz": _round(features["centroid_std"], 1),
        "temporal_variation": _round(features["temporal_variation"], 5),
        "spectral_repetition": _round(features["spectral_repetition"], 4),
        "voiced_ratio": _round(features["voiced_ratio"], 3),
        "active_duration_s": _round(features["active_duration"], 2),
        "start_ratio": _round(features["start_ratio"], 4),
        "end_ratio": _round(features["end_ratio"], 4),
        "high_frequency_ratio": _round(features["hf_ratio"], 4),
        "mfcc_spread": _round(features["mfcc_spread"], 3),
    }
    return {
        "verdict": verdict,
        "human_score": round(human_ratio, 2),
        "synthetic_risk": round(synthetic_ratio, 2),
        "replay_risk": round(replay_risk, 2),
        "confidence": round(confidence, 2),
        "indicators": indicators,
        "signals": {
            "human": [name for name, (fired, _) in human.items() if fired],
            "synthetic": [name for name, (fired, _) in synthetic.items() if fired],
            "replay": [name for name, (fired, _) in replay.items() if fired],
        },
        "reason_code": code,
        "reason": MESSAGES[code],
        "duration_s": round(float(features["duration"]), 2),
        "model": MODEL_NAME,
        "is_real_model": False,
    }


def analyze_wav(data: bytes, thresholds: dict[str, float] | None = None) -> dict[str, Any]:
    try:
        audio, rate = parse_wav(data)
        if len(audio) < max(MIN_SAMPLES, rate // 10):
            result = _blank("too_short")
            result["duration_s"] = round(len(audio) / rate, 2)
            return result
        return classify(extract_features(audio, rate), thresholds)
    except WavError as error:
        return _blank(error.code)
    except Exception:
        return _blank("error")


def decode_for_asr(data: bytes) -> tuple[Any, int] | None:
    try:
        return parse_wav(data)
    except Exception:
        return None
