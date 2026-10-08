from __future__ import annotations

import hashlib
import os
from typing import Any, Iterable, Mapping
from urllib.parse import quote

from firewall.enrichment._http import HttpConnector
from firewall.enrichment.claims import extract_claims
from firewall.enrichment.domain import DomainConnector, _public_host
from firewall.enrichment.models import Claims, EnrichmentSignal
from firewall.enrichment.roles import formats
from firewall.enrichment.roles.extract import extract_role_claims, patent_lookup_key
from firewall.enrichment.roles.models import RoleClaims
from firewall.enrichment.roles.profiles import PROFILES
from firewall.enrichment.roles.registry import NullRegistry, RoleRegistry
from firewall.enrichment.scholar import ScholarConnector
from firewall.index_keys import normalize_email, normalize_name
from firewall.models import Candidate
from firewall.signals.identity_links import name_similarity


ROLE_WEIGHTS = {
    "CERT_ID_INVALID_FORMAT": 6,
    "EMPLOYER_DOMAIN_UNREACHABLE": 4,
    "REFERENCE_EMAIL_IS_CANDIDATE": 10,
    "REFERENCE_EMAIL_FREEMAIL": 3,
    "MEMBERSHIP_ID_INVALID_FORMAT": 8,
    "MEMBERSHIP_NOT_FOUND": 15,
    "MEMBERSHIP_NAME_MISMATCH": 18,
    "DIN_INVALID_FORMAT": 6,
    "DIRECTORSHIP_NOT_FOUND": 12,
    "REGULATOR_ID_INVALID_FORMAT": 8,
    "REGULATOR_NOT_LISTED": 12,
    "PATENT_NUMBER_INVALID_FORMAT": 6,
    "PATENT_NOT_FOUND": 12,
    "PATENT_INVENTOR_MISMATCH": 14,
    "ARXIV_ID_INVALID_FORMAT": 6,
    "IEEE_DOI_INVALID_FORMAT": 6,
    "PORTFOLIO_UNREACHABLE": 6,
}
ROLE_POSITIVE_BONUSES = {
    "MEMBERSHIP_VERIFIED": 8,
    "DIRECTORSHIP_VERIFIED": 6,
    "REGULATOR_VERIFIED": 6,
    "PATENT_VERIFIED": 6,
    "HARDWARE_REPO_CORROBORATED": 4,
    "PORTFOLIO_LIVE": 2,
    "PORTFOLIO_NAME_MATCH": 3,
    "CLAIM_FOUND_ON_PORTFOLIO": 3,
}
FREEMAIL_DOMAINS = frozenset({"gmail.com", "googlemail.com", "yahoo.com", "yahoo.in", "outlook.com", "hotmail.com", "proton.me", "protonmail.com", "icloud.com"})
HARDWARE_LANGUAGES = frozenset({"verilog", "vhdl", "systemverilog"})
HARDWARE_KEYWORDS = (
    "arduino", "esp32", "esp8266", "stm32", "fpga", "verilog", "vhdl", "kicad", "pcb", "firmware", "rtos",
    "platformio", "raspberry", "embedded", "microcontroller", "altium", "schematic",
)
MAX_PAGE_CHARS = 200_000


def network_enabled(env: Mapping[str, str] | None = None) -> bool:
    return (os.environ if env is None else env).get("FIREWALL_ENRICH") == "1"


def _signal(code: str, polarity: str, severity: str, confidence: float, source: str, detail: str, claim: str | None, url: str | None = None) -> EnrichmentSignal:
    return EnrichmentSignal(
        code=code,
        polarity=polarity,
        severity=severity,
        confidence=confidence,
        source=source,
        detail=detail,
        matched_claim=claim,
        evidence_url=url,
    )


