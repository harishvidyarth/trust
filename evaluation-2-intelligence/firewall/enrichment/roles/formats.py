from __future__ import annotations

import re


MEMBERSHIP_PATTERNS = {
    "ICAI": re.compile(r"[1-9]\d{5}"),
    "ACCA": re.compile(r"\d{7,8}"),
    "CFA": re.compile(r"\d{7,8}"),
}
REGULATOR_ID_RE = re.compile(r"IN[ABHMPZ]\d{9}")
DIN_RE = re.compile(r"\d{8}")
PATENT_PATTERNS = (
    re.compile(r"US\d{7,8}[AB]\d?"),
    re.compile(r"EP\d{7}[AB]\d?"),
    re.compile(r"WO(?:19|20)\d{2}\d{6}"),
    re.compile(r"IN(?:\d{6,7}|(?:19|20)\d{10})"),
)
ARXIV_RE = re.compile(r"(?P<yy>\d{2})(?P<mm>\d{2})\.(?P<seq>\d{4,5})(?:v\d+)?")
IEEE_DOI_RE = re.compile(r"10\.1109/[A-Za-z0-9][A-Za-z0-9.\-]*[A-Za-z0-9]")


def valid_credential_id(value: str) -> bool:
    token = value.strip()
    if not 6 <= len(token) <= 40 or not re.fullmatch(r"[A-Za-z0-9\-]+", token):
        return False
    alnum = re.sub(r"-", "", token)
    if not any(character.isdigit() for character in alnum):
        return False
    if len(set(alnum.casefold())) <= 2:
        return False
    ordinals = [ord(character) for character in alnum.casefold()]
    if all(second - first == 1 for first, second in zip(ordinals, ordinals[1:])):
        return False
    return True


def valid_membership(body: str, number: str) -> bool:
    pattern = MEMBERSHIP_PATTERNS.get(body)
    return bool(pattern and pattern.fullmatch(number.strip()))


def valid_regulator_id(value: str) -> bool:
    return bool(REGULATOR_ID_RE.fullmatch(value.strip()))


def valid_din(value: str) -> bool:
    return bool(DIN_RE.fullmatch(value.strip()))


def compact_patent(value: str) -> str:
    return re.sub(r"[\s,/\-]", "", value).upper()


def valid_patent(value: str) -> bool:
    compact = compact_patent(value)
    return any(pattern.fullmatch(compact) for pattern in PATENT_PATTERNS)


def valid_arxiv(value: str) -> bool:
    match = ARXIV_RE.fullmatch(value.strip())
    if match is None or not 1 <= int(match.group("mm")) <= 12:
        return False
    stamp = int(match.group("yy") + match.group("mm"))
    return stamp >= 704 and len(match.group("seq")) == (5 if stamp >= 1501 else 4)


def valid_ieee_doi(value: str) -> bool:
    return bool(IEEE_DOI_RE.fullmatch(value.strip()))
