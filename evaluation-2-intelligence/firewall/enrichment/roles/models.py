from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class QuotedItem(BaseModel):
    kind: str
    value: str
    quote: str
    line: int


class MembershipClaim(BaseModel):
    body: str
    number: str
    quote: str


class DirectorshipClaim(BaseModel):
    company: str
    quote: str


class PendingReference(BaseModel):
    reference_name: str
    reference_email: str
    employer: str | None = None
    source_quote: str
    status: Literal["pending"] = "pending"
    sent: bool = False
    planned_action: str = "request employer reference after candidate consent"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RoleClaims(BaseModel):
    credential_ids: list[QuotedItem] = Field(default_factory=list)
    memberships: list[MembershipClaim] = Field(default_factory=list)
    directorships: list[DirectorshipClaim] = Field(default_factory=list)
    dins: list[QuotedItem] = Field(default_factory=list)
    regulator_ids: list[QuotedItem] = Field(default_factory=list)
    patents: list[QuotedItem] = Field(default_factory=list)
    arxiv_ids: list[QuotedItem] = Field(default_factory=list)
    ieee_dois: list[QuotedItem] = Field(default_factory=list)
    orcid_ids: list[QuotedItem] = Field(default_factory=list)
    awards: list[QuotedItem] = Field(default_factory=list)
    case_studies: list[QuotedItem] = Field(default_factory=list)
    references: list[PendingReference] = Field(default_factory=list)
