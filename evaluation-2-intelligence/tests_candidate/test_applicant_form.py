from __future__ import annotations

import re
import uuid

import pytest

import firewall.api as api
from firewall.api import STORE
from firewall.applicant_routes import (
    DETAILS_HELP,
    DETAILS_LABEL,
    MAX_DETAIL_NOTES,
    STATUS_BY_ROUTE,
    status_for,
    synthesize_resume,
)
from firewall.claim_check import BANK
from firewall.models import Route
from tests_candidate.conftest import FORBIDDEN, form_fields, stored_decision, stored_text

REPLY_KEYS = {"application_id", "status", "title", "message", "follow_up"}
ITEM_KEYS = {"id", "kind", "label", "help", "required", "answered"}
BANNED_WORDS = re.compile(r"score|risk|fraud|suspicious|suspect|trust|detect|flag|fake", re.I)
GOOD = (
    "I built an employee management API using FastAPI. I implemented CRUD endpoints and used PostgreSQL "
    "for persistence. I also added validation using Pydantic and separated the API routes from the database layer."
)


def assert_plain(text: str) -> None:
    assert text
    assert not any(char in text for char in FORBIDDEN), text
    assert not BANNED_WORDS.search(text), text


def assert_plain_json(value: object) -> None:
    if isinstance(value, str):
        assert_plain(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key not in {"id", "kind", "status", "application_id", "job_id"}:
                assert_plain_json(item)
    elif isinstance(value, list):
        for item in value:
            assert_plain_json(item)


def hidden_pdf_upload(session, make_pdf, **extra):
    response = session.upload(make_pdf("Claim Person", f"c{uuid.uuid4().hex[:8]}@example.com", True), **extra)
    assert response.status_code == 200, response.text
    return response.json()


def form_upload(session, **override):
    response = session.upload_form(**form_fields(**override))
    assert response.status_code == 200, response.text
    return response.json()


def force_route(monkeypatch: pytest.MonkeyPatch, route: Route) -> None:
    original = api.evaluate

    def wrapper(application, job, store, config, *args, **kwargs):
        persist = kwargs.pop("persist", True)
        decision = original(application, job, store, config, *args, persist=False, **kwargs)
        decision = decision.model_copy(update={"route": route})
        if persist:
            store.save(application, decision)
        return decision

    monkeypatch.setattr(api, "evaluate", wrapper)


def test_candidate_reply_has_exactly_the_allowed_keys(alice, make_pdf):
    reply = hidden_pdf_upload(alice, make_pdf)
    assert set(reply) == REPLY_KEYS
    assert all(set(item) - {"attempts_left"} == ITEM_KEYS for item in reply["follow_up"])
    text = str(reply).lower()
    for word in ("score", "route", "reasons", "fixes", "hidden_intent", "agreement", "llm_used", "ai_writing"):
        assert word not in text


@pytest.mark.parametrize("route", list(Route))
def test_status_words_follow_the_route(alice, make_pdf, monkeypatch, route):
    force_route(monkeypatch, route)
    reply = hidden_pdf_upload(alice, make_pdf)
    expected = {
        Route.PASS_TO_ATS: ("sent", "Your application was sent to the hiring team."),
        Route.ADDITIONAL_VERIFICATION: ("more_details", "The hiring team may need a few more details."),
        Route.MANUAL_REVIEW: ("in_review", "A person on the hiring team is reviewing your application."),
    }[route]
    assert (reply["status"], reply["title"]) == expected
    assert_plain(reply["message"])
    kinds = [item["kind"] for item in reply["follow_up"]]
    assert ("question" in kinds) == (route != Route.PASS_TO_ATS)
    assert kinds[-1] == "details"


def test_status_table_wording_is_plain():
    for route in Route:
        info = status_for(route.value)
        assert set(info) == {"status", "title", "message"}
        assert_plain(info["title"])
        assert_plain(info["message"])
    assert set(STATUS_BY_ROUTE) == {route.value for route in Route}


def test_form_only_submission_creates_a_decision_for_the_recruiter(alice, recruiter):
    reply = form_upload(alice, applicant_name="Form Only Person")
    assert set(reply) == REPLY_KEYS
    application_id = reply["application_id"]
    decision = stored_decision(application_id)
    assert decision.route in set(Route)
    listed = {item["application_id"]: item for item in recruiter.get("/v1/decisions").json()}
    assert listed[application_id]["candidate_name"] == "Form Only Person"
    assert listed[application_id]["decision"]["score"] == decision.score
    assert listed[application_id]["applicant_form_present"] is True
    assert listed[application_id]["follow_up_open"] == 0
    form = recruiter.get(f"/v1/applications/{application_id}/form").json()
    assert form["fields"]["applicant_name"] == "Form Only Person"
    assert form["fields"]["consent"] is True
    assert form["fields"]["extra_skills"] == ["Python", "FastAPI", "PostgreSQL", "Docker"]
    assert form["requests"] == [] and form["details"] == [] and form["submitted_at"]
    text = stored_text(application_id)
    assert "Form Only Person" in text and "Example Labs" in text and "employee management API" in text


def test_form_only_requires_identity_fields(alice):
    for missing in ("applicant_name", "applicant_email", "applicant_phone"):
        data = form_fields()
        data.pop(missing)
        response = alice.upload_form(**data)
        assert response.status_code == 400
        assert_plain(response.json()["detail"])
    assert alice.get("/v1/me/applications").json() == []


def test_consent_is_required_for_candidates(alice, recruiter, make_pdf):
    response = alice.upload_form(**form_fields(consent="false"))
    assert response.status_code == 400
    assert_plain(response.json()["detail"])
    assert alice.upload(make_pdf("No Consent", "nc@example.com", False), consent="false").status_code == 400
    assert alice.get("/v1/me/applications").json() == []
    assert recruiter.upload(make_pdf("Staff Upload", "su@example.com", False), consent="false").status_code == 200


def test_typed_fields_override_and_reach_claims_context(alice, recruiter, make_pdf):
    pdf = make_pdf("Parsed Name", f"parsed{uuid.uuid4().hex[:6]}@example.com", False)
    email = f"typed{uuid.uuid4().hex[:6]}@example.com"
    response = alice.upload(
        pdf,
        applicant_name="Typed Name",
        applicant_email=email,
        applicant_phone="+91 98765 43210",
        github_url="https://github.com/octocat/Hello-World",
        linkedin_url="https://www.linkedin.com/in/maya",
        portfolio_url="https://maya.example.com/work",
        papers="Sample Study on Graphs\nSecond Paper on Trees",
        certificate_ids="CERT-2024-0042, ABC-123",
        extra_skills="python, Rust, rust, Go",
    )
    assert response.status_code == 200, response.text
    application_id = response.json()["application_id"]
    application = next(item for item in STORE.applications() if item.application_id == application_id)
    assert application.candidate.name == "Typed Name"
    assert application.candidate.email == email
    assert application.candidate.phone == "+91 98765 43210"
    lowered = [skill.casefold() for skill in application.candidate.skills]
    assert len(lowered) == len(set(lowered))
    assert {"python", "rust", "go"} <= set(lowered)
    text = stored_text(application_id)
    assert "https://github.com/octocat/Hello-World" in text
    assert "https://www.linkedin.com/in/maya" in text
    assert "https://maya.example.com/work" in text
    assert "Paper: Sample Study on Graphs" in text and "CERT-2024-0042" in text
    form = recruiter.get(f"/v1/applications/{application_id}/form").json()
    assert form["fields"]["papers"] == ["Sample Study on Graphs", "Second Paper on Trees"]
    assert form["fields"]["certificate_ids"] == ["CERT-2024-0042", "ABC-123"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("github_url", "http://github.com/octocat"),
        ("github_url", "https://gitlab.com/octocat"),
        ("github_url", "https://user:pass@github.com/octocat"),
        ("github_url", "https://127.0.0.1/octocat"),
        ("github_url", "https://github.com.evil.example/octocat"),
        ("linkedin_url", "https://example.com/in/maya"),
        ("linkedin_url", "http://www.linkedin.com/in/maya"),
        ("portfolio_url", "https://localhost/me"),
        ("portfolio_url", "ftp://example.com/me"),
        ("portfolio_url", "https://10.0.0.5/me"),
        ("applicant_email", "not an email"),
        ("applicant_phone", "abc"),
        ("years_experience", "61"),
        ("years_experience", "-1"),
        ("years_experience", "many"),
        ("applicant_name", "x" * 121),
        ("about_project", "x" * 2001),
        ("papers", "\n".join(f"Paper {i}" for i in range(11))),
        ("certificate_ids", ",".join(f"CERT-{i:04d}" for i in range(11))),
        ("certificate_ids", "!!"),
        ("extra_skills", ",".join(f"skill{i}" for i in range(31))),
    ],
)
def test_invalid_typed_values_are_refused_plainly(alice, field, value):
    response = alice.upload_form(**form_fields(**{field: value}))
    assert response.status_code == 400, response.text
    assert_plain(response.json()["detail"])
    assert alice.get("/v1/me/applications").json() == []


