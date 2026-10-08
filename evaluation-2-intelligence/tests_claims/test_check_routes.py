from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from firewall.auth.deps import Principal
from firewall.check_routes import build_check_router
from firewall.enrichment.models import EnrichmentSignal, EnrichmentSummary
from firewall.models import Candidate, Decision, Route
from firewall.redis_layer import JsonCache

FORBIDDEN = re.compile(r"[-()\[\]{};:]")
TEXT = "Backend developer. GitHub github.com/octocat built the project Hello-World. Python and FastAPI."


def signal(code: str, polarity: str, source: str) -> EnrichmentSignal:
    return EnrichmentSignal(
        code=code,
        polarity=polarity,
        severity="low",
        confidence=1.0,
        source=source,
        detail="detail",
        matched_claim="octocat",
        evidence_url="https://github.com/octocat",
        fetched_at=datetime.now(timezone.utc),
    )


def candidate() -> Candidate:
    return Candidate(name="Test Person", email="t@example.com", phone="+91 90000 00000", skills=["Python", "FastAPI"], experience=[], projects=[])


def build(user: Principal, signals=None, text=TEXT, owner="alice"):
    decision = Decision(application_id="a-1", score=60, route=Route.ADDITIONAL_VERIFICATION, reasons=[], summary="s")

    def fake_enrich(claims, connectors=None, per_connector_timeout=8.0):
        return (signals if signals is not None else []), EnrichmentSummary()

    app = FastAPI()
    app.include_router(
        build_check_router(
            runner_guard=lambda: user,
            reader_guard=lambda: user,
            decision_for=lambda application_id: decision if application_id == "a-1" else None,
            text_for=lambda application_id: text,
            candidate_for=lambda application_id: candidate(),
            owner_of=lambda application_id: owner,
            cache=JsonCache(),
            enrich_function=fake_enrich,
            connector_factory=lambda profile, body, person: [],
        )
    )
    return TestClient(app)


def alice() -> Principal:
    return Principal(username="alice", role="candidate", via="session")


def test_run_returns_plain_checks_and_counts():
    client = build(alice(), [signal("GITHUB_CORROBORATED", "positive", "github"), signal("DOI_NOT_FOUND", "negative", "crossref")])
    body = client.post("/v1/checks/run", json={"application_id": "a-1"}).json()
    assert body["counts"]["confirmed"] == 1 and body["counts"]["problem"] == 1
    sources = {item["source"] for item in body["checks"]}
    assert {"GitHub", "Research papers"} <= sources
    assert body["role_profile"] in {"general", "finance", "hardware", "sales_ops_design"}
    for item in body["checks"]:
        assert not FORBIDDEN.search(item["explanation"]), item
        assert not FORBIDDEN.search(item["title"])


def test_missing_github_is_shown_as_not_checked_with_advice():
    client = build(alice(), [], text="Backend developer. Python and FastAPI.")
    body = client.post("/v1/checks/run", json={"application_id": "a-1"}).json()
    github = [item for item in body["checks"] if item["source"] == "GitHub"]
    assert github and github[0]["status"] == "not_checked" and "GitHub link" in github[0]["explanation"]
    assert body["github_found"] is False


def test_second_run_too_soon_is_refused_and_read_returns_stored():
    client = build(alice(), [signal("GITHUB_CORROBORATED", "positive", "github")])
    assert client.get("/v1/checks/application/a-1").status_code == 404
    assert client.post("/v1/checks/run", json={"application_id": "a-1"}).status_code == 200
    assert client.post("/v1/checks/run", json={"application_id": "a-1"}).status_code == 429
    assert client.get("/v1/checks/application/a-1").json()["counts"]["confirmed"] == 1


def test_other_candidate_cannot_run_or_read():
    other = Principal(username="bob", role="candidate", via="session")
    client = build(other)
    assert client.post("/v1/checks/run", json={"application_id": "a-1"}).status_code == 404
    assert client.get("/v1/checks/application/a-1").status_code == 404


def test_recruiter_can_run_for_any_application_and_unknown_id_is_404():
    recruiter = Principal(username="rita", role="recruiter", via="session")
    client = build(recruiter, [signal("GITHUB_CORROBORATED", "positive", "github")])
    assert client.post("/v1/checks/run", json={"application_id": "a-1"}).status_code == 200
    assert client.post("/v1/checks/run", json={"application_id": "nope"}).status_code == 404


def test_lookup_failure_still_returns_not_checked_list():
    decision = Decision(application_id="a-1", score=60, route=Route.ADDITIONAL_VERIFICATION, reasons=[], summary="s")

    def broken(claims, connectors=None, per_connector_timeout=8.0):
        raise RuntimeError("network down")

    app = FastAPI()
    user = alice()
    app.include_router(
        build_check_router(
            runner_guard=lambda: user,
            reader_guard=lambda: user,
            decision_for=lambda application_id: decision,
            text_for=lambda application_id: TEXT,
            candidate_for=lambda application_id: candidate(),
            owner_of=lambda application_id: "alice",
            cache=JsonCache(),
            enrich_function=broken,
            connector_factory=lambda profile, body, person: [],
        )
    )
    body = TestClient(app).post("/v1/checks/run", json={"application_id": "a-1"}).json()
    assert body["counts"]["confirmed"] == 0 and body["checks"]


def run_with(text, email):
    decision = Decision(application_id="a-1", score=60, route=Route.ADDITIONAL_VERIFICATION, reasons=[], summary="s")
    seen = {}

    def fake_enrich(claims, connectors=None, per_connector_timeout=8.0):
        seen["claims"] = claims
        return [], EnrichmentSummary()

    person = Candidate(name="Test Person", email=email, phone="+91 90000 00000", skills=["Python"], experience=[], projects=[])
    user = Principal(username="rita", role="recruiter", via="session")
    app = FastAPI()
    app.include_router(
        build_check_router(
            runner_guard=lambda: user,
            reader_guard=lambda: user,
            decision_for=lambda application_id: decision,
            text_for=lambda application_id: text,
            candidate_for=lambda application_id: person,
            owner_of=lambda application_id: "alice",
            cache=JsonCache(),
            enrich_function=fake_enrich,
            connector_factory=lambda profile, body, who: [],
        )
    )
    return TestClient(app).post("/v1/checks/run", json={"application_id": "a-1"}).json(), seen["claims"]


def test_every_gap_offers_a_plain_request_the_recruiter_can_send():
    body, _ = run_with("Backend developer. Python.", "t@gmail.com")
    rows = [item for item in body["checks"] if item["status"] == "not_checked"]
    assert len(rows) >= 3
    for item in rows:
        assert item["ask_label"] and 3 <= len(item["ask_label"]) <= 200
        assert not FORBIDDEN.search(item["ask_label"]), item["ask_label"]


def test_work_email_domain_is_used_as_the_employer_website_but_free_mail_is_not():
    _, work = run_with("Backend developer. Python.", "t@acme-labs.com")
    assert "acme-labs.com" in work.employer_domains
    _, free = run_with("Backend developer. Python.", "t@gmail.com")
    assert free.employer_domains == []


def test_any_other_website_in_the_resume_is_treated_as_a_portfolio_link():
    _, claims = run_with("My work: https://maya-rao.example/work and github.com/octocat and linkedin.com/in/maya", "t@gmail.com")
    assert claims.portfolio_url == "https://maya-rao.example/work"
