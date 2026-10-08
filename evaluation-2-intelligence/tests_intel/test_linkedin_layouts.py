from __future__ import annotations

import random
import re
import socket
import time
from datetime import date
from io import BytesIO

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

import firewall.intel.linkedin as linkedin
from firewall.intel.linkedin import cross_check, parse_linkedin_pdf, parse_text, pdf_text
from firewall.models import Candidate, Experience

from tests_intel import linkedin_samples as samples

TODAY = date(2026, 1, 15)
FORBIDDEN = re.compile(r"[-():;]")


def candidate(experience):
    return Candidate(name="Ada Synthetic", email="ada@example.com", phone="9000000000", experience=experience)


def exp(company, title, start, end):
    return Experience(company=company, title=title, start=start, end=end)


def three_page_pdf() -> bytes:
    padded = []
    for kind, text in samples.TWO_COLUMN_MAIN:
        padded.append((kind, text))
        if kind == "loc":
            padded.extend([("text", samples.LONG_DESC)] * 11)
    return samples.make_two_column_pdf(samples.TWO_COLUMN_SIDEBAR, padded)


def blank_pages_pdf(count: int) -> bytes:
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    for number in range(count):
        pdf.drawString(60, 700, f"Ada Synthetic page filler {number}")
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def squashed(text):
    return re.sub(r"\s+", " ", text).strip().casefold()


TWO_COLUMN_BUILDERS = [
    samples.two_column_pdf,
    samples.two_column_long_pdf,
    samples.flat_font_pdf,
    three_page_pdf,
]


@pytest.mark.parametrize("build", TWO_COLUMN_BUILDERS)
def test_two_column_layout_separates_sidebar_from_main(build):
    profile = parse_linkedin_pdf(build())
    assert profile.layout == "two_column"
    assert profile.name == "Ada Synthetic"
    assert profile.headline.startswith("Staff Engineer at Analytical Engines and Technical Lead")
    assert profile.headline.endswith("Scheduling Platform Group")
    assert profile.location == "London, United Kingdom"
    assert profile.skills == ["Python", "Distributed Systems", "Kubernetes and Container Orchestration Platforms"]
    assert profile.languages == ["English (Native or Bilingual)", "Hindi (Professional Working)"]
    assert profile.certifications == [
        "Certified Kubernetes Application Developer Associate Program",
        "Example Cloud Practitioner",
    ]
    assert profile.summary.startswith("Builds scheduling systems.")
    assert profile.dropped == []
    assert profile.warnings == []
    assert profile.confidence == 1.0


@pytest.mark.parametrize("build", TWO_COLUMN_BUILDERS)
def test_multi_role_company_and_wrapped_titles(build):
    profile = parse_linkedin_pdf(build())
    rows = [(item.company, item.title, item.start, item.end) for item in profile.experience]
    assert rows == [
        ("Analytical Engines", "Staff Engineer", "2023-03", "Present"),
        ("Analytical Engines", "Senior Software Engineer", "2020-01", "2023-02"),
        (
            "Difference Works Private Limited",
            "Senior Principal Machine Learning Platform Engineer for Distributed Training Infrastructure",
            "2017-06",
            "2019-12",
        ),
        ("Babbage Labs", "Intern", "2016", "2017"),
    ]
    assert profile.experience[0].company_duration == "5 years 10 months"
    assert profile.experience[1].company_duration == "5 years 10 months"
    assert profile.experience[2].company_duration == ""


def test_duration_text_never_becomes_a_date():
    profile = parse_linkedin_pdf(samples.two_column_pdf())
    for item in profile.experience:
        assert "year" not in item.start and "month" not in item.start
        assert "year" not in item.end and "month" not in item.end
    assert profile.experience[0].raw_range == "March 2023 - Present"


def test_education_with_month_and_year_ranges():
    profile = parse_linkedin_pdf(samples.two_column_pdf())
    assert [(item.school, item.start, item.end) for item in profile.education] == [
        ("University of Example", "2013", "2017"),
        ("Example Institute of Technology", "2020-08", "2022-05"),
    ]


