from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from firewall.config import Config
from firewall.index_keys import normalize_email, normalize_name, normalize_phone
from firewall.models import Application, Reason
from firewall.signals.common import reason

if TYPE_CHECKING:
    from firewall.store import ApplicationStore


IDENTITY_LINK_WEIGHTS = {"LINK_REUSE": 25, "FUZZY_IDENTITY": 20, "EMAIL_ALIAS": 22}

URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s<>\"')\]]+|(?:github\.com|linkedin\.com)/[^\s<>\"')\]]+",
    re.IGNORECASE,
)
CERT_ID_RE = re.compile(
    r"(?:credential|certificate|certification|licen[sc]e|cert)\s*(?:id|no\.?|number|#)\s*[:#\-]?\s*(?P<id>[A-Z0-9][A-Z0-9\-]{5,39})",
    re.IGNORECASE,
)
COLLEGE_RE = re.compile(
    r"(?:[A-Z][A-Za-z.&']+\s+){0,4}(?:University|College|Institute|Institution)(?:\s+of\s+[A-Z][A-Za-z&]+(?:\s+[A-Z][A-Za-z&]+){0,3})?"
)
DEGREE_RE = re.compile(r"\b(B\.?\s?Tech|B\.?\s?E|B\.?\s?Sc|M\.?\s?Tech|M\.?\s?Sc|MBA|BCA|MCA|Ph\.?D)\b", re.IGNORECASE)
GRAD_YEAR_RE = re.compile(
    r"(?:graduat\w*|class of|batch|passing year|passed out|expected)[^0-9]{0,15}(?P<year>(?:19|20)\d{2})",
    re.IGNORECASE,
)
RESERVED_GITHUB = {
    "orgs", "topics", "features", "about", "sponsors", "pricing", "login", "join", "marketplace",
    "explore", "settings", "enterprise", "collections", "events", "trending", "search",
}
GENERIC_HOSTS = {
    "doi.org", "dx.doi.org", "arxiv.org", "google.com", "youtube.com", "youtu.be", "twitter.com",
    "x.com", "facebook.com", "instagram.com", "wikipedia.org", "en.wikipedia.org", "orcid.org",
    "scholar.google.com", "stackoverflow.com", "kaggle.com", "leetcode.com", "example.com",
}
TITLE_TOKENS = {"mr", "mrs", "ms", "miss", "dr", "prof", "shri", "smt", "sri"}
FUZZY_NAME_THRESHOLD = 0.88
MAX_EVIDENCE = 3


@dataclass(frozen=True)
class Claimed:
    kind: str
    key: str
    field: str
    quote: str


def _weight(config: Config, code: str) -> int:
    return config.weights.get(code, IDENTITY_LINK_WEIGHTS[code])


def _texts(application: Application) -> list[tuple[str, str]]:
    candidate = application.candidate
    found: list[tuple[str, str]] = []
    for index, project in enumerate(candidate.projects):
        found.append((f"projects[{index}].name", project.name))
        found.append((f"projects[{index}].description", project.description))
    for index, skill in enumerate(candidate.skills):
        found.append((f"skills[{index}]", skill))
    for index, item in enumerate(candidate.experience):
        found.append((f"experience[{index}].title", item.title))
        found.append((f"experience[{index}].company", item.company))
    return [(field, text) for field, text in found if text and text.strip()]


def link_key(raw: str) -> tuple[str, str] | None:
    value = raw.strip().rstrip(".,;:")
    if not value:
        return None
    try:
        parsed = urlsplit(value if "://" in value else f"https://{value}")
    except ValueError:
        return None
    host = (parsed.hostname or "").casefold()
    if host.startswith("www."):
        host = host[4:]
    if "." not in host:
        return None
    segments = [segment for segment in parsed.path.split("/") if segment]
    if host == "github.com":
        if not segments or segments[0].casefold() in RESERVED_GITHUB:
            return None
        if not re.fullmatch(r"[A-Za-z0-9-]{1,39}", segments[0]):
            return None
        return "github", segments[0].casefold()
    if host.endswith("linkedin.com"):
        if len(segments) >= 2 and segments[0].casefold() == "in":
            return "linkedin", segments[1].casefold()
        return None
    if host in GENERIC_HOSTS or host.endswith(".doi.org"):
        return None
    return "portfolio", host + "/" + "/".join(segment.casefold() for segment in segments)


