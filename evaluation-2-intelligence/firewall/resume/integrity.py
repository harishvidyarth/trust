from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from firewall.models import JobRequirements
from firewall.resume.extract import ExtractedResume, HiddenSpan


DEFAULT_WEIGHTS = {
    "RESUME_HIDDEN_TEXT": 0,
    "RESUME_PROMPT_INJECTION": 0,
    "RESUME_KEYWORD_STUFFING": 0,
    "RESUME_PARSE_DIVERGENCE": 0,
    "RESUME_HIDDEN_NEAR_WHITE": 8,
    "RESUME_HIDDEN_TINY_FONT": 6,
    "RESUME_HIDDEN_OUTSIDE_BOUNDS": 8,
    "RESUME_HIDDEN_ZERO_WIDTH": 8,
    "RESUME_HIDDEN_OVERLAPPING_DUPLICATE": 7,
    "RESUME_HIDDEN_VANISHED_DOCX": 9,
    "RESUME_INJECTION_INSTRUCTION_PHRASE": 15,
    "RESUME_INJECTION_ROLE_PLAY_MARKER": 12,
    "RESUME_INJECTION_IGNORE_PREVIOUS": 15,
    "RESUME_INJECTION_SCREENER_ADDRESSED": 10,
    "RESUME_INJECTION_HIDDEN_LOCATION": 15,
    "RESUME_STUFFING_OVERALL_DENSITY": 8,
    "RESUME_STUFFING_CONCENTRATED_LINE": 7,
    "RESUME_STUFFING_REPEATED_SKILL_BLOCK": 6,
    "RESUME_STUFFING_UNSUPPORTED_SKILLS": 5,
    "RESUME_DIVERGENCE_LOW": 6,
    "RESUME_DIVERGENCE_MEDIUM": 12,
    "RESUME_DIVERGENCE_HIGH": 20,
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
HIDDEN_REASON_CODES = {
    "near-white text": "RESUME_HIDDEN_NEAR_WHITE",
    "font size below 2pt": "RESUME_HIDDEN_TINY_FONT",
    "text outside page bounds": "RESUME_HIDDEN_OUTSIDE_BOUNDS",
    "text in off-page text box": "RESUME_HIDDEN_OUTSIDE_BOUNDS",
    "zero-width text": "RESUME_HIDDEN_ZERO_WIDTH",
    "overlapping duplicate text": "RESUME_HIDDEN_OVERLAPPING_DUPLICATE",
    "vanished text": "RESUME_HIDDEN_VANISHED_DOCX",
}
INJECTION_CHECKS = (
    (
        "RESUME_INJECTION_IGNORE_PREVIOUS",
        "critical",
        (
            "ignorepreviousinstructions",
            "ignoreallinstructions",
            "disregardprevious",
            "disregardallinstructions",
            "disregardthesystemprompt",
            "disregardinstructions",
        ),
    ),
    (
        "RESUME_INJECTION_ROLE_PLAY_MARKER",
        "high",
        (
            "youareanaiats",
            "youareanaiassistant",
            "youareanats",
            "systemprompt",
            "developerprompt",
            "actasanai",
            "actasanats",
        ),
    ),
    (
        "RESUME_INJECTION_SCREENER_ADDRESSED",
        "high",
        (
            "rankthiscandidate",
            "rankmefirst",
            "recommendhiring",
            "recommendthiscandidate",
            "givethehighestscore",
            "assignhighestscore",
            "highestpossiblescore",
            "shortlistthiscandidate",
        ),
    ),
    (
        "RESUME_INJECTION_INSTRUCTION_PHRASE",
        "high",
        (
            "followtheseinstructions",
            "followtheinstructions",
            "obeytheseinstructions",
            "ignorepreviousinstructions",
            "ignoreallinstructions",
            "disregardprevious",
            "disregardallinstructions",
            "disregardthesystemprompt",
            "systemprompt",
            "developerprompt",
            "rankthiscandidate",
            "rankmefirst",
            "recommendhiring",
            "givethehighestscore",
            "shortlistthiscandidate",
        ),
    ),
)
PARENT_CODES = {
    "HIDDEN": "RESUME_HIDDEN_TEXT",
    "INJECTION": "RESUME_PROMPT_INJECTION",
    "STUFFING": "RESUME_KEYWORD_STUFFING",
    "DIVERGENCE": "RESUME_PARSE_DIVERGENCE",
}


def _normalise(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).translate(CONFUSABLES).casefold()
    value = "".join(char for char in value if not unicodedata.combining(char) and char not in "\u200b\u200c\u200d\ufeff")
    return re.sub(r"[^a-z0-9]+", "", value)


def _quote(value: str, limit: int = 120) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip().replace('"', "'")
    return cleaned if len(cleaned) <= limit else f"{cleaned[: limit - 1]}…"


def _reason(code: str, severity: str, detail: str) -> dict[str, str | int]:
    return {"code": code, "severity": severity, "detail": detail, "weight": DEFAULT_WEIGHTS[code]}


def _matches_injection(text: str) -> bool:
    compact = _normalise(text)
    return any(marker in compact for _, _, markers in INJECTION_CHECKS for marker in markers)


def contains_injection(text: str) -> bool:
    return _matches_injection(text)


def _keyword_occurrences(text: str, terms: list[str]) -> tuple[int, dict[str, int]]:
    lowered = unicodedata.normalize("NFKC", text).casefold()
    counts: dict[str, int] = {}
    for term in terms:
        cleaned = term.strip().casefold()
        if not cleaned:
            continue
        counts[cleaned] = len(re.findall(rf"(?<!\w){re.escape(cleaned)}(?!\w)", lowered))
    return sum(counts.values()), counts


def _job_terms(job: JobRequirements) -> list[str]:
    return list(dict.fromkeys(term.strip().casefold() for term in job.must_have_skills + job.nice_to_have if term.strip()))


def _densest_line(text: str, terms: list[str]) -> tuple[int, str, int, float]:
    best = (0, "", 0, 0.0)
    for line_number, line in enumerate(text.splitlines(), start=1):
        occurrences, _ = _keyword_occurrences(line, terms)
        tokens = max(1, len(re.findall(r"\b[\w+#.-]+\b", line)))
        density = occurrences / tokens
        if (occurrences, density) > (best[2], best[3]):
            best = (line_number, line, occurrences, density)
    return best


def _overall_density_reason(text: str, terms: list[str]) -> dict[str, str | int] | None:
    occurrences, counts = _keyword_occurrences(text, terms)
    tokens = max(1, len(re.findall(r"\b[\w+#.-]+\b", text)))
    threshold = max(12, len(terms) * 2)
    if occurrences < threshold or occurrences / tokens < 0.14 or max(counts.values(), default=0) < 4:
        return None
    line_number, line, _, _ = _densest_line(text, terms)
    repeated = sorted(((term, count) for term, count in counts.items() if count >= 3), key=lambda item: -item[1])
    summary = ", ".join(f"{term}×{count}" for term, count in repeated[:4])
    return _reason(
        "RESUME_STUFFING_OVERALL_DENSITY",
        "medium",
        f"Job-keyword density is {occurrences}/{tokens} tokens ({summary}) near ATS-view line {line_number}: \"{_quote(line)}\".",
    )


def _concentrated_line_reason(text: str, terms: list[str]) -> dict[str, str | int] | None:
    line_number, line, occurrences, density = _densest_line(text, terms)
    if occurrences < 10 or density < 0.55:
        return None
    return _reason(
        "RESUME_STUFFING_CONCENTRATED_LINE",
        "medium",
        f"ATS-view line {line_number} is {density:.0%} job keywords ({occurrences} matches): \"{_quote(line)}\".",
    )


def _repeated_skill_block_reason(text: str, terms: list[str]) -> dict[str, str | int] | None:
    signatures: dict[tuple[str, ...], tuple[int, str]] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        _, counts = _keyword_occurrences(line, terms)
        matched = tuple(term for term, count in counts.items() if count)
        total = sum(counts.values())
        tokens = max(1, len(re.findall(r"\b[\w+#.-]+\b", line)))
        if len(matched) < 3:
            continue
        if total >= len(matched) * 3:
            return _reason(
                "RESUME_STUFFING_REPEATED_SKILL_BLOCK",
                "medium",
                f"A skill sequence repeats within ATS-view line {line_number}: \"{_quote(line)}\".",
            )
        if matched in signatures and total / tokens >= 0.6:
            first_line, first_text = signatures[matched]
            return _reason(
                "RESUME_STUFFING_REPEATED_SKILL_BLOCK",
                "medium",
                f"The same skill block appears at ATS-view lines {first_line} and {line_number}: \"{_quote(first_text)}\".",
            )
        if total / tokens >= 0.6:
            signatures[matched] = (line_number, line)
    return None


def _unsupported_skills_reason(text: str, terms: list[str]) -> dict[str, str | int] | None:
    lines = text.splitlines()
    start = None
    skill_parts: list[str] = []
    support_parts: list[str] = []
    heading = re.compile(r"^\s*(?:technical\s+skills|skills|core\s+competencies)\s*:?(.*)$", re.IGNORECASE)
    section = re.compile(
        r"^\s*(?:(?:professional\s+)?experience|employment|work\s+history|projects?|education|certifications?)\s*:?.*$",
        re.IGNORECASE,
    )
    for index, line in enumerate(lines):
        match = heading.match(line)
        if match:
            start = index + 1
            if match.group(1).strip():
                skill_parts.append(match.group(1).strip())
            continue
        if start is not None and index >= start:
            if section.match(line):
                support_parts.extend(lines[index + 1 :])
                break
            skill_parts.append(line)
    if start is None:
        return None
    listed = [term for term in terms if _keyword_occurrences("\n".join(skill_parts), [term])[0]]
    unsupported = [term for term in listed if not _keyword_occurrences("\n".join(support_parts), [term])[0]]
    if len(listed) < 3 or len(unsupported) < 3 or len(unsupported) / len(listed) < 0.75:
        return None
    evidence = next((line for line in skill_parts if line.strip()), " ".join(listed))
    return _reason(
        "RESUME_STUFFING_UNSUPPORTED_SKILLS",
        "low",
        f"Skills listed near visible line {start} have no experience/project support ({', '.join(unsupported[:6])}): \"{_quote(evidence)}\".",
    )


def _hidden_reasons(spans: list[HiddenSpan]) -> list[dict[str, str | int]]:
    grouped: dict[str, list[HiddenSpan]] = {}
    for span in spans:
        code = HIDDEN_REASON_CODES.get(span.reason)
        if code:
            grouped.setdefault(code, []).append(span)
    reasons: list[dict[str, str | int]] = []
    for code, matching in grouped.items():
        labels = sorted({span.reason for span in matching})
        evidence = " | ".join(_quote(span.text, 70) for span in matching[:2])
        severity = "high" if code in {"RESUME_HIDDEN_VANISHED_DOCX", "RESUME_HIDDEN_OUTSIDE_BOUNDS"} else "medium"
        reasons.append(_reason(code, severity, f"Hidden span classified as {', '.join(labels)}: \"{evidence}\"."))
    return reasons


def _injection_sources(extracted: ExtractedResume) -> list[tuple[str, str, bool]]:
    sources = [
        (f"visible line {line_number}", line, False)
        for line_number, line in enumerate(extracted.text_visible.splitlines(), start=1)
        if line.strip()
    ]
    sources.extend((f"hidden span ({span.reason})", span.text, True) for span in extracted.hidden_spans)
    return sources


def _injection_reasons(extracted: ExtractedResume) -> list[dict[str, str | int]]:
    sources = _injection_sources(extracted)
    reasons: list[dict[str, str | int]] = []
    hidden_match: tuple[str, str, bool] | None = None
    for code, severity, markers in INJECTION_CHECKS:
        match = next((source for source in sources if any(marker in _normalise(source[1]) for marker in markers)), None)
        if match:
            location, evidence, hidden = match
            reasons.append(_reason(code, severity, f"Instruction-like language at {location}: \"{_quote(evidence)}\"."))
            if hidden and hidden_match is None:
                hidden_match = match
    if hidden_match is None:
        hidden_match = next((source for source in sources if source[2] and _matches_injection(source[1])), None)
    if hidden_match:
        location, evidence, _ = hidden_match
        reasons.append(
            _reason(
                "RESUME_INJECTION_HIDDEN_LOCATION",
                "critical",
                f"Injection language is concealed at {location}: \"{_quote(evidence)}\".",
            )
        )
    return reasons


def _stuffing_reasons(extracted: ExtractedResume, job: JobRequirements) -> list[dict[str, str | int]]:
    terms = _job_terms(job)
    if not terms:
        return []
    checks = (
        _overall_density_reason(extracted.text_all, terms),
        _concentrated_line_reason(extracted.text_all, terms),
        _repeated_skill_block_reason(extracted.text_all, terms),
        _unsupported_skills_reason(extracted.text_visible, terms),
    )
    return [reason for reason in checks if reason is not None]


def _divergence_reason(extracted: ExtractedResume) -> dict[str, str | int] | None:
    hidden = " ".join(span.text for span in extracted.hidden_spans).strip()
    hidden_words = re.findall(r"\w+", hidden)
    if len(hidden_words) < 2 or len(hidden) < 10:
        return None
    visible = re.sub(r"\s+", " ", extracted.text_visible).strip()
    all_text = re.sub(r"\s+", " ", extracted.text_all).strip()
    if not visible:
        delta = 1.0
    else:
        delta = max(len(hidden) / max(1, len(visible)), 1 - SequenceMatcher(None, visible, all_text).ratio())
    if delta < 0.03:
        return None
    if delta >= 0.35:
        code, severity = "RESUME_DIVERGENCE_HIGH", "high"
    elif delta >= 0.12:
        code, severity = "RESUME_DIVERGENCE_MEDIUM", "medium"
    else:
        code, severity = "RESUME_DIVERGENCE_LOW", "low"
    return _reason(
        code,
        severity,
        f"ATS-only text differs from the human-visible view by {delta:.0%} at hidden content: \"{_quote(hidden)}\".",
    )


def _family_for(code: str) -> str | None:
    if code.startswith("RESUME_HIDDEN_"):
        return "HIDDEN"
    if code.startswith("RESUME_INJECTION_"):
        return "INJECTION"
    if code.startswith("RESUME_STUFFING_"):
        return "STUFFING"
    if code.startswith("RESUME_DIVERGENCE_"):
        return "DIVERGENCE"
    return None


def _append_aliases(reasons: list[dict[str, str | int]]) -> None:
    active = {_family_for(str(reason["code"])) for reason in reasons}
    for family, parent in PARENT_CODES.items():
        if family in active:
            children = ", ".join(str(reason["code"]) for reason in reasons if _family_for(str(reason["code"])) == family)
            reasons.append(_reason(parent, "info", f"Compatibility alias for {family} checks: {children}."))


def detect_integrity_reasons(extracted: ExtractedResume, job: JobRequirements) -> list[dict[str, str | int]]:
    reasons = _hidden_reasons(extracted.hidden_spans)
    reasons.extend(_injection_reasons(extracted))
    reasons.extend(_stuffing_reasons(extracted, job))
    divergence = _divergence_reason(extracted)
    if divergence:
        reasons.append(divergence)
    _append_aliases(reasons)
    return reasons
