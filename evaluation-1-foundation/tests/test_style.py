import inspect
import json
from pathlib import Path

from fastapi.testclient import TestClient

import firewall.engine as engine
from firewall.api import app
from firewall.resume.style import analyze_style

client = TestClient(app)
JOB = '{"must_have_skills":["python"],"nice_to_have":[],"min_years":0}'

AI_STYLE = """Results-driven software engineer with a proven track record of delivering innovative solutions.
Spearheaded the migration of legacy systems, improving efficiency by 40%.
Leveraged cutting-edge cloud technologies to streamline workflows, reducing costs by 30%.
Orchestrated cross-functional collaboration with stakeholders, increasing productivity by 50%.
Facilitated agile ceremonies, mentored junior engineers, and championed best practices across teams.
Optimized database performance, enhancing system reliability by 25%.
Utilized robust testing frameworks to ensure seamless deployments and improve quality by 35%."""

HUMAN_STYLE = """Backend dev, 4 years. Mostly Python, some Go when the team asked for it.
Moved our billing cron jobs from a single EC2 box to Celery 5.3 on ECS; nightly run went from 3h10m to about 40m.
Wrote the retry logic for the Razorpay webhook after we double-charged 17 customers in March. Postmortem is in the wiki.
On a team of 6 at Zoho Creator, I mostly did code review and fixed flaky tests in Jenkins.
Side project: a Telegram bot for my hostel mess menu, ~300 users, runs on a Raspberry Pi 4.
Not great at frontend. Learning React slowly."""

POLISHED = """Backend engineer focused on reliable, understandable systems.
Built Python and FastAPI services backed by PostgreSQL 15 and deployed with Docker on AWS.
Improved checkout reliability and reduced incident recovery time by 35%.
Developed internal APIs and mentored two graduate engineers at Beta Labs.
Created a Redis queue observability tool with actionable production alerts."""


def test_formulaic_text_scores_higher_than_specific_text():
    ai = analyze_style(AI_STYLE)
    human = analyze_style(HUMAN_STYLE)
    assert ai["label"] == "High"
    assert human["label"] == "Low"
    assert ai["score"] > human["score"] + 40


def test_polished_but_honest_text_is_not_flagged_high():
    assert analyze_style(POLISHED)["label"] != "High"


def test_short_text_has_low_confidence():
    result = analyze_style(AI_STYLE)
    assert result["word_count"] < 150
    assert result["confidence"] == "low"


def test_empty_text_is_unavailable():
    result = analyze_style("")
    assert result["score"] is None
    assert result["label"] == "Unavailable"


def test_weight_is_zero_and_disclaimer_present():
    result = analyze_style(AI_STYLE)
    assert result["trust_weight"] == 0
    assert "not proof" in result["disclaimer"]


def test_formal_tone_alone_is_not_penalised():
    formal = "I hereby submit my application. I possess extensive experience in the field of backend software engineering."
    assert analyze_style(formal)["label"] == "Low"


def test_engine_never_imports_the_style_module():
    source = inspect.getsource(engine)
    assert "style" not in source


def test_inspect_route_returns_context_but_decision_has_no_style_field():
    sample = Path("firewall/resume/samples/honest_ai_polished.pdf")
    with sample.open("rb") as handle:
        inspected = client.post("/v1/resume/inspect", files={"file": ("r.pdf", handle)}, data={"job_json": JOB}).json()
    assert inspected["ai_writing"]["trust_weight"] == 0
    with sample.open("rb") as handle:
        decision = client.post(
            "/v1/applications/upload",
            files={"file": ("r.pdf", handle)},
            data={"job_json": JOB, "device_id": "style-dev", "application_id": "style-1", "dry_run": "true"},
        ).json()
    assert "ai_writing" not in json.dumps(decision)
    assert "style" not in {reason["code"].lower() for reason in decision["reasons"]}