def test_three_pages_footers_removed_and_pages_recorded():
    data = three_page_pdf()
    profile = parse_linkedin_pdf(data)
    assert profile.pages_read == 3
    text = pdf_text(data)
    assert not re.search(r"Page \d+ of \d+", text)
    assert {item.page for item in profile.experience} >= {1, 2}
    assert max(item.page for item in profile.experience) >= 2


def test_sidebar_text_never_leaks_into_main_fields():
    profile = parse_linkedin_pdf(samples.two_column_long_pdf())
    joined = " ".join([profile.name, profile.headline, profile.location, profile.summary])
    for sidebar_word in ("Kubernetes", "Hindi", "Certified", "Distributed Systems"):
        assert sidebar_word not in joined
    for item in profile.experience:
        assert "Certified" not in item.company + item.title


def test_accented_names_and_companies():
    profile = parse_linkedin_pdf(samples.accent_pdf())
    assert profile.name == "José Álvarez-Müller"
    assert profile.skills[0] == "Aprendizaje automático"
    assert [item.company for item in profile.experience] == ["Société Générale", "Zoë & Søn Café GmbH"]
    assert [item.start for item in profile.experience] == ["2021-09", "2018-05"]
    assert profile.education[0].school == "Universität München"


def test_localised_german_headings_and_dates():
    profile = parse_linkedin_pdf(samples.german_pdf())
    assert profile.name == "Hanna Größ"
    assert profile.skills == ["Projektleitung", "Python", "Statistik"]
    assert (profile.experience[0].company, profile.experience[0].start, profile.experience[0].end) == (
        "Beispiel AG",
        "2020-01",
        "Present",
    )
    assert profile.education[0].school == "Universität zu Köln"


def test_profile_without_experience():
    profile = parse_linkedin_pdf(samples.no_experience_pdf())
    assert profile.name == "Nia Newgrad"
    assert profile.experience == [] and profile.education == []
    assert profile.skills == ["Data Analysis", "Statistics", "SQL"]
    assert profile.confidence == 1.0
    assert profile.confidence_message == "We could read most of your export."


def test_profile_with_only_education():
    profile = parse_linkedin_pdf(samples.only_education_pdf())
    assert profile.experience == []
    assert [(item.school, item.start, item.end) for item in profile.education] == [
        ("Example University", "2022", "2024"),
        ("Sample College", "2019", "2022"),
    ]
    assert profile.confidence == 1.0


@pytest.mark.parametrize(
    "build",
    TWO_COLUMN_BUILDERS
    + [samples.accent_pdf, samples.german_pdf, samples.no_experience_pdf, samples.only_education_pdf, samples.ada_pdf, samples.bob_pdf],
)
def test_every_value_is_grounded_verbatim_in_the_pdf_text(build):
    data = build()
    profile = parse_linkedin_pdf(data)
    haystack = squashed(pdf_text(data))
    values = [profile.name, profile.headline, profile.location, *profile.skills, *profile.languages, *profile.certifications]
    for item in profile.experience:
        values.extend([item.company, item.title, item.raw_range])
    for item in profile.education:
        values.extend([item.school, item.detail])
    for value in values:
        assert squashed(value) in haystack


def test_pdf_text_roundtrip_matches_layout_parse():
    data = samples.two_column_long_pdf()
    direct = parse_linkedin_pdf(data)
    from_text = linkedin.ground(parse_text(pdf_text(data)), pdf_text(data))
    assert [(item.company, item.title, item.start, item.end) for item in from_text.experience][:2] == [
        (item.company, item.title, item.start, item.end) for item in direct.experience
    ][:2]
    assert from_text.name == direct.name
    assert from_text.skills[:3] == direct.skills[:3]
    assert from_text.layout == "two_column"


