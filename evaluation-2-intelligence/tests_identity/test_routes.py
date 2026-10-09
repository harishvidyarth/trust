from __future__ import annotations

import json
import re
import uuid

import pytest

from firewall.api import IDENTITY, OUTCOMES, STORE
from tests_candidate.conftest import stored_decision
from tests_identity.audio import sine, speech_like, wav_bytes
from tests_identity.fakes import Recorder

FORBIDDEN = re.compile(r"[-()\[\]{};:]")
HUMAN = wav_bytes(speech_like())
SYNTHETIC = wav_bytes(sine(3.0))
SESSION_KEYS = {"session_id", "expires_at", "face", "voice", "consent_text", "photo"}
RECEIPT_KEYS = {"received", "message"}
VIEW_KEYS = {"status", "face_received", "voice_received", "expires_at", "complete"}
RESULT_KEYS = {"status", "face", "voice", "photo", "advisory", "summary", "notes", "completed_at"}
CANDIDATE_BANNED = {"advisory", "indicators", "sentence_match", "code_matched", "verdict", "human_score", "synthetic_risk", "replay_risk", "confidence", "summary", "notes", "state"}


@pytest.fixture(autouse=True)
def no_asr(monkeypatch):
    monkeypatch.setattr(IDENTITY, "_asr", None)
    monkeypatch.setattr(IDENTITY, "_matcher", None)


def keys_of(value, found=None):
    found = set() if found is None else found
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(key)
            keys_of(item, found)
    if isinstance(value, list):
        for item in value:
            keys_of(item, found)
    return found


def submit(session, make_pdf) -> str:
    name = f"Id {uuid.uuid4().hex[:6]}"
    response = session.upload(make_pdf(name, f"{uuid.uuid4().hex[:8]}@example.com", False))
    assert response.status_code == 200, response.text
    return response.json()["application_id"]


def begin(session, application_id, consent=True):
    return session.post("/v1/verify/session", {"application_id": application_id, "consent": consent})


def face_body(opened, **override):
    body = {
        "steps": [{"id": step["id"], "passed": True, "ms": 700} for step in opened["face"]["steps"]],
        "frames_with_face": 90,
        "total_frames": 100,
        "duration_ms": 3000,
        "model": "mediapipe-facemesh",
    }
    body.update(override)
    return body


def send_voice(session, sid, audio=HUMAN, transcript=None, name="audio"):
    data = {} if transcript is None else {"transcript": transcript}
    return session.client.post(f"/v1/verify/{sid}/voice", files={name: ("clip.wav", audio, "audio/wav")}, data=data, headers=session.headers)


def opened_session(alice, make_pdf):
    application_id = submit(alice, make_pdf)
    response = begin(alice, application_id)
    assert response.status_code == 200, response.text
    return application_id, response.json()


def test_session_shape(alice, make_pdf):
    _, opened = opened_session(alice, make_pdf)
    assert set(opened) == SESSION_KEYS
    assert set(opened["face"]) == {"steps"} and set(opened["voice"]) == {"sentence", "max_seconds"}
    assert all(set(step) == {"id", "label", "hint"} for step in opened["face"]["steps"])
    assert "nothing" in opened["consent_text"].lower() or "no video" in opened["consent_text"].lower()


def test_consent_is_required_and_strict(alice, make_pdf):
    application_id = submit(alice, make_pdf)
    assert begin(alice, application_id, consent=False).status_code == 400
    assert alice.post("/v1/verify/session", {"application_id": application_id}).status_code == 400
    assert alice.post("/v1/verify/session", {"application_id": application_id, "consent": True, "extra": 1}).status_code == 400
    assert alice.post("/v1/verify/session", {"application_id": application_id, "consent": "yes"}).status_code == 400
    assert begin(alice, application_id).status_code == 200


def test_ownership_and_roles(alice, bob, recruiter, make_pdf):
    application_id = submit(alice, make_pdf)
    assert begin(bob, application_id).status_code == 404
    assert begin(recruiter, application_id).status_code == 403
    assert begin(alice, "missing-application").status_code == 404
    opened = begin(alice, application_id).json()
    sid = opened["session_id"]
    assert bob.post(f"/v1/verify/{sid}/face", face_body(opened)).status_code == 404
    assert send_voice(bob, sid).status_code == 404
    assert bob.get(f"/v1/verify/session/{sid}").status_code == 404
    assert recruiter.post(f"/v1/verify/{sid}/face", face_body(opened)).status_code == 403


