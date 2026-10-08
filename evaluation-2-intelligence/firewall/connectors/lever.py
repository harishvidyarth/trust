from __future__ import annotations

from pydantic import AliasChoices, BaseModel, Field

from firewall.models import (
    Application,
    Candidate,
    Experience,
    JobRequirements,
    Project,
    SubmissionSignals,
)


class LeverCandidate(BaseModel):
    name: str
    email: str
    phone: str
    skills: list[str] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    claimed_experience_years: float | None = None


class LeverJob(BaseModel):
    id: str
    must_have_skills: list[str] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)
    min_years: float = 0


class LeverMetadata(BaseModel):
    device_id: str
    ip: str
    submitted_at: float
    session_seconds: float = 60
    paste_char_ratio: float = 0


class LeverWebhook(BaseModel):

    id: str = Field(validation_alias=AliasChoices("id", "application_id"))
    candidate: LeverCandidate
    job: LeverJob = Field(validation_alias=AliasChoices("job", "posting"))
    metadata: LeverMetadata


def adapt(payload: LeverWebhook | dict[str, object]) -> tuple[Application, JobRequirements]:
    webhook = payload if isinstance(payload, LeverWebhook) else LeverWebhook.model_validate(payload)
    application = Application(
        application_id=webhook.id,
        job_id=webhook.job.id,
        candidate=Candidate(**webhook.candidate.model_dump()),
        signals=SubmissionSignals(**webhook.metadata.model_dump()),
    )
    requirements = JobRequirements(
        must_have_skills=webhook.job.must_have_skills,
        nice_to_have=webhook.job.nice_to_have,
        min_years=webhook.job.min_years,
    )
    return application, requirements
