from __future__ import annotations

import re
from datetime import date
from io import BytesIO

import pdfplumber
from pydantic import BaseModel, Field

from firewall.config import Config
from firewall.index_keys import normalize_name
from firewall.models import Candidate, Reason
from firewall.resume.parse import _normalise_date
from firewall.signals.common import reason
from firewall.signals.identity_links import jaro_winkler


LINKEDIN_WEIGHTS = {
    "LINKEDIN_EMPLOYER_MISSING": 6,
    "LINKEDIN_DATE_MISMATCH": 5,
    "LINKEDIN_TITLE_MISMATCH": 4,
}

MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
RANGE_RE = re.compile(
    rf"^(?P<start>{MONTH}\s+\d{{4}}|\d{{4}})\s*[-–—]\s*(?P<end>Present|{MONTH}\s+\d{{4}}|\d{{4}})(?:\s*\(.*\))?$",
    re.IGNORECASE,
)
EDU_RANGE_RE = re.compile(r"\(\s*(?P<start>\d{4})\s*[-–—]\s*(?P<end>\d{4})\s*\)")
SECTIONS = {
    "contact": "contact",
    "top skills": "top_skills",
    "skills": "skills",
    "languages": "languages",
    "certifications": "certifications",
    "summary": "summary",
    "experience": "experience",
    "education": "education",
    "honors-awards": "honors",
    "publications": "publications",
}
TOP_SKILLS_LIMIT = 3
DATE_TOLERANCE_MONTHS = 3
TITLE_SIMILARITY_FLOOR = 0.6


class LinkedInExperience(BaseModel):
    company: str
    title: str
    start: str
    end: str
    line: int


class LinkedInEducation(BaseModel):
    school: str
    detail: str = ""
    start: str | None = None
    end: str | None = None
    line: int


class LinkedInProfile(BaseModel):
    name: str = ""
    headline: str = ""
    location: str = ""
    skills: list[str] = Field(default_factory=list)
    experience: list[LinkedInExperience] = Field(default_factory=list)
    education: list[LinkedInEducation] = Field(default_factory=list)
    lines: list[str] = Field(default_factory=list)
    dropped: list[str] = Field(default_factory=list)


def pdf_text(data: bytes) -> str:
    pages: list[str] = []
    with pdfplumber.open(BytesIO(data)) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return "\n".join(pages)


def _clean_lines(text: str) -> list[str]:
    return [re.sub(r"\s+", " ", line).strip() for line in text.replace("\r", "\n").split("\n")]


def _section(line: str) -> str | None:
    return SECTIONS.get(line.strip().casefold())


def parse_text(text: str) -> LinkedInProfile:
    lines = _clean_lines(text)
    profile = LinkedInProfile(lines=lines)
    current = "profile"
    top_skill_count = 0
    profile_lines: list[str] = []
    experience_lines: list[tuple[int, str]] = []
    education_lines: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        if not line:
            continue
        section = _section(line)
        if section is not None:
            current = section
            top_skill_count = 0
            continue
        if current == "top_skills":
            if top_skill_count < TOP_SKILLS_LIMIT:
                profile.skills.append(line)
                top_skill_count += 1
                continue
            current = "profile"
        if current == "skills":
            profile.skills.append(line)
        elif current == "profile":
            profile_lines.append(line)
        elif current == "experience":
            experience_lines.append((index, line))
        elif current == "education":
            education_lines.append((index, line))
    if profile_lines:
        profile.name = profile_lines[0]
    if len(profile_lines) > 1:
        profile.headline = profile_lines[1]
    if len(profile_lines) > 2:
        profile.location = profile_lines[2]
    profile.experience = _experience(experience_lines)
    profile.education = _education(education_lines)
    return profile


def _experience(items: list[tuple[int, str]]) -> list[LinkedInExperience]:
    found: list[LinkedInExperience] = []
    for position, (index, line) in enumerate(items):
        match = RANGE_RE.match(line)
        if match is None or position < 2:
            continue
        title = items[position - 1][1]
        company = items[position - 2][1]
        if RANGE_RE.match(title) or RANGE_RE.match(company):
            continue
        found.append(
            LinkedInExperience(
                company=company,
                title=title,
                start=_normalise_date(match.group("start")),
                end=_normalise_date(match.group("end")),
                line=items[position - 2][0],
            )
        )
    return found


def _education(items: list[tuple[int, str]]) -> list[LinkedInEducation]:
    found: list[LinkedInEducation] = []
    pending: tuple[int, str] | None = None
    for index, line in items:
        match = EDU_RANGE_RE.search(line)
        if match is not None and pending is not None:
            detail = EDU_RANGE_RE.sub("", line).strip(" ·-")
            found.append(
                LinkedInEducation(
                    school=pending[1],
                    detail=detail,
                    start=match.group("start"),
                    end=match.group("end"),
                    line=pending[0],
                )
            )
            pending = None
        elif match is None:
            pending = (index, line)
    return found


def parse_linkedin_pdf(data: bytes) -> LinkedInProfile:
    text = pdf_text(data)
    return ground(parse_text(text), text)


