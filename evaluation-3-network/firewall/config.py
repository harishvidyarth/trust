from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


DEFAULT_WEIGHTS = {
    "DUP_EMAIL": 22,
    "DUP_PHONE": 20,
    "DUP_RESUME_NEAR": 32,
    "DUP_SAME_JOB": 40,
    "VELOCITY_HIGH": 30,
    "NETWORK_BURST": 12,
    "IDENTITY_DEVICE_ROTATION": 35,
    "FAST_SUBMIT": 12,
    "PASTE_BULK": 18,
    "TEMPLATE_REUSE": 20,
    "QUAL_MISSING_MUST_HAVE": 35,
    "QUAL_UNDER_EXPERIENCE": 20,
    "TIMELINE_INVALID": 35,
    "TIMELINE_OVERLAP": 20,
}


@dataclass(frozen=True)
class Config:
    pass_min: int = 70
    review_max: int = 40
    velocity_window_seconds: int = 60
    velocity_limit: int = 10
    identity_velocity_limit: int = 6
    identity_device_rotation_limit: int = 4
    identity_device_window_seconds: int = 3600
    duplicate_similarity_threshold: float = 0.75
    min_shingles_for_similarity: int = 8
    min_session_seconds: float = 30.0
    paste_ratio_threshold: float = 0.90
    template_reuse_limit: int = 3
    overlap_months: int = 2
    max_future_end_months: int = 12
    claimed_experience_tolerance_years: float = 1.0
    qual_pass_coverage: float = 0.6
    weights: Mapping[str, int] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))

    def __post_init__(self) -> None:
        if not 0 <= self.review_max < self.pass_min <= 100:
            raise ValueError("routing thresholds must satisfy 0 <= review_max < pass_min <= 100")
        if self.velocity_window_seconds <= 0 or self.velocity_limit <= 0 or self.identity_velocity_limit <= 0:
            raise ValueError("velocity window and limit must be positive")
        if not 0 <= self.duplicate_similarity_threshold <= 1:
            raise ValueError("duplicate similarity threshold must be between 0 and 1")
        if self.min_shingles_for_similarity <= 0:
            raise ValueError("minimum shingles must be positive")
        if self.max_future_end_months < 0:
            raise ValueError("maximum future end months must be non-negative")
        if not 0 <= self.qual_pass_coverage <= 1:
            raise ValueError("qualification pass coverage must be between 0 and 1")
        copied_weights = dict(self.weights)
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in copied_weights.values()):
            raise ValueError("weights must be non-negative integers")
        object.__setattr__(self, "weights", MappingProxyType(copied_weights))

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> Config:
        allowed = {item.name for item in fields(cls)}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"unknown config keys: {', '.join(sorted(unknown))}")
        data = dict(values)
        if "weights" in data:
            data["weights"] = {**DEFAULT_WEIGHTS, **data["weights"]}
        return cls(**data)


def load_config(values: Mapping[str, Any] | None = None) -> Config:
    if values is not None:
        return Config.from_dict(values)
    config_path = os.getenv("FIREWALL_CONFIG")
    if not config_path:
        return Config()
    with Path(config_path).open(encoding="utf-8") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, dict):
        raise ValueError("FIREWALL_CONFIG must contain a JSON object")
    return Config.from_dict(loaded)