def test_anonymous_and_csrf(alice, make_pdf):
    from fastapi.testclient import TestClient

    from firewall.api import app

    application_id = submit(alice, make_pdf)
    assert TestClient(app).post("/v1/verify/session", json={"application_id": application_id, "consent": True}).status_code in {401, 403}
    assert alice.client.post("/v1/verify/session", json={"application_id": application_id, "consent": True}).status_code == 403


def test_closed_application_is_refused_plainly(alice, recruiter, make_pdf):
    application_id = submit(alice, make_pdf)
    assert recruiter.post(f"/v1/decisions/{application_id}/reject", {"reason": "Role requirements were not met by this applicant"}).status_code == 200
    assert OUTCOMES.is_rejected(application_id)
    refused = begin(alice, application_id)
    assert refused.status_code == 409
    assert not FORBIDDEN.search(refused.json()["detail"])


def test_full_flow_candidate_sees_only_receipts(alice, make_pdf):
    application_id, opened = opened_session(alice, make_pdf)
    sid = opened["session_id"]
    face = alice.post(f"/v1/verify/{sid}/face", face_body(opened))
    assert face.status_code == 200 and face.json() == {"received": True, "message": "Thank you. Your face check was received."}
    mid = alice.get(f"/v1/verify/session/{sid}").json()
    assert set(mid) == VIEW_KEYS and mid["status"] == "face_done" and mid["face_received"] and not mid["voice_received"]
    voice = send_voice(alice, sid, transcript=opened["voice"]["sentence"])
    assert voice.status_code == 200 and voice.json() == {"received": True, "message": "Thank you. Your voice check was received."}
    final = alice.get(f"/v1/verify/session/{sid}").json()
    assert final["complete"] is True and final["status"] == "complete"
    for body in (opened, face.json(), voice.json(), mid, final):
        leaked = keys_of(body) & CANDIDATE_BANNED
        assert not leaked, leaked
    assert set(face.json()) == RECEIPT_KEYS and set(voice.json()) == RECEIPT_KEYS


def test_candidate_cannot_read_the_stored_result(alice, make_pdf):
    application_id, opened = opened_session(alice, make_pdf)
    alice.post(f"/v1/verify/{opened['session_id']}/face", face_body(opened))
    assert alice.get(f"/v1/verify/application/{application_id}").status_code == 404
    assert alice.get("/v1/verify/status").status_code == 403


def test_recruiter_reads_result(alice, recruiter, make_pdf):
    application_id, opened = opened_session(alice, make_pdf)
    sid = opened["session_id"]
    assert recruiter.get(f"/v1/verify/application/{application_id}").status_code == 404
    assert recruiter.get(f"/v1/verify/application/{application_id}").json()["detail"] == "No identity check has been done."
    alice.post(f"/v1/verify/{sid}/face", face_body(opened))
    send_voice(alice, sid, transcript=opened["voice"]["sentence"])
    seen = recruiter.get(f"/v1/verify/application/{application_id}")
    assert seen.status_code == 200
    body = seen.json()
    assert set(body) == RESULT_KEYS and body["status"] == "complete"
    assert body["voice"]["model"] == "heuristic-v1" and body["voice"]["is_real_model"] is False
    assert body["voice"]["transcript_source"] == "browser" and body["voice"]["code_matched"] is True
    assert body["face"]["client_measured"] is True
    for text in [body["summary"], *body["notes"]]:
        assert not FORBIDDEN.search(text), text


def test_status_endpoint(recruiter, admin, monkeypatch):
    monkeypatch.setattr(IDENTITY, "_matcher", None)
    for who in (recruiter, admin):
        body = who.get("/v1/verify/status").json()
        assert body == {"voice_model": "heuristic-v1", "is_real_model": False, "asr_available": False, "face": "measured in the browser", "face_match_available": False, "face_match_model": "sface-2021dec"}


def test_second_upload_of_a_part_is_409(alice, make_pdf):
    _, opened = opened_session(alice, make_pdf)
    sid = opened["session_id"]
    assert alice.post(f"/v1/verify/{sid}/face", face_body(opened)).status_code == 200
    assert alice.post(f"/v1/verify/{sid}/face", face_body(opened)).status_code == 409
    assert send_voice(alice, sid).status_code == 200
    assert send_voice(alice, sid).status_code == 409


