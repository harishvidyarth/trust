from __future__ import annotations

import pytest

from tests_auth.conftest import PASSWORD, make_env


def test_unsafe_request_without_csrf_rejected(env):
    client, _ = env.login("rita")
    response = client.post("/v1/decisions/app-bob/override", json={"route": "PASS_TO_ATS", "reason": "verified by phone"})
    assert response.status_code == 403
    assert env.service.overrides.for_application("app-bob") == []


def test_unsafe_request_with_wrong_csrf_rejected(env):
    client, _ = env.login("rita")
    response = client.post(
        "/v1/decisions/app-bob/override",
        json={"route": "PASS_TO_ATS", "reason": "verified by phone"},
        headers={"X-CSRF-Token": "nope"},
    )
    assert response.status_code == 403


def test_unsafe_request_with_csrf_accepted(env):
    client, csrf = env.login("rita")
    response = client.post(
        "/v1/decisions/app-bob/override",
        json={"route": "PASS_TO_ATS", "reason": "verified by phone"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200


def test_safe_request_needs_no_csrf(env):
    client, _ = env.login("rita")
    assert client.get("/v1/stats").status_code == 200


def test_csrf_token_from_other_session_rejected(env):
    first, _ = env.login("rita")
    _, other_token = env.login("root")
    response = first.post(
        "/v1/decisions/app-bob/override",
        json={"route": "PASS_TO_ATS", "reason": "verified by phone"},
        headers={"X-CSRF-Token": other_token},
    )
    assert response.status_code == 403


def test_api_key_is_service_role(env, monkeypatch):
    monkeypatch.setenv("FIREWALL_API_KEY", "synthetic-service-key")
    headers = {"x-api-key": "synthetic-service-key"}
    client = env.client()
    assert client.post("/v1/applications/evaluate", headers=headers).json() == {"by": "service"}
    assert client.get("/v1/stats", headers=headers).status_code == 403
    assert client.get("/v1/decisions/app-alice", headers=headers).status_code == 403
    assert client.get("/v1/admin/users", headers=headers).status_code == 403
    assert client.get("/v1/admin/audit", headers=headers).status_code == 403
    assert client.post("/v1/decisions/app-bob/override", json={"route": "PASS_TO_ATS", "reason": "service tries"}, headers=headers).status_code == 403
    assert client.get("/v1/auth/me", headers=headers).status_code == 403


def test_wrong_api_key_rejected(env, monkeypatch):
    monkeypatch.setenv("FIREWALL_API_KEY", "synthetic-service-key")
    assert env.client().post("/v1/applications/evaluate", headers={"x-api-key": "bad"}).status_code == 401


def test_api_key_header_ignored_when_unconfigured(env):
    assert env.client().post("/v1/applications/evaluate", headers={"x-api-key": "anything"}).status_code == 401


ROUTES = [
    ("get", "/v1/auth/me", None, {"candidate", "recruiter", "admin"}),
    ("post", "/v1/auth/logout", None, {"candidate", "recruiter", "admin"}),
    ("get", "/v1/admin/users", None, {"admin"}),
    ("post", "/v1/admin/users", {"username": "newbie", "password": PASSWORD, "role": "candidate"}, {"admin"}),
    ("patch", "/v1/admin/users/bob", {"disabled": False}, {"admin"}),
    ("post", "/v1/admin/users/bob/reset-password", {"new_password": "another-password"}, {"admin"}),
    ("get", "/v1/admin/audit", None, {"admin"}),
    ("post", "/v1/decisions/app-bob/override", {"route": "PASS_TO_ATS", "reason": "verified by phone"}, {"recruiter", "admin"}),
    ("get", "/v1/stats", None, {"recruiter", "admin"}),
    ("post", "/v1/applications/evaluate", None, {"recruiter", "admin"}),
]


@pytest.mark.parametrize("method,path,body,allowed", ROUTES)
@pytest.mark.parametrize("username,role", [("alice", "candidate"), ("rita", "recruiter"), ("root", "admin")])
def test_role_matrix(monkeypatch, method, path, body, allowed, username, role):
    env = make_env(monkeypatch)
    client, csrf = env.login(username)
    response = client.request(method.upper(), path, json=body, headers={"X-CSRF-Token": csrf})
    if role in allowed:
        assert response.status_code in {200, 201}, response.text
    else:
        assert response.status_code == 403


@pytest.mark.parametrize("method,path,body,allowed", ROUTES)
def test_anonymous_gets_401(env, method, path, body, allowed):
    assert env.client().request(method.upper(), path, json=body).status_code == 401


def test_candidate_reads_only_own_application(env):
    alice, _ = env.login("alice")
    assert alice.get("/v1/decisions/app-alice").status_code == 200
    assert alice.get("/v1/decisions/app-bob").status_code == 404
    assert alice.get("/v1/decisions/unbound-id").status_code == 404


def test_staff_read_any_application(env):
    for username in ("rita", "root"):
        client, _ = env.login(username)
        assert client.get("/v1/decisions/app-alice").status_code == 200
        assert client.get("/v1/decisions/app-bob").status_code == 200


def test_ownership_first_binding_wins(env):
    env.service.ownership.bind("app-alice", "bob")
    assert env.service.ownership.owner("app-alice") == "alice"


def test_admin_cannot_change_self(env):
    admin, csrf = env.login("root")
    response = admin.patch("/v1/admin/users/root", json={"disabled": True}, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 400


def test_role_change_takes_effect_immediately(env):
    alice, _ = env.login("alice")
    assert alice.get("/v1/stats").status_code == 403
    admin, csrf = env.login("root")
    admin.patch("/v1/admin/users/alice", json={"role": "recruiter"}, headers={"X-CSRF-Token": csrf})
    again, _ = env.login("alice")
    assert again.get("/v1/stats").status_code == 200


def test_admin_user_listing_hides_hashes(env):
    admin, _ = env.login("root")
    text = admin.get("/v1/admin/users").text
    assert "argon2" not in text and "password_hash" not in text