def test_confidence_drops_when_values_are_not_grounded():
    data = samples.two_column_pdf()
    text = pdf_text(data)
    profile = parse_text(text)
    profile.experience[0].company = "Hallucinated Dynamics"
    profile.name = "Somebody Else"
    grounded = linkedin.ground(profile, text)
    assert grounded.fields_found < grounded.fields_expected
    assert grounded.confidence < 1.0
    assert grounded.dropped


def test_confidence_message_levels():
    base = linkedin.LinkedInProfile(name="A", headline="B", location="C", sections=["experience"], experience_expected=4)
    base.experience = [
        linkedin.LinkedInExperience(company="X", title="Y", start="2020", end="2021", line=1) for _ in range(1)
    ]
    scored = linkedin._score(base)
    assert scored.confidence_message == "We could read part of your export."
    base.experience = []
    assert linkedin._score(base).confidence_message == "We could not read much of your export."
    assert linkedin._score(linkedin.LinkedInProfile()).confidence == 0.0


def test_plain_wording_in_user_facing_strings():
    for data in (b"", b"not a pdf", samples.two_column_pdf()[:400], blank_pages_pdf(12)):
        profile = parse_linkedin_pdf(data)
        assert not FORBIDDEN.search(profile.confidence_message)
        for warning in profile.warnings:
            assert not FORBIDDEN.search(warning)


@pytest.mark.parametrize("data", [b"", b"hello", b"%PDF-1.4 garbage", bytes(range(256)) * 20, samples.ada_pdf()[:300]])
def test_garbage_returns_empty_profile_with_warnings(data):
    profile = parse_linkedin_pdf(data)
    assert profile.experience == [] and profile.education == [] and profile.name == ""
    assert profile.confidence == 0.0
    assert profile.warnings
    assert profile.confidence_message == "We could not read much of your export."


def test_non_bytes_input_never_raises():
    for value in (None, 12, "text", [1, 2]):
        profile = parse_linkedin_pdf(value)
        assert profile.warnings


def test_page_cap_is_ten():
    profile = parse_linkedin_pdf(blank_pages_pdf(14))
    assert profile.pages_read == linkedin.MAX_PAGES == 10
    assert any("first 10 pages" in warning for warning in profile.warnings)


def test_text_cap(monkeypatch):
    monkeypatch.setattr(linkedin, "MAX_TEXT_CHARS", 300)
    profile = parse_linkedin_pdf(samples.two_column_long_pdf())
    assert sum(len(line) + 1 for line in profile.lines) <= 400
    assert any("very large amount of text" in warning for warning in profile.warnings)


