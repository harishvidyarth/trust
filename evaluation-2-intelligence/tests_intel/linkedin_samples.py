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


from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth

PAGE_W, PAGE_H = A4


def wrap_text(text: str, font: str, size: float, width: float) -> list[str]:
    pieces: list[str] = []
    for piece in simpleSplit(text, font, size, width):
        while stringWidth(piece, font, size) > width:
            cut = len(piece)
            while cut > 1 and stringWidth(piece[:cut], font, size) > width:
                cut -= 1
            pieces.append(piece[:cut])
            piece = piece[cut:]
        pieces.append(piece)
    return pieces

SIDE_X = 30.0
SIDE_W = 150.0
MAIN_X = 215.0
MAIN_W = 330.0
TOP = 790.0
BOTTOM = 70.0

STYLES = {
    "name": ("Helvetica-Bold", 20, 26),
    "headline": ("Helvetica", 11, 15),
    "location": ("Helvetica", 10, 14),
    "heading": ("Helvetica-Bold", 13, 22),
    "company": ("Helvetica-Bold", 11.5, 16),
    "title": ("Helvetica", 10.5, 14),
    "duration": ("Helvetica", 9.5, 13),
    "range": ("Helvetica", 9.5, 13),
    "loc": ("Helvetica", 9.5, 13),
    "text": ("Helvetica", 10, 13),
    "school": ("Helvetica-Bold", 11.5, 16),
    "degree": ("Helvetica", 10, 14),
}

FLAT_STYLES = {key: ("Helvetica", 10, value[2]) for key, value in STYLES.items()}


def make_two_column_pdf(
    sidebar: list[tuple[str, list[str]]],
    main: list[tuple[str, str]],
    flat: bool = False,
    footers: bool = True,
    gap_after_heading: float = 4.0,
) -> bytes:
    styles = FLAT_STYLES if flat else STYLES
    drawn: list[list[tuple[float, float, str, str, float]]] = [[]]
    y = TOP
    for heading, items in sidebar:
        y -= 6
        font, size, lead = styles["heading"]
        drawn[0].append((SIDE_X, y, heading, font, size))
        y -= lead
        font, size, lead = styles["text"]
        for item in items:
            for piece in wrap_text(item, font, size, SIDE_W):
                drawn[0].append((SIDE_X, y, piece, font, size))
                y -= lead
            y -= 3
    page = 0
    y = TOP
    for kind, text in main:
        font, size, lead = styles[kind]
        pieces = wrap_text(text, font, size, MAIN_W)
        if kind == "heading":
            y -= 6
        for piece in pieces:
            if y < BOTTOM:
                page += 1
                drawn.append([])
                y = TOP
            drawn[page].append((MAIN_X, y, piece, font, size))
            y -= lead
        if kind == "heading":
            y -= gap_after_heading
        if kind in {"loc", "degree"}:
            y -= 5
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    total = len(drawn)
    for number, items in enumerate(drawn, start=1):
        for x, ypos, text, font, size in items:
            pdf.setFont(font, size)
            pdf.drawString(x, ypos, text)
        if footers:
            pdf.setFont("Helvetica", 8)
            pdf.drawCentredString(PAGE_W / 2, 30, f"Page {number} of {total}")
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


LONG_DESC = (
    "Led a small team that designed and shipped scheduling services used by many internal groups across several "
    "regions while improving reliability and reducing cost through careful capacity planning and review."
)

TWO_COLUMN_SIDEBAR = [
    ("Contact", ["www.linkedin.com/in/ada-synthetic-engineer-example (LinkedIn)", "ada@example.com (Home)"]),
    ("Top Skills", ["Python", "Distributed Systems", "Kubernetes and Container Orchestration Platforms"]),
    ("Languages", ["English (Native or Bilingual)", "Hindi (Professional Working)"]),
    ("Certifications", ["Certified Kubernetes Application Developer Associate Program", "Example Cloud Practitioner"]),
]

TWO_COLUMN_MAIN = [
    ("name", "Ada Synthetic"),
    ("headline", "Staff Engineer at Analytical Engines and Technical Lead for the Scheduling Platform Group"),
    ("location", "London, United Kingdom"),
    ("heading", "Summary"),
    ("text", "Builds scheduling systems. " + LONG_DESC),
    ("heading", "Experience"),
    ("company", "Analytical Engines"),
    ("duration", "5 years 10 months"),
    ("title", "Staff Engineer"),
    ("range", "March 2023 - Present (2 years 10 months)"),
    ("loc", "London, United Kingdom"),
    ("text", LONG_DESC),
    ("title", "Senior Software Engineer"),
    ("range", "January 2020 - February 2023 (3 years 2 months)"),
    ("loc", "London, United Kingdom"),
    ("text", LONG_DESC),
    ("company", "Difference Works Private Limited"),
    ("title", "Senior Principal Machine Learning Platform Engineer for Distributed Training Infrastructure"),
    ("range", "June 2017 - December 2019 (2 years 7 months)"),
    ("loc", "Cambridge"),
    ("text", LONG_DESC + " " + LONG_DESC),
    ("company", "Babbage Labs"),
    ("title", "Intern"),
    ("range", "2016 - 2017 (1 year)"),
    ("text", LONG_DESC),
    ("heading", "Education"),
    ("school", "University of Example"),
    ("degree", "Bachelor of Science - BS, Mathematics · (2013 - 2017)"),
    ("school", "Example Institute of Technology"),
    ("degree", "Master of Science - MS, Computer Science · (Aug 2020 - May 2022)"),
]

