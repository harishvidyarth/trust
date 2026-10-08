from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from firewall.auth.deps import Principal
from firewall.intake_intel import RealIntelService
from firewall.intake_routes import build_intake_router
from firewall.models import Candidate, Experience
from tests_intake.test_intake import pdf_file, post
from tests_intel.linkedin_samples import ada_pdf
from tests_intel.test_passive_intel import Router, mock_client

RESUME = (
    "Ada Synthetic\nLocation: London\nUniversity of Example\n"
    "Analytical Engines Staff Engineer 2020-01 to Present\nAcme Holdings Consultant 2015-01 to 2016-01\n"
    "https://github.com/ada-real\nhttps://github.com/ada-real/scheduler\ndoi: 10.1234/abc"
)
CANDIDATE = Candidate(
    name="Ada Synthetic",
    email="ada@example.com",
    phone="9000000000",
    experience=[
        Experience(company="Analytical Engines", title="Staff Engineer", start="2020-01", end="Present"),
        Experience(company="Acme Holdings", title="Consultant", start="2015-01", end="2016-01"),
    ],
)


class Audit:
    def __init__(self):
        self.events = []

    def append(self, event, actor, target="", ip="", detail=None):
        self.events.append((event, actor, target, detail))


def build(score=55, env=None, transport=None, guard=None, authorize=None, audit=None, recruiter_guard=lambda: Principal("rita", "recruiter", "test")):
    service = RealIntelService(
        candidate_for=lambda key: CANDIDATE if key == "app-1" else None,
        score_for=lambda key: score if key == "app-1" else None,
        transport=transport,
        env=env if env is not None else {"FIREWALL_ENRICH": "1"},
    )
    app = FastAPI()
    app.include_router(
        build_intake_router(
            intel=service,
            guard=guard,
            recruiter_guard=recruiter_guard,
            resume_text_for=lambda key: RESUME if key == "app-1" else "",
            authorize=authorize,
            audit=(lambda: audit) if audit is not None else None,
        )
    )
    return TestClient(app)


def refuse(request):
    raise AssertionError("passive lookup must not run")


def test_linkedin_cross_check_findings_are_shown_without_weight():
    client = build(score=90, transport=mock_client(refuse))
    body = post(client, files=pdf_file(ada_pdf()), application_id="app-1").json()
    codes = {item["fact"] for item in body["findings"] if item["source"] == "linkedin_cross_check"}
    assert "LINKEDIN_EMPLOYER_MISSING" in codes
    assert all("weight" not in item for item in body["findings"])
    assert "lookup_report" not in body
    assert body["parsed"]["name"] == "Ada Synthetic"
    assert "text" not in body["parsed"] and "profile" not in body["parsed"]
    assert body["parsed"]["experience_count"] == 2


def test_passive_runs_only_in_band_with_env_and_consent():
    router = Router()
    client = build(score=55, transport=mock_client(router))
    body = post(client, application_id="app-1").json()
    assert router.requests
    assert "What we looked up" in body["lookup_report"]
    passive = [item for item in body["findings"] if item.get("source_url")]
    assert passive and all("Score impact: 0" in item["evidence"] for item in passive)
    flat = " ".join(RESUME.split())
    assert all(part.split(":", 1)[1] in flat for item in passive for part in item["fact"].split(", "))


@pytest.mark.parametrize("score,env", [(30, {"FIREWALL_ENRICH": "1"}), (80, {"FIREWALL_ENRICH": "1"}), (55, {}), (55, {"FIREWALL_ENRICH": "0"})])
def test_passive_skipped_outside_gate(score, env):
    client = build(score=score, env=env, transport=mock_client(refuse))
    body = post(client, application_id="app-1").json()
    assert "lookup_report" not in body


def test_passive_skipped_without_consent_and_unknown_application():
    client = build(transport=mock_client(refuse))
    assert post(client, consent="false", application_id="app-1").status_code == 400
    assert "lookup_report" not in post(client, application_id="app-404").json()


