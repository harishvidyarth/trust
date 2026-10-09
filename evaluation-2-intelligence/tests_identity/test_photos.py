from __future__ import annotations

import io
import json
import re
import struct
import sys
import threading
import types
import zlib

import pytest

from firewall.api import IDENTITY, STORE
from firewall.identity import IdentityError, facematch
from firewall.identity.challenge import PHOTO_CONSENT_TEXT
from firewall.identity.facematch import THRESHOLDS, OpenCvFaceMatcher, readable_header
from tests_candidate.conftest import stored_decision
from tests_identity.fakes import FakeMatcher, Recorder
from tests_identity.test_routes import CANDIDATE_BANNED, FORBIDDEN, face_body, keys_of, opened_session, submit
from tests_identity.test_service import HUMAN, build, good_face, start


def consented(service):
    return service.create_session("app-1", "alice", True, True)

ID_BYTES = b"id-card-bytes-for-tests"
LIVE_BYTES = b"live-frame-bytes-for-tests"
RECEIPT = {"received": True, "message": "Thank you. Your photo check was received."}
PHOTO_KEYS = {"state", "similarity", "threshold", "model", "frames_checked", "client_images"}
RESULT_KEYS = {"status", "face", "voice", "photo", "advisory", "summary", "notes", "completed_at"}


@pytest.fixture
def matcher(monkeypatch):
    fake = FakeMatcher()
    monkeypatch.setattr(IDENTITY, "_matcher", fake)
    return fake


def begin_with(session, application_id, photo_consent=True):
    return session.post("/v1/verify/session", {"application_id": application_id, "consent": True, "photo_consent": photo_consent})


def open_photo_session(alice, make_pdf, photo_consent=True):
    application_id = submit(alice, make_pdf)
    response = begin_with(alice, application_id, photo_consent)
    assert response.status_code == 200, response.text
    return application_id, response.json()


def send_photos(session, sid, id_photo=ID_BYTES, live=(LIVE_BYTES,), id_name="id_photo"):
    files = {id_name: ("id.jpg", id_photo, "image/jpeg")}
    for index, frame in enumerate(live, start=1):
        files[f"live_{index}"] = (f"live{index}.jpg", frame, "image/jpeg")
    return session.client.post(f"/v1/verify/{sid}/photos", files=files, headers=session.headers)


def png_header(width, height):
    def chunk(kind, payload):
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\x00" * 10)) + chunk(b"IEND", b"")


def real_image(width=64, height=48, kind="PNG", colour=(120, 130, 140)):
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(out, kind)
    return out.getvalue()


def test_session_photo_consent_table(alice, make_pdf, matcher):
    application_id = submit(alice, make_pdf)
    plain = alice.post("/v1/verify/session", {"application_id": application_id, "consent": True})
    assert plain.status_code == 200 and plain.json()["photo"]["enabled"] is False
    assert plain.json()["photo"]["consent_text"] == PHOTO_CONSENT_TEXT
    agreed = begin_with(alice, application_id, True).json()
    assert agreed["photo"]["enabled"] is True
    matcher.ready = False
    off = begin_with(alice, application_id, True).json()
    assert off["photo"]["enabled"] is False
    assert alice.post("/v1/verify/session", {"application_id": application_id, "consent": True, "photo_consent": "yes"}).status_code == 400
    assert alice.post("/v1/verify/session", {"application_id": application_id, "consent": False, "photo_consent": True}).status_code == 400


def test_consent_text_is_plain_and_complete():
    assert not FORBIDDEN.search(PHOTO_CONSENT_TEXT)
    lowered = PHOTO_CONSENT_TEXT.lower()
    for phrase in ("id photo", "camera frames", "server", "thrown away", "person", "can be wrong"):
        assert phrase in lowered


