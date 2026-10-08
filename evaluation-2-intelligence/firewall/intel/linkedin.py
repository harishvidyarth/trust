from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import date
from io import BytesIO
from itertools import islice

import pdfplumber
from pdfminer.pdfpage import PDFPage
from pdfplumber.page import Page
from pydantic import BaseModel, Field

from firewall.config import Config
from firewall.models import Candidate, Reason
from firewall.signals.common import reason
from firewall.signals.identity_links import jaro_winkler


LINKEDIN_WEIGHTS = {
    "LINKEDIN_EMPLOYER_MISSING": 6,
    "LINKEDIN_DATE_MISMATCH": 5,
    "LINKEDIN_TITLE_MISMATCH": 4,
}

EXTRA_EMPLOYER_WEIGHT = 1
MAX_PAGES = 10
MAX_TEXT_CHARS = 200_000
MAX_WORDS_PER_PAGE = 20_000
MAX_LINES = 6_000
TIME_LIMIT_SECONDS = 20.0
MIN_COLUMN_GAP = 9.0
MIN_COLUMN_WORDS = 3
TOP_SKILLS_LIMIT = 3
SIDEBAR_SKILL_CAP = 30
DATE_TOLERANCE_MONTHS = 3
TITLE_SIMILARITY_FLOOR = 0.6
WRAP_LEADING = 1.42
READ_MOST = 0.8
READ_SOME = 0.5

MONTH_NUMBERS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5,
    "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
    "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "mai": 5, "juni": 6, "juli": 7, "okt": 10, "dezember": 12, "dez": 12,
    "janvier": 1, "février": 2, "fevrier": 2, "févr": 2, "mars": 3, "avril": 4, "juin": 6, "juillet": 7, "août": 8,
    "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}
PRESENT_WORDS = ("present", "current", "now", "heute", "aujourd.hui", "actualidad", "actualmente", "presente", "en cours", "présent")

_MONTH_ALT = "|".join(sorted((re.escape(name) for name in MONTH_NUMBERS), key=len, reverse=True))
_PRESENT_ALT = "|".join(PRESENT_WORDS)
DATE_TOKEN = rf"(?:(?:{_MONTH_ALT})\.?\s+\d{{4}}|\d{{4}})"
RANGE_RE = re.compile(
    rf"^(?P<start>{DATE_TOKEN})\s*[-–—]\s*(?P<end>{_PRESENT_ALT}|{DATE_TOKEN})"
    r"(?P<tail>\s*(?:\(.*\)|·.*|\d+\s*(?:years?|yrs?|months?|mos?).*)?)$",
    re.IGNORECASE,
)
EDU_RANGE_RE = re.compile(
    rf"(?:^|[·,(\s])\s*\(?\s*(?P<start>{DATE_TOKEN})\s*[-–—]\s*(?P<end>{DATE_TOKEN})\s*\)?\s*$",
    re.IGNORECASE,
)
DURATION_RE = re.compile(
    r"^(?:less than a year|\d+\s*(?:years?|yrs?|ans?|jahre?|años?)(?:\s+\d+\s*(?:months?|mos?|mois|monate?|meses?))?"
    r"|\d+\s*(?:months?|mos?|mois|monate?|meses?))$",
    re.IGNORECASE,
)
FOOTER_RE = re.compile(r"^(?:page|seite|página|pagina)\s+\d+\s+(?:of|von|de|sur|di)\s+\d+$", re.IGNORECASE)
CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

HEADINGS = {
    "contact": "contact", "kontakt": "contact", "coordonnées": "contact", "contacto": "contact",
    "top skills": "top_skills", "top-kenntnisse": "top_skills", "principales compétences": "top_skills",
    "aptitudes principales": "top_skills",
    "skills": "skills", "kenntnisse": "skills", "compétences": "skills", "aptitudes": "skills",
    "languages": "languages", "sprachen": "languages", "langues": "languages", "idiomas": "languages",
    "certifications": "certifications", "licenses & certifications": "certifications",
    "licenses and certifications": "certifications", "zertifikate": "certifications", "certificaciones": "certifications",
    "summary": "summary", "about": "summary", "zusammenfassung": "summary", "résumé": "summary", "extracto": "summary",
    "experience": "experience", "berufserfahrung": "experience", "expérience": "experience", "experiencia": "experience",
    "education": "education", "ausbildung": "education", "formation": "education", "educación": "education",
    "honors-awards": "honors", "honors & awards": "honors", "honors and awards": "honors",
    "publications": "publications", "patents": "other", "projects": "other", "courses": "other",
    "test scores": "other", "organizations": "other", "volunteer experience": "other", "recommendations": "other",
    "interests": "other",
}
SIDEBAR_SECTIONS = {"contact", "top_skills", "skills", "languages", "certifications", "honors", "publications"}