def test_valid_links_are_accepted(alice):
    reply = form_upload(
        alice,
        github_url="https://github.com/octocat",
        linkedin_url="https://linkedin.com/in/maya",
        portfolio_url="https://maya.example.com",
        years_experience="0",
    )
    assert reply["application_id"]


def test_dry_run_is_ignored_for_candidates_but_kept_for_staff(alice, recruiter, make_pdf):
    reply = hidden_pdf_upload(alice, make_pdf, dry_run="true")
    assert stored_decision(reply["application_id"])
    assert len(alice.get("/v1/me/applications").json()) == 1
    staff = recruiter.upload(make_pdf("Dry Person", f"d{uuid.uuid4().hex[:6]}@example.com", False), dry_run="true")
    assert staff.status_code == 200 and "score" in staff.json() and "route" in staff.json()
    assert STORE.get_decision(staff.json()["application_id"]) is None


def test_staff_still_get_the_full_decision(recruiter, make_pdf):
    response = recruiter.upload(make_pdf("Staff Person", f"s{uuid.uuid4().hex[:6]}@example.com", False))
    assert response.status_code == 200
    assert {"score", "route", "reasons", "summary"} <= set(response.json())


def test_upload_rate_limit_for_candidates(alice, monkeypatch):
    for index in range(20):
        response = alice.upload_form(**form_fields(applicant_email=f"limit{index}{uuid.uuid4().hex[:6]}@example.com"))
        assert response.status_code == 200, response.text
    blocked = alice.upload_form(**form_fields())
    assert blocked.status_code == 429
    assert_plain(blocked.json()["detail"])


