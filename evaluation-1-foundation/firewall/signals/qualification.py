from __future__ import annotations

import re

from firewall.config import Config
from firewall.models import Application, JobRequirements, Reason
from firewall.signals.common import reason
from firewall.signals.dates import parse_month, reference_month


SYNONYMS = {
    "react": "react",
    "reactjs": "react",
    "js": "javascript",
    "javascript": "javascript",
    "nodejs": "node.js",
    "node": "node.js",
    "cpp": "c++",
    "c++": "c++",
    "golang": "go",
    "go": "go",
    "ts": "typescript",
    "typescript": "typescript",
    "ml": "machine learning",
    "machinelearning": "machine learning",
    "k8s": "kubernetes",
    "kubernetes": "kubernetes",
    "py": "python",
    "python": "python",
    "postgres": "postgresql",
    "postgresql": "postgresql",
    "tf": "tensorflow",
    "tensorflow": "tensorflow",
    "sklearn": "scikit-learn",
    "scikitlearn": "scikit-learn",
}


def canonical_skill(skill: str) -> str:
    normalized = " ".join(skill.lower().strip().split())
    lookup_key = re.sub(r"[^a-z0-9+]", "", normalized)
    return SYNONYMS.get(lookup_key, normalized)


def _experience_years(application: Application) -> float:
    present = reference_month(application.signals.submitted_at)
    total_months = 0
    for item in application.candidate.experience:
        try:
            start = parse_month(item.start, present)
            end = parse_month(item.end, present)
        except ValueError:
            continue
        if start <= present and end >= start:
            total_months += min(end, present) - start
    return total_months / 12


def evaluate_qualification(
    application: Application,
    job: JobRequirements,
    config: Config,
) -> tuple[float, list[Reason]]:
    candidate_skills = {canonical_skill(skill) for skill in application.candidate.skills}
    required = list(dict.fromkeys(canonical_skill(skill) for skill in job.must_have_skills))
    missing = sorted({skill for skill in required if skill not in candidate_skills})
    coverage = 1.0 if not required else (len(required) - len(missing)) / len(required)
    found: list[Reason] = []
    if missing:
        missing_fraction = len(missing) / len(required)
        scaled_weight = max(1, round(config.weights["QUAL_MISSING_MUST_HAVE"] * missing_fraction))
        found.append(
            reason(
                config,
                "QUAL_MISSING_MUST_HAVE",
                "low",
                f"Must-have coverage is {coverage:.0%}; missing: {', '.join(missing)}.",
                weight=scaled_weight,
            )
        )

    years = _experience_years(application)
    if years < job.min_years:
        found.append(
            reason(
                config,
                "QUAL_UNDER_EXPERIENCE",
                "low",
                f"Estimated experience is {years:.1f} years; job asks for {job.min_years:.1f}.",
            )
        )
    return coverage, found