def general_checks(
    claims: Claims,
    role_claims: RoleClaims,
    candidate: Candidate,
    transport: Any | None,
    online: bool,
) -> list[EnrichmentSignal]:
    signals: list[EnrichmentSignal] = []
    for item in role_claims.credential_ids:
        if not formats.valid_credential_id(item.value):
            signals.append(
                _signal(
                    "CERT_ID_INVALID_FORMAT", "negative", "low", 0.6, "roles.general",
                    f'The credential or registration ID "{item.value}" does not have a plausible format (line {item.line + 1}: "{item.quote}").',
                    item.value,
                )
            )
    candidate_email = normalize_email(candidate.email)
    for reference in role_claims.references:
        reference_email = normalize_email(reference.reference_email)
        if candidate_email and reference_email == candidate_email:
            signals.append(
                _signal(
                    "REFERENCE_EMAIL_IS_CANDIDATE", "negative", "high", 0.9, "roles.general",
                    "The listed reference email normalises to the candidate's own email.",
                    reference.reference_email,
                )
            )
        elif reference.employer and reference_email.rsplit("@", 1)[-1] in FREEMAIL_DOMAINS:
            signals.append(
                _signal(
                    "REFERENCE_EMAIL_FREEMAIL", "negative", "low", 0.4, "roles.general",
                    f'The reference for employer "{reference.employer}" uses a free-mail address.',
                    reference.reference_email,
                )
            )
    if online and claims.employer_domains:
        domain_signals = DomainConnector(transport=transport).check(Claims(employer_domains=claims.employer_domains))
        signals.extend(domain_signals)
        reachable = {signal.matched_claim for signal in domain_signals if signal.code == "DOMAIN_REACHABLE"}
        for domain in claims.employer_domains:
            target = _public_host(f"https://{domain}")
            if target is not None and f"https://{domain}" not in reachable:
                signals.append(
                    _signal(
                        "EMPLOYER_DOMAIN_UNREACHABLE", "negative", "low", 0.4, "roles.general",
                        "The stated employer domain did not respond successfully.",
                        f"https://{domain}",
                    )
                )
    return signals


def finance_checks(
    claims: Claims,
    role_claims: RoleClaims,
    candidate: Candidate,
    registry: RoleRegistry,
    online: bool,
) -> list[EnrichmentSignal]:
    signals: list[EnrichmentSignal] = []
    for item in role_claims.memberships:
        claim = f"{item.body} {item.number}"
        if not formats.valid_membership(item.body, item.number):
            signals.append(
                _signal("MEMBERSHIP_ID_INVALID_FORMAT", "negative", "medium", 0.7, "roles.finance",
                        f'The {item.body} membership number "{item.number}" does not match the expected format (from "{item.quote}").', claim)
            )
            continue
        record = registry.membership(item.body, item.number) if online else None
        if record is None:
            continue
        if not record.found:
            signals.append(_signal("MEMBERSHIP_NOT_FOUND", "negative", "high", 0.85, "roles.finance",
                                   f"The {item.body} register has no member with number {item.number}.", claim, record.url))
        elif record.name and name_similarity(record.name, candidate.name) < 0.8:
            signals.append(_signal("MEMBERSHIP_NAME_MISMATCH", "negative", "high", 0.85, "roles.finance",
                                   f"The {item.body} register lists number {item.number} under a different name.", claim, record.url))
        else:
            signals.append(_signal("MEMBERSHIP_VERIFIED", "positive", "info", 0.85, "roles.finance",
                                   f"The {item.body} register lists number {item.number} under a matching name.", claim, record.url))
    for item in role_claims.dins:
        if not formats.valid_din(item.value):
            signals.append(_signal("DIN_INVALID_FORMAT", "negative", "low", 0.6, "roles.finance",
                                   f'The DIN "{item.value}" is not 8 digits (line {item.line + 1}).', item.value))
    for item in role_claims.directorships:
        record = registry.directorship(candidate.name, item.company) if online else None
        if record is None:
            continue
        if record.found:
            signals.append(_signal("DIRECTORSHIP_VERIFIED", "positive", "info", 0.8, "roles.finance",
                                   f'A company registry lists the candidate as a director of "{item.company}".', item.company, record.url))
        else:
            signals.append(_signal("DIRECTORSHIP_NOT_FOUND", "negative", "medium", 0.75, "roles.finance",
                                   f'A company registry does not list the candidate as a director of "{item.company}" (from "{item.quote}").', item.company, record.url))
    for item in role_claims.regulator_ids:
        if not formats.valid_regulator_id(item.value):
            signals.append(_signal("REGULATOR_ID_INVALID_FORMAT", "negative", "medium", 0.65, "roles.finance",
                                   f'The regulator registration ID "{item.value}" does not match the expected format (line {item.line + 1}).', item.value))
            continue
        record = registry.regulator(item.value) if online else None
        if record is None:
            continue
        if record.found and (record.name is None or name_similarity(record.name, candidate.name) >= 0.8 or normalize_name(candidate.name) in normalize_name(record.name)):
            signals.append(_signal("REGULATOR_VERIFIED", "positive", "info", 0.8, "roles.finance",
                                   f"The regulator register lists {item.value} under a matching name.", item.value, record.url))
        else:
            signals.append(_signal("REGULATOR_NOT_LISTED", "negative", "medium", 0.75, "roles.finance",
                                   f"The regulator register has no matching entry for {item.value}.", item.value, record.url))
    return signals