def test_bad_inputs_are_400_and_do_not_use_the_part(alice, make_pdf):
    _, opened = opened_session(alice, make_pdf)
    sid = opened["session_id"]
    assert alice.post(f"/v1/verify/{sid}/face", face_body(opened, extra=1)).status_code == 400
    raw = alice.client.post(f"/v1/verify/{sid}/face", content=b"not json", headers=alice.headers)
    assert raw.status_code == 400
    assert send_voice(alice, sid, audio=b"garbage").status_code == 400
    assert send_voice(alice, sid, name="other").status_code == 400
    assert send_voice(alice, sid, transcript="x" * 400).status_code == 400
    wrong = alice.client.post(f"/v1/verify/{sid}/voice", content=b"abc", headers={**alice.headers, "Content-Type": "text/plain"})
    assert wrong.status_code == 400
    assert alice.post(f"/v1/verify/{sid}/face", face_body(opened)).status_code == 200
    assert send_voice(alice, sid).status_code == 200


def test_deeply_nested_json_is_a_plain_400(alice, make_pdf):
    application_id, opened = opened_session(alice, make_pdf)
    nested = b"[" * 200_000
    assert alice.client.post(f"/v1/verify/{opened['session_id']}/face", content=nested, headers=alice.headers).status_code == 400
    assert alice.client.post("/v1/verify/session", content=nested, headers=alice.headers).status_code == 400


def test_unknown_session_is_404(alice):
    assert alice.post("/v1/verify/does-not-exist/face", {}).status_code == 404
    assert send_voice(alice, "does-not-exist").status_code == 404
    assert alice.get("/v1/verify/session/does-not-exist").status_code == 404


def test_expired_session_is_refused(alice, make_pdf, monkeypatch):
    _, opened = opened_session(alice, make_pdf)
    sid = opened["session_id"]
    real = IDENTITY.clock
    monkeypatch.setattr(IDENTITY, "clock", lambda: real() + 700)
    assert alice.get(f"/v1/verify/session/{sid}").json()["status"] == "expired"
    assert alice.post(f"/v1/verify/{sid}/face", face_body(opened)).status_code == 410
    assert send_voice(alice, sid).status_code == 410


def test_session_limit_per_application(alice, make_pdf):
    application_id = submit(alice, make_pdf)
    for _ in range(3):
        assert begin(alice, application_id).status_code == 200
    refused = begin(alice, application_id)
    assert refused.status_code == 429
    assert not FORBIDDEN.search(refused.json()["detail"])


def test_body_cap_on_declared_length(alice, make_pdf):
    _, opened = opened_session(alice, make_pdf)
    sid = opened["session_id"]
    big = b"x" * (3 * 1024 * 1024 + 10)
    assert alice.client.post(f"/v1/verify/{sid}/face", content=big, headers=alice.headers).status_code == 413
    assert send_voice(alice, sid, audio=big).status_code == 413
    assert alice.client.post("/v1/verify/session", content=big, headers=alice.headers).status_code == 413


def test_body_cap_on_streamed_body_without_length(alice, make_pdf):
    _, opened = opened_session(alice, make_pdf)
    sid = opened["session_id"]

    def chunks():
        for _ in range(40):
            yield b"y" * 100_000

    response = alice.client.post(f"/v1/verify/{sid}/face", content=chunks(), headers=alice.headers)
    assert response.status_code == 413
    ok = alice.post(f"/v1/verify/{sid}/face", face_body(opened))
    assert ok.status_code == 200


def test_audio_over_service_limit_is_plain_400(alice, make_pdf):
    _, opened = opened_session(alice, make_pdf)
    sid = opened["session_id"]
    long_clip = wav_bytes(sine(16.0))
    response = send_voice(alice, sid, audio=long_clip)
    assert response.status_code == 400 and not FORBIDDEN.search(response.json()["detail"])


def test_no_media_reaches_any_store(alice, make_pdf, monkeypatch, tmp_path):
    results, sessions = Recorder(), Recorder()
    monkeypatch.setattr(IDENTITY, "results", results)
    monkeypatch.setattr(IDENTITY, "sessions", sessions)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    _, opened = opened_session(alice, make_pdf)
    sid = opened["session_id"]
    alice.post(f"/v1/verify/{sid}/face", face_body(opened))
    marker = "zebraquartz spoken marker"
    assert send_voice(alice, sid, transcript=marker).status_code == 200
    stored = json.dumps(results.written) + json.dumps(sessions.written)
    assert "RIFF" not in stored and "zebraquartz" not in stored
    assert HUMAN[200:260].hex() not in stored
    assert all(isinstance(item, dict) for item in results.written + sessions.written)
    assert list(tmp_path.iterdir()) == []


