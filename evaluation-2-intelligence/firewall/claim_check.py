from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

BANK: dict[str, list[tuple[str, list[str], str, list[str]]]] = {
    "Software Engineer": [
        (
            "FastAPI",
            ["fastapi"],
            "You stated that you have experience building FastAPI applications.\n\nDescribe one FastAPI project you built, including the problem it solved and how you handled data persistence.",
            "api endpoint endpoints crud route routes pydantic validation database postgresql sqlalchemy orm migration schema async auth deployed".split(),
        ),
        (
            "PostgreSQL",
            ["postgres", "postgresql"],
            "You stated that you have worked with PostgreSQL.\n\nDescribe a schema you designed and one query or performance problem you solved.",
            "schema index indexes query join migration table transaction normalization explain performance foreign constraint".split(),
        ),
        (
            "Python",
            ["python"],
            "You stated that you have professional Python experience.\n\nDescribe a Python system you built, the main design decision you made, and how you tested it.",
            "module class function test pytest library package async design refactor typing script service".split(),
        ),
    ],
    "Sales Manager": [
        (
            "B2B / Enterprise Sales",
            ["b2b", "enterprise", "sales", "acquisition", "quota"],
            "A major enterprise client is considering a competitor offering a lower price.\n\nHow would you handle the negotiation while protecting the relationship and closing the deal?",
            "value roi stakeholder decision-maker discount negotiation contract renewal pipeline relationship procurement pricing champion objection".split(),
        ),
    ],
    "Chartered Accountant": [
        (
            "Audit & Compliance",
            ["audit", "statutory", "compliance", "ca", "chartered"],
            "You stated that you have audit experience.\n\nDuring a statutory audit you find a material variance in receivables that management cannot explain. Walk us through what you do next.",
            "sample sampling confirmation ledger reconciliation materiality evidence working-papers management ageing provision standards ind documentation".split(),
        ),
        (
            "Taxation / GST",
            ["gst", "tax", "taxation", "itr"],
            "You stated that you have taxation experience.\n\nDescribe a GST or income-tax issue you resolved, what the exposure was and how you documented the position.",
            "gst input credit itc notice return reconciliation 2b assessment liability filing section compliance".split(),
        ),
    ],
    "UI/UX Designer": [
        (
            "Product Design",
            ["ux", "ui/ux", "ui", "product design", "redesign", "figma", "design"],
            "Describe a product you redesigned.\n\nWhat user problem did you identify, what design decision did you make, and how did you validate the result?",
            "user research interview usability testing prototype figma wireframe flow persona metric conversion iteration accessibility feedback".split(),
        ),
    ],
}
BANK["Hardware Engineer"] = [
    (
        "Circuit and Firmware Design",
        ["pcb", "firmware", "embedded", "circuit", "schematic", "microcontroller", "hardware"],
        "You stated that you have hardware or embedded experience.\n\nDescribe a board or firmware project you built, one fault you found and how you tested the fix.",
        "pcb schematic layout firmware microcontroller oscilloscope debug signal power voltage prototype bring-up test fixture sensor interface".split(),
    ),
]
BANK["Operations Manager"] = [
    (
        "Process and Operations",
        ["operations", "logistics", "supply chain", "process improvement", "vendor", "inventory"],
        "You stated that you have operations experience.\n\nDescribe a process you improved, how you measured it before and after, and who you worked with.",
        "process baseline metric turnaround cost vendor inventory forecast sla workflow stakeholder audit improvement throughput".split(),
    ),
]
ROLE_WORDS = {
    "Software Engineer": "python fastapi django postgres api backend developer software code github".split(),
    "Sales Manager": "sales b2b quota pipeline enterprise customer acquisition revenue account".split(),
    "Chartered Accountant": "audit gst tax chartered accountant ca statutory ledger ifrs compliance".split(),
    "UI/UX Designer": "ux ui design designer figma wireframe prototype product design".split(),
    "Hardware Engineer": "pcb firmware embedded circuit schematic microcontroller hardware".split(),
    "Operations Manager": "operations logistics supply chain vendor inventory process".split(),
}
SUSPICIOUS = [
    "guaranteed",
    "instant certification",
    "guaranteed placement",
    "100% success",
    "no experience needed",
    "world's best",
    "overnight expert",
    "certified in 1 day",
    "fake",
    "unlimited",
]
GENERIC_EVIDENCE = "project built worked client team result measured responsible delivered role outcome learned customer".split()
CONTRA = re.compile(
    r"\b(never (used|worked|built|done)|no experience|haven'?t (used|worked|built)|have not (used|worked|built)|"
    r"only (read|watched|studied)|just (started|learning)|first time|fresher|only tutorials?|did not (build|work))\b",
    re.I,
)
VERBS = re.compile(
    r"\b(built|implemented|designed|negotiated|reduced|increased|migrated|audited|tested|validated|deployed|"
    r"reconciled|redesigned|interviewed|closed|resolved|documented|separated|integrated|led)\b",
    re.I,
)
MAX_RESPONSE = 4000

