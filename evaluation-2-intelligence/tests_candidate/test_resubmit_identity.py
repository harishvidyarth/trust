from __future__ import annotations


def same_person_pdf(make_pdf, hidden: bool) -> bytes:
    return make_pdf("Alice Resubmit", "alice.resubmit@example.com", hidden)


def test_resubmit_is_not_penalised_as_duplicate_of_itself(alice, make_pdf):
    first = alice.upload(same_person_pdf(make_pdf, True)).json()
    second = alice.upload(same_person_pdf(make_pdf, False), replaces=first["application_id"])
    assert second.status_code == 200
    codes = {item["code"] for item in second.json()["reasons"] if item["weight"] > 0}
    assert not codes & {"DUP_EMAIL", "DUP_SAME_JOB", "DUP_PHONE"}
    assert second.json()["score"] > first["score"]


def test_same_file_without_replaces_is_still_a_duplicate(alice, make_pdf):
    alice.upload(same_person_pdf(make_pdf, False))
    again = alice.upload(same_person_pdf(make_pdf, False)).json()
    assert {item["code"] for item in again["reasons"]} & {"DUP_EMAIL", "DUP_SAME_JOB"}


def test_replaces_cannot_hide_a_copy_of_someone_elses_resume(alice, bob, make_pdf):
    other = bob.upload(make_pdf("Bob Original", "bob.original@example.com", False)).json()
    copied = alice.upload(make_pdf("Bob Original", "bob.original@example.com", False), replaces=other["application_id"])
    assert copied.status_code == 400
