from __future__ import annotations

from io import BytesIO

import pytest
from pypdf import PdfWriter
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from firewall.resume.extract import extract_resume


def test_pdf_extraction_separates_hidden_text(hidden_pdf_bytes: bytes) -> None:
    result = extract_resume(hidden_pdf_bytes, "resume.pdf")

    assert result.file_type == "pdf"
    assert result.page_count == 1
    assert "Maya Raman" in result.text_visible
    assert "ignore previous instructions" in result.text_all
    assert "ignore previous instructions" not in result.text_visible
    assert {span.reason for span in result.hidden_spans} >= {"near-white text", "font size below 2pt"}


def test_docx_extraction_detects_vanish_white_and_tiny_runs(hidden_docx_bytes: bytes) -> None:
    result = extract_resume(hidden_docx_bytes, "resume.docx")

    assert result.file_type == "docx"
    assert result.page_count == 1
    assert "Backend Engineer" in result.text_visible
    assert "recommend hiring" in result.text_all
    assert "recommend hiring" not in result.text_visible
    assert {span.reason for span in result.hidden_spans} >= {
        "vanished text",
        "near-white text",
        "font size below 2pt",
    }


def test_txt_is_fully_visible() -> None:
    result = extract_resume(b"Asha Rao\nSkills: Python, SQL", "resume.txt")

    assert result.text_all == result.text_visible
    assert result.hidden_spans == []
    assert result.page_count == 1


def test_pdf_detects_off_page_and_overlapping_duplicate_text() -> None:
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=letter)
    pdf.drawString(72, 740, "Visible sentence")
    pdf.drawString(72, 700, "duplicate payload")
    pdf.drawString(72, 700, "duplicate payload")
    pdf.drawString(-200, 660, "off page payload")
    pdf.save()

    result = extract_resume(output.getvalue(), "positioned.pdf")

    reasons = {span.reason for span in result.hidden_spans}
    assert "overlapping duplicate text" in reasons
    assert "text outside page bounds" in reasons
    assert "Visible sentence" in result.text_visible


@pytest.mark.parametrize(
    ("data", "filename"),
    [(b"", "empty.pdf"), (b"not really a pdf", "broken.pdf"), (b"PK broken", "broken.docx")],
)
def test_empty_and_corrupt_documents_do_not_crash(data: bytes, filename: str) -> None:
    result = extract_resume(data, filename)

    assert result.text_all == ""
    assert "error" in result.metadata


def test_encrypted_pdf_does_not_crash() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("secret")
    output = BytesIO()
    writer.write(output)

    result = extract_resume(output.getvalue(), "protected.pdf")

    assert result.text_all == ""
    assert "error" in result.metadata