def test_recheck_withdrawn_unhides_and_upheld_confirms_hide():
    audit = Audit()
    client = build(score=90, transport=mock_client(refuse), audit=audit)
    consent_id = post(
        client, github_url="https://github.com/ada-real", certificate_ids=["CERT-2024-0042"], application_id="app-1"
    ).json()["consent_id"]
    for index in (0, 1):
        client.post("/v1/intake/dispute", json={"consent_id": consent_id, "finding_index": index, "note": "no"})
    view = client.get(f"/v1/intake/{consent_id}/recruiter").json()
    assert view["withheld_count"] == 2 and view["findings"] == []
    assert [item["finding_index"] for item in view["pending_rechecks"]] == [0, 1]
    url = f"/v1/intake/{consent_id}/recheck"
    assert client.post(url, json={"finding_index": 0, "outcome": "withdrawn", "note": "ok"}).json()["outcome"] == "withdrawn"
    assert client.post(url, json={"finding_index": 1, "outcome": "upheld"}).status_code == 200
    view = client.get(f"/v1/intake/{consent_id}/recruiter").json()
    assert [item["index"] for item in view["findings"]] == [0]
    assert view["findings"][0]["status"] != "disputed" and view["findings"][0]["recheck_outcome"] == "withdrawn"
    assert view["withheld_count"] == 1 and view["pending_rechecks"] == []
    assert client.post(url, json={"finding_index": 1, "outcome": "withdrawn"}).status_code == 409
    candidate_view = client.get(f"/v1/intake/{consent_id}").json()
    assert candidate_view["findings"][1]["status"] == "disputed"
    assert candidate_view["findings"][1]["recheck"]["outcome"] == "upheld"
    assert "by" not in candidate_view["findings"][1]["recheck"]
    events = [item[0] for item in audit.events]
    assert events.count("intake_recheck") == 2 and "intake_dispute" in events and "intake_consent" in events
    assert all(item[1] in {"unknown", "rita"} for item in audit.events)


def test_recheck_validation():
    client = build(score=90, transport=mock_client(refuse))
    consent_id = post(client, github_url="https://github.com/ada-real", application_id="app-1").json()["consent_id"]
    url = f"/v1/intake/{consent_id}/recheck"
    assert client.post(url, json={"finding_index": 0, "outcome": "withdrawn"}).status_code == 409
    assert client.post(url, json={"finding_index": 9, "outcome": "withdrawn"}).status_code == 404
    assert client.post(url, json={"finding_index": 0, "outcome": "maybe"}).status_code == 422
    assert client.post("/v1/intake/nope/recheck", json={"finding_index": 0, "outcome": "upheld"}).status_code == 404


def test_recheck_needs_recruiter_guard():
    def deny():
        raise HTTPException(status_code=403, detail="forbidden")

    client = build(recruiter_guard=deny, transport=mock_client(refuse))
    assert client.post("/v1/intake/x/recheck", json={"finding_index": 0, "outcome": "upheld"}).status_code == 403


def test_candidates_cannot_touch_other_candidates():
    owners = {"app-1": "alice"}

    def guard(request: Request):
        return Principal(request.headers["x-user"], request.headers.get("x-role", "candidate"), "test")

    def authorize(principal, application_id):
        if principal.role == "candidate" and owners.get(application_id) != principal.username:
            raise HTTPException(status_code=404, detail="application not found")

    client = build(score=90, transport=mock_client(refuse), guard=guard, authorize=authorize)
    alice = {"x-user": "alice"}
    bob = {"x-user": "bob"}
    assert client.post("/v1/intake/profile", data={"consent": "true", "application_id": "app-1"}, headers=bob).status_code == 404
    created = client.post(
        "/v1/intake/profile",
        data={"consent": "true", "application_id": "app-1", "github_url": "https://github.com/ada-real"},
        headers=alice,
    ).json()
    consent_id = created["consent_id"]
    assert client.get(f"/v1/intake/{consent_id}", headers=bob).status_code == 404
    assert client.get(f"/v1/intake/{consent_id}", headers=alice).status_code == 200
    dispute = {"consent_id": consent_id, "finding_index": 0, "note": ""}
    assert client.post("/v1/intake/dispute", json=dispute, headers=bob).status_code == 404
    assert client.post("/v1/intake/dispute", json=dispute, headers=alice).status_code == 200
    assert client.get(f"/v1/intake/{consent_id}", headers={"x-user": "rita", "x-role": "recruiter"}).status_code == 200


def test_intake_is_rate_limited():
    client = build(transport=mock_client(refuse))
    codes = [post(client).status_code for _ in range(32)]
    assert codes[:30] == [200] * 30
    assert codes[30:] == [429, 429]


def test_dispute_updates_intel_result_and_recheck_restores_it():
    router = Router()
    service = RealIntelService(
        candidate_for=lambda key: CANDIDATE,
        score_for=lambda key: 55,
        transport=mock_client(router),
        env={"FIREWALL_ENRICH": "1"},
    )
    app = FastAPI()
    app.include_router(
        build_intake_router(intel=service, recruiter_guard=lambda: None, resume_text_for=lambda key: RESUME)
    )
    client = TestClient(app)
    body = post(client, application_id="app-1").json()
    consent_id = body["consent_id"]
    index = next(i for i, item in enumerate(body["findings"]) if item.get("source_url"))
    client.post("/v1/intake/dispute", json={"consent_id": consent_id, "finding_index": index, "note": "not me"})
    result = service._consents[consent_id]["result"]
    assert any(item.disputed and item.hidden_from_recruiter for item in result.findings)
    client.post(f"/v1/intake/{consent_id}/recheck", json={"finding_index": index, "outcome": "withdrawn"})
    result = service._consents[consent_id]["result"]
    assert not any(item.disputed or item.needs_human_recheck for item in result.findings)
