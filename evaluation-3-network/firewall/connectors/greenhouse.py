from __future__ import annotations

from pydantic import BaseModel, Field

from firewall.models import (
    Application,
    Candidate,
    Experience,
    JobRequirements,
    Project,
    SubmissionSignals,
)


class GreenhouseCandidate(BaseModel):
    name: str
    email: str
    phone: str
    skills: list[str] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    claimed_experience_years: float | None = None


class GreenhouseJob(BaseModel):
    id: str
    must_have_skills: list[str] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)
    min_years: float = 0


class GreenhouseMetadata(BaseModel):
    device_id: str
    ip: str
    submitted_at: float
    session_seconds: float = 60
    paste_char_ratio: float = 0


class GreenhouseWebhook(BaseModel):
    id: str
    candidate: GreenhouseCandidate
    job: GreenhouseJob
    metadata: GreenhouseMetadata


def adapt(payload: GreenhouseWebhook) -> tuple[Application, JobRequirements]:
    application = Application(
        application_id=payload.id,
        job_id=payload.job.id,
        candidate=Candidate(**payload.candidate.model_dump()),
        signals=SubmissionSignals(**payload.metadata.model_dump()),
    )
    job = JobRequirements(
        must_have_skills=payload.job.must_have_skills,
        nice_to_have=payload.job.nice_to_have,
        min_years=payload.job.min_years,
    )
    return application, job
