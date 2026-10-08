from __future__ import annotations

import json

import pytest

from firewall.config import DEFAULT_WEIGHTS as ENGINE_REASON_CODES
from firewall.enrichment.runner import DEFAULT_WEIGHTS as ENRICHMENT_REASON_CODES
from firewall.enrichment.runner import POSITIVE_BONUSES
from firewall.llm.ollama_client import OllamaClient
from firewall.models import Reason, Route
from firewall.reasoning import CANDIDATE_FIX_TEMPLATES, RECRUITER_TEMPLATES, deterministic_reasoning, generate_reasoning
from firewall.resume.integrity import DEFAULT_WEIGHTS as INTEGRITY_REASON_CODES


AUXILIARY_REASON_CODES = {
    "CLUSTER_SAME_FACTBASE",
    "DUP_RESUME_EXACT",
    "FED_ADVISORY",
    "FED_CONFIRMED",
    "PROBING_SUSPECTED",
    "SESSION_ELAPSED_MISMATCH",
    "TOKEN_INVALID",
    "UNATTESTED_PATH",
}
ALL_REASON_CODES = (
    set(ENGINE_REASON_CODES)
    | set(INTEGRITY_REASON_CODES)
    | set(ENRICHMENT_REASON_CODES)
    | set(POSITIVE_BONUSES)
    | AUXILIARY_REASON_CODES
)


@pytest.mark.parametrize("code", sorted(ALL_REASON_CODES))
def test_every_reason_code_has_plain_english_templates(code: str) -> None:
    assert code in RECRUITER_TEMPLATES
    assert code in CANDIDATE_FIX_TEMPLATES
    assert RECRUITER_TEMPLATES[code].strip().endswith(".")
    assert CANDIDATE_FIX_TEMPLATES[code].strip().endswith(".")


def test_deterministic_reasoning_uses_reason_codes_only() -> None:
    reasons = [
        Reason(code="RESUME_HIDDEN_TINY_FONT", severity="medium", detail="Hidden at a threshold.", weight=6),
        Reason(code="QUAL_MISSING_MUST_HAVE", severity="low", detail="Missing Python.", weight=12),
    ]

    recruiter, fixes = deterministic_reasoning(reasons)

    assert recruiter == " ".join(RECRUITER_TEMPLATES[item.code] for item in reasons)
    assert fixes == [CANDIDATE_FIX_TEMPLATES[item.code] for item in reasons]
    assert "threshold" not in " ".join(fixes).casefold()
    assert "6" not in " ".join(fixes)


def test_candidate_fixes_collapse_reason_families_without_duplicates() -> None:
    reasons = [
        Reason(code="RESUME_HIDDEN_TEXT", severity="high", detail="Hidden content.", weight=0),
        Reason(code="RESUME_HIDDEN_TINY_FONT", severity="medium", detail="Tiny content.", weight=6),
        Reason(code="RESUME_INJECTION_IGNORE_PREVIOUS", severity="high", detail="Instruction.", weight=15),
        Reason(code="RESUME_PROMPT_INJECTION", severity="high", detail="Instruction.", weight=0),
        Reason(code="DUP_EMAIL", severity="medium", detail="Duplicate.", weight=22),
        Reason(code="DUP_PHONE", severity="medium", detail="Duplicate.", weight=20),
        Reason(code="FAST_SUBMIT", severity="medium", detail="Fast.", weight=12),
        Reason(code="PASTE_BULK", severity="medium", detail="Pasted.", weight=18),
        Reason(code="QUAL_MISSING_MUST_HAVE", severity="low", detail="Missing.", weight=35),
        Reason(code="QUAL_UNDER_EXPERIENCE", severity="low", detail="Short.", weight=20),
        Reason(code="TIMELINE_INVALID", severity="medium", detail="Invalid.", weight=35),
        Reason(code="TIMELINE_OVERLAP", severity="medium", detail="Overlap.", weight=20),
        Reason(code="DOI_NOT_FOUND", severity="medium", detail="Missing.", weight=10),
        Reason(code="GITHUB_REPO_NOT_FOUND", severity="medium", detail="Missing.", weight=10),
    ]

    _, fixes = deterministic_reasoning(reasons)

    expected_codes = [
        "RESUME_HIDDEN_TEXT",
        "RESUME_INJECTION_IGNORE_PREVIOUS",
        "DUP_EMAIL",
        "FAST_SUBMIT",
        "QUAL_MISSING_MUST_HAVE",
        "TIMELINE_INVALID",
        "DOI_NOT_FOUND",
    ]
    assert fixes == [CANDIDATE_FIX_TEMPLATES[code] for code in expected_codes]
    assert len(fixes) == len(set(fixes))


def test_llm_candidate_fixes_require_one_per_reason_family() -> None:
    reasons = [
        Reason(code="RESUME_HIDDEN_TEXT", severity="high", detail="Hidden content.", weight=0),
        Reason(code="RESUME_HIDDEN_TINY_FONT", severity="medium", detail="Tiny content.", weight=6),
    ]
    response = {
        "recruiter_sentences": [
            {"reason_code": reason.code, "text": RECRUITER_TEMPLATES[reason.code]}
            for reason in reasons
        ],
        "candidate_fixes": [
            {"reason_code": reason.code, "text": CANDIDATE_FIX_TEMPLATES[reason.code]}
            for reason in reasons
        ],
    }

    def transport(request, timeout):
        return json.dumps({"response": json.dumps(response)}).encode()

    actual = generate_reasoning(
        reasons,
        Route.MANUAL_REVIEW,
        llm_client=OllamaClient(transport=transport),
        use_llm=True,
    )

    assert actual == (*deterministic_reasoning(reasons), False)


