from __future__ import annotations

import re

from firewall.claim_check import REASON_TEXT, analyze_application as A, evaluate_response as E

SE = "I have 4 years of Python experience. I have built FastAPI applications using PostgreSQL."
SALES = "5 years of B2B sales experience. Enterprise customer acquisition experience."
CA = "Chartered Accountant with 6 years of statutory audit and GST compliance experience."
UX = "3 years of UI/UX experience. Product design experience with Figma."
SUS = "Guaranteed expert. Instant certification. Guaranteed placement."
FORBIDDEN = re.compile(r"[-()\[\]{};:–—]")


def run(application, answer):
    analysis = A(application)
    return analysis, E(application, analysis["role"], analysis["claim"], answer)


def test_roles_and_distinct_questions():
    questions = {A(text)["role"]: A(text)["question"] for text in (SE, SALES, CA, UX)}
    assert set(questions) == {"Software Engineer", "Sales Manager", "Chartered Accountant", "UI/UX Designer"}
    assert len(set(questions.values())) == 4
    assert "FastAPI" in questions["Software Engineer"] and "GitHub" not in questions["Sales Manager"]
    assert {"Python", "FastAPI", "PostgreSQL", "Professional Experience"} <= set(A(SE)["claims"])


def test_good_engineer_answer_passes():
    good = (
        "I built an employee management API using FastAPI. I implemented CRUD endpoints and used PostgreSQL "
        "for persistence. I also added validation using Pydantic and separated the API routes from the database layer."
    )
    _, result = run(SE, good)
    assert result["decision"] == "PASS" and result["consistency"] == "HIGH", result


def test_vague_answer_does_not_pass():
    _, result = run(SE, "I built a small API with FastAPI for a todo app using a database.")
    assert result["decision"] == "VERIFY", result
    _, other = run(SE, "I know FastAPI, it is good.")
    assert other["decision"] == "REVIEW"


def test_contradiction_lowers_result():
    _, result = run(SE, "Honestly I have never used FastAPI, I only watched tutorials about APIs and databases.")
    assert result["decision"] == "REVIEW" and result["consistency"] == "LOW"
    assert any(item["code"] == "RESPONSE_INCONSISTENT" for item in result["reason_codes"])


def test_sales_good_vs_weak():
    good = (
        "I would first understand why they are considering the competitor, then rebuild the value case with ROI numbers "
        "for their stakeholders. Rather than cutting price I would negotiate contract length and renewal terms, "
        "involve our champion in procurement, and protect the relationship while closing the deal."
    )
    _, strong = run(SALES, good)
    _, weak = run(SALES, "I would give a big discount.")
    assert strong["decision"] == "PASS" and weak["decision"] != "PASS" and strong["trust_score"] > weak["trust_score"]


def test_suspicious_candidate():
    _, result = run(SUS, "I am simply the best and I guarantee results.")
    assert "SUSPICIOUS_CLAIM_LANGUAGE" in [item["code"] for item in result["reason_codes"]]
    assert result["decision"] == "REVIEW" and result["trust_score"] < 55


def test_ai_assistance_not_penalised():
    base = "I built an employee management API using FastAPI. I implemented CRUD endpoints and used PostgreSQL for persistence."
    polished = "As an AI language model I would say: " + base
    assert abs(run(SE, base)[1]["trust_score"] - run(SE, polished)[1]["trust_score"]) <= 10


def test_word_boundaries_stop_false_role_matches():
    text = "I build and guide teams. I enjoy building reliable software in Python and FastAPI."
    assert A(text)["role"] == "Software Engineer"
    assert A("Africa regional lead, builds quality guides")["role"] != "Chartered Accountant"


def test_keyword_dump_does_not_pass():
    _, result = run(SE, "api endpoint endpoints crud route pydantic validation database postgresql sqlalchemy orm migration schema")
    assert result["decision"] == "REVIEW"
    assert any(item["code"] == "KEYWORD_LIST_ANSWER" for item in result["reason_codes"])


def test_new_roles_detected():
    assert A("Embedded firmware engineer who designed a PCB schematic for a microcontroller board")["role"] == "Hardware Engineer"
    assert A("Operations manager improving vendor and inventory process")["role"] == "Operations Manager"


def test_user_facing_text_is_plain():
    _, result = run(SE, "I built a small API with FastAPI for a todo app using a database.")
    texts = [result["summary"], result["advice"], result["note"]]
    for reason in result["reasons"]:
        texts += [reason["title"], reason["explanation"]]
    for title, ok, bad in REASON_TEXT.values():
        texts += [title, ok, bad]
    for text in texts:
        assert not FORBIDDEN.search(text), text