COMPANY_SUFFIXES = {
    "pvt", "private", "ltd", "limited", "inc", "incorporated", "llc", "llp", "lp", "corp", "corporation", "co",
    "company", "gmbh", "ag", "sa", "sarl", "plc", "bv", "nv", "pty", "ltda", "the",
}
TITLE_ABBREVIATIONS = {
    "sr": "senior", "jr": "junior", "eng": "engineer", "engg": "engineer", "mgr": "manager", "dev": "developer",
    "swe": "software engineer", "sde": "software development engineer", "assoc": "associate", "asst": "assistant",
    "dir": "director", "vp": "vice president", "exec": "executive", "admin": "administrator",
}
TITLE_SYNONYMS = {"developer": "engineer", "programmer": "engineer", "engineers": "engineer", "managers": "manager"}
TITLE_STOPWORDS = {"and", "of", "the", "at", "for", "in", "ii", "iii", "iv", "i", "level", "a", "an"}


class LinkedInExperience(BaseModel):
    company: str
    title: str
    start: str
    end: str
    line: int
    title_line: int = -1
    page: int = 0
    raw_range: str = ""
    company_duration: str = ""


class LinkedInEducation(BaseModel):
    school: str
    detail: str = ""
    start: str | None = None
    end: str | None = None
    line: int
    page: int = 0


class LinkedInProfile(BaseModel):
    name: str = ""
    headline: str = ""
    location: str = ""
    summary: str = ""
    skills: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    experience: list[LinkedInExperience] = Field(default_factory=list)
    education: list[LinkedInEducation] = Field(default_factory=list)
    lines: list[str] = Field(default_factory=list)
    dropped: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    layout: str = "unknown"
    pages_read: int = 0
    sections: list[str] = Field(default_factory=list)
    experience_expected: int = 0
    education_expected: int = 0
    fields_found: int = 0
    fields_expected: int = 0
    confidence: float = 0.0
    confidence_message: str = ""


@dataclass
class _Line:
    text: str
    idx: int = 0
    page: int = 0
    bold: bool | None = None
    size: float | None = None
    x0: float = 0.0
    x1: float = 0.0
    top: float = 0.0


@dataclass
class _Layout:
    sidebar: list[_Line] = field(default_factory=list)
    main: list[_Line] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    pages_read: int = 0
    two_column: bool = False

    def text(self) -> str:
        main = [line.text for line in self.main]
        if not self.two_column:
            return "\n".join(main)
        return "\n".join([line.text for line in self.sidebar] + [""] + main)


def _strip_accents(value: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch))


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", CONTROL_RE.sub("", value)).strip()


def _clean_lines(text: str) -> list[str]:
    return [_clean_text(line) for line in text.replace("\r", "\n").split("\n")]


def _heading(line: str) -> str | None:
    return HEADINGS.get(line.strip().casefold())


def _is_range(line: str) -> bool:
    return RANGE_RE.match(line) is not None


def _is_duration(line: str) -> bool:
    return DURATION_RE.match(line) is not None


def _parse_date(value: str) -> str | None:
    cleaned = _clean_text(value).rstrip(".").casefold()
    if not cleaned:
        return None
    if re.fullmatch(_PRESENT_ALT, cleaned):
        return "Present"
    iso = re.fullmatch(r"(\d{4})[-/](\d{1,2})", cleaned)
    if iso is not None:
        month = int(iso.group(2))
        return f"{iso.group(1)}-{month:02d}" if 1 <= month <= 12 else None
    if re.fullmatch(r"\d{4}", cleaned):
        return cleaned
    named = re.fullmatch(r"([^\W\d_]+)\.?\s+(\d{4})", cleaned)
    if named is not None and named.group(1) in MONTH_NUMBERS:
        return f"{named.group(2)}-{MONTH_NUMBERS[named.group(1)]:02d}"
    return None


