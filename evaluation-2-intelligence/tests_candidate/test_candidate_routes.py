from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from firewall.api import app
from firewall.applicant_routes import plain
from firewall.candidate_routes import JOB_PRESETS
from tests_candidate.conftest import FORBIDDEN, stored_decision

LIST_KEYS = {
    "application_id", "job_id", "role_title", "submitted_at", "status", "title", "message", "follow_up_count", "replaces",
}


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
    assert alice.get("/v1/me/applications/nope").status_code == 404
    assert alice.post(f"/v1/me/applications/{theirs}/answers", {"item_id": "more-details", "text": "hello"}).status_code == 404


def test_list_shape_newest_first_and_summary(alice, make_pdf):
    assert alice.get("/v1/me/summary").json() == {"applications": 0, "latest_status": None}
    first = submit(alice, make_pdf)
    second = submit(alice, make_pdf, hidden=True)
    items = alice.get("/v1/me/applications").json()
    assert [item["application_id"] for item in items] == [second["application_id"], first["application_id"]]
    for item in items:
        assert set(item) == LIST_KEYS
        assert item["replaces"] is None
        assert_plain(item["title"])
        assert_plain(item["message"])
    assert items[0]["status"] == second["status"]
    assert alice.get("/v1/me/summary").json() == {"applications": 2, "latest_status": second["status"]}


def test_own_detail_has_status_and_follow_up_but_no_scores(alice, make_pdf):
    reply = submit(alice, make_pdf, hidden=True)
    detail = alice.get(f"/v1/me/applications/{reply['application_id']}").json()
    assert set(detail) == LIST_KEYS | {"follow_up"}
    assert detail["status"] == reply["status"]
    assert detail["follow_up"] == reply["follow_up"]
    open_items = [i for i in detail["follow_up"] if i["kind"] not in {"details", "identity"} and not i["answered"]]
    assert detail["follow_up_count"] == len(open_items)
    assert stored_decision(reply["application_id"]).summary not in alice.get(f"/v1/me/applications/{reply['application_id']}").text


def test_delta_route_is_gone(alice, make_pdf):
    first = submit(alice, make_pdf)
    assert alice.get(f"/v1/me/applications/{first['application_id']}/delta").status_code == 404


def test_replaces_link_is_kept(alice, make_pdf):
    first = submit(alice, make_pdf, hidden=True)
    second = submit(alice, make_pdf, replaces=first["application_id"])
    listed = {item["application_id"]: item for item in alice.get("/v1/me/applications").json()}
    assert listed[second["application_id"]]["replaces"] == first["application_id"]


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
        for path in ("/v1/me/applications", "/v1/me/summary", "/v1/me/applications/x"):
            assert session.get(path).status_code == 403


def test_anonymous_gets_401():
    anonymous = TestClient(app)
    for path in ("/v1/me/applications", "/v1/me/summary", "/v1/me/applications/x", "/v1/job-presets"):
        assert anonymous.get(path).status_code == 401


def test_plain_helper():
    assert plain("a-b (c): d; e") == "a b c, d, e"
