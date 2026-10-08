from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from firewall.config import Config
from firewall.enrichment.claims import DOI_RE, GITHUB_RE, extract_claims
from firewall.intel import (
    IntelResult,
    LinkedInProfile,
    cross_check,
    dispute,
    extract_facts,
    render_findings,
    run_passive_intel,
    should_run,
)
from firewall.intel.linkedin import parse_linkedin_pdf as read_linkedin_export, pdf_text
from firewall.models import Candidate
from firewall.resume.extract import extract_resume


MAX_TRACKED_CONSENTS = 2_000
LOOKUP_SCOPE = "name_lookup"
STATUS_FROM_INTEL = {"unverified_match": "unverified", "confirmed": "verified"}


def provenance_findings(resume_text: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
    resume = resume_text or ""
    linkedin = str(profile.get("linkedin_text") or "")
    haystack = f"{resume}\n{linkedin}"
    findings: list[dict[str, Any]] = []

    def add(source: str, fact: str, status: str, evidence: str) -> None:
        findings.append({"source": source, "fact": fact, "status": status, "evidence": evidence})

    claims = extract_claims(resume, Candidate(name=str(profile.get("linkedin_name") or ""), email="", phone=""))
    github_user = profile.get("github_username")
    if github_user:
        claimed = claims.github_username
        if claimed is None:
            add("github", github_user, "unverified", "no GitHub account named in resume text")
        elif claimed.casefold() == github_user.casefold():
            add("github", github_user, "verified", "matches GitHub account named in resume text")
        else:
            add("github", github_user, "mismatch", "resume text names a different GitHub account")
    li_name = str(profile.get("linkedin_name") or "")
    if li_name:
        if not resume.strip():
            add("linkedin", li_name, "unverified", "no resume text available to compare")
        elif li_name in resume:
            add("linkedin", li_name, "verified", "name appears verbatim in resume text")
        else:
            add("linkedin", li_name, "mismatch", "name does not appear in resume text")
    portfolio = profile.get("portfolio_url")
    if portfolio:
        host = urlsplit(portfolio).hostname or ""
        if host and host in haystack:
            add("portfolio", portfolio, "verified", "host appears in resume or LinkedIn text")
        else:
            add("portfolio", portfolio, "unverified", "host not found in resume or LinkedIn text")
    for doi in profile.get("dois") or []:
        if doi.casefold() in haystack.casefold():
            add("doi", doi, "verified", "DOI appears verbatim in resume or LinkedIn text")
        else:
            add("doi", doi, "unverified", "DOI not found in resume or LinkedIn text")
    for certificate in profile.get("certificate_ids") or []:
        if certificate in haystack:
            add("certificate", certificate, "verified", "ID appears verbatim in resume or LinkedIn text")
        else:
            add("certificate", certificate, "unverified", "ID not found in resume or LinkedIn text")
    return findings


class RealIntelService:
    def __init__(
        self,
        candidate_for: Callable[[str], Candidate | None] | None = None,
        score_for: Callable[[str], int | None] | None = None,
        config: Config | None = None,
        transport: Any | None = None,
        env: Mapping[str, str] | None = None,
        today: date | None = None,
    ) -> None:
        self._candidate_for = candidate_for or (lambda application_id: None)
        self._score_for = score_for or (lambda application_id: None)
        self._config = config
        self._transport = transport
        self._env = env
        self._today = today
        self._consents: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def parse_linkedin_pdf(self, data: bytes) -> dict[str, Any]:
        text = pdf_text(data)
        profile = read_linkedin_export(data)
        extracted = extract_resume(data, "linkedin.pdf")
        github = GITHUB_RE.search(text)
        dois = [match.group("doi").rstrip(".,;:)]}") for match in DOI_RE.finditer(text)]
        return {
            "text": text,
            "profile": profile.model_dump(),
            "name": profile.name,
            "headline": profile.headline,
            "location": profile.location,
            "skills": list(profile.skills),
            "experience_count": len(profile.experience),
            "education_count": len(profile.education),
            "github_username": github.group("username") if github else None,
            "dois": list(dict.fromkeys(dois)),
            "pages": extracted.page_count,
            "hidden_span_count": len(extracted.hidden_spans),
            "dropped_count": len(profile.dropped),
            "confidence": profile.confidence,
            "confidence_message": profile.confidence_message,
            "warnings": list(profile.warnings),
        }

    def build_findings(self, resume_text: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
        findings = provenance_findings(resume_text, profile)
        application_id = profile.get("application_id")
        candidate = self._candidate_for(application_id) if application_id else None
        linkedin = profile.get("linkedin_profile")
        if linkedin and candidate is not None:
            reasons = cross_check(LinkedInProfile.model_validate(linkedin), candidate, self._config, self._today)
            for item in reasons:
                findings.append(
                    {
                        "source": "linkedin_cross_check",
                        "fact": item.code,
                        "status": "mismatch",
                        "evidence": item.detail,
                    }
                )
        if application_id and candidate is not None and (resume_text or "").strip():
            score = self._score_for(application_id)
            if score is not None and should_run(score, self._config, self._env):
                self._passive(findings, resume_text, candidate, application_id, profile)
        return findings

    def _passive(
        self,
        findings: list[dict[str, Any]],
        resume_text: str,
        candidate: Candidate,
        application_id: str,
        profile: dict[str, Any],
    ) -> None:
        facts = extract_facts(resume_text, candidate)
        result = run_passive_intel(
            facts,
            application_id=application_id,
            consent_given=True,
            transport=self._transport,
            env=self._env,
        )
        index: dict[int, str] = {}
        for item in result.findings:
            index[len(findings)] = item.finding_id
            findings.append(
                {
                    "source": item.source,
                    "fact": ", ".join(item.matched_facts),
                    "status": STATUS_FROM_INTEL[item.status],
                    "evidence": f"{item.summary} Score impact: 0.",
                    "source_url": item.source_url,
                    "fetched_at": item.fetched_at.isoformat(),
                    "intel_finding_id": item.finding_id,
                }
            )
        profile["intel_result"] = result
        profile["intel_index"] = index
        profile["extra_scopes"] = [LOOKUP_SCOPE]
        profile["lookup_report"] = render_findings(result)

    def record_consent(self, application_id: str | None, scopes: list[str], **extra: Any) -> str:
        consent_id = secrets.token_urlsafe(18)
        with self._lock:
            self._consents[consent_id] = {
                "application_id": application_id,
                "scopes": list(scopes),
                "at": time.time(),
                "result": extra.get("intel_result"),
                "index": dict(extra.get("intel_index") or {}),
            }
            while len(self._consents) > MAX_TRACKED_CONSENTS:
                self._consents.popitem(last=False)
        return consent_id

    def _update_result(self, consent_id: str, finding_index: int, change: Callable[[IntelResult, str], IntelResult]) -> bool:
        with self._lock:
            entry = self._consents.get(consent_id)
            if entry is None or finding_index < 0:
                return False
            finding_id = entry["index"].get(finding_index)
            if entry["result"] is not None and finding_id is not None:
                entry["result"] = change(entry["result"], finding_id)
            return True

    def dispute(self, consent_id: str, finding_index: int, note: str) -> bool:
        return self._update_result(consent_id, finding_index, lambda result, fid: dispute(result, fid, note))

    def recheck(self, consent_id: str, finding_index: int, outcome: str) -> bool:
        def change(result: IntelResult, finding_id: str) -> IntelResult:
            updated = result.model_copy(deep=True)
            for item in updated.findings:
                if item.finding_id == finding_id:
                    item.needs_human_recheck = False
                    if outcome == "withdrawn":
                        item.disputed = False
                        item.hidden_from_recruiter = False
            return updated

        return self._update_result(consent_id, finding_index, change)
