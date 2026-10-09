from __future__ import annotations

import io
import struct
import wave

import numpy as np

RATE = 16000


def pcm(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()


def wav_bytes(samples: np.ndarray, rate: int = RATE, channels: int = 1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm(samples))
    return buffer.getvalue()


def chunk(name: bytes, body: bytes, declared: int | None = None, pad: bool = True) -> bytes:
    size = len(body) if declared is None else declared
    out = name + struct.pack("<I", size) + body
    if pad and len(body) % 2:
        out += b"\x00"
    return out


def fmt_body(rate: int = RATE, channels: int = 1, bits: int = 16, tag: int = 1) -> bytes:
    align = channels * bits // 8
    return struct.pack("<HHIIHH", tag, channels, rate, rate * align, align, bits)


def riff(*chunks: bytes, declared: int | None = None) -> bytes:
    body = b"WAVE" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body) if declared is None else declared) + body


def seconds(duration: float, rate: int = RATE) -> np.ndarray:
    return np.arange(int(rate * duration)) / rate


def sine(duration: float = 3.0, rate: int = RATE, freq: float = 200.0, amp: float = 0.5) -> np.ndarray:
    return amp * np.sin(2 * np.pi * freq * seconds(duration, rate))


def speech_like(duration: float = 4.0, rate: int = RATE, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = seconds(duration, rate)
    envelope = np.abs(np.sin(2 * np.pi * 3 * t)) ** 2 * (0.5 + 0.5 * np.sin(2 * np.pi * 0.7 * t))
    f0 = 120 + 20 * np.sin(2 * np.pi * 0.5 * t) + rng.normal(0, 2, len(t)).cumsum() * 0.02
    phase = 2 * np.pi * np.cumsum(f0) / rate
    voiced = sum(np.sin(k * phase) / k for k in range(1, 12))
    return 0.3 * envelope * voiced * (1 + 0.1 * rng.normal(size=len(t))) + 0.02 * rng.normal(size=len(t))


def noise(duration: float = 3.0, rate: int = RATE, seed: int = 2) -> np.ndarray:
    return 0.1 * np.random.default_rng(seed).normal(size=int(rate * duration))
