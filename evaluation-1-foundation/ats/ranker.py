from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from firewall.models import JobRequirements


def keyword_score(resume_text: str, job: JobRequirements) -> tuple[float, list[str]]:

    keywords = list(dict.fromkeys([*job.must_have_skills, *job.nice_to_have]))
    if not keywords:
        return 0.0, []
    normalized = resume_text.casefold()
    matched = [keyword for keyword in keywords if keyword.casefold() in normalized]
    return len(matched) / len(keywords), matched


def rank_candidates(
    candidates: Iterable[Mapping[str, Any]],
    job: JobRequirements,
) -> list[dict[str, Any]]:
    ranking: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        text = str(candidate.get("resume_text", ""))
        score, matched = keyword_score(text, job)
        ranking.append(
            {
                "candidate_id": str(candidate.get("candidate_id", candidate.get("id", index))),
                "name": candidate.get("name", ""),
                "score": round(score, 6),
                "matched_keywords": matched,
                "_received_order": index,
            }
        )
    ranking.sort(key=lambda item: (-item["score"], item["_received_order"]))
    for item in ranking:
        item.pop("_received_order")
    return ranking
