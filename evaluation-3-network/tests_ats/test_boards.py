from __future__ import annotations

import json
from pathlib import Path

import httpx

from firewall.connectors.boards import (
    GreenhouseJobBoardClient,
    LeverJobBoardClient,
    to_job_requirements,
)


FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


def test_greenhouse_client_and_requirements_from_recorded_fixture() -> None:
    jobs = load_fixture("greenhouse_jobs.json")
    detail = load_fixture("greenhouse_job.json")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = detail if request.url.path.endswith("/101") else jobs
        return httpx.Response(200, json=payload)

    client = GreenhouseJobBoardClient(
        "sample",
        transport=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert client.list_jobs()[0]["title"] == "Platform Engineer"
    job = client.get_job(101)
    assert requests[-1].url.params["questions"] == "true"
    assert to_job_requirements(job).model_dump() == {
        "must_have_skills": ["Kubernetes", "Python"],
        "nice_to_have": ["AWS", "Terraform"],
        "min_years": 5.0,
    }


def test_lever_client_and_requirements_from_recorded_fixture() -> None:
    jobs = load_fixture("lever_jobs.json")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["mode"] == "json"
        if request.url.path.endswith("/posting-201"):
            return httpx.Response(200, json=jobs[0])
        return httpx.Response(200, json=jobs)

    client = LeverJobBoardClient(
        "sample",
        transport=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert client.list_jobs()[0]["id"] == "posting-201"
    requirements = to_job_requirements(client.get_job("posting-201"))
    assert requirements.must_have_skills == ["React", "SQL", "TypeScript"]
    assert requirements.nice_to_have == ["AWS", "GraphQL"]
    assert requirements.min_years == 3
