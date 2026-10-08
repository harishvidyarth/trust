from __future__ import annotations

import re
from typing import Iterable


PROFILES = ("general", "finance", "hardware", "sales_ops_design")

KEYWORDS = {
    "finance": (
        "finance", "financial", "accountant", "accounting", "audit", "auditor", "tax", "treasury", "banking",
        "investment", "equity", "ca", "cfa", "acca", "icai", "chartered", "compliance", "risk", "credit",
    ),
    "hardware": (
        "hardware", "embedded", "firmware", "vlsi", "fpga", "rtl", "pcb", "electronics", "electrical", "robotics",
        "mechatronics", "iot", "verilog", "vhdl", "asic", "microcontroller", "arduino", "stm32",
    ),
    "sales_ops_design": (
        "sales", "operations", "ops", "design", "designer", "ux", "ui", "marketing", "brand", "creative",
        "account executive", "business development", "customer success", "graphic", "illustrator", "figma",
    ),
}
TITLE_WEIGHT = 3


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.casefold()))


def _hits(keywords: Iterable[str], text: str, tokens: set[str]) -> int:
    folded = text.casefold()
    return sum(1 for keyword in keywords if (keyword in tokens if " " not in keyword else keyword in folded))


def select_profile(title: str, must_have_skills: Iterable[str] = (), nice_to_have: Iterable[str] = ()) -> str:
    title_tokens = _tokens(title)
    skill_text = " ".join([*must_have_skills, *nice_to_have])
    skill_tokens = _tokens(skill_text)
    scores = {
        profile: TITLE_WEIGHT * _hits(keywords, title, title_tokens) + _hits(keywords, skill_text, skill_tokens)
        for profile, keywords in KEYWORDS.items()
    }
    best = max(scores.values())
    if best == 0:
        return "general"
    leaders = [profile for profile in KEYWORDS if scores[profile] == best]
    return leaders[0] if len(leaders) == 1 else "general"