REASON_TEXT = {
    "CLAIM_RELEVANT": ("The claim matches the role", "We found a claim in your application that we could ask about.", "We could not match a claim to ask about."),
    "RESPONSE_RELEVANT": ("The answer fits the question", "Your answer talks about the thing we asked about.", "Your answer drifts away from what we asked."),
    "ROLE_SPECIFIC_EVIDENCE": ("The answer shows real work", "You described the kind of work that this claim involves.", "Add the real steps you took and the tools you used."),
    "SPECIFICITY_PRESENT": ("The answer is specific", "You gave concrete details such as numbers, names or actions.", "Add a few concrete details such as a number, a name or what you did yourself."),
    "CLAIM_CONSISTENT": ("The answer agrees with the claim", "Nothing in your answer disagrees with your application.", "Parts of your answer disagree with what your application says."),
    "CLAIM_EVIDENCE_GAP": ("Not enough detail yet", "", "Describe the problem, what you did and the result in your own words."),
    "RESPONSE_INCONSISTENT": ("The answer disagrees with the claim", "", "You said you had not done this work. Please explain how the claim in your application is true."),
    "KEYWORD_LIST_ANSWER": ("The answer reads like a list of words", "", "Write full sentences that explain what you did instead of listing terms."),
    "SUSPICIOUS_CLAIM_LANGUAGE": ("Strong promises without detail", "", "Strong promises are hard to check. Describe one real project instead."),
}


@lru_cache(maxsize=2048)
def _pattern(term: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9])" + re.escape(term.strip().lower()) + r"(?:s|es)?(?![a-z0-9])")


def _has(text: str, term: str) -> bool:
    return _pattern(term).search(text) is not None


def _level(value: float) -> str:
    return "HIGH" if value >= 0.66 else "MEDIUM" if value >= 0.33 else "LOW"


def analyze_application(text: str | None, role: str | None = None, claims: list[str] | None = None) -> dict[str, Any]:
    lowered = (text or "").lower()
    suspicious = [item for item in SUSPICIOUS if item in lowered]
    if not role or role not in BANK:
        scores = {name: sum(_has(lowered, word) for word in words) for name, words in ROLE_WORDS.items()}
        best = max(scores, key=lambda name: scores[name])
        role = best if scores[best] > 0 else "Unknown"
    entries = BANK.get(role, [])
    hits = [entry for entry in entries if any(_has(lowered, keyword) for keyword in entry[1])]
    if claims:
        joined = " ".join(str(item).lower() for item in claims)
        hits = [entry for entry in entries if any(_has(joined, keyword) or _has(lowered, keyword) for keyword in entry[1])] or hits
    labels = [entry[0] for entry in hits]
    if re.search(r"\b\d+\+?\s*(years|yrs)\b", lowered):
        labels.append("Professional Experience")
    target = hits[0] if hits else None
    if suspicious and not target:
        question = (
            "Your application makes strong guarantees without specifics.\n\n"
            "Describe one concrete project or engagement: your role, what you personally did, and a measurable outcome."
        )
        target = ("Unsubstantiated Expertise Claim", [], question, GENERIC_EVIDENCE)
        labels = labels or ["Unsubstantiated expertise claim"]
    return {
        "role": role,
        "claims": labels,
        "claim": target[0] if target else None,
        "question": target[2] if target else None,
        "suspicious_terms": suspicious,
    }


