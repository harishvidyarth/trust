from __future__ import annotations

import re
import statistics
from typing import Any

BUZZWORDS = (
    "results-driven", "passionate", "passion for", "dynamic", "innovative", "cutting-edge", "synergy",
    "proven track record", "detail-oriented", "team player", "self-motivated", "highly motivated",
    "spearheaded", "leveraged", "orchestrated", "streamlined", "facilitated", "championed", "pioneered",
    "revolutionized", "harnessed", "fostered", "seamless", "robust", "world-class", "best-in-class",
    "go-getter", "thought leader", "value-add", "stakeholders",
)
STRONG_VERBS = (
    "spearheaded", "leveraged", "orchestrated", "streamlined", "facilitated", "championed", "pioneered",
    "revolutionized", "harnessed", "utilized", "fostered", "drove", "optimized", "elevated", "cultivated",
)
GENERIC_OPENERS = (
    "results-driven", "passionate", "dynamic professional", "motivated professional", "detail-oriented",
    "proven track record", "highly motivated", "dedicated professional",
)
WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-\+\.#']*")
PERCENT = re.compile(r"\b\d+(?:\.\d+)?\s?%")
VERSION = re.compile(r"\b\d+(?:\.\d+)+\b")
TEAM = re.compile(r"\b(?:team of|squad of|group of)\s+\d+\b", re.I)
TRIPLE = re.compile(r"\b[a-z]+(?:ed|ing|s)?,\s+[a-z]+(?:ed|ing|s)?,?\s+and\s+[a-z]+", re.I)
DISCLAIMER = "Estimate only, not proof of AI authorship."
MIN_WORDS_FOR_CONFIDENCE = 150


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def _lines(text: str) -> list[str]:
    return [line.strip(" -•*\t") for line in text.splitlines() if line.strip()]


def _bullets(text: str) -> list[str]:
    return [line for line in _lines(text) if len(WORD.findall(line)) >= 6]


def _round_share(values: list[str]) -> float:
    numbers = []
    for value in values:
        match = re.search(r"\d+(?:\.\d+)?", value)
        if match:
            numbers.append(float(match.group()))
    if not numbers:
        return 0.0
    return sum(1 for number in numbers if number >= 10 and number % 5 == 0) / len(numbers)


def _label(score: float) -> str:
    if score < 35:
        return "Low"
    if score < 65:
        return "Medium"
    return "High"


def analyze_style(text: str) -> dict[str, Any]:
    words = WORD.findall(text)
    word_count = len(words)
    lowered = text.lower()
    bullets = _bullets(text)
    signals: list[dict[str, Any]] = []
    human_like: list[str] = []

    if word_count:
        hits = sum(lowered.count(term) for term in BUZZWORDS)
        density = hits / word_count
        score = _clamp(density / 0.08 * 100)
        signals.append({"name": "Buzzword density", "score": round(score), "detail": f"{hits} buzzword hits in {word_count} words"})

    if bullets:
        strong = sum(1 for line in bullets if line.split()[0].lower().strip(",.") in STRONG_VERBS)
        score = _clamp(strong / len(bullets) * 100 * 1.5)
        signals.append({"name": "Strong-verb openings", "score": round(score), "detail": f"{strong} of {len(bullets)} bullets"})

        with_metric = [line for line in bullets if PERCENT.search(line) or re.search(r"\b\d{2,}\b", line)]
        formula = len(with_metric) / len(bullets)
        signals.append({"name": "Bullets ending in a metric", "score": round(_clamp(formula * 100)), "detail": f"{len(with_metric)} of {len(bullets)} bullets carry a number"})

        percents = PERCENT.findall(text)
        if percents:
            share = _round_share(percents)
            signals.append({"name": "Round metrics", "score": round(share * 100), "detail": f"{len(percents)} percentages, {round(share * 100)}% are round"})

        triples = sum(1 for line in bullets if TRIPLE.search(line))
        signals.append({"name": "Groups of three", "score": round(_clamp(triples / len(bullets) * 100 * 2)), "detail": f"{triples} bullets use an X, Y and Z list"})

    if word_count:
        specifics = len(VERSION.findall(text)) + len(TEAM.findall(text))
        for line in _lines(text):
            tokens = WORD.findall(line)
            specifics += sum(1 for token in tokens[1:] if token[:1].isupper() and token.lower() not in BUZZWORDS)
        per_hundred = specifics / word_count * 100
        score = _clamp(100 - per_hundred / 12 * 100)
        signals.append({"name": "Low specificity", "score": round(score), "detail": f"{specifics} named or numbered specifics, {per_hundred:.1f} per 100 words"})
        if VERSION.search(text) or TEAM.search(text):
            human_like.append("Tool versions or team sizes are named")

    if len(bullets) >= 4:
        lengths = [len(WORD.findall(line)) for line in bullets]
        mean = statistics.mean(lengths)
        variation = statistics.pstdev(lengths) / mean if mean else 0
        score = _clamp((0.6 - variation) / 0.45 * 100)
        signals.append({"name": "Sentence uniformity", "score": round(score), "detail": f"bullet length varies by {variation:.0%}"})
        if variation > 0.45:
            human_like.append("Bullet lengths vary a lot, which is typical of hand-written text")

    opener = " ".join(_lines(text)[:6]).lower()
    if any(term in opener for term in GENERIC_OPENERS):
        signals.append({"name": "Generic opening summary", "score": 80, "detail": "Opening lines use a stock phrase"})

    if not signals:
        return {
            "score": None,
            "label": "Unavailable",
            "confidence": "low",
            "signals": [],
            "patterns_found": [],
            "human_like_signals": [],
            "word_count": word_count,
            "trust_weight": 0,
            "disclaimer": DISCLAIMER,
        }

    score = sum(item["score"] for item in signals) / len(signals)
    confidence = "low" if word_count < MIN_WORDS_FOR_CONFIDENCE or len(signals) < 4 else "medium"
    patterns = [item for item in signals if item["score"] >= 50]
    return {
        "score": round(score),
        "label": _label(score),
        "confidence": confidence,
        "signals": sorted(signals, key=lambda item: -item["score"]),
        "patterns_found": patterns,
        "human_like_signals": human_like,
        "word_count": word_count,
        "trust_weight": 0,
        "disclaimer": DISCLAIMER,
    }
