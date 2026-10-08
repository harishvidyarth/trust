from __future__ import annotations

import hashlib
import re

from firewall.models import Application


WORD_RE = re.compile(r"[a-z0-9]+")


def normalize_email(email: str) -> str:
    value = email.strip().lower()
    if "@" not in value:
        return value
    local, domain = value.rsplit("@", 1)
    local = local.split("+", 1)[0]
    if domain in {"gmail.com", "googlemail.com"}:
        local = local.replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"


def normalize_phone(phone: str) -> str:
    digits = "".join(character for character in phone if character.isdigit())
    return digits[-10:]


def normalize_name(name: str) -> str:
    return " ".join(WORD_RE.findall(name.lower()))


def candidate_identity_key(application: Application) -> str:
    return (
        normalize_email(application.candidate.email)
        or normalize_phone(application.candidate.phone)
        or normalize_name(application.candidate.name)
    )


def resume_words(application: Application) -> list[str]:
    descriptions = " ".join(project.description for project in application.candidate.projects)
    return WORD_RE.findall(descriptions.lower())


def shingles(words: list[str], size: int = 3) -> set[tuple[str, ...]]:
    if len(words) < size:
        return {tuple(words)} if words else set()
    return {tuple(words[index : index + size]) for index in range(len(words) - size + 1)}


def application_shingles(application: Application) -> set[tuple[str, ...]]:
    return shingles(resume_words(application))


def stable_shingle_hash(shingle: tuple[str, ...]) -> str:
    return hashlib.sha256("\x1f".join(shingle).encode()).hexdigest()


def resume_content_hash(application: Application) -> str:
    hashes = sorted(stable_shingle_hash(item) for item in application_shingles(application))
    return hashlib.sha256("".join(hashes).encode()).hexdigest() if hashes else ""


def resume_bucket_keys(application: Application) -> tuple[str, ...]:
    shingle_hashes = [bytes.fromhex(stable_shingle_hash(item)) for item in application_shingles(application)]
    if not shingle_hashes:
        return ()
    signature = []
    for permutation in range(48):
        salt = permutation.to_bytes(2, "big")
        signature.append(min(hashlib.sha256(salt + item).digest() for item in shingle_hashes))
    return tuple(
        hashlib.sha256(b"".join(signature[index : index + 3])).hexdigest()
        for index in range(0, len(signature), 3)
    )


def template_text(application: Application) -> str:
    text = " ".join(project.description for project in application.candidate.projects)
    return " ".join(WORD_RE.findall(text.lower()))


def template_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest() if text else ""
