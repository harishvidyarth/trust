from __future__ import annotations

from tests_candidate.conftest import stored_decision


def same_person_pdf(make_pdf, hidden: bool) -> bytes:
    return make_pdf("Alice Resubmit", "alice.resubmit@example.com", hidden)


def positive_codes(application_id: str) -> set[str]:
    return {item.code for item in stored_decision(application_id).reasons if item.weight > 0}


def test_resubmit_is_not_penalised_as_duplicate_of_itself(alice, make_pdf):
    first = alice.upload(same_person_pdf(make_pdf, True)).json()
    second = alice.upload(same_person_pdf(make_pdf, False), replaces=first["application_id"])
    assert second.status_code == 200
    assert not positive_codes(second.json()["application_id"]) & {"DUP_EMAIL", "DUP_SAME_JOB", "DUP_PHONE"}
    assert stored_decision(second.json()["application_id"]).score > stored_decision(first["application_id"]).score


def test_same_file_without_replaces_is_still_a_duplicate(alice, make_pdf):
    alice.upload(same_person_pdf(make_pdf, False))
    again = alice.upload(same_person_pdf(make_pdf, False)).json()
    assert positive_codes(again["application_id"]) & {"DUP_EMAIL", "DUP_SAME_JOB"}


def test_replaces_cannot_hide_a_copy_of_someone_elses_resume(alice, bob, make_pdf):
    other = bob.upload(make_pdf("Bob Original", "bob.original@example.com", False)).json()
    copied = alice.upload(make_pdf("Bob Original", "bob.original@example.com", False), replaces=other["application_id"])
    assert copied.status_code == 400