def test_plain_wording_of_every_candidate_string(alice, make_pdf):
    application_id, opened = opened_session(alice, make_pdf)
    sid = opened["session_id"]
    texts = [opened["consent_text"], opened["voice"]["sentence"]]
    for step in opened["face"]["steps"]:
        texts += [step["label"], step["hint"]]
    texts.append(alice.post(f"/v1/verify/{sid}/face", face_body(opened)).json()["message"])
    texts.append(send_voice(alice, sid).json()["message"])
    detail = alice.get(f"/v1/me/applications/{application_id}").json()
    item = next(entry for entry in detail["follow_up"] if entry["id"] == "identity-check")
    texts += [item["label"], item["help"]]
    for text in texts:
        assert text and not FORBIDDEN.search(text), text
    errors = [
        begin(alice, application_id, consent=False).json()["detail"],
        alice.post(f"/v1/verify/{sid}/face", face_body(opened)).json()["detail"],
        send_voice(alice, sid).json()["detail"],
        alice.get("/v1/verify/session/nope").json()["detail"],
    ]
    for text in errors:
        assert text and not FORBIDDEN.search(text), text


def test_follow_up_item_is_optional_and_not_counted(alice, make_pdf):
    application_id, opened = opened_session(alice, make_pdf)
    sid = opened["session_id"]

    def item_and_count():
        detail = alice.get(f"/v1/me/applications/{application_id}").json()
        entry = next(entry for entry in detail["follow_up"] if entry["id"] == "identity-check")
        return entry, detail["follow_up_count"]

    entry, before = item_and_count()
    assert entry == {
        "id": "identity-check",
        "kind": "identity",
        "label": "Quick identity check",
        "help": "About one minute. You use your camera and microphone to follow three simple prompts. Nothing is recorded or kept.",
        "required": False,
        "answered": False,
    }
    alice.post(f"/v1/verify/{sid}/face", face_body(opened))
    assert item_and_count()[0]["answered"] is False
    send_voice(alice, sid)
    entry, after = item_and_count()
    assert entry["answered"] is True and after == before


def test_closed_application_has_no_identity_item(alice, recruiter, make_pdf):
    application_id = submit(alice, make_pdf)
    recruiter.post(f"/v1/decisions/{application_id}/reject", {"reason": "Role requirements were not met by this applicant"})
    detail = alice.get(f"/v1/me/applications/{application_id}").json()
    assert all(entry["id"] != "identity-check" for entry in detail["follow_up"])


def test_recruiter_list_items_carry_summary(alice, recruiter, make_pdf):
    application_id, opened = opened_session(alice, make_pdf)
    sid = opened["session_id"]

    def row():
        listed = recruiter.get("/v1/decisions?limit=1000").json()
        return next(item for item in listed if item["application_id"] == application_id)

    assert row()["identity_check"] is None and row()["identity_advisory"] is None
    alice.post(f"/v1/verify/{sid}/face", face_body(opened))
    assert row()["identity_check"] == "partial" and row()["identity_advisory"] is None
    send_voice(alice, sid, audio=SYNTHETIC)
    assert row()["identity_check"] == "complete" and row()["identity_advisory"] == "ask_for_live_check"


def test_advisory_never_changes_the_decision(alice, recruiter, make_pdf):
    application_id, opened = opened_session(alice, make_pdf)
    sid = opened["session_id"]
    before = stored_decision(application_id).model_dump(mode="json")
    listed_before = next(item for item in recruiter.get("/v1/decisions?limit=1000").json() if item["application_id"] == application_id)["decision"]
    bad = face_body(opened, frames_with_face=1)
    assert alice.post(f"/v1/verify/{sid}/face", bad).status_code == 200
    assert send_voice(alice, sid, audio=SYNTHETIC, transcript="wrong words").status_code == 200
    result = recruiter.get(f"/v1/verify/application/{application_id}").json()
    assert result["advisory"] == "ask_for_live_check"
    after = stored_decision(application_id).model_dump(mode="json")
    assert after == before
    assert STORE.get_decision(application_id).score == before["score"]
    listed_after = next(item for item in recruiter.get("/v1/decisions?limit=1000").json() if item["application_id"] == application_id)["decision"]
    assert listed_after == listed_before


def test_audit_event_is_written_on_completion(alice, admin, make_pdf):
    application_id, opened = opened_session(alice, make_pdf)
    sid = opened["session_id"]
    alice.post(f"/v1/verify/{sid}/face", face_body(opened))
    send_voice(alice, sid)
    from firewall.auth import get_service

    entries = get_service().audit.entries(500)
    assert any(entry.event == "identity_check_completed" and entry.target == application_id for entry in entries)
