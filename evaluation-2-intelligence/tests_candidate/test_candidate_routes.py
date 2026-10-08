from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from firewall.api import app
from firewall.candidate_routes import JOB_PRESETS, delta_message, plain, title_for
from firewall.reasoning import EXPLANATIONS
from tests_candidate.conftest import FORBIDDEN


def email() -> str:
    return f"p{uuid.uuid4().hex[:8]}@example.com"


def assert_plain(text: str) -> None:
    assert text
    assert not any(char in text for char in FORBIDDEN), text


def submit(session, make_pdf, hidden: bool = False, **extra):
    response = session.upload(make_pdf("Maya Raman", email(), hidden), **extra)
    assert response.status_code == 200, response.text
    return response.json()


def test_ownership_isolation(alice, bob, make_pdf):
    mine = submit(alice, make_pdf)["application_id"]
    theirs = submit(bob, make_pdf)["application_id"]
    listed = [item["application_id"] for item in alice.get("/v1/me/applications").json()]
    assert listed == [mine]
    assert [item["application_id"] for item in bob.get("/v1/me/applications").json()] == [theirs]
    assert alice.get(f"/v1/me/applications/{mine}").status_code == 200
    assert alice.get(f"/v1/me/applications/{theirs}").status_code == 404
    assert alice.get(f"/v1/me/applications/{theirs}/delta").status_code == 404
    assert alice.get("/v1/me/applications/nope").status_code == 404


def test_list_shape_newest_first_and_summary(alice, make_pdf):
    assert alice.get("/v1/me/summary").json() == {"applications": 0, "best_score": None, "latest_route": None}
    first = submit(alice, make_pdf)
    second = submit(alice, make_pdf, hidden=True)
    items = alice.get("/v1/me/applications").json()
    assert [item["application_id"] for item in items] == [second["application_id"], first["application_id"]]
    item = items[0]
    assert set(item) == {
        "application_id", "job_id", "submitted_at", "score", "route", "summary",
        "concern_count", "fix_count", "replaces", "is_test",
    }
    assert item["is_test"] is False and item["replaces"] is None
    assert item["score"] == second["score"] and item["route"] == second["route"]
    summary = alice.get("/v1/me/summary").json()
    assert summary["applications"] == 2
    assert summary["best_score"] == max(first["score"], second["score"])
    assert summary["latest_route"] == second["route"]


def test_own_detail_matches_decision(alice, make_pdf):
    decision = submit(alice, make_pdf, hidden=True)
    detail = alice.get(f"/v1/me/applications/{decision['application_id']}").json()
    assert detail["score"] == decision["score"]
    assert detail["route"] == decision["route"]
    assert "reasons" in detail and "summary" in detail


def test_delta_when_hidden_text_removed(alice, make_pdf):
    first = submit(alice, make_pdf, hidden=True)
    stored_before = alice.get(f"/v1/me/applications/{first['application_id']}").json()
    second = submit(alice, make_pdf, hidden=False, replaces=first["application_id"])
    delta = alice.get(f"/v1/me/applications/{second['application_id']}/delta")
    assert delta.status_code == 200
    body = delta.json()
    assert set(body) == {
        "previous_id", "score_before", "score_after", "score_change", "cleared", "new", "unchanged_count", "message",
    }
    assert body["previous_id"] == first["application_id"]
    assert body["score_before"] == first["score"]
    assert body["score_after"] == second["score"]
    assert body["score_change"] == second["score"] - first["score"]
    assert len(body["cleared"]) >= 1
    assert any("hidden" in item["title"].lower() or "screening" in item["title"].lower() for item in body["cleared"])
    for group in (body["cleared"], body["new"]):
        for item in group:
            assert set(item) == {"title", "explanation"}
            assert_plain(item["title"])
            assert_plain(item["explanation"])
            assert not item["title"].isupper() and "_" not in item["title"]
    assert_plain(body["message"])
    assert f"{len(body['cleared'])}" in body["message"]
    assert alice.get(f"/v1/me/applications/{first['application_id']}").json() == stored_before
    listed = {item["application_id"]: item for item in alice.get("/v1/me/applications").json()}
    assert listed[second["application_id"]]["replaces"] == first["application_id"]


def test_delta_without_replaces_is_404(alice, make_pdf):
    only = submit(alice, make_pdf)
    response = alice.get(f"/v1/me/applications/{only['application_id']}/delta")
    assert response.status_code == 404
    assert_plain(response.json()["detail"])


def test_replaces_validation(alice, bob, make_pdf):
    theirs = submit(bob, make_pdf)["application_id"]
    for bad in (theirs, "does-not-exist"):
        response = alice.upload(make_pdf("Maya Raman", email(), False), replaces=bad)
        assert response.status_code == 400
        assert_plain(response.json()["detail"])
    own = submit(alice, make_pdf)["application_id"]
    response = alice.upload(make_pdf("Maya Raman", email(), False), replaces=own, application_id=own)
    assert response.status_code == 400
    assert alice.upload(make_pdf("Maya Raman", email(), False), replaces=own).status_code == 200


def test_upload_hygiene_messages(alice, make_pdf):
    empty = alice.upload(b"")
    assert empty.status_code == 400
    wrong = alice.upload(b"hello", filename="resume.exe")
    assert wrong.status_code == 415
    huge = alice.upload(b"a" * (5 * 1024 * 1024 + 10), filename="resume.txt")
    assert huge.status_code == 413
    broken = alice.upload(b"%PDF-1.4 this is not really a pdf", filename="resume.pdf")
    assert broken.status_code == 400
    for response in (empty, wrong, huge, broken):
        assert_plain(response.json()["detail"])
    assert alice.get("/v1/me/applications").json() == []


def test_job_presets(alice, recruiter):
    response = alice.get("/v1/job-presets")
    assert response.status_code == 200
    presets = response.json()
    assert [item["id"] for item in presets] == ["software", "finance", "hardware", "design", "operations"]
    for item in presets:
        assert item["must_have"] and item["nice_to_have"] and item["min_years"] > 0
        assert_plain(item["title"])
    assert recruiter.get("/v1/job-presets").status_code == 200
    assert presets == JOB_PRESETS


def test_recruiter_and_admin_get_403(recruiter, admin):
    for session in (recruiter, admin):
        for path in ("/v1/me/applications", "/v1/me/summary", "/v1/me/applications/x", "/v1/me/applications/x/delta"):
            assert session.get(path).status_code == 403


def test_anonymous_gets_401():
    anonymous = TestClient(app)
    for path in ("/v1/me/applications", "/v1/me/summary", "/v1/me/applications/x", "/v1/me/applications/x/delta", "/v1/job-presets"):
        assert anonymous.get(path).status_code == 401


def test_wording_helpers_are_plain():
    for code in EXPLANATIONS:
        assert_plain(title_for(code))
        assert_plain(plain(EXPLANATIONS[code]))
    assert_plain(title_for("SOMETHING_NEW"))
    assert delta_message(50, 72, 3, 0) == "Your score went up by 22 points. 3 concerns are gone."
    assert delta_message(72, 71, 0, 1) == "Your score went down by 1 point. 1 new concern appeared."
    assert delta_message(60, 60, 0, 0) == "Your score stayed the same. No concerns were added or removed."
    assert plain("a-b (c): d; e") == "a b c, d, e"
