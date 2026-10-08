from __future__ import annotations

from pathlib import Path

import pytest

from eval.run_eval import compute_metrics, run_ablations
from firewall.config import Config
from firewall.models import Application, Candidate, Experience, JobRequirements, Project, SubmissionSignals


def test_metric_function_on_tiny_handmade_case():
    rows = [
        {"label": "ABUSE", "route": "MANUAL_REVIEW"},
        {"label": "ABUSE", "route": "PASS_TO_ATS"},
        {"label": "LEGIT", "route": "ADDITIONAL_VERIFICATION"},
        {"label": "LEGIT", "route": "PASS_TO_ATS"},
    ]

    metrics = compute_metrics(rows)

    assert metrics["tp"] == 1
    assert metrics["fp"] == 1
    assert metrics["fn"] == 1
    assert metrics["tn"] == 1
    assert metrics["precision"] == pytest.approx(0.5)
    assert metrics["recall"] == pytest.approx(0.5)
    assert metrics["f1"] == pytest.approx(0.5)
    assert metrics["false_positive_rate"] == pytest.approx(0.5)


def test_automation_ablation_removes_hard_escalation_reason() -> None:
    records = []
    for index in range(2):
        application = Application(
            application_id=f"app-{index}",
            job_id="job-1",
            candidate=Candidate(
                name=f"Candidate {index}",
                email=f"candidate{index}@example.com",
                phone=f"900000000{index}",
                skills=["Python"],
                experience=[Experience(company=f"Company {index}", title="Engineer", start="2020-01", end="2022-01")],
                projects=[
                    Project(
                        name=f"Project {index}",
                        description=(
                            "Built lunar telemetry reconciliation for orbital sensor payloads"
                            if index == 0
                            else "Created botanical archive indexing for regional seed libraries"
                        ),
                    )
                ],
            ),
            signals=SubmissionSignals(
                device_id="shared-device",
                ip=f"203.0.113.{index + 1}",
                session_seconds=120,
                paste_char_ratio=0.1,
                submitted_at=1_700_000_000 + index,
            ),
        )
        records.append(
            {
                "application": application.model_dump(),
                "job": JobRequirements(must_have_skills=["Python"], min_years=0).model_dump(),
                "class": "automation",
                "label": "ABUSE",
            }
        )

    baseline = {
        "count": 2,
        "tp": 1,
        "fp": 0,
        "fn": 1,
        "tn": 0,
        "precision": 1.0,
        "recall": 0.5,
        "f1": 2 / 3,
        "false_positive_rate": 0.0,
    }
    results = run_ablations(records, Config(velocity_limit=1), baseline)
    without_automation = next(item for item in results if item["group"] == "without_automation")

    assert without_automation["recall"] == 0.0
    assert without_automation["fn"] == 2


def test_eval_requirements_exclude_unused_dataframe_dependencies() -> None:
    requirements = (Path(__file__).parents[1] / "requirements.txt").read_text(encoding="utf-8").splitlines()

    assert "scikit-learn" not in requirements
    assert "pandas" not in requirements
