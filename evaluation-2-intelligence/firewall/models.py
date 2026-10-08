from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, PrivateAttr


class Experience(BaseModel):
    company: str
    title: str
    start: str
    end: str


class Project(BaseModel):
    name: str
    description: str


class Candidate(BaseModel):
    _qualification_only_experience: bool = PrivateAttr(default=False)

    name: str
    email: str
    phone: str
    skills: list[str] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    claimed_experience_years: float | None = Field(default=None, ge=0)


class SubmissionSignals(BaseModel):
    device_id: str
    ip: str
    session_seconds: float = Field(ge=0)
    paste_char_ratio: float = Field(ge=0, le=1)
    submitted_at: float


class Application(BaseModel):
    application_id: str
    job_id: str
    candidate: Candidate
    signals: SubmissionSignals


class JobRequirements(BaseModel):
    must_have_skills: list[str] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)
    min_years: float = Field(default=0, ge=0)


class Route(StrEnum):
    PASS_TO_ATS = "PASS_TO_ATS"
    ADDITIONAL_VERIFICATION = "ADDITIONAL_VERIFICATION"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class Reason(BaseModel):
    code: str
    severity: str
    detail: str
    weight: int = Field(ge=0)
    explanation: str = ""
    evidence: str = ""


class HiddenIntent(BaseModel):
    span_index: int = Field(ge=0)
    hidden_reason: str
    label: Literal["keyword_stuffing", "screener_instruction", "harmless"]
    evidence: str


class ResumeAgreement(BaseModel):
    score: Annotated[float, Field(ge=0, le=100)]
    label: str


class Decision(BaseModel):
    application_id: str
    score: Annotated[int, Field(ge=0, le=100)]
    route: Route
    reasons: list[Reason] = Field(default_factory=list)
    summary: str
    recruiter_summary: str | None = None
    candidate_fixes: list[str] | None = None
    hidden_intent: list[HiddenIntent] = Field(default_factory=list)
    agreement: ResumeAgreement | None = None
    llm_used: bool = False


class EvaluationRequest(BaseModel):
    application: Application
    job: JobRequirements
