from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Protocol

from firewall.auth.passwords import hash_password


CANDIDATE = "candidate"
RECRUITER = "recruiter"
ADMIN = "admin"
SERVICE = "service"
USER_ROLES = (CANDIDATE, RECRUITER, ADMIN)
USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")


class UserExistsError(Exception):
    pass


class UserNotFoundError(Exception):
    pass


def normalize_username(username: str) -> str:
    return username.strip().lower()


def valid_username(username: str) -> bool:
    return USERNAME_PATTERN.fullmatch(username) is not None


@dataclass(frozen=True)
class User:
    username: str
    password_hash: str
    role: str
    disabled: bool = False
    created_at: float = 0.0

    def public(self) -> dict[str, object]:
        return {
            "username": self.username,
            "role": self.role,
            "disabled": self.disabled,
            "created_at": self.created_at,
        }


class UserStore(Protocol):
    def get(self, username: str) -> User | None: ...

    def create(self, user: User) -> None: ...

    def update(self, user: User) -> None: ...

    def list(self) -> list[User]: ...


class InMemoryUserStore:
    def __init__(self) -> None:
        self._users: dict[str, User] = {}
        self._lock = threading.RLock()

    def get(self, username: str) -> User | None:
        with self._lock:
            return self._users.get(normalize_username(username))

    def create(self, user: User) -> None:
        with self._lock:
            if user.username in self._users:
                raise UserExistsError(user.username)
            self._users[user.username] = user
            self._persist()

    def update(self, user: User) -> None:
        with self._lock:
            if user.username not in self._users:
                raise UserNotFoundError(user.username)
            self._users[user.username] = user
            self._persist()

    def list(self) -> list[User]:
        with self._lock:
            return sorted(self._users.values(), key=lambda item: item.username)

    def _persist(self) -> None:
        return None


class JsonFileUserStore(InMemoryUserStore):
    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self._path = Path(path)
        if self._path.exists():
            raw = json.loads(self._path.read_text(encoding="utf-8") or "[]")
            for item in raw:
                user = User(**item)
                self._users[user.username] = user

    def _persist(self) -> None:
        payload = json.dumps([asdict(user) for user in self.list()], indent=2)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(dir=self._path.parent, prefix=".users-")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(payload)
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, self._path)
        except BaseException:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
            raise


def build_user_store_from_env() -> UserStore:
    path = os.getenv("FIREWALL_USERS_FILE")
    if path:
        return JsonFileUserStore(path)
    return InMemoryUserStore()


def make_user(username: str, password: str, role: str, now: float) -> User:
    return User(
        username=normalize_username(username),
        password_hash=hash_password(password),
        role=role,
        disabled=False,
        created_at=now,
    )


def with_changes(user: User, **changes: object) -> User:
    return replace(user, **changes)


def bootstrap_admin(store: UserStore, now: float) -> bool:
    username = os.getenv("FIREWALL_ADMIN_USER", "")
    password = os.getenv("FIREWALL_ADMIN_PASSWORD", "")
    if not username or not password:
        return False
    username = normalize_username(username)
    if not valid_username(username):
        raise ValueError("FIREWALL_ADMIN_USER is not a valid username")
    if store.get(username) is not None:
        return False
    store.create(make_user(username, password, ADMIN, now))
    return True
