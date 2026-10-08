from __future__ import annotations

from firewall.config import Config
from firewall.engine import evaluate
from firewall.models import Decision, Experience, Project, Route
from firewall.signals import identity_links
from firewall.store import InMemoryApplicationStore


def stored_decision(application_id: str) -> Decision:
    return Decision(
        application_id=application_id,
        score=100,
        route=Route.PASS_TO_ATS,
        summary="stored",
    )


def test_store_indexes_and_inclusive_out_of_order_time_queries(application_factory):
    store = InMemoryApplicationStore()
    later = application_factory(application_id="later", submitted_at=200, device_id="shared", ip="same-ip")
    earlier = application_factory(application_id="earlier", submitted_at=100, device_id="shared", ip="same-ip")
    store.save(later, stored_decision("later"))
    store.save(earlier, stored_decision("earlier"))

    assert {item.application_id for item in store.by_email("ada@example.com")} == {"earlier", "later"}
    assert {item.application_id for item in store.by_phone("9876543210")} == {"earlier", "later"}
    assert {item.application_id for item in store.by_job("job-1")} == {"earlier", "later"}
    assert [item.application_id for item in store.recent(100, 100)] == ["earlier"]
    assert [item.application_id for item in store.recent_by_device("shared", 100, 200)] == ["later", "earlier"]
    assert {item.application_id for item in store.recent_by_ip("same-ip", 100, 200)} == {"earlier", "later"}
    assert {item.application_id for item in store.recent_by_identity("ada@example.com", "9876543210", 100, 200)} == {
        "earlier",
        "later",
    }


def test_store_resume_template_replacement_and_clear(application_factory):
    store = InMemoryApplicationStore()
    original = application_factory(application_id="replace-me")
    store.save(original, stored_decision("replace-me"))
    query = application_factory(application_id="query", name="Other", email="other@example.com", phone="9000000000")
    assert [item.application_id for item in store.resume_candidates(query)] == ["replace-me"]
    normalized_template = "built a reliable distributed job scheduling service in modern python"
    assert store.template_identity_count(normalized_template, "ada@example.com") == 1
    assert store.template_identity_count(normalized_template, "other@example.com") == 2
    assert store.has_exact_resume_from_other_identity(query) is True

    replacement = application_factory(
        application_id="replace-me",
        email="replacement@example.com",
        phone="8111111111",
        projects=[],
    )
    store.save(replacement, stored_decision("replace-me"))
    assert store.by_email("ada@example.com") == ()
    assert store.resume_candidates(query) == ()
    assert store.has_exact_resume_from_other_identity(query) is False
    assert store.template_identity_count(normalized_template, "ada@example.com") == 1
    assert store.template_identity_count(normalized_template, "other@example.com") == 1
    store.clear()
    assert store.applications() == ()
    assert store.by_email("replacement@example.com") == ()


def test_engine_uses_index_queries_not_full_application_scan(application_factory, job_factory):
    class IndexedOnlyStore(InMemoryApplicationStore):
        def applications(self):
            raise AssertionError("engine performed a full application scan")

    decision = evaluate(application_factory(), job_factory(), IndexedOnlyStore(), Config())
    assert decision.route == Route.PASS_TO_ATS


def test_unrelated_applications_are_never_compared(application_factory, job_factory, monkeypatch):
    compared = []
    real_similarity = identity_links.name_similarity
    real_claims = identity_links.extract_link_claims

    def counting_similarity(first, second):
        compared.append((first, second))
        return real_similarity(first, second)

    claim_calls = []

    def counting_claims(application):
        claim_calls.append(application.application_id)
        return real_claims(application)

    monkeypatch.setattr(identity_links, "name_similarity", counting_similarity)
    monkeypatch.setattr(identity_links, "extract_link_claims", counting_claims)

    def build(application_id, name, company, text, index):
        application = application_factory(
            application_id=application_id,
            name=name,
            email=f"user{index}@example.com",
            phone=f"90000{index:05d}",
            projects=[Project(name="Portfolio", description=text)],
        )
        application.candidate.experience = [Experience(company=company, title="Engineer", start="2022-07", end="2025-01")]
        return application

    store = InMemoryApplicationStore()
    for index in range(200):
        store.save(build(f"noise-{index}", f"Person{index} Surname{index}", f"Firm {index}", f"Studied at nowhere {index}", index), stored_decision(f"noise-{index}"))
    store.save(build("twin", "Priya Raman", "Analytical Engines", "https://github.com/shared-dev B.Tech from Rajalakshmi Engineering College, graduated 2022", 900), stored_decision("twin"))
    claim_calls.clear()
    current = build("current", "Raman Priya", "Analytical Engines", "https://github.com/shared-dev B.Tech from Rajalakshmi Engineering College, graduated 2022", 901)
    decision = evaluate(current, job_factory(), store, Config())
    codes = {item.code for item in decision.reasons}
    assert {"LINK_REUSE", "FUZZY_IDENTITY"} <= codes
    assert {pair[1] for pair in compared} == {"Priya Raman"}
    assert set(claim_calls) - {"current"} == {"twin"}

