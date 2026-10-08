from __future__ import annotations

import threading

import pytest

from firewall.config import Config
from firewall.engine import evaluate
from firewall.models import Decision, Experience, Project, Route
from firewall.signals.identity_links import detect_fuzzy_identity, detect_link_reuse
from firewall.redis_layer import RedisApplicationStore
from firewall.store import InMemoryApplicationStore
from tests_redis.conftest import BASE_TS, stored_decision


def ids(items):
    return [item.application_id for item in items]


def test_indexes_and_inclusive_out_of_order_time_queries(store, application_factory):
    store.save(application_factory(application_id="later", submitted_at=200, device_id="shared", ip="same-ip"), stored_decision("later"))
    store.save(application_factory(application_id="earlier", submitted_at=100, device_id="shared", ip="same-ip"), stored_decision("earlier"))
    assert set(ids(store.by_email("ada@example.com"))) == {"earlier", "later"}
    assert set(ids(store.by_phone("9876543210"))) == {"earlier", "later"}
    assert set(ids(store.by_job("job-1"))) == {"earlier", "later"}
    assert ids(store.recent(100, 100)) == ["earlier"]
    assert ids(store.recent_by_device("shared", 100, 200)) == ["later", "earlier"]
    assert set(ids(store.recent_by_ip("same-ip", 100, 200))) == {"earlier", "later"}
    assert set(ids(store.recent_by_identity("ada@example.com", "9876543210", 100, 200))) == {"earlier", "later"}
    assert store.recent_by_identity("", "", 100, 200) == ()
    assert store.recent_by_device("shared", 201, 300) == ()


def test_applications_and_decisions_keep_first_save_order(store, application_factory):
    for name in ("c", "a", "b"):
        store.save(application_factory(application_id=name, email=f"{name}@example.com", phone=f"900000000{ord(name) % 10}"), stored_decision(name))
    assert ids(store.applications()) == ["c", "a", "b"]
    assert [item.application_id for item in store.decisions()] == ["c", "a", "b"]
    assert store.get_decision("a").score == 100
    assert store.get_decision("missing") is None


def test_resume_template_replacement_and_clear(store, application_factory):
    store.save(application_factory(application_id="replace-me"), stored_decision("replace-me"))
    query = application_factory(application_id="query", name="Other", email="other@example.com", phone="9000000000")
    assert ids(store.resume_candidates(query)) == ["replace-me"]
    text = "built a reliable distributed job scheduling service in modern python"
    assert store.template_identity_count(text, "ada@example.com") == 1
    assert store.template_identity_count(text, "other@example.com") == 2
    assert store.has_exact_resume_from_other_identity(query) is True
    store.save(
        application_factory(application_id="replace-me", email="replacement@example.com", phone="8111111111", projects=[]),
        stored_decision("replace-me"),
    )
    assert store.by_email("ada@example.com") == ()
    assert store.resume_candidates(query) == ()
    assert store.has_exact_resume_from_other_identity(query) is False
    assert store.template_identity_count(text, "ada@example.com") == 1
    assert store.template_identity_count(text, "other@example.com") == 1
    store.clear()
    assert store.applications() == ()
    assert store.by_email("replacement@example.com") == ()
    assert store.decisions() == ()


def test_same_identity_resume_is_not_other_identity(store, application_factory):
    store.save(application_factory(application_id="one"), stored_decision("one"))
    assert store.has_exact_resume_from_other_identity(application_factory(application_id="two")) is False
    store.save(application_factory(application_id="three", name="Eve", email="eve@example.com", phone="9111111111"), stored_decision("three"))
    assert store.has_exact_resume_from_other_identity(application_factory(application_id="four")) is True


def test_resume_candidates_include_all(store, application_factory):
    store.save(application_factory(application_id="x", projects=[]), stored_decision("x"))
    store.save(application_factory(application_id="y", email="y@example.com", phone="9222222222"), stored_decision("y"))
    query = application_factory(application_id="q", email="q@example.com", phone="9333333333")
    assert ids(store.resume_candidates(query)) == ["y"]
    assert ids(store.resume_candidates(query, include_all=True)) == ["x", "y"]


def test_save_is_idempotent_by_application_id(store, application_factory):
    application = application_factory(application_id="same")
    store.save(application, stored_decision("same", 10))
    store.save(application, stored_decision("same", 20))
    assert ids(store.applications()) == ["same"]
    assert len(store.decisions()) == 1
    assert store.get_decision("same").score == 20
    assert ids(store.by_email("ada@example.com")) == ["same"]
    assert store.template_identity_count("built a reliable distributed job scheduling service in modern python", "ada@example.com") == 1