def test_follow_up_always_offers_optional_details(alice, monkeypatch):
    force_route(monkeypatch, Route.PASS_TO_ATS)
    reply = form_upload(alice)
    details = [item for item in reply["follow_up"] if item["kind"] == "details"]
    assert details == [
        {
            "id": "more-details", "kind": "details", "label": DETAILS_LABEL, "help": DETAILS_HELP,
            "required": False, "answered": False, "attempts_left": MAX_DETAIL_NOTES,
        }
    ]
    assert alice.get("/v1/me/applications").json()[0]["follow_up_count"] == 0


def test_claim_question_item_and_answer(alice, recruiter, make_pdf):
    reply = hidden_pdf_upload(alice, make_pdf)
    question = next(item for item in reply["follow_up"] if item["id"] == "claim-question")
    assert question["kind"] == "question" and question["answered"] is False and question["attempts_left"] == 3
    assert_plain(question["label"])
    assert alice.get("/v1/me/applications").json()[0]["follow_up_count"] == 1
    path = f"/v1/me/applications/{reply['application_id']}/answers"
    for left in (2, 1, 0):
        done = alice.post(path, {"item_id": "claim-question", "text": GOOD})
        assert done.status_code == 200, done.text
        assert done.json() == {
            "received": True, "message": "Thank you. Your answer was sent to the hiring team.", "attempts_left": left,
        }
    assert alice.post(path, {"item_id": "claim-question", "text": GOOD}).status_code == 429
    detail = alice.get(f"/v1/me/applications/{reply['application_id']}").json()
    item = next(entry for entry in detail["follow_up"] if entry["id"] == "claim-question")
    assert item["answered"] is True and item["attempts_left"] == 0
    assert detail["follow_up_count"] == 0
    seen = recruiter.get(f"/v1/claims/application/{reply['application_id']}")
    assert seen.status_code == 200 and "answer_score" in seen.json()
    assert GOOD not in seen.text


def test_claim_question_is_missing_when_nothing_needs_asking(alice, monkeypatch):
    force_route(monkeypatch, Route.PASS_TO_ATS)
    reply = form_upload(alice)
    response = alice.post(f"/v1/me/applications/{reply['application_id']}/answers", {"item_id": "claim-question", "text": GOOD})
    assert response.status_code == 404
    assert_plain(response.json()["detail"])


