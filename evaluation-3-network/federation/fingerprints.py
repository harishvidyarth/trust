
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
from typing import Any

from firewall.models import Application


WORD_RE = re.compile(r"[a-z0-9]+")
MINHASH_ROWS = 4
MINHASH_BANDS = 4


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


def _hmac(secret: bytes, namespace: str, value: str | bytes) -> str:
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    return hmac.new(secret, namespace.encode("ascii") + b"\0" + raw, hashlib.sha256).hexdigest()


def _network_prefix(value: str) -> str:
    address = ipaddress.ip_address(value.strip())
    prefix = 24 if address.version == 4 else 64
    return str(ipaddress.ip_network(f"{address}/{prefix}", strict=False))


def _safe_network_prefix(value: str) -> str:
    first = value.split(",", 1)[0].strip()
    try:
        return _network_prefix(first)
    except ValueError:
        return ""


def _resume_words(application: Application) -> list[str]:
    parts: list[str] = []
    for item in application.candidate.experience:
        parts.extend((item.company, item.title))
    for project in application.candidate.projects:
        parts.extend((project.name, project.description))
    resume_text = getattr(application.candidate, "resume_text", "")
    if resume_text:
        parts.append(str(resume_text))
    return WORD_RE.findall(" ".join(parts).lower())


def _shingles(words: list[str], size: int = 3) -> set[str]:
    if not words:
        return set()
    if len(words) < size:
        return {" ".join(words)}
    return {" ".join(words[index : index + size]) for index in range(len(words) - size + 1)}


def _resume_bands(application: Application, secret: bytes) -> dict[str, str]:
    shingles = _shingles(_resume_words(application))
    if not shingles:
        return {}
    minima: list[int] = []
    for permutation in range(MINHASH_ROWS * MINHASH_BANDS):
        values = (
            int.from_bytes(
                hmac.new(
                    secret,
                    f"minhash:{permutation}\0{shingle}".encode("utf-8"),
                    hashlib.sha256,
                ).digest()[:8],
                "big",
            )
            for shingle in shingles
        )
        minima.append(min(values))

    bands: dict[str, str] = {}
    for band in range(MINHASH_BANDS):
        start = band * MINHASH_ROWS
        packed = b"".join(value.to_bytes(8, "big") for value in minima[start : start + MINHASH_ROWS])
        bands[f"resume_band_{band}"] = _hmac(secret, f"resume-band:{band}", packed)
    return bands


def to_fingerprints(application: Application | dict[str, Any], secret: str | bytes) -> dict[str, str]:
    if not isinstance(application, Application):
        application = Application.model_validate(application)
    key = secret.encode("utf-8") if isinstance(secret, str) else secret
    if len(key) < 16:
        raise ValueError("federation secret must be at least 16 bytes")

    values = {
        "email": normalize_email(application.candidate.email),
        "phone": normalize_phone(application.candidate.phone),
        "device": application.signals.device_id.strip(),
        "ip24": _safe_network_prefix(application.signals.ip),
    }
    result = {
        kind: _hmac(key, kind, value)
        for kind, value in values.items()
        if value
    }
    result.update(_resume_bands(application, key))
    return result
