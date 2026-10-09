from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

MODEL_NAME = "tiny.en"
MODEL_DIR_ENV = "FIREWALL_ASR_MODEL_DIR"
CACHE_DIR_ENV = "FIREWALL_ASR_CACHE_DIR"
CACHE_FOLDER = "models--Systran--faster-whisper-tiny.en"
TARGET_RATE = 16000


def _library_present() -> bool:
    try:
        return importlib.util.find_spec("faster_whisper") is not None
    except (ImportError, ValueError):
        return False


def _cache_root() -> Path:
    override = os.environ.get(CACHE_DIR_ENV, "").strip()
    if override:
        return Path(override)
    try:
        from huggingface_hub.constants import HF_HUB_CACHE

        return Path(HF_HUB_CACHE)
    except Exception:
        return Path(os.path.expanduser("~/.cache/huggingface/hub"))


def model_location() -> str | None:
    configured = os.environ.get(MODEL_DIR_ENV, "").strip()
    if configured:
        path = Path(configured)
        return str(path) if path.is_dir() else None
    snapshots = _cache_root() / CACHE_FOLDER / "snapshots"
    try:
        if snapshots.is_dir() and any(snapshots.iterdir()):
            return MODEL_NAME
    except OSError:
        return None
    return None


def asr_available() -> bool:
    return _library_present() and model_location() is not None


class FasterWhisperAdapter:
    def __init__(self, location: str) -> None:
        self._location = location
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(self._location, device="cpu", compute_type="int8", local_files_only=True)
        return self._model

    def transcribe(self, samples: Any, sample_rate: int) -> str:
        import numpy as np

        audio = np.asarray(samples, dtype=np.float32)
        if sample_rate != TARGET_RATE and len(audio) > 1:
            target = int(len(audio) * TARGET_RATE / sample_rate)
            audio = np.interp(np.linspace(0, len(audio) - 1, target), np.arange(len(audio)), audio).astype(np.float32)
        segments, _ = self._load().transcribe(audio, language="en", beam_size=1, vad_filter=False)
        return " ".join(segment.text.strip() for segment in segments)[:600]


def default_adapter() -> FasterWhisperAdapter | None:
    if not _library_present():
        return None
    location = model_location()
    return FasterWhisperAdapter(location) if location is not None else None
