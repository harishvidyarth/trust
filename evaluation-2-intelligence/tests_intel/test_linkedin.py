from __future__ import annotations

from datetime import date

from firewall.intel.linkedin import cross_check, ground, parse_linkedin_pdf, parse_text, pdf_text
from firewall.models import Candidate, Experience

from tests_intel.linkedin_samples import ADA_LINES, ada_pdf, bob_pdf

TODAY = date(2026, 1, 15)


def candidate(experience):
    return Candidate(name="Ada Synthetic", email="ada@example.com", phone="9000000000", experience=experience)


def test_parse_ada_pdf_fields():
    profile = parse_linkedin_pdf(ada_pdf())
    assert profile.name == "Ada Synthetic"
    assert profile.headline == "Staff Engineer at Analytical Engines"
    assert profile.location == "London, United Kingdom"
    assert profile.skills == ["Python", "Distributed Systems", "Kubernetes"]
    assert [(item.company, item.title, item.start, item.end) for item in profile.experience] == [
        ("Analytical Engines", "Staff Engineer", "2020-01", "Present"),
        ("Difference Works", "Software Engineer", "2017-06", "2019-12"),
    ]
    assert profile.education[0].school == "University of Example"
    assert (profile.education[0].start, profile.education[0].end) == ("2013", "2017")
    assert profile.dropped == []


def test_parse_bob_pdf_fields():
    profile = parse_linkedin_pdf(bob_pdf())
    assert profile.name == "Bob Synthetic"
    assert [item.company for item in profile.experience] == ["Northwind Traders", "Contoso Retail"]
    assert profile.experience[1].start == "2018-04" and profile.experience[1].end == "2021-02"
    assert profile.education[0].detail.startswith("Master of Business Administration")


def test_grounding_drops_non_verbatim_values():
    text = pdf_text(ada_pdf())
    profile = parse_text(text)
    profile.experience[0].company = "Hallucinated Dynamics"
    profile.skills.append("Quantum Telepathy")
    profile.headline = "Chief Wizard"
    grounded = ground(profile, text)
    assert [item.company for item in grounded.experience] == ["Difference Works"]
    assert "Quantum Telepathy" not in grounded.skills
    assert grounded.headline == ""
    assert any("Hallucinated Dynamics" in item for item in grounded.dropped)
    assert any("Quantum Telepathy" in item for item in grounded.dropped)


def test_grounding_drops_dates_not_in_source():
    text = pdf_text(ada_pdf())
    profile = parse_text(text)
    profile.experience[1].start = "1999-01"
    grounded = ground(profile, text)
    assert [item.company for item in grounded.experience] == ["Analytical Engines"]


def test_cross_check_consistent_resume_has_no_reasons():
    profile = parse_linkedin_pdf(ada_pdf())
    resume = candidate(
        [
            Experience(company="Analytical Engines", title="Staff Engineer", start="2020-01", end="Present"),
            Experience(company="Difference Works", title="Software Engineer", start="2017-06", end="2019-12"),
        ]
    )
    assert cross_check(profile, resume, today=TODAY) == []


def test_cross_check_employer_missing_both_directions():
    profile = parse_linkedin_pdf(ada_pdf())
    resume = candidate(
        [
            Experience(company="Analytical Engines", title="Staff Engineer", start="2020-01", end="Present"),
            Experience(company="Phantom Labs", title="Engineer", start="2015-01", end="2017-05"),
        ]
    )
    reasons = cross_check(profile, resume, today=TODAY)
    codes = [item.code for item in reasons]
    assert codes == ["LINKEDIN_EMPLOYER_MISSING", "LINKEDIN_EMPLOYER_MISSING"]
    assert any("Phantom Labs" in item.detail and "experience[1]" in item.detail for item in reasons)
    assert any("Difference Works" in item.detail and "line" in item.detail for item in reasons)
    assert all(item.weight == 6 for item in reasons)


def test_cross_check_date_mismatch_over_three_months_only():
    profile = parse_linkedin_pdf(ada_pdf())
    close = candidate([Experience(company="Difference Works", title="Software Engineer", start="2017-08", end="2019-12")])
    far = candidate([Experience(company="Difference Works", title="Software Engineer", start="2017-06", end="2020-06")])
    close_codes = [item.code for item in cross_check(profile, close, today=TODAY) if item.code == "LINKEDIN_DATE_MISMATCH"]
    far_reasons = [item for item in cross_check(profile, far, today=TODAY) if item.code == "LINKEDIN_DATE_MISMATCH"]
    assert close_codes == []
    assert len(far_reasons) == 1 and "2020-06" in far_reasons[0].detail and "2019-12" in far_reasons[0].detail
    assert far_reasons[0].weight == 5


def test_cross_check_title_mismatch():
    profile = parse_linkedin_pdf(ada_pdf())
    resume = candidate([Experience(company="Difference Works", title="Chief Marketing Officer", start="2017-06", end="2019-12")])
    reasons = [item for item in cross_check(profile, resume, today=TODAY) if item.code == "LINKEDIN_TITLE_MISMATCH"]
    assert len(reasons) == 1
    assert "Chief Marketing Officer" in reasons[0].detail and "Software Engineer" in reasons[0].detail
    assert reasons[0].weight == 4


def test_sample_pdfs_are_distinct_and_synthetic():
    assert ada_pdf() != bob_pdf()
    assert "Analytical Engines" in "\n".join(ADA_LINES)