def test_photos_happy_path_and_candidate_sees_only_receipt(alice, recruiter, make_pdf, matcher):
    application_id, opened = open_photo_session(alice, make_pdf)
    sid = opened["session_id"]
    response = send_photos(alice, sid, live=(LIVE_BYTES, LIVE_BYTES + b"2"))
    assert response.status_code == 200 and response.json() == RECEIPT
    assert matcher.calls == [(ID_BYTES, [LIVE_BYTES, LIVE_BYTES + b"2"])]
    view = alice.get(f"/v1/verify/session/{sid}").json()
    for body in (opened, response.json(), view):
        assert not (keys_of(body) & (CANDIDATE_BANNED | {"similarity", "threshold"}))
    assert set(opened["photo"]) == {"enabled", "consent_text"}
    seen = recruiter.get(f"/v1/verify/application/{application_id}").json()
    assert set(seen) == RESULT_KEYS and seen["status"] == "partial"
    assert set(seen["photo"]) == PHOTO_KEYS
    assert seen["photo"] == {"state": "match", "similarity": 0.61, "threshold": 0.363, "model": "sface-2021dec", "frames_checked": 2, "client_images": True}
    assert seen["voice"]["state"] == "missing" and seen["advisory"] == "none"
    for text in [seen["summary"], *seen["notes"]]:
        assert not FORBIDDEN.search(text), text
    joined = " ".join(seen["notes"]).lower()
    assert "poor light" in joined and "look alike" in joined and "held up" in joined and "not measured" in joined


def test_photos_in_any_order_then_complete(alice, recruiter, make_pdf, matcher):
    application_id, opened = open_photo_session(alice, make_pdf)
    sid = opened["session_id"]
    assert send_photos(alice, sid).status_code == 200
    assert alice.post(f"/v1/verify/{sid}/face", face_body(opened)).status_code == 200
    from tests_identity.test_routes import send_voice

    assert send_voice(alice, sid, transcript=opened["voice"]["sentence"]).status_code == 200
    seen = recruiter.get(f"/v1/verify/application/{application_id}").json()
    assert seen["status"] == "complete" and seen["photo"]["state"] == "match"
    assert alice.get(f"/v1/verify/session/{sid}").json()["status"] == "complete"


def test_repeat_is_409_and_expired_is_410(alice, make_pdf, matcher, monkeypatch):
    _, opened = open_photo_session(alice, make_pdf)
    sid = opened["session_id"]
    assert send_photos(alice, sid).status_code == 200
    assert send_photos(alice, sid).status_code == 409
    _, other = open_photo_session(alice, make_pdf)
    real = IDENTITY.clock
    monkeypatch.setattr(IDENTITY, "clock", lambda: real() + 700)
    assert send_photos(alice, other["session_id"]).status_code == 410


def test_no_consent_is_400_and_does_not_use_the_part(alice, make_pdf, matcher):
    _, opened = open_photo_session(alice, make_pdf, photo_consent=False)
    refused = send_photos(alice, opened["session_id"])
    assert refused.status_code == 400 and not FORBIDDEN.search(refused.json()["detail"])
    assert matcher.calls == []


def test_ownership_roles_and_unknown_session(alice, bob, recruiter, make_pdf, matcher):
    _, opened = open_photo_session(alice, make_pdf)
    sid = opened["session_id"]
    assert send_photos(bob, sid).status_code == 404
    assert send_photos(recruiter, sid).status_code == 403
    assert send_photos(alice, "does-not-exist").status_code == 404


def test_size_limits(alice, make_pdf, matcher):
    _, opened = open_photo_session(alice, make_pdf)
    sid = opened["session_id"]
    assert send_photos(alice, sid, id_photo=b"x" * (1536 * 1024 + 1)).status_code == 413
    assert send_photos(alice, sid, live=(b"x" * (700 * 1024 + 1),)).status_code == 413
    assert send_photos(alice, sid, live=(LIVE_BYTES, b"x" * (700 * 1024 + 1))).status_code == 413
    assert send_photos(alice, sid, id_photo=b"x" * (4 * 1024 * 1024 + 10)).status_code == 413
    assert matcher.calls == []
    assert send_photos(alice, sid, id_photo=b"x" * 1536 * 1024, live=(b"y" * 700 * 1024, b"z" * 700 * 1024)).status_code == 200


def test_body_cap_checked_before_parsing(alice, make_pdf, matcher):
    _, opened = open_photo_session(alice, make_pdf)
    sid = opened["session_id"]

    def chunks():
        for _ in range(50):
            yield b"y" * 100_000

    response = alice.client.post(f"/v1/verify/{sid}/photos", content=chunks(), headers=alice.headers)
    assert response.status_code == 413
    big = b"x" * (4 * 1024 * 1024 + 10)
    assert alice.client.post(f"/v1/verify/{sid}/photos", content=big, headers=alice.headers).status_code == 413


