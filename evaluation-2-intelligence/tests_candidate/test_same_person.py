from __future__ import annotations

import uuid

from firewall.api import OUTCOMES
from tests_candidate.test_outcomes import REASON, reject, reopen


def typed(session, name, job, email=None, phone=None):
    tag = uuid.uuid4().hex[:8]
    response = session.upload_form(
        applicant_name=name,
        applicant_email=email or f"p{tag}@example.com",
        applicant_phone=phone or f"+91 9{int(tag[:7], 16) % 10**9:09d}",
        job_id=job,
    )
    assert response.status_code == 200, response.text
    return response.json()["application_id"]


def test_rejecting_one_closes_every_other_application_for_the_same_name_and_job(alice, bob, recruiter):
    job = f"job-{uuid.uuid4().hex[:6]}"
    first = typed(alice, "Maya Raman", job)
    second = typed(alice, "maya  RAMAN", job)
    other_job = typed(alice, "Maya Raman", job + "-other")
    other_name = typed(bob, "Someone Else", job)
    result = reject(recruiter, first).json()
    assert result["outcome"] == "REJECTED" and result["also_rejected"] == [second]
    assert OUTCOMES.is_rejected(first) and OUTCOMES.is_rejected(second)
    assert not OUTCOMES.is_rejected(other_job) and not OUTCOMES.is_rejected(other_name)


def test_same_email_or_phone_for_the_same_job_counts_as_the_same_person(alice, recruiter):
    job = f"job-{uuid.uuid4().hex[:6]}"
    first = typed(alice, "Asha Nair", job, email="asha.same@example.com", phone="+91 90000 77777")
    second = typed(alice, "A Nair", job, email="asha.same@example.com", phone="+91 90000 88888")
    assert reject(recruiter, first).json()["also_rejected"] == [second]


def test_linked_applications_show_closed_to_the_candidate_and_never_the_reason(alice, recruiter):
    job = f"job-{uuid.uuid4().hex[:6]}"
    first = typed(alice, "Ravi Kumar", job)
    second = typed(alice, "Ravi Kumar", job)
    reject(recruiter, first)
    mine = {item["application_id"]: item for item in alice.get("/v1/me/applications").json()}
    assert mine[first]["status"] == "closed" and mine[second]["status"] == "closed"
    assert REASON not in str(mine[second])
    assert alice.get(f"/v1/me/applications/{second}").json()["follow_up"] == []


def test_a_later_application_by_a_rejected_person_for_the_same_job_is_closed_too(alice, recruiter):
    job = f"job-{uuid.uuid4().hex[:6]}"
    first = typed(alice, "Neha Iyer", job)
    reject(recruiter, first)
    later = typed(alice, "Neha  Iyer", job)
    assert OUTCOMES.is_rejected(later)
    assert alice.get(f"/v1/me/applications/{later}").json()["status"] == "closed"
    listed = [item for item in recruiter.get("/v1/decisions").json() if item["application_id"] == later][0]
    assert listed["outcome"] == "rejected"


def test_a_different_job_is_not_closed_by_an_earlier_rejection(alice, recruiter):
    first = typed(alice, "Dev Patel", f"job-{uuid.uuid4().hex[:6]}")
    reject(recruiter, first)
    other = typed(alice, "Dev Patel", f"job-{uuid.uuid4().hex[:6]}")
    assert not OUTCOMES.is_rejected(other)


def test_reopening_one_does_not_reopen_the_others_and_history_is_kept(alice, recruiter):
    job = f"job-{uuid.uuid4().hex[:6]}"
    first = typed(alice, "Kiran Das", job)
    second = typed(alice, "Kiran Das", job)
    reject(recruiter, first)
    assert reopen(recruiter, first).status_code == 200
    assert not OUTCOMES.is_rejected(first) and OUTCOMES.is_rejected(second)
    history = recruiter.get(f"/v1/decisions/{second}/outcome").json()["history"]
    assert history[0]["outcome"] == "REJECTED" and "same applicant and job" in history[0]["reason"]
