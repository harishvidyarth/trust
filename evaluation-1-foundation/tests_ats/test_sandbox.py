from __future__ import annotations

from fastapi.testclient import TestClient

from ats.ranker import keyword_score, rank_candidates
from ats.sandbox import create_app
from firewall.models import JobRequirements


AUTH = ("test-sandbox-key", "")


def test_sandbox_requires_basic_auth_and_labels_itself() -> None:
    client = TestClient(create_app("test-sandbox-key"))
    unauthorized = client.get("/v1/candidates")
    assert unauthorized.status_code == 401
    response = client.get("/v1/candidates", auth=AUTH)
    assert response.status_code == 200
    assert "sandbox simulator" in response.json()["notice"]


def test_greenhouse_subset_stores_candidates_and_applications() -> None:
    client = TestClient(create_app("test-sandbox-key"))
    response = client.post(
        "/v1/candidates",
        auth=AUTH,
        json={
            "first_name": "Honest",
            "last_name": "Candidate",
            "resume_text": "I use Python to build reliable services.",
            "applications": [{"job_id": "job-1", "must_have_skills": ["Python"]}],
        },
    )
    assert response.status_code == 200
    candidate = response.json()["candidate"]
    assert candidate["sandbox_simulator"] is True
    assert client.get("/v1/candidates", auth=AUTH).json()["candidates"][0]["name"] == "Honest Candidate"
    assert client.get("/v1/applications", auth=AUTH).json()["applications"][0]["job_id"] == "job-1"


def test_naive_ranker_rewards_keyword_stuffing() -> None:
    job = JobRequirements(
        must_have_skills=["Python", "Kubernetes"],
        nice_to_have=["AWS", "Terraform"],
    )
    honest = {
        "candidate_id": "honest",
        "name": "Honest Candidate",
        "resume_text": "Built reliable Python services and supported customers.",
    }
    stuffed = {
        "candidate_id": "fraud",
        "name": "Stuffed Candidate",
        "resume_text": "Python Kubernetes AWS Terraform " * 20,
    }
    ranking = rank_candidates([honest, stuffed], job)
    assert ranking[0]["candidate_id"] == "fraud"
    assert keyword_score(stuffed["resume_text"], job)[0] == 1.0
    assert ranking[0]["score"] > ranking[1]["score"]


def test_ranking_endpoint_orders_stuffed_resume_first() -> None:
    client = TestClient(create_app("test-sandbox-key"))
    requirements = {
        "job_id": "job-9",
        "must_have_skills": ["Python", "Kubernetes"],
        "nice_to_have": ["AWS"],
    }
    for name, resume in (
        ("Honest", "Built maintainable Python services."),
        ("Stuffed", "Python Kubernetes AWS Python Kubernetes AWS"),
    ):
        response = client.post(
            "/v1/candidates",
            auth=AUTH,
            json={"name": name, "resume_text": resume, "applications": [requirements]},
        )
        assert response.status_code == 200

    response = client.get("/v1/ranking", params={"job_id": "job-9"}, auth=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert "naive keyword ranker" in body["warning"]
    assert body["ranking"][0]["name"] == "Stuffed"


def test_lever_like_endpoint() -> None:
    client = TestClient(create_app("test-sandbox-key"))
    response = client.post(
        "/lever/v1/opportunities",
        auth=AUTH,
        json={"name": "Lever Candidate", "posting": "posting-1"},
    )
    assert response.status_code == 200
    assert response.json()["candidate"]["provider_shape"] == "lever-like-subset"
