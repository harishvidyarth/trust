from __future__ import annotations

from pathlib import Path

from firewall.resume.parse import parse_candidate
from firewall.resume.service import analyze_resume, naive_ats_rank


SAMPLES = Path(__file__).parents[1] / "firewall" / "resume" / "samples"


def test_deterministic_parser_extracts_candidate_sections() -> None:
    text = """Maya Raman
maya.raman@example.com | +91 98765 43210
Skills
Python, FastAPI | PostgreSQL; Docker
Experience
Senior Backend Engineer | Acme Systems | Jan 2022 - Present
Built resilient APIs for payment processing.
Software Engineer | Beta Labs | 2019-06 - Dec 2021
Projects
Fraud Lens
Built an explainable transaction risk dashboard.
Education
B.E. Computer Science, 2019
"""

    candidate = parse_candidate(text, use_llm=False)

    assert candidate.name == "Maya Raman"
    assert candidate.email == "maya.raman@example.com"
    assert "98765" in candidate.phone
    assert candidate.skills[:4] == ["Python", "FastAPI", "PostgreSQL", "Docker"]
    assert candidate.experience[0].start == "2022-01"
    assert candidate.experience[0].end == "Present"
    assert candidate.experience[1].start == "2019-06"
    assert candidate.experience[1].end == "2021-12"
    assert candidate.projects[0].name == "Fraud Lens"


def test_empty_text_returns_valid_empty_candidate() -> None:
    candidate = parse_candidate("", use_llm=False)

    assert candidate.name == ""
    assert candidate.experience == []


def test_honest_ai_polished_samples_have_no_resume_reasons(job) -> None:
    for filename in ("honest_ai_polished.pdf", "honest_ai_polished.docx"):
        analysis = analyze_resume((SAMPLES / filename).read_bytes(), filename, job)
        assert analysis.parsed_ok
        assert analysis.reasons == [], (filename, analysis.reasons)
        assert analysis.candidate.email == "maya.raman@example.com"
        assert "Python" in analysis.candidate.skills


def test_generated_pdf_samples_parse_dates_and_projects(job) -> None:
    fabricated = analyze_resume(
        (SAMPLES / "fabricated_timeline.pdf").read_bytes(), "fabricated_timeline.pdf", job
    )
    near_copy = analyze_resume((SAMPLES / "near_copy.pdf").read_bytes(), "near_copy.pdf", job)

    assert [(item.start, item.end) for item in fabricated.candidate.experience] == [
        ("2022-01", "2025-12"),
        ("2022-06", "Present"),
        ("2026-01", "2024-12"),
    ]
    assert fabricated.candidate.projects[0].name == "Audit Stream"
    assert near_copy.candidate.projects[0].name == "Queue Watch"


def test_stuffed_resume_beats_honest_naively_but_firewall_flags_it(job) -> None:
    honest_data = (SAMPLES / "honest_ai_polished.pdf").read_bytes()
    stuffed_data = (SAMPLES / "stuffed_hidden_text.pdf").read_bytes()
    honest = analyze_resume(honest_data, "honest_ai_polished.pdf", job)
    stuffed = analyze_resume(stuffed_data, "stuffed_hidden_text.pdf", job)

    assert naive_ats_rank(stuffed.ats_view_text, job) > naive_ats_rank(honest.ats_view_text, job)
    assert {"RESUME_HIDDEN_TEXT", "RESUME_PROMPT_INJECTION", "RESUME_PARSE_DIVERGENCE"} <= {
        reason["code"] for reason in stuffed.reasons
    }


def test_visible_injection_docx_is_flagged(job) -> None:
    data = (SAMPLES / "visible_injection.docx").read_bytes()
    analysis = analyze_resume(data, "visible_injection.docx", job)

    assert "RESUME_PROMPT_INJECTION" in {reason["code"] for reason in analysis.reasons}


def test_service_handles_corrupt_input(job) -> None:
    analysis = analyze_resume(b"not a pdf", "broken.pdf", job)

    assert not analysis.parsed_ok
    assert analysis.candidate.name == ""
