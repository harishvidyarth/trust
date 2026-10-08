from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Any, Mapping

from firewall.config import Config
from firewall.enrichment.models import EnrichmentSignal
from firewall.intel.models import ConsentRecord, Hit, IntelFinding, IntelResult, ResumeFacts
from firewall.intel.shrink import shrink
from firewall.intel.sources import PublicSources
from firewall.signals.identity_links import link_key


DEFAULT_PURPOSE = "Corroborate resume claims in the borderline score band; findings are shown to the candidate and never decide a route."
INTEL_POSITIVE_BONUSES = {
    "ORCID_REGISTRY_MATCH": 6,
    "DOI_VERIFIED": 6,
    "GITHUB_CORROBORATED": 6,
}
BAND_MIN = 41
BAND_MAX = 69
MAX_HITS_PER_SOURCE = 5


def should_run(score: int, config: Config | None = None, env: Mapping[str, str] | None = None) -> bool:
    environment = os.environ if env is None else env
    low = config.corroboration_min if config is not None else BAND_MIN
    high = config.corroboration_max if config is not None else BAND_MAX
    return environment.get("FIREWALL_ENRICH") == "1" and low <= score <= high


def trust_bonus(result: IntelResult, cap: int = 15) -> int:
    total = 0.0
    for finding in result.findings:
        if finding.disputed:
            continue
        for signal in result.signals_by_finding.get(finding.finding_id, []):
            if signal.polarity == "positive":
                total += INTEL_POSITIVE_BONUSES.get(signal.code, 0) * signal.confidence
    return min(max(0, cap), int(round(total)))


def _summary(hit: Hit) -> str:
    parts = [f'name "{hit.name}"'] if hit.name else []
    if hit.org:
        parts.append("organisation " + "; ".join(f'"{item}"' for item in hit.org))
    if hit.location:
        parts.append("location " + "; ".join(f'"{item}"' for item in hit.location))
    if hit.orcid:
        parts.append(f"ORCID {hit.orcid}")
    return f"Public {hit.source.replace('_', ' ')} record: " + ", ".join(parts) + "."


def _finding(hit: Hit, agreed: list[str], application_id: str) -> IntelFinding:
    digest = hashlib.sha256(f"{application_id}|{hit.source}|{hit.source_url}".encode()).hexdigest()[:12]
    return IntelFinding(
        finding_id=digest,
        source=hit.source,
        source_url=hit.source_url,
        fetched_at=hit.fetched_at,
        matched_facts=agreed,
        summary=_summary(hit),
    )


def _confirm(
    hit: Hit, agreed: list[str], facts: ResumeFacts, sources: PublicSources, finding: IntelFinding
) -> list[EnrichmentSignal]:
    signals: list[EnrichmentSignal] = []
    kinds = {item.split(":", 1)[0] for item in agreed}
    base = dict(polarity="positive", severity="info", source="intel", evidence_url=hit.source_url, fetched_at=hit.fetched_at)
    if "orcid" in kinds:
        signals.append(
            EnrichmentSignal(
                code="ORCID_REGISTRY_MATCH",
                confidence=0.9,
                detail="An ORCID stated on the resume appears on a public author record whose name matches.",
                matched_claim=hit.orcid,
                **base,
            )
        )
        finding.confirmation = "orcid registry id"
    if "doi" in kinds and "name" in kinds:
        doi = next(item.split(":", 1)[1] for item in agreed if item.startswith("doi:"))
        signals.append(
            EnrichmentSignal(
                code="DOI_VERIFIED",
                confidence=0.85,
                detail="A DOI stated on the resume lists an author whose name matches the candidate.",
                matched_claim=doi,
                **base,
            )
        )
        finding.confirmation = finding.confirmation or "doi author"
    if hit.login:
        for repo in facts.repos:
            owner_login, _, repo_name = repo.partition("/")
            if owner_login.casefold() != hit.login.casefold() or not repo_name:
                continue
            owner = sources.github_repo_owner(hit.login, repo_name)
            if owner is not None and owner[0].casefold() == hit.login.casefold() and not owner[1]:
                signals.append(
                    EnrichmentSignal(
                        code="GITHUB_CORROBORATED",
                        confidence=0.8,
                        detail="A repository stated on the resume is owned by the matched public account and is not a fork.",
                        matched_claim=f"https://github.com/{repo}",
                        **{**base, "evidence_url": owner[2]},
                    )
                )
                finding.confirmation = finding.confirmation or "github repo ownership"
                break
    if finding.confirmation:
        finding.status = "confirmed"
    return signals


def run_passive_intel(
    facts: ResumeFacts,
    *,
    application_id: str,
    consent_given: bool = False,
    purpose: str = DEFAULT_PURPOSE,
    transport: Any | None = None,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> IntelResult:
    environment = os.environ if env is None else env
    started = now or datetime.now(timezone.utc)
    record = ConsentRecord(
        record_id=hashlib.sha256(f"{application_id}|{started.isoformat()}".encode()).hexdigest()[:12],
        application_id=application_id,
        purpose=purpose,
        consent_given=consent_given,
        started_at=started,
    )
    result = IntelResult(consent=record)
    if environment.get("FIREWALL_ENRICH") != "1":
        record.skipped_reason = "FIREWALL_ENRICH is not 1"
        return result
    if not consent_given:
        record.skipped_reason = "candidate consent not recorded"
        return result
    sources = PublicSources(transport=transport)
    seen_urls: set[str] = set()

    def discard() -> None:
        record.discarded += 1

    def consume(stream, label: str) -> None:
        record.searched.append(label)
        for hit, agreed in shrink(stream, facts, on_discard=discard):
            if hit.source_url in seen_urls:
                continue
            seen_urls.add(hit.source_url)
            if hit.source not in record.sources:
                record.sources.append(hit.source)
            finding = _finding(hit, agreed, application_id)
            signals = _confirm(hit, agreed, facts, sources, finding)
            result.findings.append(finding)
            result.signals_by_finding[finding.finding_id] = signals
            record.kept += 1

    def supplied_profiles():
        for link in facts.links:
            key = link_key(link)
            if key is not None and key[0] == "github":
                hit = sources.github_profile(key[1], source="github_supplied_link")
                if hit is not None:
                    yield hit

    if any((link_key(link) or ("",))[0] == "github" for link in facts.links):
        consume(supplied_profiles(), "candidate-supplied GitHub link lookup")
    if facts.name:
        consume(sources.github_users(facts.name, MAX_HITS_PER_SOURCE), f'github user search by name "{facts.name}"')
        consume(sources.openalex_authors(facts.name, MAX_HITS_PER_SOURCE), f'openalex author search by name "{facts.name}"')
        consume(sources.crossref_works(facts.name, MAX_HITS_PER_SOURCE), f'crossref author search by name "{facts.name}"')
    return result