class _RepoProbe(HttpConnector):
    def repo_text(self, username: str, repo: str) -> str | None:
        response = self._get(
            f"https://api.github.com/repos/{quote(username, safe='')}/{quote(repo, safe='')}",
            headers={"Accept": "application/vnd.github+json"},
        )
        if response is None or response.status_code != 200:
            return None
        payload = self._json(response)
        if not isinstance(payload, dict):
            return None
        topics = payload.get("topics") if isinstance(payload.get("topics"), list) else []
        parts = [payload.get("name"), payload.get("description"), payload.get("language"), *topics]
        return " ".join(str(part) for part in parts if isinstance(part, str)).casefold()


def hardware_checks(
    claims: Claims,
    role_claims: RoleClaims,
    candidate: Candidate,
    registry: RoleRegistry,
    transport: Any | None,
    online: bool,
) -> list[EnrichmentSignal]:
    signals: list[EnrichmentSignal] = []
    for item in role_claims.patents:
        if not formats.valid_patent(item.value):
            signals.append(_signal("PATENT_NUMBER_INVALID_FORMAT", "negative", "low", 0.6, "roles.hardware",
                                   f'The patent number "{item.value}" does not match a known jurisdiction format (line {item.line + 1}: "{item.quote}").', item.value))
            continue
        record = registry.patent(patent_lookup_key(item.value)) if online else None
        if record is None:
            continue
        if not record.found:
            signals.append(_signal("PATENT_NOT_FOUND", "negative", "high", 0.8, "roles.hardware",
                                   f"The patent register has no record for {item.value}.", item.value, record.url))
        elif record.inventors and not any(name_similarity(candidate.name, inventor) >= 0.85 for inventor in record.inventors):
            signals.append(_signal("PATENT_INVENTOR_MISMATCH", "negative", "high", 0.8, "roles.hardware",
                                   f"The candidate is not among the inventors recorded for {item.value}.", item.value, record.url))
        else:
            signals.append(_signal("PATENT_VERIFIED", "positive", "info", 0.8, "roles.hardware",
                                   f"The patent register lists {item.value} with the candidate as inventor.", item.value, record.url))
    for item in role_claims.arxiv_ids:
        if not formats.valid_arxiv(item.value):
            signals.append(_signal("ARXIV_ID_INVALID_FORMAT", "negative", "low", 0.6, "roles.hardware",
                                   f'The arXiv identifier "{item.value}" is not a valid format (line {item.line + 1}).', item.value))
    for item in role_claims.ieee_dois:
        if not formats.valid_ieee_doi(item.value):
            signals.append(_signal("IEEE_DOI_INVALID_FORMAT", "negative", "low", 0.6, "roles.hardware",
                                   f'The IEEE DOI "{item.value}" is not a valid format (line {item.line + 1}).', item.value))
    if online:
        if claims.papers:
            signals.extend(ScholarConnector(transport=transport).check(claims))
        if claims.github_username and claims.project_repos:
            probe = _RepoProbe(transport=transport)
            for repo in claims.project_repos:
                text = probe.repo_text(claims.github_username, repo.name)
                if text and (any(language in text.split() for language in HARDWARE_LANGUAGES) or any(word in text for word in HARDWARE_KEYWORDS)):
                    signals.append(_signal("HARDWARE_REPO_CORROBORATED", "positive", "info", 0.7, "roles.hardware",
                                           "The claimed public repository exists and its metadata indicates hardware or embedded work.",
                                           f"https://github.com/{claims.github_username}/{repo.name}",
                                           f"https://github.com/{claims.github_username}/{repo.name}"))
    return signals


class _PageProbe(HttpConnector):
    def page(self, url: str) -> tuple[int, str] | None:
        response = self._get(url, follow_redirects=True)
        if response is None:
            return None
        return response.status_code, response.text[:MAX_PAGE_CHARS]