def _squash(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def ground(profile: LinkedInProfile, source_text: str) -> LinkedInProfile:
    haystack = _squash(source_text)
    dropped: list[str] = list(profile.dropped)

    def keep(label: str, value: str | None) -> bool:
        if value is None or value == "":
            return True
        if _squash(value) in haystack:
            return True
        dropped.append(f"{label}={value}")
        return False

    grounded = profile.model_copy(deep=True)
    grounded.name = grounded.name if keep("name", grounded.name) else ""
    grounded.headline = grounded.headline if keep("headline", grounded.headline) else ""
    grounded.location = grounded.location if keep("location", grounded.location) else ""
    grounded.skills = [skill for skill in grounded.skills if keep("skill", skill)]
    grounded.experience = [
        item
        for item in grounded.experience
        if keep("experience.company", item.company) and keep("experience.title", item.title) and _date_in_text(item, haystack, dropped)
    ]
    grounded.education = [
        item for item in grounded.education if keep("education.school", item.school) and keep("education.detail", item.detail)
    ]
    grounded.dropped = dropped
    return grounded


def _date_in_text(item: LinkedInExperience, haystack: str, dropped: list[str]) -> bool:
    for label, value in (("start", item.start), ("end", item.end)):
        if value == "Present":
            if "present" not in haystack:
                dropped.append(f"experience.{label}={value}")
                return False
            continue
        year = value[:4]
        if year not in haystack:
            dropped.append(f"experience.{label}={value}")
            return False
    return True


def _months(value: str, today: date) -> int | None:
    if value == "Present":
        return today.year * 12 + today.month
    match = re.fullmatch(r"(\d{4})(?:-(\d{2}))?", value.strip())
    if match is None:
        return None
    return int(match.group(1)) * 12 + int(match.group(2) or 1)


def _company_match(first: str, second: str) -> bool:
    left = normalize_name(first)
    right = normalize_name(second)
    if not left or not right:
        return False
    return left == right or left in right or right in left or jaro_winkler(left, right) >= 0.93


def _title_tokens(value: str) -> set[str]:
    return set(normalize_name(value).split())


def _title_similarity(first: str, second: str) -> float:
    left = _title_tokens(first)
    right = _title_tokens(second)
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def _weight(config: Config | None, code: str) -> int:
    if config is not None and code in config.weights:
        return config.weights[code]
    return LINKEDIN_WEIGHTS[code]


def _make(config: Config | None, code: str, severity: str, detail: str) -> Reason:
    weight = _weight(config, code)
    if config is None:
        return Reason(code=code, severity=severity, detail=detail, weight=weight)
    return reason(config, code, severity, detail, weight)


def cross_check(
    profile: LinkedInProfile,
    candidate: Candidate,
    config: Config | None = None,
    today: date | None = None,
) -> list[Reason]:
    now = today or date.today()
    reasons: list[Reason] = []
    matched_linkedin: set[int] = set()
    for resume_index, resume_item in enumerate(candidate.experience):
        pairing = next(
            (
                (position, item)
                for position, item in enumerate(profile.experience)
                if _company_match(resume_item.company, item.company)
            ),
            None,
        )
        if pairing is None:
            reasons.append(
                _make(
                    config,
                    "LINKEDIN_EMPLOYER_MISSING",
                    "low",
                    f'Resume experience[{resume_index}] employer "{resume_item.company}" '
                    f'({resume_item.start} to {resume_item.end}) has no matching entry in the uploaded LinkedIn export.',
                )
            )
            continue
        position, item = pairing
        matched_linkedin.add(position)
        for label, resume_value, linkedin_value in (
            ("start", resume_item.start, item.start),
            ("end", resume_item.end, item.end),
        ):
            first = _months(_normalise_date(resume_value), now)
            second = _months(linkedin_value, now)
            if first is not None and second is not None and abs(first - second) > DATE_TOLERANCE_MONTHS:
                reasons.append(
                    _make(
                        config,
                        "LINKEDIN_DATE_MISMATCH",
                        "low",
                        f'Employer "{resume_item.company}" {label} date is "{resume_value}" on the resume '
                        f'(experience[{resume_index}]) but "{linkedin_value}" in the LinkedIn export (line {item.line + 1}), '
                        f"a gap of {abs(first - second)} months.",
                    )
                )
        if _title_similarity(resume_item.title, item.title) < TITLE_SIMILARITY_FLOOR:
            reasons.append(
                _make(
                    config,
                    "LINKEDIN_TITLE_MISMATCH",
                    "low",
                    f'Employer "{resume_item.company}" title is "{resume_item.title}" on the resume '
                    f'(experience[{resume_index}]) but "{item.title}" in the LinkedIn export (line {item.line + 2}).',
                )
            )
    for position, item in enumerate(profile.experience):
        if position in matched_linkedin:
            continue
        if not any(_company_match(item.company, resume_item.company) for resume_item in candidate.experience):
            reasons.append(
                _make(
                    config,
                    "LINKEDIN_EMPLOYER_MISSING",
                    "low",
                    f'LinkedIn export employer "{item.company}" ({item.start} to {item.end}, line {item.line + 1}) '
                    "is not listed in the resume experience.",
                )
            )
    return reasons
