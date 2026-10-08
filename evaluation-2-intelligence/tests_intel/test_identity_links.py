from __future__ import annotations

from firewall.models import Decision, Experience, Project, Route
from firewall.signals.identity_links import (
    canonical_email,
    canonical_phone,
    detect_email_alias,
    detect_fuzzy_identity,
    detect_identity_links,
    detect_link_reuse,
    jaro_winkler,
    link_key,
    name_similarity,
)

from tests_intel.conftest import make_application


def save(store, application):
    store.save(application, Decision(application_id=application.application_id, score=80, route=Route.PASS_TO_ATS, summary="ok"))


def project(text):
    return [Project(name="Portfolio", description=text)]


def test_link_key_normalises_hosts_and_kinds():
    assert link_key("https://www.GitHub.com/Ada-L/repo") == ("github", "ada-l")
    assert link_key("github.com/ada-l") == ("github", "ada-l")
    assert link_key("https://linkedin.com/in/ada-l/") == ("linkedin", "ada-l")
    assert link_key("https://www.ada.dev/work/") == ("portfolio", "ada.dev/work")
    assert link_key("https://doi.org/10.1000/xyz") is None
    assert link_key("https://github.com/orgs/acme") is None


def test_link_reuse_flags_same_github_under_different_identity(store, config):
    save(store, make_application("a1", name="Grace Hopper", email="grace@example.com", phone="9000000001", projects=project("Profile https://github.com/shared-dev with COBOL work")))
    current = make_application("a2", name="Alan Turing", email="alan@example.com", phone="9000000002", projects=project("See github.com/Shared-Dev for code"))
    reasons = detect_link_reuse(current, store, config)
    assert [item.code for item in reasons] == ["LINK_REUSE"]
    assert reasons[0].weight == 25
    assert "projects[0].description" in reasons[0].detail
    assert "github.com/Shared-Dev" in reasons[0].detail
    assert "a1" in reasons[0].detail


def test_link_reuse_ignores_same_person(store, config):
    save(store, make_application("a1", projects=project("https://github.com/ada-l")))
    current = make_application("a2", job_id="job-2", projects=project("https://github.com/ada-l"))
    assert detect_link_reuse(current, store, config) == []


def test_link_reuse_certificate_id(store, config):
    save(store, make_application("a1", name="Grace Hopper", email="g@example.com", phone="9000000001", projects=project("AWS Certified, Credential ID: ABC12345XYZ")))
    current = make_application("a2", name="Alan Turing", email="a@example.com", phone="9000000002", projects=project("Certificate ID ABC12345XYZ issued 2024"))
    reasons = detect_link_reuse(current, store, config)
    assert reasons and "certificate ID" in reasons[0].detail
    assert "ABC12345XYZ" in reasons[0].detail


def test_link_reuse_distinct_links_not_flagged(store, config):
    save(store, make_application("a1", name="Grace Hopper", email="g@example.com", phone="9000000001", projects=project("https://github.com/grace")))
    current = make_application("a2", name="Alan Turing", email="a@example.com", phone="9000000002", projects=project("https://github.com/alan"))
    assert detect_link_reuse(current, store, config) == []


def test_jaro_winkler_and_name_similarity():
    assert jaro_winkler("martha", "marhta") > 0.96
    assert name_similarity("Ada Lovelace", "Lovelace Ada") == 1.0
    assert name_similarity("A. Lovelace", "Ada Lovelace") >= 0.95
    assert name_similarity("Dr. Ada Lovelace", "Ada Lovelace") >= 0.95
    assert name_similarity("Ada Lovelace", "Charles Babbage") < 0.7


FUZZY_TEXT = "B.Tech from Rajalakshmi Engineering College, graduated 2022"


def fuzzy_app(app_id, name, email, phone, company="Analytical Engines", text=FUZZY_TEXT):
    return make_application(
        app_id,
        name=name,
        email=email,
        phone=phone,
        experience=[Experience(company=company, title="Engineer", start="2022-07", end="2025-01")],
        projects=project(text),
    )


def test_fuzzy_identity_name_variant_same_signature(store, config):
    save(store, fuzzy_app("a1", "Priya Raman", "priya@example.com", "9100000001"))
    current = fuzzy_app("a2", "Raman Priya", "p.raman.work@example.org", "9100000002")
    reasons = detect_fuzzy_identity(current, store, config)
    assert [item.code for item in reasons] == ["FUZZY_IDENTITY"]
    assert reasons[0].weight == 20
    assert "Priya Raman" in reasons[0].detail and "analytical engines" in reasons[0].detail


def test_fuzzy_identity_requires_two_signature_components(store, config):
    save(store, fuzzy_app("a1", "Priya Raman", "priya@example.com", "9100000001"))
    current = fuzzy_app("a2", "Raman Priya", "other@example.org", "9100000002", company="Other Corp", text="Studied elsewhere")
    assert detect_fuzzy_identity(current, store, config) == []


def test_fuzzy_identity_different_name_not_flagged(store, config):
    save(store, fuzzy_app("a1", "Priya Raman", "priya@example.com", "9100000001"))
    current = fuzzy_app("a2", "Zoya Khan", "zoya@example.org", "9100000002")
    assert detect_fuzzy_identity(current, store, config) == []


def test_fuzzy_identity_skips_same_person(store, config):
    save(store, fuzzy_app("a1", "Priya Raman", "priya@example.com", "9100000001"))
    current = fuzzy_app("a2", "Priya Raman", "priya@example.com", "9100000001")
    assert detect_fuzzy_identity(current, store, config) == []


def test_email_normaliser_functions():
    assert canonical_email("A.d.a+jobs@googlemail.com") == "ada@gmail.com"
    assert canonical_phone("+91 98765 43210") == canonical_phone("09876543210") == canonical_phone("0091-9876543210") == "9876543210"


def test_email_alias_gmail_dots_plus(store, config):
    save(store, make_application("a1", email="ada@gmail.com", phone="9111111111"))
    current = make_application("a2", job_id="job-2", email="a.da+x@googlemail.com", phone="9222222222")
    reasons = detect_email_alias(current, store, config)
    assert [item.code for item in reasons] == ["EMAIL_ALIAS"]
    assert reasons[0].weight == 22
    assert '"a.da+x@googlemail.com"' in reasons[0].detail and "ada@gmail.com" in reasons[0].detail


def test_email_alias_phone_variants(store, config):
    save(store, make_application("a1", email="one@example.com", phone="+91 98765 43210"))
    current = make_application("a2", email="two@example.com", phone="09876543210")
    reasons = detect_email_alias(current, store, config)
    assert reasons and "phone" in reasons[0].detail and "3210" in reasons[0].detail


def test_email_alias_identical_raw_forms_not_alias(store, config):
    save(store, make_application("a1", email="ada@example.com", phone="+91 98765 43210"))
    current = make_application("a2", email="ada@example.com", phone="+91 98765 43210")
    assert detect_email_alias(current, store, config) == []


def test_config_weight_override_and_aggregate(store):
    from firewall.config import Config

    save(store, make_application("a1", email="ada@gmail.com", phone="9111111111"))
    current = make_application("a2", job_id="job-2", email="a.da@gmail.com", phone="9222222222")
    custom = Config.from_dict({"weights": {"EMAIL_ALIAS": 7}})
    reasons = detect_identity_links(current, store, custom)
    assert [(item.code, item.weight) for item in reasons] == [("EMAIL_ALIAS", 7)]