def test_missing_fields_and_wrong_type_are_400(alice, make_pdf, matcher):
    _, opened = open_photo_session(alice, make_pdf)
    sid = opened["session_id"]
    assert send_photos(alice, sid, id_name="other").status_code == 400
    assert send_photos(alice, sid, live=()).status_code == 400
    wrong = alice.client.post(f"/v1/verify/{sid}/photos", content=b"abc", headers={**alice.headers, "Content-Type": "text/plain"})
    assert wrong.status_code == 400
    files = {name: (name, b"data", "image/jpeg") for name in ("id_photo", "live_1", "live_2", "live_3")}
    assert alice.client.post(f"/v1/verify/{sid}/photos", files=files, headers=alice.headers).status_code == 400
    assert matcher.calls == []
    assert send_photos(alice, sid).status_code == 200


def test_unreadable_image_is_400_and_keeps_the_part(alice, make_pdf, matcher):
    _, opened = open_photo_session(alice, make_pdf)
    sid = opened["session_id"]
    bad = send_photos(alice, sid, id_photo=b"unreadable")
    assert bad.status_code == 400 and not FORBIDDEN.search(bad.json()["detail"])
    assert send_photos(alice, sid).status_code == 200


def test_capabilities_for_candidates_and_status_for_staff(alice, recruiter, admin, matcher):
    assert alice.get("/v1/verify/capabilities").json() == {"face_match_available": True}
    matcher.ready = False
    assert alice.get("/v1/verify/capabilities").json() == {"face_match_available": False}
    assert recruiter.get("/v1/verify/capabilities").status_code == 403
    for who in (recruiter, admin):
        body = who.get("/v1/verify/status").json()
        assert body["face_match_available"] is False and body["face_match_model"] == "sface-2021dec"
    matcher.ready = True
    assert recruiter.get("/v1/verify/status").json()["face_match_available"] is True
    assert alice.get("/v1/verify/status").status_code == 403


@pytest.mark.parametrize(
    "state,advisory",
    [
        ("match", "none"),
        ("no_match", "ask_for_live_check"),
        ("no_face_in_id", "none"),
        ("no_face_live", "none"),
        ("several_faces", "none"),
        ("not_available", "none"),
    ],
)
def test_advisory_only_for_no_match(state, advisory):
    fake = FakeMatcher(state=state, similarity=0.1 if state in {"match", "no_match"} else None)
    service, _, audit = build(matcher=fake)
    session = consented(service)
    service.record_photos(session["session_id"], ID_BYTES, [LIVE_BYTES])
    result = service.result("app-1")
    assert result["advisory"] == advisory and result["status"] == "partial"
    assert result["photo"]["state"] == state and set(result["photo"]) == PHOTO_KEYS
    assert result["photo"]["similarity"] is None or state in {"match", "no_match"}
    assert result["completed_at"] is None
    assert audit.events == [("identity_photo_checked", "alice", "app-1")]
    assert service.summary("app-1")["identity_advisory"] == (advisory if advisory != "none" else None)
    for text in [result["summary"], *result["notes"]]:
        assert text and not FORBIDDEN.search(text), text


def test_negative_similarity_note_has_no_hyphen():
    service, _, _ = build(matcher=FakeMatcher(state="no_match", similarity=-0.2))
    session = consented(service)
    service.record_photos(session["session_id"], ID_BYTES, [LIVE_BYTES])
    assert all(not FORBIDDEN.search(note) for note in service.result("app-1")["notes"])


def test_unavailable_matcher_records_not_available_without_advisory():
    for fake in (None, FakeMatcher(ready=False)):
        service, _, _ = build(matcher=fake)
        session = consented(service)
        assert session["photo"]["enabled"] is False
        service.record_photos(session["session_id"], ID_BYTES, [LIVE_BYTES])
        result = service.result("app-1")
        assert result["photo"]["state"] == "not_available" and result["advisory"] == "none"
        assert result["photo"]["similarity"] is None


