from __future__ import annotations

import re

from firewall.enrichment.claims import DOI_RE
from firewall.enrichment.roles.formats import compact_patent
from firewall.enrichment.roles.models import (
    DirectorshipClaim,
    MembershipClaim,
    PendingReference,
    QuotedItem,
    RoleClaims,
)
from firewall.models import Candidate


CREDENTIAL_RE = re.compile(
    r"(?:credential|certificate|certification|licen[sc]e|cert|enrol+ment|registration)\s*(?:id|no\.?|number|#)\s*[:#\-]?\s*(?P<id>[A-Za-z0-9][A-Za-z0-9\-]{0,39})",
    re.IGNORECASE,
)
BODY_RES = (
    ("ICAI", re.compile(r"\bICAI\b|Institute of Chartered Accountants of India", re.IGNORECASE)),
    ("ACCA", re.compile(r"\bACCA\b")),
    ("CFA", re.compile(r"\bCFA\b")),
)
MEMBER_NUMBER_RE = re.compile(
    r"(?:membership|member|m\.?\s?no|id|number|no\.?|#)\s*[:#\-.]*\s*(?P<num>\d[\d\-]{3,11})",
    re.IGNORECASE,
)
DIRECTOR_RE = re.compile(
    r"\b(?i:(?:(?:independent|whole-?time|managing|executive|non-executive)\s+)?(?:director|partner)\s+(?:at|of))\s+(?P<company>[A-Z][\w&.'-]*(?:\s+[A-Z&][\w&.'-]*){0,5})"
)
DIN_RE = re.compile(r"\bDIN\s*[:#\-]?\s*(?P<din>\d{4,12})\b", re.IGNORECASE)
REGULATOR_RE = re.compile(r"\b(?P<id>IN[ABHMPZ]\d{4,12})\b")
PATENT_RE = re.compile(
    r"patent(?:\s+(?:no\.?|number|#))?\s*[:#\-]?\s*(?P<num>(?-i:[A-Z]{2})[ \-]?[\dA-Z][\dA-Z,/ \-]{3,18}[\dA-Z])",
    re.IGNORECASE,
)
ARXIV_CLAIM_RE = re.compile(r"arxiv\s*:\s*(?P<id>[^\s,;)]+)", re.IGNORECASE)
AWARD_RE = re.compile(r"\b(?:award|winner|won|prize|champion|scholarship|recognition|finalist)\b", re.IGNORECASE)
CASE_STUDY_RE = re.compile(r"\bcase[- ]stud(?:y|ies)\b", re.IGNORECASE)
REFERENCE_RE = re.compile(
    r"\b(?:references?|referees?)\b\s*[:\-]\s*(?P<name>[A-Za-z][A-Za-z .'-]{1,60}?)\s*[,|<(\-]\s*(?:[^<>@\n]{0,60}?[,|<(]\s*)?(?P<email>[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})",
    re.IGNORECASE,
)
MAX_QUOTE = 200


def _lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").replace("\r", "\n").split("\n")]


def _quote(line: str) -> str:
    return line[:MAX_QUOTE]


def extract_role_claims(text: str, candidate: Candidate) -> RoleClaims:
    claims = RoleClaims()
    seen: set[tuple[str, str]] = set()

    def add(bucket: list[QuotedItem], kind: str, value: str, line: str, index: int) -> None:
        if (kind, value.casefold()) in seen or value not in text:
            return
        seen.add((kind, value.casefold()))
        bucket.append(QuotedItem(kind=kind, value=value, quote=_quote(line), line=index))

    for index, line in enumerate(_lines(text)):
        if not line:
            continue
        for match in CREDENTIAL_RE.finditer(line):
            add(claims.credential_ids, "credential_id", match.group("id"), line, index)
        for body, body_re in BODY_RES:
            if body_re.search(line):
                number = MEMBER_NUMBER_RE.search(line)
                if number and number.group("num") in text:
                    claims.memberships.append(MembershipClaim(body=body, number=number.group("num"), quote=_quote(line)))
        for match in DIRECTOR_RE.finditer(line):
            company = match.group("company").strip(" .,")
            if company and company in text:
                claims.directorships.append(DirectorshipClaim(company=company, quote=_quote(line)))
        for match in DIN_RE.finditer(line):
            add(claims.dins, "din", match.group("din"), line, index)
        for match in REGULATOR_RE.finditer(line):
            add(claims.regulator_ids, "regulator_id", match.group("id"), line, index)
        for match in PATENT_RE.finditer(line):
            raw = match.group("num").strip(" .,;")
            if sum(character.isdigit() for character in raw) < 5:
                continue
            add(claims.patents, "patent", raw, line, index)
        for match in ARXIV_CLAIM_RE.finditer(line):
            add(claims.arxiv_ids, "arxiv", match.group("id").rstrip(".,;"), line, index)
        for match in DOI_RE.finditer(line):
            doi = match.group("doi").rstrip(".,;:)]}")
            if doi.startswith("10.1109/"):
                add(claims.ieee_dois, "ieee_doi", doi, line, index)
        if AWARD_RE.search(line):
            add(claims.awards, "award", _quote(line), line, index)
        if CASE_STUDY_RE.search(line):
            add(claims.case_studies, "case_study", _quote(line), line, index)
        reference = REFERENCE_RE.search(line)
        if reference and reference.group("email") in text:
            employer = next((item.company for item in candidate.experience if item.company and item.company in line), None)
            claims.references.append(
                PendingReference(
                    reference_name=reference.group("name").strip(),
                    reference_email=reference.group("email"),
                    employer=employer,
                    source_quote=_quote(line),
                )
            )
    claims.memberships = list({(item.body, item.number): item for item in claims.memberships}.values())
    claims.directorships = list({item.company.casefold(): item for item in claims.directorships}.values())
    return claims


def patent_lookup_key(raw: str) -> str:
    return compact_patent(raw)
