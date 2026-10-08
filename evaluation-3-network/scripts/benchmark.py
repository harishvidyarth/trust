from __future__ import annotations

import sys
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from firewall.config import Config
from firewall.engine import evaluate
from firewall.models import Application, Candidate, Experience, JobRequirements, Project, SubmissionSignals
from firewall.store import InMemoryApplicationStore


CHECKPOINTS = {500, 1000, 2500, 5000}


def synthetic_application(index: int) -> Application:
    copied_description = "Built a reliable shared screening project with retries dashboards alerts and tests"
    return Application(
        application_id=f"benchmark-{index}",
        job_id="shared-platform-job",
        candidate=Candidate(
            name=f"Candidate {index}",
            email=f"candidate{index}@example.com",
            phone=str(7_000_000_000 + index),
            skills=["Python", "Kubernetes"],
            experience=[Experience(company="Benchmark Co", title="Engineer", start="2020-01", end="2025-01")],
            projects=[Project(name=f"project{index}", description=copied_description)],
        ),
        signals=SubmissionSignals(
            device_id=f"device-{index}",
            ip=f"benchmark-ip-{index}",
            session_seconds=120,
            paste_char_ratio=0.1,
            submitted_at=1_767_225_600 + index * 2,
        ),
    )


def main() -> None:
    store = InMemoryApplicationStore()
    config = Config()
    job = JobRequirements(must_have_skills=["Python", "Kubernetes"], min_years=3)
    started = perf_counter()
    elapsed = 0.0
    for index in range(1, 5001):
        evaluate(synthetic_application(index), job, store, config)
        if index in CHECKPOINTS:
            elapsed = perf_counter() - started
            print(f"{index}: {elapsed:.3f}s cumulative")
    print(f"apps/sec: {5000 / elapsed:.1f}")


if __name__ == "__main__":
    main()