def test_reasoning_response_requires_every_present_reason_code() -> None:
    reasons = [
        Reason(code="QUAL_MISSING_MUST_HAVE", severity="low", detail="Missing Python.", weight=12),
        Reason(code="TIMELINE_INVALID", severity="medium", detail="A date is invalid.", weight=35),
    ]
    response = {
        "recruiter_sentences": [
            {"reason_code": reasons[0].code, "text": RECRUITER_TEMPLATES[reasons[0].code]}
        ],
        "candidate_fixes": [
            {"reason_code": reasons[0].code, "text": CANDIDATE_FIX_TEMPLATES[reasons[0].code]}
        ],
    }

    def transport(request, timeout):
        return json.dumps({"response": json.dumps(response)}).encode()

    actual = generate_reasoning(
        reasons,
        Route.MANUAL_REVIEW,
        llm_client=OllamaClient(transport=transport),
        use_llm=True,
    )

    assert actual == (*deterministic_reasoning(reasons), False)


def test_valid_llm_reasoning_is_accepted() -> None:
    reason = Reason(code="QUAL_MISSING_MUST_HAVE", severity="low", detail="Missing Python.", weight=12)
    response = {
        "recruiter_sentences": [
            {"reason_code": reason.code, "text": RECRUITER_TEMPLATES[reason.code]}
        ],
        "candidate_fixes": [
            {"reason_code": reason.code, "text": CANDIDATE_FIX_TEMPLATES[reason.code]}
        ],
    }

    def transport(request, timeout):
        return json.dumps({"response": json.dumps(response)}).encode()

    recruiter, fixes, llm_used = generate_reasoning(
        [reason],
        Route.ADDITIONAL_VERIFICATION,
        llm_client=OllamaClient(transport=transport),
        use_llm=True,
    )

    assert recruiter == response["recruiter_sentences"][0]["text"]
    assert fixes == [response["candidate_fixes"][0]["text"]]
    assert llm_used is True


def test_valid_llm_reasoning_may_repeat_the_decided_route() -> None:
    reason = Reason(code="QUAL_MISSING_MUST_HAVE", severity="low", detail="Missing Python.", weight=12)
    response = {
        "recruiter_sentences": [
            {"reason_code": reason.code, "text": "The route remains additional verification."}
        ],
        "candidate_fixes": [
            {"reason_code": reason.code, "text": CANDIDATE_FIX_TEMPLATES[reason.code]}
        ],
    }

    def transport(request, timeout):
        return json.dumps({"response": json.dumps(response)}).encode()

    recruiter, _, llm_used = generate_reasoning(
        [reason],
        Route.ADDITIONAL_VERIFICATION,
        llm_client=OllamaClient(transport=transport),
        use_llm=True,
    )

    assert recruiter == "The route remains additional verification."
    assert llm_used is True


@pytest.mark.parametrize(
    "invented",
    [
        "The candidate worked at Google.",
        "The candidate has 99 missing qualifications.",
        "The candidate should be rejected.",
        "The candidate should move to manual review.",
    ],
)
def test_llm_reasoning_rejects_invented_facts_and_route_changes(invented: str) -> None:
    reason = Reason(code="QUAL_MISSING_MUST_HAVE", severity="low", detail="Missing Python.", weight=12)
    response = {
        "recruiter_sentences": [{"reason_code": reason.code, "text": invented}],
        "candidate_fixes": [
            {"reason_code": reason.code, "text": CANDIDATE_FIX_TEMPLATES[reason.code]}
        ],
    }

    def transport(request, timeout):
        return json.dumps({"response": json.dumps(response)}).encode()

    actual = generate_reasoning(
        [reason],
        Route.ADDITIONAL_VERIFICATION,
        llm_client=OllamaClient(transport=transport),
        use_llm=True,
    )
    expected = deterministic_reasoning([reason])

    assert actual == (*expected, False)


def test_llm_reasoning_rejects_candidate_threshold_guidance() -> None:
    reason = Reason(
        code="RESUME_HIDDEN_TINY_FONT",
        severity="medium",
        detail="Text used font size 1.",
        weight=6,
    )
    response = {
        "recruiter_sentences": [
            {"reason_code": reason.code, "text": RECRUITER_TEMPLATES[reason.code]}
        ],
        "candidate_fixes": [
            {"reason_code": reason.code, "text": "Use font size 3 to avoid the threshold."}
        ],
    }

    def transport(request, timeout):
        return json.dumps({"response": json.dumps(response)}).encode()

    actual = generate_reasoning(
        [reason],
        Route.MANUAL_REVIEW,
        llm_client=OllamaClient(transport=transport),
        use_llm=True,
    )

    assert actual == (*deterministic_reasoning([reason]), False)


def test_deterministic_reasoning_output_has_no_forbidden_characters() -> None:
    reasons = [Reason(code=code, severity="medium", detail="Technical evidence.", weight=1) for code in sorted(ALL_REASON_CODES)]

    recruiter, fixes = deterministic_reasoning(reasons)

    forbidden = set("-()[]{}:;")
    assert not forbidden & set(recruiter)
    assert all(not forbidden & set(fix) for fix in fixes)
    assert all(not any(character.isdigit() for character in fix) for fix in fixes)
