from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from firewall.enrichment.models import EnrichmentSignal


class ResumeFacts(BaseModel):
    name: str = ""
    employers: list[str] = Field(default_factory=list)
    colleges: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    certs: list[str] = Field(default_factory=list)
    orcids: list[str] = Field(default_factory=list)
    dois: list[str] = Field(default_factory=list)
    repos: list[str] = Field(default_factory=list)


@dataclass
class Hit:
    source: str
    source_url: str
    fetched_at: datetime
    name: str = ""
    org: tuple[str, ...] = ()
    location: tuple[str, ...] = ()
    links: tuple[str, ...] = ()
    orcid: str = ""
    dois: tuple[str, ...] = ()
    login: str = ""
    extra: dict[str, str] = field(default_factory=dict)


class IntelFinding(BaseModel):
    finding_id: str
    source: str
    source_url: str
    fetched_at: datetime
    matched_facts: list[str]
    status: Literal["unverified_match", "confirmed"] = "unverified_match"
    weight: int = Field(default=0, ge=0, le=0)
    summary: str
    confirmation: str | None = None
    disputed: bool = False
    dispute_reason: str | None = None
    hidden_from_recruiter: bool = False
    needs_human_recheck: bool = False


class ConsentRecord(BaseModel):
    record_id: str
    application_id: str
    purpose: str
    consent_given: bool
    searched: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    kept: int = 0
    discarded: int = 0
    skipped_reason: str | None = None


class IntelResult(BaseModel):
    consent: ConsentRecord
    findings: list[IntelFinding] = Field(default_factory=list)
    signals_by_finding: dict[str, list[EnrichmentSignal]] = Field(default_factory=dict)