def creative_checks(
    claims: Claims,
    role_claims: RoleClaims,
    candidate: Candidate,
    transport: Any | None,
    online: bool,
) -> list[EnrichmentSignal]:
    signals: list[EnrichmentSignal] = []
    if not online or not claims.portfolio_url or _public_host(claims.portfolio_url) is None:
        return signals
    fetched = _PageProbe(transport=transport).page(claims.portfolio_url)
    if fetched is None:
        return signals
    status, body = fetched
    url = claims.portfolio_url
    if status in {404, 410} or status >= 500:
        signals.append(_signal("PORTFOLIO_UNREACHABLE", "negative", "low", 0.7, "roles.creative",
                               f"The candidate-supplied portfolio returned HTTP {status}.", url, url))
        return signals
    if not 200 <= status < 400:
        return signals
    signals.append(_signal("PORTFOLIO_LIVE", "positive", "info", 0.5, "roles.creative", "The candidate-supplied portfolio responded successfully.", url, url))
    folded = " ".join(body.split()).casefold()
    if candidate.name and " ".join(candidate.name.split()).casefold() in folded:
        signals.append(_signal("PORTFOLIO_NAME_MATCH", "positive", "info", 0.6, "roles.creative", "The portfolio page names the candidate.", url, url))
    for item in [*role_claims.awards, *role_claims.case_studies]:
        phrase = " ".join(item.value.split()).casefold()
        if phrase and phrase in folded:
            signals.append(_signal("CLAIM_FOUND_ON_PORTFOLIO", "positive", "info", 0.6, "roles.creative",
                                   f'The {item.kind.replace("_", " ")} claim appears verbatim on the portfolio page.', item.value, url))
    return signals


def run_role_checks(
    profile: str,
    text: str,
    candidate: Candidate,
    *,
    claims: Claims | None = None,
    transport: Any | None = None,
    registry: RoleRegistry | None = None,
    env: Mapping[str, str] | None = None,
) -> list[EnrichmentSignal]:
    if profile not in PROFILES:
        raise ValueError(f"unknown role profile: {profile}")
    active_claims = claims if claims is not None else extract_claims(text, candidate)
    role_claims = extract_role_claims(text, candidate)
    online = network_enabled(env)
    active_registry = registry if registry is not None else NullRegistry()
    signals = general_checks(active_claims, role_claims, candidate, transport, online)
    if profile == "finance":
        signals.extend(finance_checks(active_claims, role_claims, candidate, active_registry, online))
    elif profile == "hardware":
        signals.extend(hardware_checks(active_claims, role_claims, candidate, active_registry, transport, online))
    elif profile == "sales_ops_design":
        signals.extend(creative_checks(active_claims, role_claims, candidate, transport, online))
    return signals


def pending_references(text: str, candidate: Candidate):
    return extract_role_claims(text, candidate).references


class RoleConnector:
    def __init__(
        self,
        profile: str,
        text: str,
        candidate: Candidate,
        transport: Any | None = None,
        registry: RoleRegistry | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        if profile not in PROFILES:
            raise ValueError(f"unknown role profile: {profile}")
        self.profile = profile
        self.text = text
        self.candidate = candidate
        self.transport = transport
        self.registry = registry
        self.env = env
        digest = hashlib.sha256(f"{profile}\x1f{candidate.name}\x1f{text}".encode()).hexdigest()[:12]
        self.name = f"role_{profile}_{digest}"

    def check(self, claims: Claims) -> list[EnrichmentSignal]:
        return run_role_checks(
            self.profile, self.text, self.candidate, claims=claims, transport=self.transport, registry=self.registry, env=self.env
        )


def role_reasons(
    signals: Iterable[EnrichmentSignal],
    bonus_cap: int = 15,
    negative_cap: int = 25,
) -> tuple[list[dict[str, Any]], int]:
    from firewall.enrichment.runner import DEFAULT_WEIGHTS, to_reasons

    material = list(signals)
    reasons, trust = to_reasons(material, weights={**DEFAULT_WEIGHTS, **ROLE_WEIGHTS}, bonus_cap=bonus_cap)
    capped: list[dict[str, Any]] = []
    remaining = max(0, negative_cap)
    for item in reasons:
        weight = min(item["weight"], remaining)
        remaining -= weight
        capped.append({**item, "weight": weight})
    extra = sum(ROLE_POSITIVE_BONUSES.get(signal.code, 0) * signal.confidence for signal in material if signal.polarity == "positive")
    return capped, min(max(0, bonus_cap), trust + int(round(extra)))
