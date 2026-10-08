from __future__ import annotations

from datetime import datetime, timezone

import pytest

from firewall.models import Application, Candidate, Experience, JobRequirements, Project, SubmissionSignals


BASE_TS = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()


def make_application(
    application_id: str = "app-1",
    job_id: str = "job-1",
    *,
    name: str = "Ada Lovelace",
    email: str = "ada@example.com",
    phone: str = "+91 98765 43210",
    skills: list[str] | None = None,
    experience: list[Experience] | None = None,
    projects: list[Project] | None = None,
    device_id: str = "device-1",
    ip: str = "203.0.113.1",
    session_seconds: float = 120,
    paste_char_ratio: float = 0.2,
    submitted_at: float = BASE_TS,
    claimed_experience_years: float | None = None,
) -> Application:
    return Application(
        application_id=application_id,
        job_id=job_id,
        candidate=Candidate(
            name=name,
            email=email,
            phone=phone,
            skills=skills if skills is not None else ["Python", "K8s"],
            experience=experience
            if experience is not None
            else [Experience(company="Analytical Engines", title="Engineer", start="2020-01", end="2025-01")],
            projects=projects
            if projects is not None
            else [Project(name="Scheduler", description="Built a reliable distributed job scheduling service in modern Python")],
            claimed_experience_years=claimed_experience_years,
        ),
        signals=SubmissionSignals(
            device_id=device_id,
            ip=ip,
            session_seconds=session_seconds,
            paste_char_ratio=paste_char_ratio,
            submitted_at=submitted_at,
        ),
    )


def make_job(**overrides: object) -> JobRequirements:
    values: dict[str, object] = {"must_have_skills": ["Python", "Kubernetes"], "nice_to_have": [], "min_years": 3}
    values.update(overrides)
    return JobRequirements(**values)


@pytest.fixture
def application_factory():
    return make_application


@pytest.fixture
def job_factory():
    return make_job
