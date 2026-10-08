from __future__ import annotations

import re
import unicodedata

from pydantic import BaseModel, Field

from firewall.models import Candidate, JobRequirements
from firewall.resume.extract import HiddenSpan, extract_resume
from firewall.resume.integrity import detect_integrity_reasons
from firewall.resume.parse import parse_candidate


class ResumeAnalysis(BaseModel):
    candidate: Candidate
    reasons: list[dict[str, str | int]] = Field(default_factory=list)
    visible_text: str
    ats_view_text: str
    hidden_spans: list[HiddenSpan] = Field(default_factory=list)
    parsed_ok: bool


def _term_present(text: str, term: str) -> bool:
    normalised = unicodedata.normalize("NFKC", text).casefold()
    return bool(re.search(rf"(?<!\w){re.escape(term.strip().casefold())}(?!\w)", normalised))


def naive_ats_rank(text_all: str, job: JobRequirements) -> float:
    weighted_terms = [(term, 1.0) for term in job.must_have_skills] + [(term, 0.5) for term in job.nice_to_have]
    if not weighted_terms:
        return 0.0
    denominator = sum(weight for _, weight in weighted_terms)
    coverage = sum(weight for term, weight in weighted_terms if _term_present(text_all, term)) / denominator
    weighted_occurrences = 0.0
    lowered = unicodedata.normalize("NFKC", text_all).casefold()
    for term, weight in weighted_terms:
        count = len(re.findall(rf"(?<!\w){re.escape(term.strip().casefold())}(?!\w)", lowered))
        weighted_occurrences += count * weight
    frequency_bonus = min(25.0, weighted_occurrences / (denominator * 4) * 25)
    return round(min(100.0, coverage * 75 + frequency_bonus), 2)


def analyze_resume(data: bytes, filename: str, job: JobRequirements) -> ResumeAnalysis:
    extracted = extract_resume(data, filename)
    candidate = parse_candidate(extracted.text_visible)
    return ResumeAnalysis(
        candidate=candidate,
        reasons=detect_integrity_reasons(extracted, job),
        visible_text=extracted.text_visible,
        ats_view_text=extracted.text_all,
        hidden_spans=extracted.hidden_spans,
        parsed_ok=bool(extracted.text_visible.strip()) and "error" not in extracted.metadata,
    )
