from __future__ import annotations

from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


ADA_LINES = [
    "Contact",
    "www.linkedin.com/in/ada-synthetic (LinkedIn)",
    "Top Skills",
    "Python",
    "Distributed Systems",
    "Kubernetes",
    "Ada Synthetic",
    "Staff Engineer at Analytical Engines",
    "London, United Kingdom",
    "Summary",
    "Builds scheduling systems.",
    "Experience",
    "Analytical Engines",
    "Staff Engineer",
    "January 2020 - Present (5 years)",
    "London",
    "Difference Works",
    "Software Engineer",
    "June 2017 - December 2019 (2 years 7 months)",
    "Education",
    "University of Example",
    "Bachelor of Science - BS, Mathematics · (2013 - 2017)",
]

BOB_LINES = [
    "Contact",
    "www.linkedin.com/in/bob-synthetic (LinkedIn)",
    "Top Skills",
    "Sales Strategy",
    "CRM",
    "Negotiation",
    "Bob Synthetic",
    "Regional Sales Manager at Northwind Traders",
    "Chennai, India",
    "Experience",
    "Northwind Traders",
    "Regional Sales Manager",
    "March 2021 - Present (4 years)",
    "Contoso Retail",
    "Sales Executive",
    "April 2018 - February 2021 (2 years 11 months)",
    "Education",
    "Example Institute of Management",
    "Master of Business Administration - MBA · (2016 - 2018)",
]


def make_pdf(lines: list[str]) -> bytes:
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    y = 800
    for line in lines:
        pdf.drawString(50, y, line)
        y -= 18
        if y < 50:
            pdf.showPage()
            y = 800
    pdf.save()
    return buffer.getvalue()


def ada_pdf() -> bytes:
    return make_pdf(ADA_LINES)


def bob_pdf() -> bytes:
    return make_pdf(BOB_LINES)