def test_gmail_style_email_normalisation_matches_engine_keys(store, application_factory):
    store.save(application_factory(application_id="g", email="a.d.a+x@gmail.com"), stored_decision("g"))
    assert ids(store.by_email("ada@gmail.com")) == ["g"]


def test_engine_decisions_match_in_memory_reference(store, application_factory, job_factory):
    reference = InMemoryApplicationStore()
    cases = [
        application_factory(application_id=f"a{index}", device_id="rot", submitted_at=BASE_TS + index, email=f"u{index}@example.com", phone=f"90000000{index:02d}")
        for index in range(8)
    ]
    cases.append(application_factory(application_id="dup", name="Zed", email="zed@example.com", phone="9444444444"))
    for application in cases:
        expected = evaluate(application, job_factory(), reference, Config())
        actual = evaluate(application, job_factory(), store, Config())
        assert actual.model_dump() == expected.model_dump()
    assert [d.model_dump() for d in store.decisions()] == [d.model_dump() for d in reference.decisions()]


def test_engine_uses_indexed_queries_on_this_backend(store, application_factory, job_factory):
    class Guarded(type(store)):
        def applications(self):
            raise AssertionError("full scan")

    if isinstance(store, RedisApplicationStore):
        guarded = Guarded(store._link)
    else:
        guarded = Guarded()
    assert evaluate(application_factory(), job_factory(), guarded, Config()).route == Route.PASS_TO_ATS


def test_qualification_only_flag_survives_roundtrip(store, application_factory):
    application = application_factory(application_id="flag")
    application.candidate._qualification_only_experience = True
    store.save(application, stored_decision("flag"))
    assert store.applications()[0].candidate._qualification_only_experience is True