def extract_link_claims(application: Application) -> list[Claimed]:
    claims: list[Claimed] = []
    for field, text in _texts(application):
        for match in URL_RE.finditer(text):
            key = link_key(match.group(0))
            if key is not None:
                claims.append(Claimed(key[0], key[1], field, match.group(0).rstrip(".,;:")))
        for match in CERT_ID_RE.finditer(text):
            cert_id = match.group("id")
            if any(character.isdigit() for character in cert_id):
                claims.append(Claimed("certificate", cert_id.casefold(), field, match.group(0)))
    seen: set[tuple[str, str, str]] = set()
    unique: list[Claimed] = []
    for claim in claims:
        marker = (claim.kind, claim.key, claim.field)
        if marker not in seen:
            seen.add(marker)
            unique.append(claim)
    return unique


def same_person(first: Application, second: Application) -> bool:
    email = normalize_email(first.candidate.email)
    phone = normalize_phone(first.candidate.phone)
    email_match = bool(email) and email == normalize_email(second.candidate.email)
    phone_match = bool(phone) and phone == normalize_phone(second.candidate.phone)
    return (email_match or phone_match) and (
        normalize_name(first.candidate.name) == normalize_name(second.candidate.name)
    )


def detect_link_reuse(application: Application, store: ApplicationStore, config: Config) -> list[Reason]:
    mine = extract_link_claims(application)
    if not mine:
        return []
    by_key: dict[tuple[str, str], Claimed] = {(claim.kind, claim.key): claim for claim in mine}
    priors: dict[str, Application] = {}
    for kind, key in by_key:
        for prior in store.by_link(f"{kind}:{key}"):
            if prior.application_id != application.application_id and not same_person(application, prior):
                priors.setdefault(prior.application_id, prior)
    evidence: list[str] = []
    for prior in priors.values():
        for claim in extract_link_claims(prior):
            own = by_key.get((claim.kind, claim.key))
            if own is None:
                continue
            label = "certificate ID" if own.kind == "certificate" else f"{own.kind} link"
            evidence.append(
                f'{label} "{own.quote}" at {own.field} also claimed by {prior.application_id} '
                f'at {claim.field} as "{claim.quote}" under a different identity'
            )
    if not evidence:
        return []
    shown = "; ".join(evidence[:MAX_EVIDENCE])
    extra = f" (+{len(evidence) - MAX_EVIDENCE} more)" if len(evidence) > MAX_EVIDENCE else ""
    return [reason(config, "LINK_REUSE", "high", f"{shown}{extra}.", _weight(config, "LINK_REUSE"))]