def _norm_date(value: str) -> str:
    return _parse_date(value) or _clean_text(value)


def _words_to_lines(words: list[dict], page_number: int) -> list[_Line]:
    if not words:
        return []
    ordered = sorted(words, key=lambda word: (word["top"], word["x0"]))
    rows: list[list[dict]] = []
    for word in ordered:
        height = max(word["bottom"] - word["top"], 1.0)
        if rows and abs(word["top"] - rows[-1][0]["top"]) <= 0.45 * height:
            rows[-1].append(word)
        else:
            rows.append([word])
    lines: list[_Line] = []
    for row in rows:
        row.sort(key=lambda word: word["x0"])
        text = _clean_text(" ".join(str(word["text"]) for word in row))
        if not text:
            continue
        weights: dict[tuple[bool, float], int] = {}
        for word in row:
            font = str(word.get("fontname") or "")
            bold = any(token in font.lower() for token in ("bold", "black", "heavy"))
            size = round(float(word.get("size") or 0.0) * 2) / 2
            weights[(bold, size)] = weights.get((bold, size), 0) + len(str(word["text"]))
        bold, size = max(weights.items(), key=lambda item: item[1])[0]
        lines.append(
            _Line(
                text=text,
                page=page_number,
                bold=bold,
                size=size or None,
                x0=min(word["x0"] for word in row),
                x1=max(word["x1"] for word in row),
                top=min(word["top"] for word in row),
            )
        )
    return lines


def _find_boundary(words: list[dict], page_width: float) -> float | None:
    total = len(words)
    if total < 2 * MIN_COLUMN_WORDS or page_width <= 0:
        return None
    size = int(page_width) + 2
    coverage = [0] * size
    for word in words:
        for cell in range(max(int(word["x0"]), 0), min(int(word["x1"]) + 1, size)):
            coverage[cell] += 1
    for limit in (0, 1, 2, 3):
        found = _boundary_at(words, coverage, limit, page_width)
        if found is not None:
            return found
    return None


def _boundary_at(words: list[dict], coverage: list[int], limit: int, page_width: float) -> float | None:
    total = len(words)
    size = len(coverage)
    low = int(0.12 * page_width)
    high = int(0.6 * page_width)
    best: tuple[float, float] | None = None
    cell = low
    while cell <= high:
        if coverage[cell] > limit:
            cell += 1
            continue
        start = cell
        while cell < size and coverage[cell] <= limit:
            cell += 1
        end = cell
        width = end - start
        center = (start + end) / 2
        if width < MIN_COLUMN_GAP or not (low <= center <= high):
            continue
        left = [word for word in words if word["x1"] <= start + 0.5]
        right = [word for word in words if word["x0"] >= end - 0.5]
        crossers = total - len(left) - len(right)
        if len(left) < MIN_COLUMN_WORDS or len(right) < MIN_COLUMN_WORDS or crossers > max(2, int(0.03 * total)):
            continue
        left_rows = {round(word["top"] / 3) for word in left}
        shared = sum(
            1
            for row in {round(word["top"] / 3) for word in right}
            if row in left_rows or row - 1 in left_rows or row + 1 in left_rows
        )
        if shared < MIN_COLUMN_WORDS:
            continue
        if best is None or width > best[0]:
            best = (width, center)
    return best[1] if best else None


def _first_word_width(text: str, size: float | None) -> float:
    word = text.split(" ", 1)[0]
    return len(word) * (size or 10.0) * 0.46


def _merge_wraps(lines: list[_Line], extent: float | None) -> list[_Line]:
    if len(lines) < 2:
        return lines
    left = min(line.x0 for line in lines)
    limit = max(line.x1 for line in lines)
    width = limit - left
    if width < 60:
        return lines
    if extent is not None:
        near = [line for line in lines if line.x1 >= limit - 0.06 * width]
        reaches_edge = limit >= extent - 0.12 * max(extent - left, 1.0)
        if len(near) < 3 and not reaches_edge:
            return lines
    out: list[_Line] = []
    for line in lines:
        previous = out[-1] if out else None
        if (
            previous is not None
            and previous.page == line.page
            and previous.x1 >= left + 0.45 * width
            and 0 < line.top - previous.top <= WRAP_LEADING * (previous.size or 10.0)
            and previous.x1 + _first_word_width(line.text, previous.size) > limit
            and len(previous.text) >= 18
            and _heading(previous.text) is None
            and _heading(line.text) is None
            and not _is_range(previous.text)
            and EDU_RANGE_RE.search(previous.text) is None
            and not _is_range(line.text)
            and not _is_duration(previous.text)
            and not _is_duration(line.text)
            and not re.match(r"^[•·\-*▪●]", line.text)
        ):
            previous.text = f"{previous.text} {line.text}"
            previous.x1 = line.x1
            previous.top = line.top
            continue
        out.append(replace(line))
    return out


