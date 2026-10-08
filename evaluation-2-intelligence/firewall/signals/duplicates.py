from __future__ import annotations

import hashlib
import math
from collections import OrderedDict
from threading import RLock

from firewall.config import Config
from firewall.index_keys import application_shingles, normalize_email, normalize_name, normalize_phone
from firewall.llm.ollama_client import OllamaClient
from firewall.models import Application, Reason
from firewall.signals.common import reason
from firewall.store import ApplicationStore


_EMBEDDING_CACHE: OrderedDict[str, tuple[float, ...]] = OrderedDict()
_EMBEDDING_CACHE_LOCK = RLock()
_EMBEDDING_CACHE_LIMIT = 4_096


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


def clear_embedding_cache() -> None:
    with _EMBEDDING_CACHE_LOCK:
        _EMBEDDING_CACHE.clear()


def _resume_text(application: Application) -> str:
    return "\n".join(
        project.description.strip()
        for project in application.candidate.projects
        if project.description.strip()
    )


def _text_chunks(text: str, chunk_chars: int, max_chunks: int) -> tuple[str, ...]:
    words = text.split()
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    for word in words:
        pieces = [word[index : index + chunk_chars] for index in range(0, len(word), chunk_chars)]
        for piece in pieces:
            added = len(piece) + (1 if current else 0)
            if current and current_length + added > chunk_chars:
                chunks.append(" ".join(current))
                if len(chunks) >= max_chunks:
                    return tuple(chunks)
                current = []
                current_length = 0
            current.append(piece)
            current_length += len(piece) + (1 if current_length else 0)
    if current and len(chunks) < max_chunks:
        chunks.append(" ".join(current))
    return tuple(chunks)


def _embedding(text: str, embedder: OllamaClient, config: Config) -> tuple[float, ...] | None:
    text_hash = hashlib.sha256(text.encode()).hexdigest()
    with _EMBEDDING_CACHE_LOCK:
        cached = _EMBEDDING_CACHE.get(text_hash)
        if cached is not None:
            _EMBEDDING_CACHE.move_to_end(text_hash)
            return cached
    vectors: list[tuple[float, ...]] = []
    for chunk in _text_chunks(text, config.semantic_dup_chunk_chars, config.semantic_dup_max_chunks):
        try:
            vector = embedder.embed(chunk)
        except Exception:
            return None
        if vector is None or not vector:
            return None
        if vectors and len(vector) != len(vectors[0]):
            return None
        if any(not math.isfinite(value) for value in vector):
            return None
        vectors.append(tuple(float(value) for value in vector))
    if not vectors:
        return None
    averaged = tuple(sum(vector[index] for vector in vectors) / len(vectors) for index in range(len(vectors[0])))
    with _EMBEDDING_CACHE_LOCK:
        _EMBEDDING_CACHE[text_hash] = averaged
        _EMBEDDING_CACHE.move_to_end(text_hash)
        while len(_EMBEDDING_CACHE) > _EMBEDDING_CACHE_LIMIT:
            _EMBEDDING_CACHE.popitem(last=False)
    return averaged


def _cosine(first: tuple[float, ...], second: tuple[float, ...]) -> float | None:
    if not first or len(first) != len(second):
        return None
    first_norm = math.sqrt(sum(value * value for value in first))
    second_norm = math.sqrt(sum(value * value for value in second))
    if first_norm == 0 or second_norm == 0:
        return None
    similarity = sum(left * right for left, right in zip(first, second)) / (first_norm * second_norm)
    return max(-1.0, min(1.0, similarity))


def _semantic_similarity(
    application: Application,
    store: ApplicationStore,
    config: Config,
    embedder: OllamaClient,
) -> float | None:
    priors = store.applications()
    if not priors:
        return None
    text = _resume_text(application)
    if len(text) < config.semantic_dup_min_chars:
        return None
    current_embedding = _embedding(text, embedder, config)
    if current_embedding is None:
        return None
    best: float | None = None
    for prior in priors:
        if _same_identity(application, prior):
            continue
        prior_text = _resume_text(prior)
        if len(prior_text) < config.semantic_dup_min_chars:
            continue
        prior_embedding = _embedding(prior_text, embedder, config)
        if prior_embedding is None:
            continue
        similarity = _cosine(current_embedding, prior_embedding)
        if similarity is not None and (best is None or similarity > best):
            best = similarity
    return best


def detect_duplicates(
    application: Application,
    store: ApplicationStore,
    config: Config,
    embedder: OllamaClient | None = None,
) -> list[Reason]:
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

    shingle_duplicate = best_similarity is not None and best_similarity >= config.duplicate_similarity_threshold
    semantic_similarity: float | None = None
    if config.semantic_dup_enabled and not shingle_duplicate:
        semantic_similarity = _semantic_similarity(application, store, config, embedder or OllamaClient())

    found: list[Reason] = []
    if same_job:
        found.append(reason(config, "DUP_SAME_JOB", "high", "Same identity already applied to this job."))
    if email_collision:
        found.append(reason(config, "DUP_EMAIL", "medium", f"Normalized email {email} matches an earlier application."))
    if phone_collision:
        found.append(reason(config, "DUP_PHONE", "medium", f"Normalized phone ending {phone[-4:]} matches an earlier application."))
    if shingle_duplicate:
        found.append(
            reason(
                config,
                "DUP_RESUME_NEAR",
                "high",
                f"Resume content has {best_similarity:.3f} shingle similarity to an earlier candidate.",
            )
        )
    elif semantic_similarity is not None and semantic_similarity >= config.semantic_dup_similarity_threshold:
        found.append(
            reason(
                config,
                "DUP_RESUME_NEAR",
                "high",
                f"Resume content has {semantic_similarity:.3f} semantic similarity to an earlier candidate.",
            )
        )
    return found
