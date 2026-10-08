from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from firewall.models import JobRequirements
from firewall.resume.extract import ExtractedResume


DEFAULT_WEIGHTS = {
    "RESUME_HIDDEN_TEXT": 30,
    "RESUME_PROMPT_INJECTION": 45,
    "RESUME_KEYWORD_STUFFING": 25,
    "RESUME_PARSE_DIVERGENCE": 20,
}

CONFUSABLES = str.maketrans(
    {
        "а": "a",
        "е": "e",
        "і": "i",
        "о": "o",
        "р": "p",
        "с": "c",
        "х": "x",
        "у": "y",
        "Α": "a",
        "Ι": "i",
        "Ο": "o",
    }
)
INJECTION_MARKERS = (
    "ignorepreviousinstructions",
    "ignoreallinstructions",
    "youareanai",
    "youareanats",
    "rankthiscandidate",
    "highestscore",
    "recommendhiring",
    "systemprompt",
    "disregard",
)


def _normalise(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).translate(CONFUSABLES).casefold()
    value = "".join(char for char in value if not unicodedata.combining(char) and char not in "\u200b\u200c\u200d\ufeff")
    return re.sub(r"[^a-z0-9]+", "", value)


def _reason(code: str, severity: str, detail: str) -> dict[str, str | int]:
    return {"code": code, "severity": severity, "detail": detail, "weight": DEFAULT_WEIGHTS[code]}


def _contains_injection(text: str) -> bool:
    compact = _normalise(text)
    return any(marker in compact for marker in INJECTION_MARKERS)


def _keyword_occurrences(text: str, terms: list[str]) -> tuple[int, dict[str, int]]:
    lowered = unicodedata.normalize("NFKC", text).casefold()
    counts: dict[str, int] = {}
    for term in terms:
        cleaned = term.strip().casefold()
        if not cleaned:
            continue
        counts[cleaned] = len(re.findall(rf"(?<!\w){re.escape(cleaned)}(?!\w)", lowered))
    return sum(counts.values()), counts


def _is_keyword_stuffing(extracted: ExtractedResume, job: JobRequirements) -> tuple[bool, str]:
    terms = list(dict.fromkeys(job.must_have_skills + job.nice_to_have))
    if not terms:
        return False, ""
    text = extracted.text_all
    total_occurrences, counts = _keyword_occurrences(text, terms)
    token_count = max(1, len(re.findall(r"\b[\w+#.-]+\b", text)))
    max_repeat = max(counts.values(), default=0)

    concentrated_line = False
    for line in text.splitlines():
        line_occurrences, _ = _keyword_occurrences(line, terms)
        line_tokens = max(1, len(re.findall(r"\b[\w+#.-]+\b", line)))
        if line_occurrences >= 10 and line_occurrences / line_tokens >= 0.55:
            concentrated_line = True
            break

    threshold = max(12, len(terms) * 2)
    overall_dense = total_occurrences >= threshold and total_occurrences / token_count >= 0.14 and max_repeat >= 4
    if concentrated_line or overall_dense:
        repeated = sorted(((term, count) for term, count in counts.items() if count >= 3), key=lambda item: -item[1])
        summary = ", ".join(f"{term}×{count}" for term, count in repeated[:4])
        return True, f"Job keywords are repeated at abnormal density ({summary or f'{total_occurrences} matches'})."
    return False, ""


def _has_material_divergence(extracted: ExtractedResume) -> bool:
    hidden = " ".join(span.text for span in extracted.hidden_spans).strip()
    if not hidden:
        return False
    hidden_words = re.findall(r"\w+", hidden)
    if len(hidden_words) < 2 or len(hidden) < 10:
        return False
    visible = re.sub(r"\s+", " ", extracted.text_visible).strip()
    all_text = re.sub(r"\s+", " ", extracted.text_all).strip()
    if not visible:
        return True
    similarity = SequenceMatcher(None, visible, all_text).ratio()
    return len(hidden) / max(1, len(visible)) >= 0.03 or similarity < 0.96


def detect_integrity_reasons(extracted: ExtractedResume, job: JobRequirements) -> list[dict[str, str | int]]:
    reasons: list[dict[str, str | int]] = []
    hidden_text = "\n".join(span.text for span in extracted.hidden_spans)
    if extracted.hidden_spans:
        descriptions = sorted({span.reason for span in extracted.hidden_spans})
        reasons.append(
            _reason("RESUME_HIDDEN_TEXT", "high", f"Content hidden from human view: {', '.join(descriptions)}.")
        )

    hidden_injection = _contains_injection(hidden_text)
    visible_injection = _contains_injection(extracted.text_visible)
    if hidden_injection or visible_injection:
        location = "hidden content" if hidden_injection else "visible content"
        severity = "critical" if hidden_injection else "high"
        reasons.append(_reason("RESUME_PROMPT_INJECTION", severity, f"Instruction-like language found in {location}."))

    stuffing, detail = _is_keyword_stuffing(extracted, job)
    if stuffing:
        reasons.append(_reason("RESUME_KEYWORD_STUFFING", "high", detail))

    if _has_material_divergence(extracted):
        reasons.append(
            _reason(
                "RESUME_PARSE_DIVERGENCE",
                "high",
                "The ATS text layer contains material content absent from the human-visible resume.",
            )
        )
    return reasons