def _jaro(first: str, second: str) -> float:
    if first == second:
        return 1.0
    if not first or not second:
        return 0.0
    window = max(max(len(first), len(second)) // 2 - 1, 0)
    first_flags = [False] * len(first)
    second_flags = [False] * len(second)
    matches = 0
    for index, character in enumerate(first):
        start = max(0, index - window)
        end = min(len(second), index + window + 1)
        for other in range(start, end):
            if not second_flags[other] and second[other] == character:
                first_flags[index] = True
                second_flags[other] = True
                matches += 1
                break
    if matches == 0:
        return 0.0
    transpositions = 0
    pointer = 0
    for index, flagged in enumerate(first_flags):
        if flagged:
            while not second_flags[pointer]:
                pointer += 1
            if first[index] != second[pointer]:
                transpositions += 1
            pointer += 1
    half = transpositions / 2
    return (matches / len(first) + matches / len(second) + (matches - half) / matches) / 3


def jaro_winkler(first: str, second: str, scale: float = 0.1) -> float:
    base = _jaro(first, second)
    prefix = 0
    for left, right in zip(first[:4], second[:4]):
        if left != right:
            break
        prefix += 1
    return base + prefix * scale * (1 - base)


def _name_tokens(name: str) -> list[str]:
    decomposed = unicodedata.normalize("NFKD", name)
    plain = "".join(character for character in decomposed if not unicodedata.combining(character))
    return [token for token in normalize_name(plain).split() if token not in TITLE_TOKENS]


def _initial_match(shorter: list[str], longer: list[str]) -> bool:
    remaining = list(longer)
    exact = 0
    for token in shorter:
        choice = None
        for candidate in remaining:
            if candidate == token:
                choice = candidate
                exact += 1
                break
        if choice is None:
            for candidate in remaining:
                if len(token) == 1 and candidate.startswith(token):
                    choice = candidate
                    break
        if choice is None:
            return False
        remaining.remove(choice)
    return exact >= 1 and len(shorter) >= 2


def name_similarity(first: str, second: str) -> float:
    left = _name_tokens(first)
    right = _name_tokens(second)
    if not left or not right:
        return 0.0
    score = max(
        jaro_winkler(" ".join(left), " ".join(right)),
        jaro_winkler(" ".join(sorted(left)), " ".join(sorted(right))),
    )
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    if _initial_match(shorter, longer):
        score = max(score, 0.95)
    return score


@dataclass(frozen=True)
class Signature:
    employers: frozenset[tuple[str, str]]
    colleges: frozenset[str]
    degree_year: frozenset[tuple[str, str]]


def signature(application: Application) -> Signature:
    employers = frozenset(
        (normalize_name(item.company), item.start.strip()[:4])
        for item in application.candidate.experience
        if normalize_name(item.company)
    )
    blob = "\n".join(text for _, text in _texts(application))
    colleges = frozenset(normalize_name(match.group(0)) for match in COLLEGE_RE.finditer(blob))
    degrees = {re.sub(r"[^a-z]", "", match.group(0).lower()) for match in DEGREE_RE.finditer(blob)}
    years = {match.group("year") for match in GRAD_YEAR_RE.finditer(blob)}
    degree_year = frozenset((degree, year) for degree in degrees for year in years)
    return Signature(employers, colleges, degree_year)


def _shared(first: Signature, second: Signature) -> list[str]:
    shared: list[str] = []
    for company, start in sorted(first.employers & second.employers):
        shared.append(f'employer "{company}" starting {start}')
    for college in sorted(first.colleges & second.colleges):
        shared.append(f'college "{college}"')
    for degree, year in sorted(first.degree_year & second.degree_year):
        shared.append(f"degree {degree} year {year}")
    return shared


def _signature_keys(sig: Signature) -> list[tuple[str, str]]:
    keys = [("employer", f"emp:{company}\x1f{start}") for company, start in sorted(sig.employers)]
    keys.extend(("college", f"col:{college}") for college in sorted(sig.colleges))
    keys.extend(("degree", f"deg:{degree}\x1f{year}") for degree, year in sorted(sig.degree_year))
    return keys


def link_index_keys(application: Application) -> frozenset[str]:
    return frozenset(f"{claim.kind}:{claim.key}" for claim in extract_link_claims(application))


def name_index_keys(name: str) -> frozenset[str]:
    return frozenset(token for token in _name_tokens(name) if len(token) > 1)


def signature_index_keys(application: Application) -> frozenset[str]:
    return frozenset(key for _, key in _signature_keys(signature(application)))


def _fuzzy_candidates(application: Application, store: ApplicationStore, mine: Signature) -> list[Application]:
    kinds: dict[str, set[str]] = {}
    found: dict[str, Application] = {}
    for kind, key in _signature_keys(mine):
        for prior in store.by_signature(key):
            if prior.application_id == application.application_id or same_person(application, prior):
                continue
            kinds.setdefault(prior.application_id, set()).add(kind)
            found.setdefault(prior.application_id, prior)
    return [found[application_id] for application_id, shared in kinds.items() if len(shared) >= 2]


def detect_fuzzy_identity(application: Application, store: ApplicationStore, config: Config) -> list[Reason]:
    mine = signature(application)
    if not (mine.employers or mine.colleges or mine.degree_year):
        return []
    evidence: list[str] = []
    for prior in _fuzzy_candidates(application, store, mine):
        similarity = name_similarity(application.candidate.name, prior.candidate.name)
        if similarity < FUZZY_NAME_THRESHOLD:
            continue
        theirs = signature(prior)
        shared = _shared(mine, theirs)
        components = sum(
            1
            for present in (
                bool(mine.employers & theirs.employers),
                bool(mine.colleges & theirs.colleges),
                bool(mine.degree_year & theirs.degree_year),
            )
            if present
        )
        if components < 2:
            continue
        evidence.append(
            f'name "{application.candidate.name}" vs "{prior.candidate.name}" in {prior.application_id} '
            f"(similarity {similarity:.2f}) with identical {', '.join(shared)}"
        )
    if not evidence:
        return []
    shown = "; ".join(evidence[:MAX_EVIDENCE])
    return [reason(config, "FUZZY_IDENTITY", "medium", f"{shown}.", _weight(config, "FUZZY_IDENTITY"))]


def canonical_email(raw: str) -> str:
    return normalize_email(raw)


def canonical_phone(raw: str) -> str:
    digits = "".join(character for character in raw if character.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) > 10 and digits.startswith("91"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits[-10:] if len(digits) >= 10 else digits


def alias_forms(application: Application, prior: Application) -> list[str]:
    forms: list[str] = []
    raw_email = application.candidate.email.strip().lower()
    prior_email = prior.candidate.email.strip().lower()
    canon = canonical_email(application.candidate.email)
    if canon and canon == canonical_email(prior.candidate.email) and raw_email != prior_email:
        forms.append(
            f'email "{application.candidate.email.strip()}" (candidate.email) collapses to "{canon}", '
            f'matching "{prior.candidate.email.strip()}" in {prior.application_id}'
        )
    raw_digits = "".join(character for character in application.candidate.phone if character.isdigit())
    prior_digits = "".join(character for character in prior.candidate.phone if character.isdigit())
    phone = canonical_phone(application.candidate.phone)
    if phone and len(phone) == 10 and phone == canonical_phone(prior.candidate.phone) and raw_digits != prior_digits:
        forms.append(
            f'phone "{application.candidate.phone.strip()}" (candidate.phone) collapses to ending {phone[-4:]}, '
            f'matching "{prior.candidate.phone.strip()}" in {prior.application_id}'
        )
    return forms


def detect_email_alias(application: Application, store: ApplicationStore, config: Config) -> list[Reason]:
    email = canonical_email(application.candidate.email)
    phone = canonical_phone(application.candidate.phone)
    candidates = {
        item.application_id: item
        for item in (
            *(store.by_email(email) if email else ()),
            *(store.by_phone(phone) if phone else ()),
        )
        if item.application_id != application.application_id
    }
    evidence: list[str] = []
    for prior in candidates.values():
        evidence.extend(alias_forms(application, prior))
    if not evidence:
        return []
    shown = "; ".join(list(dict.fromkeys(evidence))[:MAX_EVIDENCE])
    return [reason(config, "EMAIL_ALIAS", "medium", f"{shown}.", _weight(config, "EMAIL_ALIAS"))]


def detect_identity_links(application: Application, store: ApplicationStore, config: Config) -> list[Reason]:
    return [
        *detect_email_alias(application, store, config),
        *detect_link_reuse(application, store, config),
        *detect_fuzzy_identity(application, store, config),
    ]