def test_no_network_and_links_are_not_followed(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.drawString(60, 760, "Ada Synthetic")
    pdf.drawString(60, 740, "Staff Engineer at Example")
    pdf.drawString(60, 720, "London, United Kingdom")
    pdf.drawString(60, 700, "https://example.invalid/profile")
    pdf.linkURL("https://example.invalid/profile", (60, 695, 250, 712))
    pdf.showPage()
    pdf.save()
    profile = parse_linkedin_pdf(buffer.getvalue())
    assert profile.name == "Ada Synthetic"


def test_fuzz_random_bytes_never_crash_or_hang():
    rng = random.Random(7)
    started = time.monotonic()
    for _ in range(150):
        blob = rng.randbytes(rng.randint(0, 3000))
        if rng.random() < 0.5:
            blob = b"%PDF-1.4\n" + blob
        profile = parse_linkedin_pdf(blob)
        assert isinstance(profile, linkedin.LinkedInProfile)
        assert pdf_text(blob) is not None
    assert time.monotonic() - started < 40


def test_fuzz_truncated_pdfs_never_crash_or_hang():
    data = samples.two_column_long_pdf()
    started = time.monotonic()
    for cut in range(0, len(data), max(len(data) // 60, 1)):
        profile = parse_linkedin_pdf(data[:cut])
        assert isinstance(profile, linkedin.LinkedInProfile)
    assert time.monotonic() - started < 60


def test_fuzz_mutated_pdfs_never_crash_or_hang():
    rng = random.Random(11)
    data = bytearray(samples.two_column_pdf())
    started = time.monotonic()
    for _ in range(60):
        mutated = bytearray(data)
        for _ in range(rng.randint(1, 40)):
            mutated[rng.randrange(len(mutated))] = rng.randrange(256)
        assert isinstance(parse_linkedin_pdf(bytes(mutated)), linkedin.LinkedInProfile)
    assert time.monotonic() - started < 60


def test_huge_page_count_is_capped_and_fast():
    data = blank_pages_pdf(1500)
    started = time.monotonic()
    profile = parse_linkedin_pdf(data)
    assert time.monotonic() - started < 20
    assert profile.pages_read == 10
    assert any("first 10 pages" in warning for warning in profile.warnings)


def test_forged_page_count_never_hangs():
    data = blank_pages_pdf(3)
    forged = re.sub(rb"/Count 3", b"/Count 99999999", data)
    assert forged != data
    started = time.monotonic()
    profile = parse_linkedin_pdf(forged)
    assert time.monotonic() - started < 20
    assert isinstance(profile, linkedin.LinkedInProfile)


def test_time_limit_stops_reading(monkeypatch):
    monkeypatch.setattr(linkedin, "TIME_LIMIT_SECONDS", -1.0)
    profile = parse_linkedin_pdf(samples.two_column_pdf())
    assert any("took too long" in warning for warning in profile.warnings)
    assert profile.pages_read == 0


def test_cross_check_company_normalisation():
    profile = parse_linkedin_pdf(samples.two_column_pdf())
    resume = candidate(
        [
            exp("Analytical Engines Inc.", "Staff Engineer", "2023-03", "Present"),
            exp("DIFFERENCE WORKS PVT. LTD.", "Senior Principal ML Platform Engineer", "2017-06", "2019-12"),
            exp("Babbage Labs, LLC", "Intern", "2016", "2017"),
        ]
    )
    codes = [item.code for item in cross_check(profile, resume, today=TODAY)]
    assert "LINKEDIN_EMPLOYER_MISSING" not in codes


def test_cross_check_accent_company_matches_plain_ascii():
    profile = parse_linkedin_pdf(samples.accent_pdf())
    resume = candidate([exp("Societe Generale", "Ingenieur logiciel", "2021-09", "Present"), exp("Zoe and Son Cafe", "Developpeur", "2018-05", "2021-08")])
    assert [item.code for item in cross_check(profile, resume, today=TODAY) if item.code == "LINKEDIN_EMPLOYER_MISSING"] == []


def test_cross_check_title_variants_tolerated():
    profile = parse_linkedin_pdf(samples.two_column_pdf())
    resume = candidate(
        [
            exp("Analytical Engines", "Sr. Software Developer", "2020-01", "2023-02"),
            exp("Analytical Engines", "Staff Engineer II", "2023-03", "Present"),
        ]
    )
    assert [item.code for item in cross_check(profile, resume, today=TODAY) if item.code == "LINKEDIN_TITLE_MISMATCH"] == []


def test_cross_check_real_title_conflict_is_flagged_with_both_sides():
    profile = parse_linkedin_pdf(samples.two_column_pdf())
    resume = candidate([exp("Babbage Labs", "Chief Financial Officer", "2016", "2017")])
    reasons = [item for item in cross_check(profile, resume, today=TODAY) if item.code == "LINKEDIN_TITLE_MISMATCH"]
    assert len(reasons) == 1
    assert "Chief Financial Officer" in reasons[0].evidence and "Intern" in reasons[0].evidence
    assert reasons[0].weight == 4


def test_cross_check_dates_within_three_months_pass_and_four_fail():
    profile = parse_linkedin_pdf(samples.two_column_pdf())
    inside = candidate([exp("Difference Works", "Senior Principal Machine Learning Platform Engineer", "2017-09", "2019-09")])
    outside = candidate([exp("Difference Works", "Senior Principal Machine Learning Platform Engineer", "2017-10", "2019-12")])
    assert [item for item in cross_check(profile, inside, today=TODAY) if item.code == "LINKEDIN_DATE_MISMATCH"] == []
    flagged = [item for item in cross_check(profile, outside, today=TODAY) if item.code == "LINKEDIN_DATE_MISMATCH"]
    assert len(flagged) == 1
    assert "2017-10" in flagged[0].evidence and "June 2017 - December 2019" in flagged[0].evidence
    assert "4 months" in flagged[0].detail


def test_cross_check_year_only_dates_and_present():
    profile = parse_linkedin_pdf(samples.two_column_pdf())
    ok = candidate([exp("Babbage Labs", "Intern", "2016-08", "2017-02"), exp("Analytical Engines", "Staff Engineer", "2023-03", "Present")])
    assert [item.code for item in cross_check(profile, ok, today=TODAY) if item.code != "LINKEDIN_EMPLOYER_MISSING"] == []
    ended = candidate([exp("Analytical Engines", "Staff Engineer", "2023-03", "2024-01")])
    flagged = [item for item in cross_check(profile, ended, today=TODAY) if item.code == "LINKEDIN_DATE_MISMATCH"]
    assert len(flagged) == 1 and "Present" in flagged[0].evidence


def test_cross_check_multi_role_resume_may_list_any_role_or_the_span():
    profile = parse_linkedin_pdf(samples.two_column_pdf())
    second_only = candidate([exp("Analytical Engines", "Senior Software Engineer", "2020-01", "2023-02")])
    first_only = candidate([exp("Analytical Engines", "Staff Engineer", "2023-03", "Present")])
    span = candidate([exp("Analytical Engines", "Staff Engineer", "2020-01", "Present")])
    for resume in (second_only, first_only, span):
        assert cross_check(profile, resume, today=TODAY) == [] or all(
            item.weight <= 1 for item in cross_check(profile, resume, today=TODAY)
        )


def test_profile_employer_missing_from_resume_is_low_weight_only():
    profile = parse_linkedin_pdf(samples.two_column_pdf())
    resume = candidate([exp("Analytical Engines", "Staff Engineer", "2023-03", "Present")])
    reasons = [item for item in cross_check(profile, resume, today=TODAY) if item.code == "LINKEDIN_EMPLOYER_MISSING"]
    assert {item.weight for item in reasons} == {1}
    assert any("Babbage Labs" in item.evidence and "Analytical Engines" in item.evidence for item in reasons)


def test_resume_employer_missing_from_profile_keeps_full_weight_and_quotes_both_sides():
    profile = parse_linkedin_pdf(samples.two_column_pdf())
    resume = candidate([exp("Phantom Labs", "Engineer", "2015-01", "2016-05")])
    reasons = [item for item in cross_check(profile, resume, today=TODAY) if "Phantom Labs" in item.detail]
    assert len(reasons) == 1 and reasons[0].weight == 6
    assert "Resume experience 1" in reasons[0].evidence and "Analytical Engines" in reasons[0].evidence


def test_every_reason_has_located_evidence_on_both_sides():
    profile = parse_linkedin_pdf(samples.two_column_long_pdf())
    resume = candidate(
        [
            exp("Analytical Engines", "Janitor", "2015-01", "Present"),
            exp("Babbage Labs", "Intern", "2016", "2017"),
            exp("Phantom Labs", "Engineer", "2015-01", "2016-05"),
        ]
    )
    reasons = cross_check(profile, resume, today=TODAY)
    assert len(reasons) >= 4
    for item in reasons:
        assert item.evidence
        assert "Resume experience" in item.evidence or "The resume lists" in item.evidence
        assert "LinkedIn export" in item.evidence
        if item.code != "LINKEDIN_EMPLOYER_MISSING" or "Phantom" not in item.detail:
            assert re.search(r"page \d+ line \d+", item.evidence)