def _read_layout(data: bytes) -> _Layout:
    layout = _Layout()
    if not isinstance(data, (bytes, bytearray)) or not data:
        layout.warnings.append("The file was empty.")
        return layout
    if b"%PDF" not in bytes(data[:1024]):
        layout.warnings.append("The file does not look like a PDF.")
        return layout
    deadline = time.monotonic() + TIME_LIMIT_SECONDS
    page_words: list[tuple[int, float, list[dict]]] = []
    try:
        with pdfplumber.open(BytesIO(bytes(data))) as pdf:
            for number, raw in enumerate(islice(PDFPage.create_pages(pdf.doc), MAX_PAGES + 1), start=1):
                if number > MAX_PAGES:
                    layout.warnings.append(f"Only the first {MAX_PAGES} pages were read.")
                    break
                if time.monotonic() > deadline:
                    layout.warnings.append("Reading took too long so the rest of the file was skipped.")
                    break
                try:
                    page = Page(pdf, raw, page_number=number, initial_doctop=0)
                    words = page.extract_words(x_tolerance=2, y_tolerance=3, extra_attrs=["fontname", "size"])
                    width = float(page.width)
                except Exception:
                    layout.warnings.append(f"Page {number} could not be read.")
                    continue
                if len(words) > MAX_WORDS_PER_PAGE:
                    layout.warnings.append(f"Page {number} had too much text so part of it was skipped.")
                    words = words[:MAX_WORDS_PER_PAGE]
                page_words.append((number, width, words))
                layout.pages_read += 1
    except Exception:
        if not page_words:
            layout.warnings.append("The file could not be opened as a PDF.")
            return layout
        layout.warnings.append("Part of the file could not be read.")
    if not page_words:
        if not layout.warnings:
            layout.warnings.append("No readable pages were found.")
        return layout

    kept: list[tuple[int, float, list[dict]]] = []
    for number, width, words in page_words:
        footer_tops = _footer_tops(words)
        kept.append((number, width, [word for word in words if round(word["top"], 1) not in footer_tops]))
    every_word = [word for _, _, words in kept for word in words]
    width = kept[0][1]
    boundary = _find_boundary(every_word, width)
    if boundary is not None:
        sidebar = _stream(kept, lambda word: (word["x0"] + word["x1"]) / 2 < boundary)
        main = _stream(kept, lambda word: (word["x0"] + word["x1"]) / 2 >= boundary)
        if sidebar and main:
            layout.sidebar = _finish(sidebar, None)
            layout.main = _finish(main, width - 20)
            layout.two_column = True
    if not layout.two_column:
        layout.main = _finish(_stream(kept, lambda word: True), width - 20)
    _assign_indexes(layout)
    return layout


def _footer_tops(words: list[dict]) -> set[float]:
    groups: dict[float, list[dict]] = {}
    for word in words:
        groups.setdefault(round(word["top"], 1), []).append(word)
    tops: set[float] = set()
    for top, group in groups.items():
        text = _clean_text(" ".join(str(word["text"]) for word in sorted(group, key=lambda word: word["x0"])))
        if FOOTER_RE.match(text):
            tops.add(top)
    return tops


def _stream(kept: list[tuple[int, float, list[dict]]], pick) -> list[_Line]:
    lines: list[_Line] = []
    for number, _, words in kept:
        lines.extend(_words_to_lines([word for word in words if pick(word)], number))
    return lines


def _finish(lines: list[_Line], extent: float | None) -> list[_Line]:
    return _merge_wraps(lines, extent)[:MAX_LINES]


