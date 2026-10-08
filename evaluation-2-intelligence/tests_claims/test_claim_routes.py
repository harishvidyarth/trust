from __future__ import annotations

import re

from fastapi.testclient import TestClient

from firewall.api import app
from firewall.models import Route
from tests_candidate.conftest import stored_decision

GOOD = (
    "I built an employee management API using FastAPI. I implemented CRUD endpoints and used PostgreSQL "
    "for persistence. I also added validation using Pydantic and separated the API routes from the database layer."
)
FORBIDDEN = re.compile(r"[-()\[\]{};:]")


def submit(alice, make_pdf):
    first = alice.upload(make_pdf("Claim Person", "claim.person@example.com", True)).json()
    assert stored_decision(first["application_id"]).route != Route.PASS_TO_ATS
    return first["application_id"]


def post(session, path, body):
    return session.client.post(path, json=body, headers=session.headers)


def test_candidate_question_view_has_no_claim_labels(alice, make_pdf):
    application_id = submit(alice, make_pdf)
    asked = post(alice, "/v1/claims/question", {"application_id": application_id}).json()
    assert set(asked) == {"question", "attempts_left", "answered"}
    assert asked["question"] and asked["attempts_left"] == 3 and asked["answered"] is False


def test_verify_returns_only_receipt_to_candidate(alice, make_pdf):
    application_id = submit(alice, make_pdf)
    answered = post(alice, "/v1/claims/verify", {"application_id": application_id, "response": GOOD})
    assert answered.status_code == 200, answered.text
    body = answered.json()
    assert set(body) == {"received", "message", "attempts_left"}
    assert body["received"] is True and body["attempts_left"] == 2
    assert not FORBIDDEN.search(body["message"])
    again = post(alice, "/v1/claims/question", {"application_id": application_id}).json()
    assert again["answered"] is True and again["attempts_left"] == 2


def test_attempt_limit(alice, make_pdf):
    application_id = submit(alice, make_pdf)
    for _ in range(3):
        assert post(alice, "/v1/claims/verify", {"application_id": application_id, "response": "I know FastAPI."}).status_code == 200
    assert post(alice, "/v1/claims/verify", {"application_id": application_id, "response": GOOD}).status_code == 429


def test_other_candidate_cannot_touch_it(alice, bob, make_pdf):
    application_id = submit(alice, make_pdf)
    assert post(bob, "/v1/claims/question", {"application_id": application_id}).status_code == 404
    assert post(bob, "/v1/claims/verify", {"application_id": application_id, "response": GOOD}).status_code == 404
    assert bob.get(f"/v1/claims/application/{application_id}").status_code == 404


def test_candidate_cannot_read_claim_results_but_recruiter_can(alice, recruiter, make_pdf):
    application_id = submit(alice, make_pdf)
    post(alice, "/v1/claims/verify", {"application_id": application_id, "response": GOOD})
    assert alice.get(f"/v1/claims/application/{application_id}").status_code == 404
    seen = recruiter.get(f"/v1/claims/application/{application_id}")
    assert seen.status_code == 200
    assert seen.json()["evidence_terms_found"] and "answer_score" in seen.json()
    assert post(recruiter, "/v1/claims/verify", {"application_id": application_id, "response": GOOD}).status_code == 403
    for item in [seen.json()["summary"], seen.json()["advice"], seen.json()["note"]]:
        assert not FORBIDDEN.search(item), item


def test_empty_and_long_answers_are_refused_plainly(alice, make_pdf):
    application_id = submit(alice, make_pdf)
    empty = post(alice, "/v1/claims/verify", {"application_id": application_id, "response": "   "})
    assert empty.status_code == 400 and "Please" in empty.json()["detail"]
    long = post(alice, "/v1/claims/verify", {"application_id": application_id, "response": "x" * 5000})
    assert long.status_code == 400 or long.status_code == 422


def test_anonymous_is_refused_and_stored_decision_unchanged(alice, make_pdf):
    application_id = submit(alice, make_pdf)
    assert TestClient(app).post("/v1/claims/question", json={"application_id": application_id}).status_code == 401
    before = stored_decision(application_id)
    post(alice, "/v1/claims/verify", {"application_id": application_id, "response": GOOD})
    after = stored_decision(application_id)
    assert (before.score, before.route) == (after.score, after.route)


def test_answer_text_is_not_stored(alice, recruiter, make_pdf):
    application_id = submit(alice, make_pdf)
    marker = "UniqueMarkerSentenceForStorageCheck"
    post(alice, "/v1/claims/verify", {"application_id": application_id, "response": GOOD + " " + marker})
    assert marker not in recruiter.get(f"/v1/claims/application/{application_id}").text
