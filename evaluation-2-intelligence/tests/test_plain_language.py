from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from firewall import reasoning
from firewall.api import CONFIG, MOCK_ATS, STORE, app
from firewall.config import DEFAULT_WEIGHTS as ENGINE_WEIGHTS
from firewall.engine import _summary, score_and_route
from firewall.enrichment.roles import ROLE_POSITIVE_BONUSES, ROLE_WEIGHTS
from firewall.enrichment.runner import DEFAULT_WEIGHTS as ENRICHMENT_WEIGHTS
from firewall.enrichment.runner import POSITIVE_BONUSES
from firewall.intel.linkedin import LINKEDIN_WEIGHTS
from firewall.intel.passive import INTEL_POSITIVE_BONUSES
from firewall.llm.ollama_client import OllamaClient
from firewall.models import Reason, Route
from firewall.reasoning import (
    CANDIDATE_FIX_TEMPLATES,
    EXPLANATIONS,
    RECRUITER_TEMPLATES,
    annotate_reasons,
    deterministic_reasoning,
    explain_reason,
    plain_text_ok,
)
from firewall.resume.integrity import DEFAULT_WEIGHTS as INTEGRITY_WEIGHTS
from firewall.signals.identity_links import IDENTITY_LINK_WEIGHTS


FORBIDDEN = set("-‐‑‒–—―−－()[]{}:;（）［］｛｝：；")
AUXILIARY_CODES = {
    "CLUSTER_SAME_FACTBASE",
    "DUP_RESUME_EXACT",
    "FED_ADVISORY",
    "FED_CONFIRMED",
    "PROBING_SUSPECTED",
    "SESSION_ELAPSED_MISMATCH",
    "TOKEN_INVALID",
    "UNATTESTED_PATH",
}
ALL_CODES = sorted(
    set(ENGINE_WEIGHTS)
    | set(INTEGRITY_WEIGHTS)
    | set(ENRICHMENT_WEIGHTS)
    | set(POSITIVE_BONUSES)
    | set(ROLE_WEIGHTS)
    | set(ROLE_POSITIVE_BONUSES)
    | set(LINKEDIN_WEIGHTS)
    | set(INTEL_POSITIVE_BONUSES)
    | set(IDENTITY_LINK_WEIGHTS)
    | AUXILIARY_CODES
)
NOT_REASON_CODES = {
    "SLACK_REVIEW_HOOK",
    "SLACK_VERIFY_HOOK",
    "ADDITIONAL_VERIFICATION",
    "ENV_URL",
    "GITHUB_TOKEN",
    "INTEL_POSITIVE_BONUSES",
    "KEY_PREFIX",
    "MANUAL_REVIEW",
    "OLLAMA_HOST",
    "PASS_TO_ATS",
    "ROLE_POSITIVE_BONUSES",
    "ROLE_WEIGHTS",
    "SLACK_REVIEW_HOOK",
    "SLACK_VERIFY_HOOK",
    "SEMANTIC_SCHOLAR_API_KEY",
}
CODE_LITERAL = re.compile(r"[\"']([A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+)[\"']")
SAMPLES = Path("firewall/resume/samples")
JOB = '{"must_have_skills":["python","kubernetes"],"nice_to_have":[],"min_years":3}'
SENTENCE_END = re.compile(r"[.!?]")
client = TestClient(app)


def assert_plain(text: str) -> None:
    assert text.strip()
    assert not FORBIDDEN & set(text)
    assert plain_text_ok(text)


def reason_for(code: str, severity: str = "medium") -> Reason:
    return Reason(code=code, severity=severity, detail="Technical evidence 12.5.", weight=7)


def test_discovered_code_list_is_not_empty() -> None:
    assert len(ALL_CODES) > 80


def test_every_code_literal_in_source_is_explained() -> None:
    found: set[str] = set()
    for path in (item for item in Path("firewall").rglob("*.py") if item.name != "claim_check.py"):
        found.update(CODE_LITERAL.findall(path.read_text(encoding="utf-8")))
    candidates = {item for item in found if not item.startswith("FIREWALL_")} - NOT_REASON_CODES
    assert candidates - set(EXPLANATIONS) == set()


@pytest.mark.parametrize("code", ALL_CODES)
def test_every_code_has_plain_explanation(code: str) -> None:
    assert code in EXPLANATIONS
    text = explain_reason(reason_for(code))
    assert_plain(text)
    assert text == EXPLANATIONS[code]
    assert not re.search(r"\d", text)
    assert code not in text
    assert 1 <= len(SENTENCE_END.findall(text)) <= 2
    assert text.endswith(".")


@pytest.mark.parametrize("code", ALL_CODES)
def test_every_code_has_plain_recruiter_and_fix_text(code: str) -> None:
    assert_plain(RECRUITER_TEMPLATES[code])
    assert_plain(CANDIDATE_FIX_TEMPLATES[code])
    assert not re.search(r"\d", CANDIDATE_FIX_TEMPLATES[code])
    banned = {"density", "font", "limit", "ratio", "score", "threshold", "weight"}
    words = {word.casefold() for word in re.findall(r"[A-Za-z]+", CANDIDATE_FIX_TEMPLATES[code])}
    assert not banned & words