def _assign_indexes(layout: _Layout) -> None:
    total = sum(len(line.text) + 1 for stream in (layout.sidebar, layout.main) for line in stream)
    if total > MAX_TEXT_CHARS:
        layout.warnings.append("The file had a very large amount of text so only the start was used.")
        budget = MAX_TEXT_CHARS
        for stream in (layout.sidebar, layout.main):
            kept: list[_Line] = []
            for line in stream:
                budget -= len(line.text) + 1
                if budget < 0:
                    break
                kept.append(line)
            stream[:] = kept
    position = 0
    if layout.two_column:
        for line in layout.sidebar:
            line.idx = position
            position += 1
        position += 1
    for line in layout.main:
        line.idx = position
        position += 1


def pdf_text(data: bytes) -> str:
    try:
        return _read_layout(data).text()
    except Exception:
        return ""


def _split_sections(lines: list[_Line], top_cap: int | None) -> tuple[dict[str, list[_Line]], list[str]]:
    sections: dict[str, list[_Line]] = {}
    seen: list[str] = []
    current = "profile"
    top_count = 0
    for line in lines:
        if not line.text:
            continue
        heading = _heading(line.text)
        if heading is not None:
            current = heading
            top_count = 0
            if heading not in seen:
                seen.append(heading)
            sections.setdefault(heading, [])
            continue
        if current == "top_skills" and top_cap is not None:
            if top_count < top_cap:
                sections["top_skills"].append(line)
                top_count += 1
                continue
            current = "profile"
        sections.setdefault(current, []).append(line)
    return sections, seen


def _text_lines(text: str) -> tuple[list[_Line], list[_Line] | None, list[_Line]]:
    raw = _clean_lines(text)
    everything = [_Line(text=value, idx=index) for index, value in enumerate(raw)]
    blank = next((index for index, value in enumerate(raw) if not value), None)
    first = next((value for value in raw if value), "")
    if blank is not None and _heading(first) in SIDEBAR_SECTIONS and any(raw[blank:]):
        return everything, [line for line in everything[:blank] if line.text], [line for line in everything[blank:] if line.text]
    return everything, None, [line for line in everything if line.text]


def parse_text(text: str) -> LinkedInProfile:
    everything, sidebar, main = _text_lines(text)
    profile = _build(sidebar, main)
    profile.lines = [line.text for line in everything]
    profile.layout = "two_column" if sidebar is not None else "single_column"
    return profile


def _build(sidebar: list[_Line] | None, main: list[_Line]) -> LinkedInProfile:
    profile = LinkedInProfile()
    if sidebar is not None:
        side_sections, side_seen = _split_sections(sidebar, None)
        main_sections, main_seen = _split_sections(main, None)
        skills = [line.text for line in side_sections.get("top_skills", [])][:SIDEBAR_SKILL_CAP]
        skills += [line.text for line in side_sections.get("skills", [])][:SIDEBAR_SKILL_CAP]
        languages = [line.text for line in side_sections.get("languages", [])]
        certifications = [line.text for line in side_sections.get("certifications", [])]
        seen = side_seen + [name for name in main_seen if name not in side_seen]
    else:
        main_sections, seen = _split_sections(main, TOP_SKILLS_LIMIT)
        skills = [line.text for line in main_sections.get("top_skills", [])] + [
            line.text for line in main_sections.get("skills", [])
        ]
        languages = [line.text for line in main_sections.get("languages", [])]
        certifications = [line.text for line in main_sections.get("certifications", [])]
    profile.skills = list(dict.fromkeys(skills))
    profile.languages = languages
    profile.certifications = certifications
    head = main_sections.get("profile", [])
    profile.name, profile.headline, profile.location = _identity([line.text for line in head])
    profile.summary = " ".join(line.text for line in main_sections.get("summary", []))
    experience_lines = main_sections.get("experience", [])
    education_lines = main_sections.get("education", [])
    profile.experience = _experience(experience_lines)
    profile.education = _education(education_lines)
    profile.sections = [name for name in seen if name in {"top_skills", "skills", "experience", "education"}]
    profile.experience_expected = max(
        sum(1 for line in experience_lines if _is_range(line.text)), 1 if "experience" in seen and experience_lines else 0
    )
    profile.education_expected = max(
        sum(1 for line in education_lines if EDU_RANGE_RE.search(line.text)), 1 if "education" in seen and education_lines else 0
    )
    return profile


def _identity(head: list[str]) -> tuple[str, str, str]:
    if not head:
        return "", "", ""
    name = head[0]
    rest = head[1:]
    if not rest:
        return name, "", ""
    if len(rest) == 1:
        only = rest[0]
        if "," in only and "|" not in only and " at " not in only.casefold():
            return name, "", only
        return name, only, ""
    return name, " ".join(rest[:-1]), rest[-1]


