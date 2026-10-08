from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class ProjectRepoClaim(BaseModel):
    name: str
    claimed_start: str | None = None
    claimed_end: str | None = None


class DoiClaim(BaseModel):
    doi: str
    claimed_title: str


class PaperClaim(BaseModel):
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None


class Claims(BaseModel):

    application_name: str | None = None
    github_username: str | None = None
    project_repos: list[ProjectRepoClaim] = Field(default_factory=list)
    dois: list[DoiClaim] = Field(default_factory=list)
    papers: list[PaperClaim] = Field(default_factory=list)
    portfolio_url: str | None = None
    oidc_verified_name: str | None = None
    oidc_verified_email: str | None = None
    employers: list[str] = Field(default_factory=list)


class EnrichmentSignal(BaseModel):
    code: str
    polarity: Literal["negative", "positive"]
    severity: str
    confidence: float = Field(ge=0, le=1)
    source: str
    detail: str
    evidence_url: str | None = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EnrichmentSummary(BaseModel):
    ran: list[str] = Field(default_factory=list)
    timed_out: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)
    cached: list[str] = Field(default_factory=list)