ACCENT_SIDEBAR = [
    ("Contact", ["www.linkedin.com/in/jose-alvarez (LinkedIn)"]),
    ("Top Skills", ["Aprendizaje automático", "Investigación", "Gestión de proyectos"]),
    ("Languages", ["Español (Native or Bilingual)", "Français (Limited Working)"]),
]

ACCENT_MAIN = [
    ("name", "José Álvarez-Müller"),
    ("headline", "Ingénieur logiciel chez Société Générale"),
    ("location", "Zürich, Switzerland"),
    ("heading", "Summary"),
    ("text", "Développeur passionné. " + LONG_DESC),
    ("heading", "Experience"),
    ("company", "Société Générale"),
    ("title", "Ingénieur logiciel"),
    ("range", "Sept 2021 - Present (4 years)"),
    ("loc", "Zürich"),
    ("company", "Zoë & Søn Café GmbH"),
    ("title", "Développeur"),
    ("range", "May 2018 - August 2021 (3 years 4 months)"),
    ("loc", "München"),
    ("heading", "Education"),
    ("school", "Universität München"),
    ("degree", "Diplom, Informatik · (2013 - 2018)"),
]

NO_EXPERIENCE_SIDEBAR = [
    ("Contact", ["www.linkedin.com/in/new-grad-example (LinkedIn)"]),
    ("Top Skills", ["Data Analysis", "Statistics", "SQL"]),
]

NO_EXPERIENCE_MAIN = [
    ("name", "Nia Newgrad"),
    ("headline", "Aspiring Data Analyst"),
    ("location", "Pune, India"),
    ("heading", "Summary"),
    ("text", "Recent graduate looking for a first role. " + LONG_DESC),
]

ONLY_EDUCATION_SIDEBAR = [
    ("Contact", ["www.linkedin.com/in/only-edu-example (LinkedIn)"]),
    ("Top Skills", ["Research", "Writing", "Teaching"]),
]

ONLY_EDUCATION_MAIN = [
    ("name", "Edu Only"),
    ("headline", "Graduate Student at Example University"),
    ("location", "Berlin, Germany"),
    ("heading", "Education"),
    ("school", "Example University"),
    ("degree", "Master of Science - MS, Physics · (2022 - 2024)"),
    ("school", "Sample College"),
    ("degree", "Bachelor of Science - BS, Physics · (2019 - 2022)"),
]

GERMAN_SIDEBAR = [
    ("Kontakt", ["www.linkedin.com/in/hanna-beispiel (LinkedIn)"]),
    ("Top-Kenntnisse", ["Projektleitung", "Python", "Statistik"]),
    ("Sprachen", ["Deutsch (Muttersprache)"]),
]

GERMAN_MAIN = [
    ("name", "Hanna Größ"),
    ("headline", "Projektleiterin bei Beispiel AG"),
    ("location", "Köln, Deutschland"),
    ("heading", "Zusammenfassung"),
    ("text", "Leitet Projekte. " + LONG_DESC),
    ("heading", "Berufserfahrung"),
    ("company", "Beispiel AG"),
    ("title", "Projektleiterin"),
    ("range", "Januar 2020 - Heute (5 Jahre 10 Monate)"),
    ("loc", "Köln"),
    ("heading", "Ausbildung"),
    ("school", "Universität zu Köln"),
    ("degree", "Master, Statistik · (2015 - 2019)"),
]


def two_column_pdf() -> bytes:
    return make_two_column_pdf(TWO_COLUMN_SIDEBAR, TWO_COLUMN_MAIN)


def two_column_long_pdf() -> bytes:
    main = list(TWO_COLUMN_MAIN)
    padded: list[tuple[str, str]] = []
    for kind, text in main:
        padded.append((kind, text))
        if kind == "loc":
            padded.extend([("text", LONG_DESC)] * 6)
    return make_two_column_pdf(TWO_COLUMN_SIDEBAR, padded)


def flat_font_pdf() -> bytes:
    return make_two_column_pdf(TWO_COLUMN_SIDEBAR, TWO_COLUMN_MAIN, flat=True)


def accent_pdf() -> bytes:
    return make_two_column_pdf(ACCENT_SIDEBAR, ACCENT_MAIN)


def no_experience_pdf() -> bytes:
    return make_two_column_pdf(NO_EXPERIENCE_SIDEBAR, NO_EXPERIENCE_MAIN)


def only_education_pdf() -> bytes:
    return make_two_column_pdf(ONLY_EDUCATION_SIDEBAR, ONLY_EDUCATION_MAIN)


def german_pdf() -> bytes:
    return make_two_column_pdf(GERMAN_SIDEBAR, GERMAN_MAIN)