def _company_style(before: _Line, title: _Line) -> bool | None:
    if before.bold is None or title.bold is None:
        return None
    if before.bold != title.bold:
        return before.bold
    if before.size and title.size and abs(before.size - title.size) >= 0.5:
        return before.size > title.size
    return None


def _looks_company(value: str) -> bool:
    return (
        0 < len(value) <= 80
        and len(value.split()) <= 8
        and not value.rstrip().endswith((".", ":", ";"))
        and not re.match(r"^[•·\-*▪●]", value)
        and not _is_range(value)
        and not _is_duration(value)
    )


def _experience(items: list[_Line]) -> list[LinkedInExperience]:
    found: list[LinkedInExperience] = []
    group: _Line | None = None
    group_duration = ""
    last_range = -1
    for position, line in enumerate(items):
        match = RANGE_RE.match(line.text)
        if match is None or position == 0:
            continue
        title = items[position - 1]
        if _is_range(title.text) or _is_duration(title.text):
            continue
        before = items[position - 2] if position >= 2 else None
        company: _Line | None = None
        duration = ""
        if before is not None and _is_duration(before.text):
            if position >= 3 and not _is_range(items[position - 3].text):
                company = items[position - 3]
                group = company
                group_duration = before.text
                duration = before.text
            else:
                last_range = position
                continue
        elif group is not None:
            between = position - 2 - last_range
            if before is None or between <= 0 or _is_range(before.text):
                is_new = False
            else:
                style = _company_style(before, title)
                is_new = style if style is not None else (between >= 2 and _looks_company(before.text))
            if is_new and before is not None:
                company = before
                group = None
                group_duration = ""
            else:
                company = group
                duration = group_duration
        else:
            if before is None or _is_range(before.text):
                last_range = position
                continue
            company = before
        last_range = position
        found.append(
            LinkedInExperience(
                company=company.text,
                title=title.text,
                start=_norm_date(match.group("start")),
                end=_norm_date(match.group("end")),
                line=company.idx,
                title_line=title.idx,
                page=title.page,
                raw_range=_clean_text(f"{match.group('start')} - {match.group('end')}"),
                company_duration=duration,
            )
        )
    return found


def _education(items: list[_Line]) -> list[LinkedInEducation]:
    found: list[LinkedInEducation] = []
    index = 0
    while index < len(items):
        current = items[index]
        following = items[index + 1] if index + 1 < len(items) else None
        third = items[index + 2] if index + 2 < len(items) else None
        if EDU_RANGE_RE.search(current.text):
            index += 1
            continue
        if following is not None:
            match = EDU_RANGE_RE.search(following.text)
            if match is not None:
                found.append(
                    LinkedInEducation(
                        school=current.text,
                        detail=EDU_RANGE_RE.sub("", following.text).strip(" ·-,("),
                        start=_norm_date(match.group("start")),
                        end=_norm_date(match.group("end")),
                        line=current.idx,
                        page=current.page,
                    )
                )
                index += 2
                continue
            if third is not None and EDU_RANGE_RE.search(third.text) is not None:
                found.append(LinkedInEducation(school=current.text, line=current.idx, page=current.page))
                index += 1
                continue
            found.append(LinkedInEducation(school=current.text, detail=following.text, line=current.idx, page=current.page))
            index += 2
            continue
        found.append(LinkedInEducation(school=current.text, line=current.idx, page=current.page))
        index += 1
    return found


def _empty(warnings: list[str]) -> LinkedInProfile:
    return _score(LinkedInProfile(warnings=list(dict.fromkeys(warnings))))


