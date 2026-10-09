from __future__ import annotations

import re

from firewall.identity.challenge import DIGIT_WORDS

MAX_TRANSCRIPT_CHARS = 300
MAX_TOKENS = 120
NON_WORD = re.compile(r"[^a-z0-9]+")


def tokens(text: str) -> list[str]:
    lowered = text.lower()
    spaced = "".join(f" {DIGIT_WORDS[int(ch)]} " if ch.isdigit() and ch.isascii() else ch for ch in lowered)
    return [part for part in NON_WORD.sub(" ", spaced).split() if part][:MAX_TOKENS]


def clean(text: str) -> str:
    return "".join(ch if ch.isprintable() else " " for ch in text).strip()


def longest_common(left: list[str], right: list[str]) -> int:
    previous = [0] * (len(right) + 1)
    for item in left:
        current = [0]
        for index, other in enumerate(right, start=1):
            current.append(previous[index - 1] + 1 if item == other else max(previous[index], current[index - 1]))
        previous = current
    return previous[-1]


def match(expected_sentence: str, expected_digits: list[str], spoken: str) -> tuple[float, bool]:
    wanted = tokens(expected_sentence)
    heard = tokens(spoken)
    ratio = longest_common(wanted, heard) / len(wanted) if wanted else 0.0
    said = [word for word in heard if word in DIGIT_WORDS]
    exact = said == expected_digits or said == expected_digits * 2
    return round(ratio, 2), exact
