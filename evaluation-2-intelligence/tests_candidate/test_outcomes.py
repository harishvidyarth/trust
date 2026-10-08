from __future__ import annotations

import json
import uuid

import fakeredis
import pytest

from firewall.api import OUTCOMES, STORE
from firewall.auth import get_service
from firewall.auth.records import OUTCOME_TTL_S, OutcomeConflict, OutcomeStore
from firewall.redis_layer import RedisLink, build_cache
from tests_candidate.conftest import FORBIDDEN, Session, form_fields, stored_decision

REASON = "Role requirements were not met by this applicant"
SECRET_REASON = "zebra quartz internal note about fit"
CLOSED_MESSAGE = (
    "The hiring team has decided not to move forward with your application at this time. We appreciate the time you took."
)


def email() -> str:
    return f"o{uuid.uuid4().hex[:8]}@example.com"


def submit(session: Session, make_pdf, hidden: bool = False) -> str:
    response = session.upload(make_pdf("Maya Raman", email(), hidden))
    assert response.status_code == 200, response.text
    return response.json()["application_id"]


def reject(recruiter: Session, application_id: str, reason: str = REASON):
    return recruiter.post(f"/v1/decisions/{application_id}/reject", {"reason": reason})


def reopen(recruiter: Session, application_id: str, reason: str = REASON):
    return recruiter.post(f"/v1/decisions/{application_id}/reopen", {"reason": reason})


def assert_plain(text: str) -> None:
    assert text
    assert not any(char in text for char in FORBIDDEN), text


def test_reject_happy_path_and_shape(alice, recruiter, make_pdf):
    application_id = submit(alice, make_pdf)
    decision = stored_decision(application_id)
    response = reject(recruiter, application_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"application_id", "outcome", "reason", "by", "at", "original"}
    assert body["outcome"] == "REJECTED"
    assert body["by"] == recruiter.username
    assert body["reason"] == REASON
    assert body["original"] == {"score": decision.score, "route": decision.route.value}


def test_admin_can_reject_and_reopen(alice, admin, make_pdf):
    application_id = submit(alice, make_pdf)
    assert reject(admin, application_id).status_code == 200
    again = reopen(admin, application_id)
    assert again.status_code == 200
    assert again.json()["outcome"] == "REOPENED"


def test_history_order_and_outcome_route(alice, recruiter, make_pdf):
    application_id = submit(alice, make_pdf)
    assert recruiter.get(f"/v1/decisions/{application_id}/outcome").status_code == 404
    reject(recruiter, application_id, "First rejection reason here")
    reopen(recruiter, application_id, "Reopened after review call")
    reject(recruiter, application_id, "Second rejection reason here")
    data = recruiter.get(f"/v1/decisions/{application_id}/outcome").json()
    assert data["state"] == "rejected"
    assert [item["outcome"] for item in data["history"]] == ["REJECTED", "REOPENED", "REJECTED"]
    assert [item["reason"] for item in data["history"]][1] == "Reopened after review call"
    times = [item["at"] for item in data["history"]]
    assert times == sorted(times)
    assert all(item["by"] == recruiter.username for item in data["history"])
    reopen(recruiter, application_id)
    assert recruiter.get(f"/v1/decisions/{application_id}/outcome").json()["state"] == "open"


def test_conflicts_and_unknown(alice, recruiter, make_pdf):
    application_id = submit(alice, make_pdf)
    missing = reject(recruiter, "no-such-application")
    assert missing.status_code == 404
    assert reopen(recruiter, "no-such-application").status_code == 404
    not_rejected = reopen(recruiter, application_id)
    assert not_rejected.status_code == 409
    assert_plain(not_rejected.json()["detail"])
    assert reject(recruiter, application_id).status_code == 200
    twice = reject(recruiter, application_id)
    assert twice.status_code == 409
    assert_plain(twice.json()["detail"])
    assert_plain(missing.json()["detail"])


def test_reason_length_limits(alice, recruiter, make_pdf):
    application_id = submit(alice, make_pdf)
    assert reject(recruiter, application_id, "too short").status_code == 422
    assert reject(recruiter, application_id, "x" * 501).status_code == 422
    assert reject(recruiter, application_id, "   padded   ").status_code == 422
    assert reject(recruiter, application_id, "y" * 500).status_code == 200