def evaluate_response(
    text: str | None,
    role: str,
    claim: str | None,
    response: str | None,
    base_score: float | None = None,
) -> dict[str, Any]:
    lowered_text = (text or "").lower()
    entry = next((item for item in BANK.get(role, []) if item[0] == claim), None)
    keywords, evidence_terms = (entry[1], entry[3]) if entry else ([], GENERIC_EVIDENCE)
    answer = (response or "").strip()[:MAX_RESPONSE]
    lowered = answer.lower()
    words = re.findall(r"[A-Za-z0-9\-']+", answer)
    evidence_hits = sorted({term for term in evidence_terms if _has(lowered, term)})
    keyword_in = any(_has(lowered, keyword) for keyword in keywords)
    evidence = min(1.0, len(evidence_hits) / 4)
    relevance = min(1.0, (0.35 if (keyword_in or not keywords) else 0) + 0.65 * min(1.0, len(evidence_hits) / 3))
    capitals = re.findall(r"(?<![.!?]\s)(?<!^)\b[A-Z][A-Za-z0-9]{2,}\b", answer)
    specificity = min(
        1.0,
        (0.35 if len(words) >= 30 else 0.15 if len(words) >= 15 else 0)
        + (0.2 if re.search(r"\d", answer) else 0)
        + (0.25 if len(VERBS.findall(answer)) >= 2 else 0.1 if VERBS.search(answer) else 0)
        + (0.2 if capitals else 0),
    )
    keyword_list = len(evidence_hits) >= 5 and len(words) < len(evidence_hits) * 4 and not VERBS.search(answer)
    if keyword_list:
        evidence = min(evidence, 0.4)
        relevance = min(relevance, 0.4)
        specificity = min(specificity, 0.3)
    contradiction = bool(CONTRA.search(answer))
    guarantee = any(item in lowered for item in SUSPICIOUS)
    consistency = 0.0 if (contradiction or guarantee) else 1.0
    score = 10 + relevance * 25 + evidence * 25 + specificity * 25 + consistency * 15
    suspicious = [item for item in SUSPICIOUS if item in lowered_text]
    if suspicious:
        score -= 10 if (specificity >= 0.6 and evidence >= 0.5) else 15
    if contradiction:
        score -= 15
    answer_score = max(0, min(100, round(score)))
    final = round(0.3 * base_score + 0.7 * answer_score) if isinstance(base_score, (int, float)) else answer_score
    decision = "PASS" if final >= 80 else "VERIFY" if final >= 55 else "REVIEW"
    verification = "SUPPORTED" if final >= 80 else "PARTIALLY SUPPORTED" if final >= 55 else "NOT SUPPORTED"
    checks = [
        ("CLAIM_RELEVANT", entry is not None or bool(suspicious)),
        ("RESPONSE_RELEVANT", relevance >= 0.5),
        ("ROLE_SPECIFIC_EVIDENCE", evidence >= 0.5),
        ("SPECIFICITY_PRESENT", specificity >= 0.5),
        ("CLAIM_CONSISTENT", consistency == 1.0),
    ]
    reason_codes = [{"code": code, "ok": ok} for code, ok in checks]
    if evidence < 0.5:
        reason_codes.append({"code": "CLAIM_EVIDENCE_GAP", "ok": False})
    if consistency == 0.0:
        reason_codes.append({"code": "RESPONSE_INCONSISTENT", "ok": False})
    if keyword_list:
        reason_codes.append({"code": "KEYWORD_LIST_ANSWER", "ok": False})
    if suspicious:
        reason_codes.append({"code": "SUSPICIOUS_CLAIM_LANGUAGE", "ok": False})
    summary = {
        "SUPPORTED": "Your answer is specific and fits the claim you made.",
        "PARTIALLY SUPPORTED": "Your answer fits the claim but needs more detail.",
        "NOT SUPPORTED": "Your answer does not yet show how you did this work.",
    }[verification]
    advice = {
        "PASS": "A person will see this answer as a good sign.",
        "VERIFY": "You can answer again with more detail about what you did yourself.",
        "REVIEW": "A person will look at your application and your answer.",
    }[decision]
    return {
        "claim": claim,
        "verification_result": verification,
        "relevance": _level(relevance),
        "consistency": "HIGH" if consistency == 1.0 else "LOW",
        "specificity": _level(specificity),
        "evidence_terms_found": evidence_hits,
        "verification_score": answer_score,
        "answer_score": answer_score,
        "base_score": base_score,
        "trust_score": final,
        "decision": decision,
        "reason_codes": reason_codes,
        "reasons": [
            {
                "title": REASON_TEXT[item["code"]][0],
                "ok": item["ok"],
                "explanation": REASON_TEXT[item["code"]][1 if item["ok"] else 2] or REASON_TEXT[item["code"]][2],
            }
            for item in reason_codes
        ],
        "summary": summary,
        "advice": advice,
        "note": "This checks the written answer only. It cannot prove real work history. Using an AI tool to help is not held against you.",
    }