def parse_linkedin_pdf(data: bytes) -> LinkedInProfile:
    try:
        layout = _read_layout(data)
        text = layout.text()
        if not text.strip():
            return _empty(layout.warnings or ["No readable text was found in the file."])
        profile = _build(layout.sidebar if layout.two_column else None, layout.main)
        profile.lines = text.split("\n")
        profile.layout = "two_column" if layout.two_column else "single_column"
        profile.pages_read = layout.pages_read
        profile.warnings = list(dict.fromkeys(layout.warnings))
        return ground(profile, text)
    except Exception:
        return _empty(["The file could not be understood."])


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
    grounded.summary = grounded.summary if keep("summary", grounded.summary) else ""
    grounded.skills = [skill for skill in grounded.skills if keep("skill", skill)]
    grounded.languages = [value for value in grounded.languages if keep("language", value)]
    grounded.certifications = [value for value in grounded.certifications if keep("certification", value)]
    grounded.experience = [
        item
        for item in grounded.experience
        if keep("experience.company", item.company)
        and keep("experience.title", item.title)
        and keep("experience.range", item.raw_range)
        and _date_in_text(item, haystack, dropped)
    ]
    grounded.education = [
        item for item in grounded.education if keep("education.school", item.school) and keep("education.detail", item.detail)
    ]
    grounded.dropped = dropped
    return _score(grounded)


def _date_in_text(item: LinkedInExperience, haystack: str, dropped: list[str]) -> bool:
    for label, value in (("start", item.start), ("end", item.end)):
        if value == "Present":
            if not re.search(_PRESENT_ALT, haystack):
                dropped.append(f"experience.{label}={value}")
                return False
            continue
        if value[:4] not in haystack:
            dropped.append(f"experience.{label}={value}")
            return False
    return True


def _score(profile: LinkedInProfile) -> LinkedInProfile:
    expected = 3
    found = sum(1 for value in (profile.name, profile.headline, profile.location) if value)
    sections = set(profile.sections)
    if sections & {"top_skills", "skills"}:
        expected += 1
        found += 1 if profile.skills else 0
    if "experience" in sections:
        wanted = max(profile.experience_expected, 1)
        expected += wanted
        found += min(len(profile.experience), wanted)
    if "education" in sections:
        wanted = max(profile.education_expected, 1)
        expected += wanted
        found += min(len(profile.education), wanted)
    profile.fields_expected = expected
    profile.fields_found = found
    profile.confidence = round(found / expected, 3) if expected else 0.0
    if not (profile.name or profile.experience or profile.education):
        profile.confidence = 0.0
    if profile.confidence >= READ_MOST:
        profile.confidence_message = "We could read most of your export."
    elif profile.confidence >= READ_SOME:
        profile.confidence_message = "We could read part of your export."
    else:
        profile.confidence_message = "We could not read much of your export."
    return profile


def _month_bounds(value: str, today: date) -> tuple[int, int] | None:
    parsed = _parse_date(value)
    if parsed is None:
        return None
    if parsed == "Present":
        now = today.year * 12 + today.month
        return now, now
    if re.fullmatch(r"\d{4}", parsed):
        base = int(parsed) * 12
        return base + 1, base + 12
    year, month = parsed.split("-")
    point = int(year) * 12 + int(month)
    return point, point


def _months(value: str, today: date) -> int | None:
    bounds = _month_bounds(value, today)
    return bounds[0] if bounds else None


def _gap(first: str, second: str, today: date) -> int | None:
    left = _month_bounds(first, today)
    right = _month_bounds(second, today)
    if left is None or right is None:
        return None
    if left[1] < right[0]:
        return right[0] - left[1]
    if right[1] < left[0]:
        return left[0] - right[1]
    return 0


def _company_tokens(value: str) -> list[str]:
    cleaned = _strip_accents(value).casefold().replace("&", " and ")
    tokens = re.findall(r"[a-z0-9]+", cleaned)
    reduced = [token for token in tokens if token not in COMPANY_SUFFIXES]
    return reduced or tokens


def _company_match(first: str, second: str) -> bool:
    left = _company_tokens(first)
    right = _company_tokens(second)
    if not left or not right:
        return False
    if left == right:
        return True
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    if len(" ".join(shorter)) >= 3 and set(shorter) <= set(longer):
        return True
    return jaro_winkler(" ".join(left), " ".join(right)) >= 0.93


def _title_tokens(value: str) -> set[str]:
    cleaned = _strip_accents(value).casefold()
    tokens: list[str] = []
    for token in re.findall(r"[a-z0-9]+", cleaned):
        tokens.extend(TITLE_ABBREVIATIONS.get(token, token).split())
    mapped = {TITLE_SYNONYMS.get(token, token) for token in tokens}
    return {token for token in mapped if token not in TITLE_STOPWORDS and not token.isdigit()}


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