def test_matcher_that_raises_or_lies_is_not_available():
    class Broken:
        def available(self):
            return True

        def compare(self, a, b):
            raise RuntimeError("boom")

    class Liar(Broken):
        def compare(self, a, b):
            return {"state": "definitely_same", "similarity": 9}

    for fake in (Broken(), Liar()):
        service, _, _ = build(matcher=fake)
        session = service.create_session("app-1", "alice", True, True)
        service.record_photos(session["session_id"], ID_BYTES, [LIVE_BYTES])
        assert service.result("app-1")["photo"]["state"] == "not_available"


def test_service_refuses_without_consent_and_repeat():
    service, _, _ = build(matcher=FakeMatcher())
    session = start(service)
    with pytest.raises(IdentityError) as error:
        service.record_photos(session["session_id"], ID_BYTES, [LIVE_BYTES])
    assert error.value.status == 400
    session = service.create_session("app-1", "alice", True, True)
    service.record_photos(session["session_id"], ID_BYTES, [LIVE_BYTES])
    with pytest.raises(IdentityError) as error:
        service.record_photos(session["session_id"], ID_BYTES, [LIVE_BYTES])
    assert error.value.status == 409


def test_photo_later_does_not_reset_completion_or_repeat_audit():
    service, clock, audit = build(matcher=FakeMatcher())
    session = service.create_session("app-1", "alice", True, True)
    sid = session["session_id"]
    service.record_face(sid, good_face(session))
    service.record_voice(sid, HUMAN, None)
    stamp = service.result("app-1")["completed_at"]
    clock.advance(30)
    service.record_photos(sid, ID_BYTES, [LIVE_BYTES])
    result = service.result("app-1")
    assert result["status"] == "complete" and result["completed_at"] == stamp
    assert [event[0] for event in audit.events] == ["identity_check_completed", "identity_photo_checked"]


