from __future__ import annotations

from tests_auth.conftest import PASSWORD, make_env


def login(client, username, password):
    return client.post("/v1/auth/login", json={"username": username, "password": password})


def test_login_ok_sets_hardened_cookie(env):
    client = env.client()
    response = login(client, "alice", PASSWORD)
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "candidate" and body["csrf_token"]
    cookie = response.headers["set-cookie"].lower()
    assert "trust_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert "secure" not in cookie.replace("samesite", "")
    assert "password" not in response.text


def test_cookie_secure_flag(monkeypatch):
    secure_env = make_env(monkeypatch, cookie_secure=True)
    response = login(secure_env.client(), "alice", PASSWORD)
    assert "secure" in response.headers["set-cookie"].lower().replace("samesite", "")


def test_login_bad_password_and_unknown_user_identical(env):
    client = env.client()
    bad = login(client, "alice", "wrong-password-x")
    unknown = login(client, "nobody", "wrong-password-x")
    assert bad.status_code == unknown.status_code == 401
    assert bad.json() == unknown.json()
    assert "set-cookie" not in bad.headers


def test_disabled_user_cannot_login_and_message_generic(env):
    admin, csrf = env.login("root")
    patched = admin.patch("/v1/admin/users/alice", json={"disabled": True}, headers={"X-CSRF-Token": csrf})
    assert patched.status_code == 200
    response = login(env.client(), "alice", PASSWORD)
    assert response.status_code == 401
    assert response.json() == login(env.client(), "nobody", PASSWORD).json()


def test_disabling_kills_live_session(env):
    alice, _ = env.login("alice")
    assert alice.get("/v1/auth/me").status_code == 200
    admin, csrf = env.login("root")
    admin.patch("/v1/admin/users/alice", json={"disabled": True}, headers={"X-CSRF-Token": csrf})
    assert alice.get("/v1/auth/me").status_code == 401


def test_lockout_after_repeated_failures(env):
    client = env.client()
    for _ in range(5):
        assert login(client, "alice", "wrong-password-x").status_code == 401
    locked = login(client, "alice", PASSWORD)
    assert locked.status_code == 429
    env.clock.advance(901)
    assert login(client, "alice", PASSWORD).status_code == 200


def test_lockout_applies_to_unknown_username_too(env):
    client = env.client()
    for _ in range(5):
        login(client, "ghost", "wrong-password-x")
    assert login(client, "ghost", "wrong-password-x").status_code == 429


def test_ip_lockout_across_usernames(env):
    client = env.client()
    for index in range(20):
        login(client, f"user{index}", "wrong-password-x")
    assert login(client, "alice", PASSWORD).status_code == 429


def test_success_resets_user_counter(env):
    client = env.client()
    for _ in range(4):
        login(client, "alice", "wrong-password-x")
    assert login(client, "alice", PASSWORD).status_code == 200
    for _ in range(4):
        login(client, "alice", "wrong-password-x")
    assert login(client, "alice", PASSWORD).status_code == 200


def test_session_idle_expiry(env):
    client, _ = env.login("alice")
    env.clock.advance(29 * 60)
    assert client.get("/v1/auth/me").status_code == 200
    env.clock.advance(29 * 60)
    assert client.get("/v1/auth/me").status_code == 200
    env.clock.advance(31 * 60)
    assert client.get("/v1/auth/me").status_code == 401


def test_session_absolute_expiry(env):
    client, _ = env.login("alice")
    for _ in range(16):
        env.clock.advance(29 * 60)
        assert client.get("/v1/auth/me").status_code == 200
    env.clock.advance(29 * 60)
    assert client.get("/v1/auth/me").status_code == 401


def test_session_ids_are_long_and_unique(env):
    first = env.service.sessions.create("alice")
    second = env.service.sessions.create("alice")
    assert first.session_id != second.session_id
    assert len(first.session_id) >= 43


def test_logout_invalidates_session(env):
    client, csrf = env.login("alice")
    assert client.post("/v1/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert client.get("/v1/auth/me").status_code == 401


def test_me_requires_auth(env):
    assert env.client().get("/v1/auth/me").status_code == 401


def test_forged_cookie_rejected(env):
    client = env.client()
    client.cookies.set("trust_session", "forged-session-value")
    assert client.get("/v1/auth/me").status_code == 401


def test_password_reset_revokes_sessions_and_changes_password(env):
    alice, _ = env.login("alice")
    admin, csrf = env.login("root")
    response = admin.post(
        "/v1/admin/users/alice/reset-password",
        json={"new_password": "brand-new-password"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    assert alice.get("/v1/auth/me").status_code == 401
    assert login(env.client(), "alice", PASSWORD).status_code == 401
    assert login(env.client(), "alice", "brand-new-password").status_code == 200


def test_expired_or_unknown_session_cookie_gets_clear_json_401(env):
    client = env.client()
    anonymous = client.get("/v1/auth/me")
    assert anonymous.status_code == 401 and anonymous.json() == {"detail": "authentication required"}
    client.cookies.set("trust_session", "not-a-real-session")
    stale = client.get("/v1/auth/me")
    assert stale.status_code == 401
    assert stale.json() == {"detail": "session expired, please sign in again"}
    assert stale.headers["X-Auth-Reason"] == "session_expired"