def _make(config: Config | None, code: str, severity: str, detail: str, evidence: str, weight: int | None = None) -> Reason:
    value = _weight(config, code) if weight is None else min(weight, _weight(config, code))
    if config is None:
        built = Reason(code=code, severity=severity, detail=detail, weight=value)
    else:
        built = reason(config, code, severity, detail, value)
    built.evidence = evidence
    return built


def _where(item: LinkedInExperience) -> str:
    return f"page {item.page} line {item.line + 1}" if item.page else f"line {item.line + 1}"


def _linkedin_quote(item: LinkedInExperience) -> str:
    span = item.raw_range or f"{item.start} to {item.end}"
    return f'LinkedIn export {_where(item)} says "{item.company}" as "{item.title}" for "{span}".'


def _resume_quote(index: int, resume_item) -> str:
    return (
        f'Resume experience {index + 1} says "{resume_item.company}" as "{resume_item.title}" '
        f'from "{resume_item.start}" to "{resume_item.end}".'
    )


def _best(resume_item, options: list[tuple[int, LinkedInExperience]], today: date) -> tuple[int, LinkedInExperience]:
    def score(entry: tuple[int, LinkedInExperience]) -> tuple[float, int]:
        item = entry[1]
        total = (_gap(resume_item.start, item.start, today) or 0) + (_gap(resume_item.end, item.end, today) or 0)
        return (-_title_similarity(resume_item.title, item.title), total)

    return min(options, key=score)


def cross_check(
    profile: LinkedInProfile,
    candidate: Candidate,
    config: Config | None = None,
    today: date | None = None,
) -> list[Reason]:
    now = today or date.today()
    reasons: list[Reason] = []
    matched: set[int] = set()
    for resume_index, resume_item in enumerate(candidate.experience):
        options = [
            (position, item)
            for position, item in enumerate(profile.experience)
            if _company_match(resume_item.company, item.company)
        ]
        if not options:
            listed = ", ".join(f'"{item.company}"' for item in profile.experience[:6]) or "no employers"
            reasons.append(
                _make(
                    config,
                    "LINKEDIN_EMPLOYER_MISSING",
                    "low",
                    f'The resume employer "{resume_item.company}" in experience {resume_index + 1} has no match in the LinkedIn export.',
                    f"{_resume_quote(resume_index, resume_item)} The LinkedIn export lists {listed}.",
                )
            )
            continue
        matched.update(position for position, _ in options)
        _, item = _best(resume_item, options, now)
        for label, resume_value in (("start", resume_item.start), ("end", resume_item.end)):
            scored = [
                (value, getattr(option, label))
                for _, option in options
                if (value := _gap(resume_value, getattr(option, label), now)) is not None
            ]
            if not scored:
                continue
            smallest, nearest = min(scored, key=lambda pair: pair[0])
            if smallest <= DATE_TOLERANCE_MONTHS:
                continue
            reasons.append(
                _make(
                    config,
                    "LINKEDIN_DATE_MISMATCH",
                    "low",
                    f'The {label} date for "{resume_item.company}" is "{resume_value}" on the resume but "{nearest}" in the LinkedIn export, a gap of {smallest} months.',
                    f"{_resume_quote(resume_index, resume_item)} {_linkedin_quote(item)}",
                )
            )
        if max(_title_similarity(resume_item.title, option.title) for _, option in options) < TITLE_SIMILARITY_FLOOR:
            reasons.append(
                _make(
                    config,
                    "LINKEDIN_TITLE_MISMATCH",
                    "low",
                    f'The job title for "{resume_item.company}" is "{resume_item.title}" on the resume but "{item.title}" in the LinkedIn export.',
                    f"{_resume_quote(resume_index, resume_item)} {_linkedin_quote(item)}",
                )
            )
    for position, item in enumerate(profile.experience):
        if position in matched:
            continue
        if any(_company_match(item.company, resume_item.company) for resume_item in candidate.experience):
            continue
        listed = ", ".join(f'"{resume_item.company}"' for resume_item in candidate.experience[:6]) or "no employers"
        reasons.append(
            _make(
                config,
                "LINKEDIN_EMPLOYER_MISSING",
                "low",
                f'The LinkedIn export employer "{item.company}" is not listed in the resume.',
                f"{_linkedin_quote(item)} The resume lists {listed}.",
                EXTRA_EMPLOYER_WEIGHT,
            )
        )
    return reasons