def test_two_threads_same_photos_part_one_succeeds():
    service, _, _ = build(matcher=FakeMatcher())
    session = service.create_session("app-1", "alice", True, True)
    barrier = threading.Barrier(4)
    outcomes: list[int] = []
    lock = threading.Lock()

    def work():
        barrier.wait()
        try:
            service.record_photos(session["session_id"], ID_BYTES, [LIVE_BYTES])
            code = 200
        except IdentityError as error:
            code = error.status
        with lock:
            outcomes.append(code)

    threads = [threading.Thread(target=work) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == [200, 409, 409, 409]


def test_no_media_reaches_any_store(alice, make_pdf, matcher, monkeypatch, tmp_path):
    results, sessions = Recorder(), Recorder()
    monkeypatch.setattr(IDENTITY, "results", results)
    monkeypatch.setattr(IDENTITY, "sessions", sessions)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    _, opened = open_photo_session(alice, make_pdf)
    marker = b"zebraquartz-photo-marker-bytes"
    assert send_photos(alice, opened["session_id"], id_photo=marker * 20, live=(marker * 10,)).status_code == 200
    stored = json.dumps(results.written) + json.dumps(sessions.written)
    assert "zebraquartz" not in stored
    assert all(isinstance(item, dict) for item in results.written + sessions.written)
    assert list(tmp_path.iterdir()) == []


def test_advisory_never_changes_the_decision(alice, recruiter, make_pdf, monkeypatch):
    monkeypatch.setattr(IDENTITY, "_matcher", FakeMatcher(state="no_match", similarity=0.05))
    application_id, opened = open_photo_session(alice, make_pdf)
    before = stored_decision(application_id).model_dump(mode="json")
    assert send_photos(alice, opened["session_id"]).status_code == 200
    seen = recruiter.get(f"/v1/verify/application/{application_id}").json()
    assert seen["advisory"] == "ask_for_live_check"
    assert stored_decision(application_id).model_dump(mode="json") == before
    assert STORE.get_decision(application_id).score == before["score"]
    row = next(item for item in recruiter.get("/v1/decisions?limit=1000").json() if item["application_id"] == application_id)
    assert row["identity_check"] == "partial" and row["identity_advisory"] == "ask_for_live_check"


def test_photo_strings_are_plain(alice, make_pdf, matcher):
    _, opened = open_photo_session(alice, make_pdf, photo_consent=False)
    sid = opened["session_id"]
    texts = [opened["photo"]["consent_text"], send_photos(alice, sid).json()["detail"], send_photos(alice, sid, id_photo=b"unreadable").json()["detail"]]
    _, again = open_photo_session(alice, make_pdf)
    texts.append(send_photos(alice, again["session_id"]).json()["message"])
    texts.append(send_photos(alice, again["session_id"]).json()["detail"])
    texts.append(send_photos(alice, "nope").json()["detail"])
    for text in texts:
        assert text and not FORBIDDEN.search(text), text


def test_header_guard_rejects_bombs_and_wrong_types():
    assert readable_header(real_image()) is True
    assert readable_header(real_image(kind="JPEG")) is True
    assert readable_header(png_header(40000, 40000)) is False
    assert readable_header(png_header(5001, 10)) is False
    assert readable_header(png_header(4000, 4000)) is False
    assert readable_header(real_image(kind="GIF")) is False
    assert readable_header(real_image(kind="BMP")) is False
    assert readable_header(b"") is False and readable_header(b"not an image") is False and readable_header(None) is False


def test_bomb_is_rejected_before_any_decode(tmp_path, monkeypatch):
    (tmp_path / "yunet.onnx").write_bytes(b"x")
    (tmp_path / "sface.onnx").write_bytes(b"x")

    class Trap:
        def __getattr__(self, name):
            raise AssertionError("decoder was reached")

    monkeypatch.setattr(facematch, "load_cv2", lambda: Trap())
    adapter = OpenCvFaceMatcher(tmp_path)
    for blob in (png_header(40000, 40000), b"junk"):
        result = adapter.compare(blob, [real_image()])
        assert result["state"] == "unreadable"
        result = adapter.compare(real_image(), [blob])
        assert result["state"] == "unreadable"


def test_adapter_is_not_available_without_models(tmp_path, monkeypatch):
    adapter = OpenCvFaceMatcher(tmp_path)
    assert adapter.available() is False
    result = adapter.compare(real_image(), [real_image()])
    assert result == {"state": "not_available", "similarity": None, "threshold": THRESHOLDS["cosine"], "model": "sface-2021dec", "frames_checked": 0}
    monkeypatch.setattr(facematch, "load_cv2", lambda: None)
    assert OpenCvFaceMatcher().available() is False
    assert OpenCvFaceMatcher().compare(real_image(), [real_image()])["state"] == "not_available"


def test_adapter_never_raises_on_internal_failure(tmp_path, monkeypatch):
    (tmp_path / "yunet.onnx").write_bytes(b"x")
    (tmp_path / "sface.onnx").write_bytes(b"x")
    fake = types.SimpleNamespace()
    monkeypatch.setattr(facematch, "load_cv2", lambda: fake)
    adapter = OpenCvFaceMatcher(tmp_path)
    assert adapter.compare(real_image(), [real_image()])["state"] == "not_available"


def test_model_dir_follows_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("FIREWALL_FACE_MODEL_DIR", str(tmp_path))
    assert facematch.model_dir() == tmp_path
    monkeypatch.delenv("FIREWALL_FACE_MODEL_DIR")
    assert facematch.model_dir().parts[-2:] == (".state", "face")


def models_ready():
    return OpenCvFaceMatcher().available()


@pytest.mark.skipif(not models_ready(), reason="face models or opencv are not installed")
def test_flat_images_have_no_face_with_real_models():
    adapter = OpenCvFaceMatcher()
    flat = real_image(320, 240, "PNG", (200, 200, 200))
    first = adapter.compare(flat, [flat])
    assert first["state"] == "no_face_in_id" and first["similarity"] is None and first["frames_checked"] == 0
    assert first["model"] == "sface-2021dec" and first["threshold"] == 0.363


def test_user_facing_code_has_no_comments_or_docstrings():
    import pathlib

    root = pathlib.Path(facematch.__file__).parent
    text = (root / "facematch.py").read_text()
    assert '"""' not in text and not re.search(r"^\s*#", text, re.M) and not re.search(r"\S\s+#\s", text)
    assert "requests" not in text and "urllib" not in text and "socket" not in text
