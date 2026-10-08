from __future__ import annotations

import json

import pytest

from firewall.auth.passwords import PasswordPolicyError, hash_password, verify_password
from firewall.auth.users import (
    ADMIN,
    InMemoryUserStore,
    JsonFileUserStore,
    UserExistsError,
    bootstrap_admin,
    make_user,
)


def test_hash_is_argon2id_and_salted():
    first = hash_password("long-enough-pass")
    second = hash_password("long-enough-pass")
    assert first.startswith("$argon2id$")
    assert first != second


def test_verify_accepts_right_and_rejects_wrong():
    stored = hash_password("long-enough-pass")
    assert verify_password(stored, "long-enough-pass")
    assert not verify_password(stored, "long-enough-pasX")
    assert not verify_password(stored, "")


def test_verify_missing_hash_is_false():
    assert not verify_password(None, "anything-at-all")


def test_verify_garbage_hash_is_false():
    assert not verify_password("not-a-hash", "long-enough-pass")


def test_min_length_enforced():
    with pytest.raises(PasswordPolicyError):
        hash_password("short")
    hash_password("x" * 10)


def test_duplicate_user_rejected():
    store = InMemoryUserStore()
    store.create(make_user("dana", "long-enough-pass", ADMIN, 1.0))
    with pytest.raises(UserExistsError):
        store.create(make_user("Dana", "long-enough-pass", ADMIN, 2.0))


def test_json_store_roundtrip_and_no_plaintext(tmp_path):
    path = tmp_path / "users.json"
    store = JsonFileUserStore(path)
    store.create(make_user("erin", "long-enough-pass", ADMIN, 1.0))
    raw = path.read_text()
    assert "long-enough-pass" not in raw
    assert json.loads(raw)[0]["username"] == "erin"
    reloaded = JsonFileUserStore(path)
    assert reloaded.get("erin") is not None
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_bootstrap_admin_from_env(monkeypatch):
    monkeypatch.setenv("FIREWALL_ADMIN_USER", "Boss")
    monkeypatch.setenv("FIREWALL_ADMIN_PASSWORD", "synthetic-admin-pass")
    store = InMemoryUserStore()
    assert bootstrap_admin(store, 1.0)
    assert store.get("boss").role == ADMIN
    assert not bootstrap_admin(store, 2.0)


def test_no_default_account_without_env(monkeypatch):
    monkeypatch.delenv("FIREWALL_ADMIN_USER", raising=False)
    monkeypatch.delenv("FIREWALL_ADMIN_PASSWORD", raising=False)
    store = InMemoryUserStore()
    assert not bootstrap_admin(store, 1.0)
    assert store.list() == []


def test_bootstrap_rejects_short_password_without_echo(monkeypatch):
    monkeypatch.setenv("FIREWALL_ADMIN_USER", "boss")
    monkeypatch.setenv("FIREWALL_ADMIN_PASSWORD", "tiny")
    with pytest.raises(PasswordPolicyError) as raised:
        bootstrap_admin(InMemoryUserStore(), 1.0)
    assert "tiny" not in str(raised.value)
