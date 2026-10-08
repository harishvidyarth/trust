from __future__ import annotations

import re


REGULATOR_ID_RE = re.compile(r"IN[ABHMPZ]\d{9}")
DIN_RE = re.compile(r"\d{8}")
ORCID_RE = re.compile(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]")
US_PATENT_RES = (
    re.compile(r"US\d{7,8}(?:[AB]\d)?"),
    re.compile(r"US(?:19|20)\d{2}\d{7}A\d"),
    re.compile(r"USD\d{6,7}S?"),
    re.compile(r"USRE\d{5,6}E?"),
    re.compile(r"USPP\d{5,6}P?\d?"),
)
EP_PATENT_RE = re.compile(r"EP\d{6,7}[AB]\d?")
WO_PATENT_RE = re.compile(r"WO(?:19|20)\d{2}\d{6}")
WO_LEGACY_RE = re.compile(r"WO\d{7}")
IN_APPLICATION_RE = re.compile(r"IN(?:19|20)\d{2}[1-4][1-9]\d{6}")
ARXIV_NEW_RE = re.compile(r"(?P<yy>\d{2})(?P<mm>\d{2})\.(?P<seq>\d{4,5})(?:v\d+)?")
ARXIV_OLD_RE = re.compile(r"[a-z]+(?:-[a-z]+)*(?:\.[A-Za-z]{2})?/(?P<yy>\d{2})(?P<mm>\d{2})\d{3}(?:v\d+)?")
IEEE_DOI_RE = re.compile(r"10\.1109/\S+")


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


def valid_membership(body: str, number: str) -> bool | None:
    return None


def valid_regulator_id(value: str) -> bool | None:
    return True if REGULATOR_ID_RE.fullmatch(value.strip()) else None


def valid_din(value: str) -> bool | None:
    return True if DIN_RE.fullmatch(value.strip()) else None


def compact_patent(value: str) -> str:
    return re.sub(r"[\s,/\-]", "", value).upper()


def valid_patent(value: str) -> bool | None:
    compact = compact_patent(value)
    if compact.startswith("US"):
        return any(pattern.fullmatch(compact) for pattern in US_PATENT_RES)
    if compact.startswith("WO"):
        if WO_PATENT_RE.fullmatch(compact):
            return True
        return None if WO_LEGACY_RE.fullmatch(compact) else False
    if EP_PATENT_RE.fullmatch(compact) or IN_APPLICATION_RE.fullmatch(compact):
        return True
    return None


def valid_orcid(value: str) -> bool:
    token = value.strip().upper()
    if not ORCID_RE.fullmatch(token):
        return False
    digits = token.replace("-", "")
    total = 0
    for character in digits[:15]:
        total = (total + int(character)) * 2
    result = (12 - total % 11) % 11
    return digits[15] == ("X" if result == 10 else str(result))


def valid_arxiv(value: str) -> bool:
    token = value.strip()
    match = ARXIV_NEW_RE.fullmatch(token)
    if match is not None:
        if not 1 <= int(match.group("mm")) <= 12:
            return False
        stamp = int(match.group("yy") + match.group("mm"))
        return stamp >= 704 and len(match.group("seq")) == (5 if stamp >= 1501 else 4)
    old = ARXIV_OLD_RE.fullmatch(token)
    if old is None or not 1 <= int(old.group("mm")) <= 12:
        return False
    stamp = int(old.group("yy") + old.group("mm"))
    return stamp >= 9108 or stamp < 704


def valid_ieee_doi(value: str) -> bool:
    return bool(IEEE_DOI_RE.fullmatch(value.strip()))
