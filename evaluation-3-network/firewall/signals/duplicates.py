from __future__ import annotations

from firewall.config import Config
from firewall.index_keys import application_shingles, normalize_email, normalize_name, normalize_phone
from firewall.models import Application, Reason
from firewall.signals.common import reason
from firewall.store import ApplicationStore


def _same_identity(first: Application, second: Application) -> bool:
    email_match = bool(normalize_email(first.candidate.email)) and (
        normalize_email(first.candidate.email) == normalize_email(second.candidate.email)
    )
    phone_match = bool(normalize_phone(first.candidate.phone)) and (
        normalize_phone(first.candidate.phone) == normalize_phone(second.candidate.phone)
    )
    return (email_match or phone_match) and (
        normalize_name(first.candidate.name) == normalize_name(second.candidate.name)
    )


def resume_similarity(first: Application, second: Application, min_shingles: int = 8) -> float | None:
    first_shingles = application_shingles(first)
    second_shingles = application_shingles(second)
    if len(first_shingles) < min_shingles or len(second_shingles) < min_shingles:
        return None
    return len(first_shingles & second_shingles) / len(first_shingles | second_shingles)


def detect_duplicates(application: Application, store: ApplicationStore, config: Config) -> list[Reason]:
    email = normalize_email(application.candidate.email)
    phone = normalize_phone(application.candidate.phone)
    email_matches = store.by_email(email) if email else ()
    phone_matches = store.by_phone(phone) if phone else ()
    collision_candidates = {item.application_id: item for item in (*email_matches, *phone_matches)}
    email_collision = False
    phone_collision = False
    same_job = False

    for prior in collision_candidates.values():
        prior_email_match = bool(email) and email == normalize_email(prior.candidate.email)
        prior_phone_match = bool(phone) and phone == normalize_phone(prior.candidate.phone)
        identity_match = _same_identity(application, prior)
        if identity_match and prior.job_id == application.job_id:
            same_job = True
            email_collision = email_collision or prior_email_match
            phone_collision = phone_collision or prior_phone_match
        elif not identity_match:
            email_collision = email_collision or prior_email_match
            phone_collision = phone_collision or prior_phone_match

    application_has_enough_text = len(application_shingles(application)) >= config.min_shingles_for_similarity
    best_similarity: float | None = (
        1.0
        if application_has_enough_text and store.has_exact_resume_from_other_identity(application)
        else None
    )
    if best_similarity is None:
        similarity_candidates = store.resume_candidates(
            application,
            include_all=config.duplicate_similarity_threshold <= 0,
        )
        for prior in similarity_candidates:
            if _same_identity(application, prior):
                continue
            similarity = resume_similarity(application, prior, config.min_shingles_for_similarity)
            if similarity is not None and (best_similarity is None or similarity > best_similarity):
                best_similarity = similarity

    found: list[Reason] = []
    if same_job:
        found.append(reason(config, "DUP_SAME_JOB", "high", "Same identity already applied to this job."))
    if email_collision:
        found.append(reason(config, "DUP_EMAIL", "medium", f"Normalized email {email} matches an earlier application."))
    if phone_collision:
        found.append(reason(config, "DUP_PHONE", "medium", f"Normalized phone ending {phone[-4:]} matches an earlier application."))
    if best_similarity is not None and best_similarity >= config.duplicate_similarity_threshold:
        found.append(
            reason(
                config,
                "DUP_RESUME_NEAR",
                "high",
                f"Resume content has {best_similarity:.3f} shingle similarity to an earlier candidate.",
            )
        )
    return found