def test_concurrent_saves_of_same_id_keep_one_record(backend, application_factory):
    if backend is None:
        pytest.skip("redis only")
    store = RedisApplicationStore(backend)
    errors = []

    def worker(score):
        try:
            store.save(application_factory(application_id="race"), stored_decision("race", score))
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=worker, args=(score,)) for score in range(10, 20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert ids(store.applications()) == ["race"]
    assert backend.client.zcard("trust:store:order") == 1
    assert backend.client.hget("trust:store:xc", next(iter(backend.client.hkeys("trust:store:xc")))) == "1"


def test_window_ttl_applied_to_velocity_indexes(backend, application_factory):
    if backend is None:
        pytest.skip("redis only")
    store = RedisApplicationStore(backend, window_ttl_s=120)
    store.save(application_factory(application_id="t"), stored_decision("t"))
    assert 0 < backend.client.ttl("trust:store:tdev:device-1") <= 120
    assert 0 < backend.client.ttl("trust:store:tip:203.0.113.1") <= 120
    assert backend.client.ttl("trust:store:app:t") == -1


def test_all_keys_use_trust_prefix(backend, application_factory):
    if backend is None:
        pytest.skip("redis only")
    RedisApplicationStore(backend).save(application_factory(), stored_decision("app-1"))
    keys = list(backend.client.scan_iter())
    assert keys and all(key.startswith("trust:") for key in keys)


LINK_TEXT = "Portfolio at https://github.com/shared-dev and Credential ID: ABC12345XYZ"
FUZZY_TEXT = "B.Tech from Rajalakshmi Engineering College, graduated 2022"


def with_projects(factory, application_id, text, **kwargs):
    return factory(application_id=application_id, projects=[Project(name="Portfolio", description=text)], **kwargs)


def with_company(application, company):
    application.candidate.experience = [Experience(company=company, title="Engineer", start="2022-07", end="2025-01")]
    return application


def test_link_index_covers_urls_and_certificate_ids(store, application_factory):
    store.save(with_projects(application_factory, "one", LINK_TEXT), stored_decision("one"))
    store.save(with_projects(application_factory, "two", "see github.com/Other-Dev", email="b@example.com", phone="9111111111"), stored_decision("two"))
    assert ids(store.by_link("github:shared-dev")) == ["one"]
    assert ids(store.by_link("certificate:abc12345xyz")) == ["one"]
    assert ids(store.by_link("github:other-dev")) == ["two"]
    assert store.by_link("github:nobody") == ()


def test_name_token_index_is_case_and_order_insensitive(store, application_factory):
    store.save(application_factory(application_id="n1", name="Dr. Priya Raman"), stored_decision("n1"))
    store.save(application_factory(application_id="n2", name="Raman Kumar", email="k@example.com", phone="9111111111"), stored_decision("n2"))
    assert ids(store.by_name_token("raman")) == ["n1", "n2"]
    assert ids(store.by_name_token("priya")) == ["n1"]
    assert store.by_name_token("dr") == ()
    assert store.by_name_token("zzz") == ()


def test_signature_index_keys_employer_college_degree(store, application_factory):
    application = with_company(with_projects(application_factory, "s1", FUZZY_TEXT), "Analytical Engines")
    store.save(application, stored_decision("s1"))
    assert ids(store.by_signature("emp:analytical engines\x1f2022")) == ["s1"]
    assert ids(store.by_signature("deg:btech\x1f2022")) == ["s1"]
    assert len(store.by_signature("col:rajalakshmi engineering college")) == 1
    assert store.by_signature("emp:analytical engines\x1f2021") == ()


def test_identity_indexes_follow_replacement_and_clear(store, application_factory):
    store.save(with_projects(application_factory, "r", LINK_TEXT), stored_decision("r"))
    store.save(with_projects(application_factory, "r", "nothing linkable here", name="Grace Hopper"), stored_decision("r"))
    assert store.by_link("github:shared-dev") == ()
    assert store.by_name_token("ada") == ()
    assert ids(store.by_name_token("hopper")) == ["r"]
    store.save(with_projects(application_factory, "r", LINK_TEXT), stored_decision("r"))
    store.save(with_projects(application_factory, "r", LINK_TEXT), stored_decision("r"))
    assert ids(store.by_link("github:shared-dev")) == ["r"]
    store.clear()
    assert store.by_link("github:shared-dev") == ()
    assert store.by_name_token("ada") == ()
    assert store.by_signature("emp:analytical engines\x1f2020") == ()


def test_identity_index_keys_use_trust_prefix_and_clear_leaves_nothing(backend, application_factory):
    if backend is None:
        pytest.skip("redis only")
    store = RedisApplicationStore(backend)
    store.save(with_projects(application_factory, "k", LINK_TEXT + " " + FUZZY_TEXT), stored_decision("k"))
    keys = set(backend.client.scan_iter())
    assert {"trust:store:lk:github:shared-dev", "trust:store:nt:ada", "trust:store:nt:lovelace"} <= keys
    assert all(key.startswith("trust:") for key in keys)
    store.clear()
    assert list(backend.client.scan_iter(match="trust:store:*")) == []


def test_link_reuse_fires_through_indexes_on_this_backend(store, application_factory):
    config = Config()
    store.save(with_projects(application_factory, "a1", LINK_TEXT, name="Grace Hopper", email="g@example.com", phone="9000000001"), stored_decision("a1"))
    current = with_projects(application_factory, "a2", LINK_TEXT, name="Alan Turing", email="a@example.com", phone="9000000002")
    reasons = detect_link_reuse(current, store, config)
    assert [item.code for item in reasons] == ["LINK_REUSE"]
    assert "a1" in reasons[0].detail
    same_person = with_projects(application_factory, "a3", LINK_TEXT, name="Grace Hopper", email="g@example.com", phone="9000000001")
    assert detect_link_reuse(same_person, store, config) == []


def test_fuzzy_identity_fires_through_indexes_on_this_backend(store, application_factory):
    config = Config()
    store.save(with_company(with_projects(application_factory, "f1", FUZZY_TEXT, name="Priya Raman", email="p@example.com", phone="9100000001"), "Analytical Engines"), stored_decision("f1"))
    current = with_company(with_projects(application_factory, "f2", FUZZY_TEXT, name="Raman Priya", email="q@example.org", phone="9100000002"), "Analytical Engines")
    reasons = detect_fuzzy_identity(current, store, config)
    assert [item.code for item in reasons] == ["FUZZY_IDENTITY"]
    assert "f1" in reasons[0].detail
    weak = with_company(with_projects(application_factory, "f3", "Studied elsewhere", name="Raman Priya", email="r@example.org", phone="9100000003"), "Other Corp")
    assert detect_fuzzy_identity(weak, store, config) == []


def test_engine_identity_checks_never_full_scan_on_this_backend(store, application_factory, job_factory):
    class Guarded(type(store)):
        def applications(self):
            raise AssertionError("full scan")

    guarded = Guarded(store._link) if isinstance(store, RedisApplicationStore) else Guarded()
    guarded.save(with_projects(application_factory, "p1", LINK_TEXT, name="Grace Hopper", email="g@example.com", phone="9000000001"), stored_decision("p1"))
    decision = evaluate(with_projects(application_factory, "p2", LINK_TEXT, name="Alan Turing", email="a@example.com", phone="9000000002"), job_factory(), guarded, Config())
    assert any(item.code == "LINK_REUSE" for item in decision.reasons)
