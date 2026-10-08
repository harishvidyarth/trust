from __future__ import annotations

from io import BytesIO

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from firewall.intake_routes import build_intake_router


def make_pdf(lines: list[str], pages: int = 1) -> bytes:
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=letter)
    for _ in range(pages):
        pdf.setFont("Helvetica", 11)
        y = 740
        for line in lines:
            pdf.drawString(72, y, line)
            y -= 18
        pdf.showPage()
    pdf.save()
    return output.getvalue()


LINKEDIN_LINES = [
    "Maya Raman",
    "Backend Engineer at Example Labs",
    "github.com/mayaraman",
    "Certificate: CERT-2024-0042",
    "Paper: Sample Study doi: 10.1234/sample.2024.01",
]


@pytest.fixture
def linkedin_pdf() -> bytes:
    return make_pdf(LINKEDIN_LINES)


@pytest.fixture
def resume_store() -> dict[str, str]:
    return {"app-1": "Maya Raman\nhttps://github.com/mayaraman\nCERT-2024-0042\ndoi: 10.1234/sample.2024.01"}


@pytest.fixture
def client(resume_store: dict[str, str]) -> TestClient:
    app = FastAPI()
    app.include_router(build_intake_router(resume_text_for=lambda key: resume_store.get(key, "")))
    return TestClient(app)