def test_roles_and_csrf(alice, recruiter, make_pdf):
    application_id = submit(alice, make_pdf)
    path = f"/v1/decisions/{application_id}/reject"
    assert alice.post(path, {"reason": REASON}).status_code == 403
    assert alice.get(f"/v1/decisions/{application_id}/outcome").status_code == 403
    assert alice.post(f"/v1/decisions/{application_id}/reopen", {"reason": REASON}).status_code == 403
    from fastapi.testclient import TestClient

    from firewall.api import app

    anonymous = TestClient(app)
    assert anonymous.post(path, json={"reason": REASON}).status_code == 401
    assert anonymous.post(f"/v1/decisions/{application_id}/reopen", json={"reason": REASON}).status_code == 401
    assert anonymous.get(f"/v1/decisions/{application_id}/outcome").status_code == 401
    assert recruiter.client.post(path, json={"reason": REASON}).status_code == 403
    assert recruiter.client.post(path, json={"reason": REASON}, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert OUTCOMES.is_rejected(application_id) is False


def test_stored_decision_never_changes(alice, recruiter, make_pdf):
    application_id = submit(alice, make_pdf, hidden=True)
    before = stored_decision(application_id).model_dump(mode="json")
    reject(recruiter, application_id)
    assert stored_decision(application_id).model_dump(mode="json") == before
    reopen(recruiter, application_id)
    assert stored_decision(application_id).model_dump(mode="json") == before
    assert recruiter.get(f"/v1/decisions/{application_id}").json() == before


def test_override_blocked_while_rejected(alice, recruiter, make_pdf):
    application_id = submit(alice, make_pdf)
    reject(recruiter, application_id)
    blocked = recruiter.post(
        f"/v1/decisions/{application_id}/override", {"route": "PASS_TO_ATS", "reason": "Trying an override now"}
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "Reopen the application first."
    reopen(recruiter, application_id)
    allowed = recruiter.post(
        f"/v1/decisions/{application_id}/override", {"route": "PASS_TO_ATS", "reason": "Trying an override now"}
    )
    assert allowed.status_code == 200


def test_recruiter_list_fields(alice, recruiter, make_pdf):
    application_id = submit(alice, make_pdf)
    other = submit(alice, make_pdf)
    reject(recruiter, application_id)
    items = {item["application_id"]: item for item in recruiter.get("/v1/decisions").json()}
    assert items[application_id]["outcome"] == "rejected"
    assert items[application_id]["outcome_by"] == recruiter.username
    assert isinstance(items[application_id]["outcome_at"], float)
    assert items[other]["outcome"] is None
    assert items[other]["outcome_by"] is None
    reopen(recruiter, application_id)
    refreshed = {item["application_id"]: item for item in recruiter.get("/v1/decisions").json()}
    assert refreshed[application_id]["outcome"] is None


def test_stats_rejected_count(alice, recruiter, make_pdf):
    first = submit(alice, make_pdf)
    second = submit(alice, make_pdf)
    before = recruiter.get("/v1/stats").json()
    reject(recruiter, first)
    reject(recruiter, second)
    after = recruiter.get("/v1/stats").json()
    assert after["rejected"] == before["rejected"] + 2
    assert after["counts_per_route"] == before["counts_per_route"]
    assert after["total_received"] == before["total_received"]
    reopen(recruiter, first)
    assert recruiter.get("/v1/stats").json()["rejected"] == before["rejected"] + 1


def test_candidate_status_closed_everywhere(alice, recruiter, make_pdf):
    application_id = submit(alice, make_pdf, hidden=True)
    open_detail = alice.get(f"/v1/me/applications/{application_id}").json()
    assert open_detail["follow_up"]
    reject(recruiter, application_id, SECRET_REASON)
    listing = alice.get("/v1/me/applications").json()
    item = next(entry for entry in listing if entry["application_id"] == application_id)
    detail = alice.get(f"/v1/me/applications/{application_id}").json()
    summary = alice.get("/v1/me/summary").json()
    for view in (item, detail):
        assert view["status"] == "closed"
        assert view["title"] == "Thank you for applying."
        assert view["message"] == CLOSED_MESSAGE
        assert_plain(view["title"].rstrip(".") + " ")
    assert item["follow_up_count"] == 0
    assert detail["follow_up"] == []
    assert summary["latest_status"] == "closed"
    reopen(recruiter, application_id, SECRET_REASON)
    restored = alice.get(f"/v1/me/applications/{application_id}").json()
    assert restored["status"] != "closed"
    assert restored["follow_up"]


def test_reason_never_leaks_to_candidate(alice, recruiter, make_pdf):
    application_id = submit(alice, make_pdf, hidden=True)
    reject(recruiter, application_id, SECRET_REASON)
    responses = [
        alice.get("/v1/me/applications"),
        alice.get(f"/v1/me/applications/{application_id}"),
        alice.get("/v1/me/summary"),
        alice.post(f"/v1/me/applications/{application_id}/answers", {"item_id": "more-details", "text": "hello there"}),
        alice.post("/v1/claims/verify", {"application_id": application_id, "response": "I built this with Python"}),
        alice.get(f"/v1/decisions/{application_id}"),
        alice.get(f"/v1/decisions/{application_id}/outcome"),
        alice.post(f"/v1/decisions/{application_id}/reject", {"reason": SECRET_REASON}),
    ]
    for response in responses:
        assert "zebra" not in response.text
        assert SECRET_REASON not in response.text
        assert recruiter.username not in response.text or response.status_code == 403 or "by" not in response.json()


def test_answers_and_claims_closed(alice, recruiter, make_pdf):
    application_id = submit(alice, make_pdf, hidden=True)
    reject(recruiter, application_id)
    answer = alice.post(f"/v1/me/applications/{application_id}/answers", {"item_id": "more-details", "text": "hello there"})
    assert answer.status_code == 409
    assert answer.json()["detail"] == "This application is closed."
    claim = alice.post("/v1/claims/verify", {"application_id": application_id, "response": "I built this with Python"})
    assert claim.status_code == 409
    assert claim.json()["detail"] == "This application is closed."
    assert_plain(answer.json()["detail"])


def test_replacement_upload_for_closed_application_allowed(alice, recruiter, make_pdf):
    application_id = submit(alice, make_pdf)
    reject(recruiter, application_id)
    response = alice.upload(make_pdf("Maya Raman", email(), False), replaces=application_id)
    assert response.status_code == 200, response.text
    fresh = response.json()
    assert fresh["application_id"] != application_id
    assert fresh["status"] != "closed"
    items = {item["application_id"]: item for item in alice.get("/v1/me/applications").json()}
    assert items[application_id]["status"] == "closed"
    assert items[fresh["application_id"]]["replaces"] == application_id


def test_inbox_excludes_rejected(alice, recruiter, make_pdf):
    application_id = submit(alice, make_pdf, hidden=True)

    def listed() -> set[str]:
        boxes = recruiter.get("/v1/delivery/inbox").json()["inboxes"]
        return {item["application_id"] for box in boxes for item in box["items"]}

    def counts() -> int:
        return sum(box["count"] for box in recruiter.get("/v1/delivery/inbox").json()["inboxes"])

    assert application_id in listed()
    before = counts()
    reject(recruiter, application_id)
    assert application_id not in listed()
    assert counts() == before - 1
    reopen(recruiter, application_id)
    assert application_id in listed()


def test_audit_entries(alice, recruiter, make_pdf):
    application_id = submit(alice, make_pdf)
    reject(recruiter, application_id, SECRET_REASON)
    reopen(recruiter, application_id, SECRET_REASON)
    entries = [entry for entry in get_service().audit.entries(1000) if entry.target == application_id]
    events = [entry.event for entry in entries]
    assert "application_rejected" in events
    assert "application_reopened" in events
    rejected = next(entry for entry in entries if entry.event == "application_rejected")
    assert rejected.actor == recruiter.username
    assert rejected.detail["reason"] == SECRET_REASON


def test_redis_cache_path_and_ttl():
    link = RedisLink(fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True))
    cache = build_cache("outcomes", link=link, default_ttl_s=OUTCOME_TTL_S)
    store = OutcomeStore(cache, lambda: 1000.0)
    store.change("app-1", "rejected", REASON, "rita")
    assert store.is_rejected("app-1")
    other = OutcomeStore(build_cache("outcomes", link=link, default_ttl_s=OUTCOME_TTL_S), lambda: 2000.0)
    assert other.is_rejected("app-1")
    with pytest.raises(OutcomeConflict):
        other.change("app-1", "rejected", REASON, "rita")
    other.change("app-1", "open", REASON, "rita")
    record = other.get("app-1")
    assert record["state"] == "open"
    assert [item["outcome"] for item in record["history"]] == ["REJECTED", "REOPENED"]
    keys = link.call(lambda client: client.keys("*outcomes*"), lambda: [])
    assert keys
    ttl = link.call(lambda client: client.ttl(keys[0]), lambda: 0)
    assert ttl > OUTCOME_TTL_S - 10
    assert json.dumps(record)


def test_new_strings_are_plain():
    from firewall.applicant_routes import CLOSED_DETAIL, CLOSED_STATUS

    for text in (CLOSED_DETAIL, CLOSED_STATUS["title"].rstrip("."), CLOSED_STATUS["message"].rstrip(".")):
        assert_plain(text)
    assert CLOSED_STATUS["message"] == CLOSED_MESSAGE
    assert STORE is not None