def test_answer_validation_and_unknown_items(alice):
    reply = form_upload(alice)
    path = f"/v1/me/applications/{reply['application_id']}/answers"
    assert alice.post(path, {"item_id": "more-details", "text": ""}).status_code == 422
    blank = alice.post(path, {"item_id": "more-details", "text": "    "})
    assert blank.status_code == 400
    assert_plain(blank.json()["detail"])
    assert alice.post(path, {"item_id": "more-details", "text": "x" * 4001}).status_code == 422
    for item_id in ("request-9", "nonsense"):
        missing = alice.post(path, {"item_id": item_id, "text": "hello"})
        assert missing.status_code == 404
        assert_plain(missing.json()["detail"])


def test_more_details_notes_are_kept_and_limited(alice, recruiter):
    reply = form_upload(alice)
    application_id = reply["application_id"]
    path = f"/v1/me/applications/{application_id}/answers"
    for index in range(MAX_DETAIL_NOTES):
        done = alice.post(path, {"item_id": "more-details", "text": f"Extra note {index}"})
        assert done.status_code == 200, done.text
        assert done.json()["received"] is True
        assert done.json()["attempts_left"] == MAX_DETAIL_NOTES - index - 1
        assert_plain(done.json()["message"])
    full = alice.post(path, {"item_id": "more-details", "text": "one more"})
    assert full.status_code == 429
    assert_plain(full.json()["detail"])
    notes = recruiter.get(f"/v1/applications/{application_id}/form").json()["details"]
    assert [note["text"] for note in notes] == [f"Extra note {index}" for index in range(MAX_DETAIL_NOTES)]
    assert all(note["at"] for note in notes)
    assert alice.get(f"/v1/me/applications/{application_id}").json()["follow_up"][-1]["answered"] is True


def test_recruiter_request_flow_end_to_end(alice, bob, recruiter, admin):
    reply = form_upload(alice)
    application_id = reply["application_id"]
    created = recruiter.post(
        f"/v1/applications/{application_id}/requests",
        {"label": "Please send your degree year (BTech)", "help": "Type the year you finished: it helps."},
    )
    assert created.status_code == 200, created.text
    request = created.json()
    assert request["id"] == "request-1" and request["answer"] is None and request["asked_by"] == recruiter.username
    second = recruiter.post(f"/v1/applications/{application_id}/requests", {"label": "Name of your last manager"})
    assert second.json()["id"] == "request-2"
    listed = {item["application_id"]: item for item in recruiter.get("/v1/decisions").json()}
    assert listed[application_id]["follow_up_open"] == 2
    detail = alice.get(f"/v1/me/applications/{application_id}").json()
    requests = [item for item in detail["follow_up"] if item["kind"] == "request"]
    assert [item["id"] for item in requests] == ["request-1", "request-2"]
    for item in requests:
        assert set(item) == ITEM_KEYS and item["required"] is True and item["answered"] is False
        assert_plain(item["label"])
        assert_plain(item["help"])
    assert detail["follow_up_count"] == 2 + len([item for item in detail["follow_up"] if item["kind"] == "question"])
    assert bob.get(f"/v1/me/applications/{application_id}").status_code == 404
    path = f"/v1/me/applications/{application_id}/answers"
    sent = alice.post(path, {"item_id": "request-1", "text": "2019"})
    assert sent.json() == {"received": True, "message": "Thank you. Your answer was sent to the hiring team.", "attempts_left": None}
    alice.post(path, {"item_id": "request-1", "text": "2020"})
    form = admin.get(f"/v1/applications/{application_id}/form").json()
    first, other = form["requests"]
    assert first["answer"] == "2020" and first["answered_at"] and other["answer"] is None
    assert first["asked_by"] == recruiter.username and first["asked_at"]
    assert set(first) == {"id", "label", "help", "asked_by", "asked_at", "answer", "answered_at"}
    after = alice.get(f"/v1/me/applications/{application_id}").json()
    assert after["follow_up_count"] == detail["follow_up_count"] - 1
    listed = {item["application_id"]: item for item in recruiter.get("/v1/decisions").json()}
    assert listed[application_id]["follow_up_open"] == 1


def test_request_creation_is_audited_and_validated(alice, recruiter):
    from firewall.auth import get_service

    application_id = form_upload(alice)["application_id"]
    path = f"/v1/applications/{application_id}/requests"
    assert recruiter.post(path, {"label": "ab"}).status_code == 422
    assert recruiter.post(path, {"label": "x" * 201}).status_code == 422
    assert recruiter.post(path, {"label": "Fine label", "help": "y" * 301}).status_code == 422
    assert recruiter.post("/v1/applications/missing/requests", {"label": "Fine label"}).status_code == 404
    assert recruiter.post(path, {"label": "Fine label"}).status_code == 200
    events = [entry for entry in get_service().audit.entries() if entry.event == "details_requested" and entry.target == application_id]
    assert len(events) == 1 and events[0].actor == recruiter.username


