from __future__ import annotations

from datetime import datetime, timezone

from firewall.enrichment.models import EnrichmentSignal
from firewall.intel.models import IntelFinding, IntelResult


def render_findings(result: IntelResult) -> str:
    consent = result.consent
    lines = [
        f"What we looked up for application {consent.application_id} (record {consent.record_id})",
        f"Purpose: {consent.purpose}",
        f"Searched on {consent.started_at.isoformat()}:",
    ]
    lines.extend(f"  - {item}" for item in consent.searched) if consent.searched else lines.append("  - nothing")
    lines.append(f"Public records kept: {consent.kept}. Records discarded without being stored: {consent.discarded}.")
    if not result.findings:
        lines.append("No public records matched your resume closely enough to keep.")
        return "\n".join(lines)
    lines.append("Records we kept (you can dispute any of them; a disputed record is hidden from recruiters until a person re-checks it):")
    for finding in result.findings:
        state = "disputed, awaiting human re-check" if finding.disputed else finding.status.replace("_", " ")
        lines.append(f"  [{finding.finding_id}] {finding.summary}")
        lines.append(f"      source: {finding.source_url}")
        lines.append(f"      fetched: {finding.fetched_at.isoformat()}")
        lines.append(f"      matched your resume on: {', '.join(finding.matched_facts)}")
        lines.append(f"      status: {state}; score impact: 0" if not finding.confirmation else f"      status: {state}; confirmed by {finding.confirmation}")
    return "\n".join(lines)


def dispute(result: IntelResult, finding_id: str, reason: str, now: datetime | None = None) -> IntelResult:
    updated = result.model_copy(deep=True)
    for finding in updated.findings:
        if finding.finding_id == finding_id:
            finding.disputed = True
            finding.dispute_reason = f"{reason} (at {(now or datetime.now(timezone.utc)).isoformat()})"
            finding.hidden_from_recruiter = True
            finding.needs_human_recheck = True
            return updated
    raise KeyError(finding_id)


def recruiter_view(result: IntelResult) -> list[IntelFinding]:
    return [finding.model_copy(deep=True) for finding in result.findings if not finding.hidden_from_recruiter]


def pending_rechecks(result: IntelResult) -> list[IntelFinding]:
    return [finding.model_copy(deep=True) for finding in result.findings if finding.needs_human_recheck]


def active_signals(result: IntelResult) -> list[EnrichmentSignal]:
    return [
        signal.model_copy(deep=True)
        for finding in result.findings
        if not finding.disputed
        for signal in result.signals_by_finding.get(finding.finding_id, [])
    ]