@pytest.mark.parametrize("severity", ["info", "low", "medium", "high", "critical", "unexpected"])
def test_unknown_code_falls_back_to_severity_sentence(severity: str) -> None:
    text = explain_reason(reason_for("NOT_A_REAL_CODE", severity))
    assert_plain(text)
    assert "NOT_A_REAL_CODE" not in text


def test_unknown_code_gets_plain_recruiter_and_fix_text() -> None:
    recruiter, fixes = deterministic_reasoning([reason_for("NOT_A_REAL_CODE")])
    assert_plain(recruiter)
    assert all(plain_text_ok(item) for item in fixes)
    assert "NOT_A_REAL_CODE" not in recruiter


def test_annotate_reasons_keeps_scoring_fields_and_separates_quoted_evidence() -> None:
    original = Reason(code="RESUME_HIDDEN_TEXT", severity="high", detail='Hidden span classified as harmless: "Python Kubernetes".', weight=0)
    annotated = annotate_reasons([original])[0]
    assert annotated.explanation == EXPLANATIONS["RESUME_HIDDEN_TEXT"]
    assert annotated.evidence == "Python Kubernetes"
    assert (annotated.code, annotated.severity, annotated.detail, annotated.weight) == (
        original.code,
        original.severity,
        original.detail,
        original.weight,
    )
    assert original.explanation == ""


@pytest.mark.parametrize("route", list(Route))
@pytest.mark.parametrize("count", [0, 2])
def test_summary_is_plain(route: Route, count: int) -> None:
    reasons = [reason_for("DUP_EMAIL"), reason_for("FAST_SUBMIT")][:count]
    text = _summary(39, route, reasons)
    assert_plain(text)
    assert "39 out of 100" in text
    assert "DUP_EMAIL" not in text


def test_llm_output_with_forbidden_characters_falls_back_to_template() -> None:
    reason = reason_for("QUAL_MISSING_MUST_HAVE", "low")
    for bad in (
        RECRUITER_TEMPLATES[reason.code].rstrip(".") + " (see below).",
        RECRUITER_TEMPLATES[reason.code].rstrip(".") + ": see below.",
        RECRUITER_TEMPLATES[reason.code].rstrip(".") + "; see below.",
        RECRUITER_TEMPLATES[reason.code].replace("skills", "must-have skills"),
    ):
        response = {
            "recruiter_sentences": [{"reason_code": reason.code, "text": bad}],
            "candidate_fixes": [{"reason_code": reason.code, "text": CANDIDATE_FIX_TEMPLATES[reason.code]}],
        }

        def transport(request, timeout, response=response):
            return json.dumps({"response": json.dumps(response)}).encode()

        actual = reasoning.generate_reasoning(
            [reason], Route.MANUAL_REVIEW, llm_client=OllamaClient(transport=transport), use_llm=True
        )
        assert actual == (*deterministic_reasoning([reason]), False)


def upload(path: Path, application_id: str) -> dict:
    with path.open("rb") as handle:
        response = client.post(
            "/v1/applications/upload",
            files={"file": (path.name, handle)},
            data={
                "job_json": JOB,
                "device_id": f"device-{application_id}",
                "application_id": application_id,
                "session_seconds": "140",
                "dry_run": "true",
            },
        )
    assert response.status_code == 200, response.text
    return response.json()


SAMPLE_FILES = sorted(item for item in SAMPLES.iterdir() if item.suffix in {".pdf", ".docx", ".txt"})


@pytest.mark.parametrize("path", SAMPLE_FILES, ids=lambda item: item.name)
def test_sample_resume_decisions_are_plain_and_scoring_is_unchanged(path: Path) -> None:
    STORE.clear()
    MOCK_ATS.clear()
    decision = upload(path, f"plain-{path.stem}")
    STORE.clear()
    MOCK_ATS.clear()
    assert_plain(decision["summary"])
    assert_plain(decision["recruiter_summary"])
    for fix in decision["candidate_fixes"]:
        assert_plain(fix)
    assert len(decision["candidate_fixes"]) == len(set(decision["candidate_fixes"]))
    for item in decision["reasons"]:
        assert_plain(item["explanation"])
        assert item["detail"]
    stripped = [
        Reason(code=item["code"], severity=item["severity"], detail=item["detail"], weight=item["weight"])
        for item in decision["reasons"]
    ]
    score, route = score_and_route(stripped, CONFIG)
    assert decision["score"] == score
    assert decision["route"] == route.value
    for item in decision["reasons"]:
        known = {**ENGINE_WEIGHTS, **INTEGRITY_WEIGHTS}
        if item["code"] in known:
            assert item["weight"] <= known[item["code"]]
    repeat = upload(path, f"plain-repeat-{path.stem}")
    STORE.clear()
    MOCK_ATS.clear()
    assert [(i["code"], i["weight"], i["explanation"]) for i in repeat["reasons"]] == [
        (i["code"], i["weight"], i["explanation"]) for i in decision["reasons"]
    ]
    assert (repeat["score"], repeat["route"]) == (decision["score"], decision["route"])


def test_samples_exercise_many_reason_codes() -> None:
    seen: set[str] = set()
    for path in SAMPLE_FILES:
        STORE.clear()
        MOCK_ATS.clear()
        seen.update(item["code"] for item in upload(path, f"coverage-{path.stem}")["reasons"])
    STORE.clear()
    MOCK_ATS.clear()
    assert len(seen) >= 8
