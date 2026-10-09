from __future__ import annotations

from firewall.auth import overrides_for
from tests_auth.conftest import PASSWORD, make_env


def test_override_leaves_original_intact_and_returns_both(env):
    before = env.decisions["app-bob"].model_dump()
    client, csrf = env.login("rita")
    response = client.post(
        "/v1/decisions/app-bob/override",
        json={"route": "PASS_TO_ATS", "reason": "verified identity by phone"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["original"] == {"route": "MANUAL_REVIEW", "score": 35}
    assert body["override"]["override_route"] == "PASS_TO_ATS"
    assert body["override"]["actor"] == "rita"
    assert env.decisions["app-bob"].model_dump() == before
    stored = overrides_for("app-bob")
    assert len(stored) == 1 and stored[0].original_score == 35 and stored[0].original_route == "MANUAL_REVIEW"
    assert client.get("/v1/decisions/app-bob").json()["route"] == "MANUAL_REVIEW"


def test_override_writes_audit_entry(env):
    client, csrf = env.login("rita")
    client.post(
        "/v1/decisions/app-bob/override",
        json={"route": "PASS_TO_ATS", "reason": "verified identity by phone"},
        headers={"X-CSRF-Token": csrf},
    )
    events = [entry for entry in env.service.audit.entries() if entry.event == "decision_override"]
    assert len(events) == 1
    assert events[0].actor == "rita" and events[0].target == "app-bob"
    assert events[0].detail["from"] == "MANUAL_REVIEW" and events[0].detail["to"] == "PASS_TO_ATS"


def test_override_multiple_kept_as_history(env):
    client, csrf = env.login("root")
    for route in ("PASS_TO_ATS", "ADDITIONAL_VERIFICATION"):
        client.post(
            "/v1/decisions/app-bob/override",
            json={"route": route, "reason": "second look by admin"},
            headers={"X-CSRF-Token": csrf},
        )
    assert [item.override_route for item in overrides_for("app-bob")] == ["PASS_TO_ATS", "ADDITIONAL_VERIFICATION"]
    assert all(item.original_route == "MANUAL_REVIEW" for item in overrides_for("app-bob"))


def test_override_validation(env):
    client, csrf = env.login("rita")
    headers = {"X-CSRF-Token": csrf}
    assert client.post("/v1/decisions/app-bob/override", json={"route": "PASS_TO_ATS", "reason": "short"}, headers=headers).status_code == 422
    assert client.post("/v1/decisions/app-bob/override", json={"route": "BOGUS", "reason": "long enough reason"}, headers=headers).status_code == 422
    assert client.post("/v1/decisions/missing/override", json={"route": "PASS_TO_ATS", "reason": "long enough reason"}, headers=headers).status_code == 404
    assert env.service.overrides.for_application("app-bob") == []


def test_candidate_override_denied_and_not_recorded(env):
    client, csrf = env.login("alice")
    response = client.post(
        "/v1/decisions/app-alice/override",
        json={"route": "PASS_TO_ATS", "reason": "please approve me"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 403
    assert env.service.overrides.for_application("app-alice") == []


def test_audit_records_login_events_and_user_changes(env):
    login = lambda name, pw: env.client().post("/v1/auth/login", json={"username": name, "password": pw})
    login("alice", "wrong-password-x")
    admin, csrf = env.login("root")
    admin.post("/v1/admin/users", json={"username": "newbie", "password": PASSWORD, "role": "recruiter"}, headers={"X-CSRF-Token": csrf})
    admin.patch("/v1/admin/users/bob", json={"disabled": True}, headers={"X-CSRF-Token": csrf})
    entries = admin.get("/v1/admin/audit").json()
    events = {entry["event"] for entry in entries}
    assert {"login_ok", "login_failed", "user_created", "user_updated"} <= events
    assert PASSWORD not in admin.get("/v1/admin/audit").text
    assert "wrong-password-x" not in admin.get("/v1/admin/audit").text


def test_audit_log_has_no_mutators(env):
    log = env.service.audit
    assert not any(hasattr(log, name) for name in ("update", "delete", "clear", "remove", "pop"))
    first = log.append("one", "a")
    second = log.append("two", "a")
    assert second.seq == first.seq + 1


def test_audit_file_is_append_only_lines(monkeypatch, tmp_path):
    from firewall.auth.audit import InMemoryAuditLog

    path = tmp_path / "audit.jsonl"
    log = InMemoryAuditLog(lambda: 5.0, path)
    log.append("one", "a")
    log.append("two", "b")
    assert len(path.read_text().splitlines()) == 2


def test_signup_creates_candidate_only(env):
    client = env.client()
    response = client.post("/v1/auth/register", json={"username": "Carol", "password": PASSWORD})
    assert response.status_code == 201
    assert response.json()["role"] == "candidate" and response.json()["username"] == "carol"
    assert "hash" not in response.text
    assert client.post("/v1/auth/login", json={"username": "carol", "password": PASSWORD}).status_code == 200


def test_signup_cannot_choose_admin(env):
    client = env.client()
    response = client.post("/v1/auth/register", json={"username": "mallory", "password": PASSWORD, "role": "admin"})
    assert response.status_code == 422
    assert env.service.users.get("mallory") is None


def test_recruiter_signup_off_without_code_setting(env, monkeypatch):
    monkeypatch.delenv("FIREWALL_RECRUITER_SIGNUP_CODE", raising=False)
    client = env.client()
    body = {"username": "newrec", "password": PASSWORD, "role": "recruiter", "access_code": "anything-at-all"}
    response = client.post("/v1/auth/register", json=body)
    assert response.status_code == 403
    assert "not turned on" in response.json()["detail"]
    assert env.service.users.get("newrec") is None


def test_recruiter_signup_short_code_setting_is_ignored(env, monkeypatch):
    monkeypatch.setenv("FIREWALL_RECRUITER_SIGNUP_CODE", "short")
    client = env.client()
    body = {"username": "newrec", "password": PASSWORD, "role": "recruiter", "access_code": "short"}
    assert client.post("/v1/auth/register", json=body).status_code == 403
    assert env.service.users.get("newrec") is None


def test_recruiter_signup_wrong_and_missing_code(env, monkeypatch):
    monkeypatch.setenv("FIREWALL_RECRUITER_SIGNUP_CODE", "lab-code-1234")
    client = env.client()
    wrong = client.post("/v1/auth/register", json={"username": "newrec", "password": PASSWORD, "role": "recruiter", "access_code": "lab-code-9999"})
    missing = client.post("/v1/auth/register", json={"username": "newrec", "password": PASSWORD, "role": "recruiter"})
    assert wrong.status_code == 403 and missing.status_code == 403
    assert env.service.users.get("newrec") is None


def test_recruiter_signup_with_right_code(env, monkeypatch):
    monkeypatch.setenv("FIREWALL_RECRUITER_SIGNUP_CODE", "lab-code-1234")
    client = env.client()
    body = {"username": "NewRec", "password": PASSWORD, "role": "recruiter", "access_code": "lab-code-1234"}
    response = client.post("/v1/auth/register", json=body)
    assert response.status_code == 201
    assert response.json()["role"] == "recruiter"
    assert "lab-code-1234" not in response.text
    assert env.service.users.get("newrec").role == "recruiter"


def test_candidate_signup_ignores_access_code(env, monkeypatch):
    monkeypatch.setenv("FIREWALL_RECRUITER_SIGNUP_CODE", "lab-code-1234")
    client = env.client()
    body = {"username": "cora", "password": PASSWORD, "role": "candidate", "access_code": "lab-code-1234"}
    assert client.post("/v1/auth/register", json=body).json()["role"] == "candidate"


def test_wrong_recruiter_code_counts_against_the_limit(env, monkeypatch):
    monkeypatch.setenv("FIREWALL_RECRUITER_SIGNUP_CODE", "lab-code-1234")
    client = env.client()
    statuses = []
    for number in range(12):
        body = {"username": "guess%d" % number, "password": PASSWORD, "role": "recruiter", "access_code": "wrong-%d" % number}
        statuses.append(client.post("/v1/auth/register", json=body).status_code)
    assert 429 in statuses
    assert all(env.service.users.get("guess%d" % n) is None for n in range(12))


def test_signup_validation_and_duplicates(env):
    client = env.client()
    assert client.post("/v1/auth/register", json={"username": "ok-name", "password": "short"}).status_code == 422
    assert client.post("/v1/auth/register", json={"username": "a b", "password": PASSWORD}).status_code == 422
    assert client.post("/v1/auth/register", json={"username": "alice", "password": PASSWORD}).status_code == 409


def test_signup_disabled_switch(monkeypatch):
    closed = make_env(monkeypatch, allow_signup=False)
    response = closed.client().post("/v1/auth/register", json={"username": "carol", "password": PASSWORD})
    assert response.status_code == 403
    assert closed.service.users.get("carol") is None


def test_signup_switch_read_from_env(monkeypatch):
    from firewall.auth import AuthSettings

    monkeypatch.setenv("FIREWALL_ALLOW_SIGNUP", "0")
    monkeypatch.setenv("FIREWALL_COOKIE_SECURE", "1")
    settings = AuthSettings.from_env()
    assert settings.allow_signup is False and settings.cookie_secure is True
    monkeypatch.delenv("FIREWALL_ALLOW_SIGNUP")
    assert AuthSettings.from_env().allow_signup is True


def test_signup_rate_limited(env):
    client = env.client()
    codes = [
        client.post("/v1/auth/register", json={"username": f"person{index}", "password": PASSWORD}).status_code
        for index in range(7)
    ]
    assert codes[:5] == [201] * 5
    assert codes[5:] == [429, 429]
    env.clock.advance(3601)
    assert client.post("/v1/auth/register", json={"username": "later", "password": PASSWORD}).status_code == 201
