from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError


MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 256

_HASHER = PasswordHasher()
_DUMMY_HASH = _HASHER.hash("timing-equalizer-not-a-real-password")


class PasswordPolicyError(ValueError):
    pass


def validate_password(password: str) -> None:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"password must be at most {MAX_PASSWORD_LENGTH} characters")


def hash_password(password: str) -> str:
    validate_password(password)
    return _HASHER.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    target = password_hash or _DUMMY_HASH
    try:
        ok = _HASHER.verify(target, password[:MAX_PASSWORD_LENGTH])
    except (VerificationError, InvalidHashError):
        ok = False
    return ok and password_hash is not None
