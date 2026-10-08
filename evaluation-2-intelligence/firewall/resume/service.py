from __future__ import annotations

import re
import unicodedata

from pydantic import BaseModel, Field

from firewall.models import Candidate, JobRequirements, ResumeAgreement
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
    agreement: ResumeAgreement


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


def view_agreement(text_all: str, text_visible: str) -> ResumeAgreement:
    ats_tokens = set(re.findall(r"\w+", unicodedata.normalize("NFKC", text_all).casefold()))
    visible_tokens = set(re.findall(r"\w+", unicodedata.normalize("NFKC", text_visible).casefold()))
    if not ats_tokens and not visible_tokens:
        return ResumeAgreement(score=0.0, label="No comparable text")
    score = round(len(ats_tokens & visible_tokens) / len(ats_tokens | visible_tokens) * 100, 2)
    if score >= 90:
        label = "High agreement"
    elif score >= 70:
        label = "Moderate agreement"
    else:
        label = "Low agreement"
    return ResumeAgreement(score=score, label=label)


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
        agreement=view_agreement(extracted.text_all, extracted.text_visible),
    )
