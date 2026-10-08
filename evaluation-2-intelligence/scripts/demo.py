from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from firewall.config import Config
from firewall.engine import evaluate
from firewall.models import Application, Candidate, Experience, JobRequirements, Project, SubmissionSignals
from firewall.store import InMemoryApplicationStore


BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()


def application(
    application_id: str,
    name: str,
    email: str,
    phone: str,
    project: Project,
    *,
    device: str,
    ip: str,
    submitted_at: float = BASE_TIME,
    session_seconds: float = 120,
    paste_ratio: float = 0.1,
) -> Application:
    return Application(
        application_id=application_id,
        job_id="platform-engineer",
        candidate=Candidate(
            name=name,
            email=email,
            phone=phone,
            skills=["Python", "K8s", "Postgres"],
            experience=[Experience(company=f"{name} Labs", title="Engineer", start="2020-01", end="2025-01")],
            projects=[project],
        ),
        signals=SubmissionSignals(
            device_id=device,
            ip=ip,
            session_seconds=session_seconds,
            paste_char_ratio=paste_ratio,
            submitted_at=submitted_at,
        ),
    )


def main() -> None:
    config = Config(velocity_limit=3)
    store = InMemoryApplicationStore()
    job = JobRequirements(must_have_skills=["Python", "Kubernetes"], nice_to_have=["PostgreSQL"], min_years=3)
    original_project = Project(
        name="Queue Guardian",
        description="Built a reliable distributed queue monitor with retries and operational dashboards",
    )
    visible = [
        application("honest-1", "Ada Rao", "ada@example.com", "9000000001", original_project, device="ada-laptop", ip="203.0.113.1"),
        application(
            "honest-2",
            "Lin Chen",
            "lin@example.com",
            "9000000002",
            Project(name="Release Lens", description="Created deployment health checks and safe rollback automation"),
            device="lin-laptop",
            ip="203.0.113.2",
        ),
        application(
            "honest-3",
            "Sam Iyer",
            "sam@example.com",
            "9000000003",
            Project(name="Cost Map", description="Measured container utilization and reduced idle cluster capacity"),
            device="sam-laptop",
            ip="203.0.113.3",
        ),
        application(
            "near-copy",
            "Copy Candidate",
            "copy@example.com",
            "9000000004",
            original_project,
            device="copy-device",
            ip="203.0.113.4",
        ),
    ]

    decisions = [(item.application_id, evaluate(item, job, store, config)) for item in visible]

    for index in range(3):
        warmup = application(
            f"bot-warmup-{index}",
            f"Bot Warmup {index}",
            f"warmup{index}@example.com",
            f"811111{index:04d}",
            Project(name=f"Generated {index}", description=f"Background burst payload with marker unique{index}"),
            device="bot-device",
            ip="198.51.100.8",
            submitted_at=BASE_TIME + 30 + index,
        )
        evaluate(warmup, job, store, config)

    bot = application(
        "bot-burst",
        "Burst Bot",
        "burst@example.com",
        "8222222222",
        Project(name="Rapid Submit", description="Final automated burst submission payload"),
        device="bot-device",
        ip="198.51.100.8",
        submitted_at=BASE_TIME + 34,
        session_seconds=4,
        paste_ratio=0.99,
    )
    decisions.append((bot.application_id, evaluate(bot, job, store, config)))

    print(f"{'APPLICATION':<14} {'SCORE':>5}  {'ROUTE':<27} REASONS")
    print(f"{'-' * 14} {'-' * 5}  {'-' * 27} {'-' * 40}")
    for application_id, decision in decisions:
        reason_codes = ",".join(item.code for item in decision.reasons) or "-"
        print(f"{application_id:<14} {decision.score:>5}  {decision.route.value:<27} {reason_codes}")


if __name__ == "__main__":
    main()