def test_form_routes_are_staff_only(alice, bob, recruiter):
    application_id = form_upload(alice)["application_id"]
    for session in (alice, bob):
        assert session.get(f"/v1/applications/{application_id}/form").status_code == 403
        assert session.post(f"/v1/applications/{application_id}/requests", {"label": "Fine label"}).status_code == 403
    assert recruiter.get("/v1/applications/missing/form").status_code == 404


def test_recruiter_cannot_answer_for_the_candidate(alice, recruiter):
    application_id = form_upload(alice)["application_id"]
    response = recruiter.post(f"/v1/me/applications/{application_id}/answers", {"item_id": "more-details", "text": "hi"})
    assert response.status_code == 403


def test_candidates_cannot_run_or_read_checks(alice, recruiter, make_pdf):
    application_id = hidden_pdf_upload(alice, make_pdf)["application_id"]
    assert alice.post("/v1/checks/run", {"application_id": application_id}).status_code == 403
    assert alice.get(f"/v1/checks/application/{application_id}").status_code == 403
    assert recruiter.get(f"/v1/checks/application/{application_id}").status_code in {200, 404}


def test_candidate_cannot_open_the_decision_directly_for_others(alice, bob):
    mine = form_upload(alice)["application_id"]
    assert bob.get(f"/v1/decisions/{mine}").status_code == 404
    assert bob.post(f"/v1/me/applications/{mine}/answers", {"item_id": "more-details", "text": "hi"}).status_code == 404


def test_recruiter_list_badges_for_file_uploads(recruiter, make_pdf):
    response = recruiter.upload(make_pdf("Plain File", f"pf{uuid.uuid4().hex[:6]}@example.com", False))
    application_id = response.json()["application_id"]
    listed = {item["application_id"]: item for item in recruiter.get("/v1/decisions").json()}
    assert listed[application_id]["applicant_form_present"] is False
    assert listed[application_id]["follow_up_open"] == 0


def test_every_claim_question_is_plain_and_neutral():
    for entries in BANK.values():
        for entry in entries:
            assert not BANNED_WORDS.search(entry[2]), entry[2]


def test_candidate_facing_strings_are_plain(alice, make_pdf):
    reply = hidden_pdf_upload(alice, make_pdf)
    assert_plain_json({key: value for key, value in reply.items()})
    listing = alice.get("/v1/me/applications").json()
    assert_plain_json(listing)
    assert_plain_json(alice.get(f"/v1/me/applications/{reply['application_id']}").json())


def test_synthesized_resume_is_plain_text_without_markup():
    fields = {
        "applicant_name": "Maya Raman", "applicant_email": "m@example.com", "applicant_phone": "+91 90000 12345",
        "role_title": "Engineer", "current_employer": "Example Labs", "education": "BTech", "github_url": "",
        "linkedin_url": "", "portfolio_url": "", "papers": [], "certificate_ids": [], "years_experience": 3.0,
        "extra_skills": ["Python", "Go"], "about_project": "Line one\nLine two",
    }
    text = synthesize_resume(fields)
    assert text.splitlines()[0] == "Maya Raman"
    assert "3 years of experience" in text and "Skills\nPython, Go" in text and "Line one Line two" in text


def test_empty_file_part_from_a_browser_form_is_treated_as_no_file(alice):
    response = alice.client.post(
        "/v1/applications/upload",
        data=alice.base_data(form_fields()),
        files={"file": ("", b"", "application/octet-stream")},
        headers=alice.headers,
    )
    assert response.status_code == 200, response.text
    assert set(response.json()) == REPLY_KEYS


def test_typed_links_survive_a_restart_for_the_claim_checks(alice):
    from firewall import api as api_module
    from firewall.api import CONTEXT

    response = alice.upload_form(
        applicant_name="Restart Person",
        applicant_email="restart.person@example.com",
        applicant_phone="+91 90000 12345",
        github_url="https://github.com/octocat",
        about_project="I built a small project",
    )
    assert response.status_code == 200, response.text
    application_id = response.json()["application_id"]
    CONTEXT.delete(f"ctx:{application_id}") if hasattr(CONTEXT, "delete") else CONTEXT.set(f"ctx:{application_id}", {"resume_text": "", "candidate": {}})
    text = api_module._resume_text_for(application_id)
    assert "github.com/octocat" in text
