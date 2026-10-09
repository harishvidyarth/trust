from __future__ import annotations

import json
import pathlib
import random
import re
import threading

import pytest

from firewall.identity import IdentityError, IdentityService
from firewall.identity.challenge import DIGIT_WORDS, FACE_STEPS, SENTENCES
from firewall.identity.transcript import match
from tests_identity.audio import noise, sine, speech_like, wav_bytes
from tests_identity.fakes import Audit, Clock, Counter, FakeAsr, Recorder

FORBIDDEN = re.compile(r"[-()\[\]{};:]")
HUMAN = wav_bytes(speech_like())
SYNTHETIC = wav_bytes(sine(3.0))


def build(asr=None, seed=1, results=None, matcher=None):
    clock = Clock()
    audit = Audit()
    service = IdentityService(
        results=results or Recorder(),
        sessions=Recorder(),
        counter=Counter(clock),
        clock=clock,
        rng=random.Random(seed),
        asr=asr,
        matcher=matcher,
        audit=audit,
    )
    return service, clock, audit


def start(service, application_id="app-1", username="alice"):
    return service.create_session(application_id, username, True)


def good_face(session, ms=700, duration=3000, **override):
    report = {
        "steps": [{"id": step["id"], "passed": True, "ms": ms} for step in session["face"]["steps"]],
        "frames_with_face": 90,
        "total_frames": 100,
        "duration_ms": duration,
        "model": "mediapipe-facemesh",
    }
    report.update(override)
    return report


def spoken_for(session, extra=""):
    return session["voice"]["sentence"] + extra


def status_of(service, application_id="app-1"):
    return service.result(application_id)


def test_consent_is_required():
    service, _, _ = build()
    for value in (False, None, "true", 1):
        with pytest.raises(IdentityError) as error:
            service.create_session("app-1", "alice", value)
        assert error.value.status == 400


def test_session_shape_and_challenge():
    service, clock, _ = build()
    session = start(service)
    assert set(session) == {"session_id", "expires_at", "face", "voice", "consent_text", "photo"}
    assert len(session["session_id"]) >= 32
    assert session["expires_at"] == int(clock.now + 600)
    steps = session["face"]["steps"]
    assert len(steps) == 3 and len({step["id"] for step in steps}) == 3
    assert all(step["id"] in FACE_STEPS and step["label"] and step["hint"] for step in steps)
    assert session["voice"]["max_seconds"] == 12
    words = session["voice"]["sentence"].lower().replace(".", "").split()
    assert sum(1 for word in words if word in DIGIT_WORDS) == 3


def test_sessions_differ():
    service, _, _ = build(seed=3)
    seen = {start(service, f"app-{n}", f"user{n}")["voice"]["sentence"] for n in range(4)}
    assert len(seen) > 1
    ids = {start(service, f"x-{n}", f"u{n}")["session_id"] for n in range(4)}
    assert len(ids) == 4


def test_templates_are_plain_and_right_length():
    assert len(SENTENCES) == 8
    for template in SENTENCES:
        sentence = template.format(a="one", b="two", c="three")
        assert 8 <= len(sentence.rstrip(".").split()) <= 14, sentence
        assert not FORBIDDEN.search(sentence)
        bare = template.replace("{a}", "").replace("{b}", "").replace("{c}", "").lower()
        assert not any(word in bare.split() for word in DIGIT_WORDS)


def test_face_pass_then_state_machine():
    service, _, _ = build()
    session = start(service)
    sid = session["session_id"]
    assert service.session_view(sid)["status"] == "created"
    service.record_face(sid, good_face(session))
    view = service.session_view(sid)
    assert view["status"] == "face_done" and view["face_received"] and not view["voice_received"] and not view["complete"]
    service.record_voice(sid, HUMAN, spoken_for(session))
    view = service.session_view(sid)
    assert view["status"] == "complete" and view["complete"] is True


def test_voice_first_then_face():
    service, _, _ = build()
    session = start(service)
    sid = session["session_id"]
    service.record_voice(sid, HUMAN, "")
    assert service.session_view(sid)["status"] == "voice_done"
    assert status_of(service)["status"] == "partial"
    service.record_face(sid, good_face(session))
    assert status_of(service)["status"] == "complete"


def test_expiry_uses_injected_clock():
    service, clock, _ = build()
    session = start(service)
    sid = session["session_id"]
    clock.advance(601)
    assert service.session_view(sid)["status"] == "expired"
    with pytest.raises(IdentityError) as error:
        service.record_voice(sid, HUMAN, "")
    assert error.value.status == 410
    with pytest.raises(IdentityError) as error:
        service.record_face(sid, good_face(session))
    assert error.value.status == 410


def test_complete_session_stays_complete_after_expiry():
    service, clock, _ = build()
    session = start(service)
    service.record_face(session["session_id"], good_face(session))
    service.record_voice(session["session_id"], HUMAN, "")
    clock.advance(5000)
    assert service.session_view(session["session_id"])["status"] == "complete"


def test_unknown_session_is_404():
    service, _, _ = build()
    for value in ("nope", "", "x" * 500):
        with pytest.raises(IdentityError) as error:
            service.session_view(value)
        assert error.value.status == 404


def test_each_part_is_single_use():
    service, _, _ = build()
    session = start(service)
    sid = session["session_id"]
    service.record_face(sid, good_face(session))
    with pytest.raises(IdentityError) as error:
        service.record_face(sid, good_face(session))
    assert error.value.status == 409
    service.record_voice(sid, HUMAN, "")
    with pytest.raises(IdentityError) as error:
        service.record_voice(sid, HUMAN, "")
    assert error.value.status == 409


def test_rejected_input_does_not_use_up_the_part():
    service, _, _ = build()
    session = start(service)
    sid = session["session_id"]
    with pytest.raises(IdentityError) as error:
        service.record_voice(sid, b"garbage", "")
    assert error.value.status == 400
    with pytest.raises(IdentityError):
        service.record_face(sid, {"junk": 1})
    service.record_voice(sid, HUMAN, "")
    service.record_face(sid, good_face(session))


def test_session_limits_per_application_and_user():
    service, clock, _ = build()
    for _ in range(3):
        start(service, "app-1", "alice")
    with pytest.raises(IdentityError) as error:
        start(service, "app-1", "alice")
    assert error.value.status == 429
    clock.advance(86401)
    start(service, "app-1", "alice")
    service, clock, _ = build()
    for n in range(5):
        start(service, f"app-{n}", "bob")
    with pytest.raises(IdentityError) as error:
        start(service, "app-9", "bob")
    assert error.value.status == 429
    clock.advance(3601)
    start(service, "app-9", "bob")
    start(service, "other-app", "carol")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r, s: r.update(steps=list(reversed(r["steps"]))),
        lambda r, s: r.update(steps=r["steps"][:2]),
        lambda r, s: r["steps"].__setitem__(0, {"id": "wave", "passed": True, "ms": 700}),
        lambda r, s: r["steps"][0].update(ms=299),
        lambda r, s: r["steps"][1].update(ms=40001),
        lambda r, s: r.update(duration_ms=1999),
        lambda r, s: r.update(duration_ms=120001),
        lambda r, s: r.update(duration_ms=110_000),
        lambda r, s: r.update(duration_ms=9000),
    ],
)
def test_face_report_table_implausible(mutate):
    service, _, _ = build()
    session = start(service)
    report = good_face(session)
    mutate(report, session)
    service.record_face(session["session_id"], report)
    assert status_of(service)["face"]["state"] == "implausible"
    assert status_of(service)["advisory"] == "ask_for_live_check"


def test_face_report_cannot_be_longer_than_the_session_has_lasted():
    service, clock, _ = build()
    session = start(service)
    clock.advance(10)
    service.record_face(session["session_id"], good_face(session, duration=14_900))
    assert status_of(service)["face"]["state"] == "passed"
    service, clock, _ = build()
    session = start(service)
    clock.advance(10)
    service.record_face(session["session_id"], good_face(session, duration=15_100))
    assert status_of(service)["face"]["state"] == "implausible"


def test_too_few_face_frames_is_not_seen():
    for seen, total in ((59, 100), (0, 100), (0, 0), (5, 8)):
        service, _, _ = build()
        session = start(service)
        service.record_face(session["session_id"], good_face(session, frames_with_face=seen, total_frames=total))
        face = status_of(service)["face"]
        assert face["state"] == "not_seen" and face["client_measured"] is True
    service, _, _ = build()
    session = start(service)
    service.record_face(session["session_id"], good_face(session, frames_with_face=60, total_frames=100))
    assert status_of(service)["face"]["state"] == "passed"


def test_failed_steps_are_incomplete():
    service, _, _ = build()
    session = start(service)
    report = good_face(session)
    report["steps"][2]["passed"] = False
    service.record_face(session["session_id"], report)
    face = status_of(service)["face"]
    assert face == {"state": "steps_incomplete", "steps_done": 2, "steps_total": 3, "client_measured": True}
    assert status_of(service)["advisory"] == "none"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update(extra=1),
        lambda r: r.pop("model"),
        lambda r: r.update(model="other"),
        lambda r: r.update(frames_with_face=-1),
        lambda r: r.update(frames_with_face=101),
        lambda r: r.update(total_frames=10**9),
        lambda r: r.update(duration_ms=True),
        lambda r: r.update(duration_ms="3000"),
        lambda r: r.update(duration_ms=3000.5),
        lambda r: r.update(steps="blink"),
        lambda r: r.update(steps=[{"id": "blink"}] * 3),
        lambda r: r["steps"][0].update(extra="x"),
        lambda r: r["steps"][0].update(passed=1),
        lambda r: r["steps"][0].update(id="A" * 40),
        lambda r: r["steps"][0].update(ms=-5),
        lambda r: r.update(steps=r["steps"] * 4),
    ],
)
def test_forged_or_malformed_reports_are_refused(mutate):
    service, _, _ = build()
    session = start(service)
    report = good_face(session)
    mutate(report)
    with pytest.raises(IdentityError) as error:
        service.record_face(session["session_id"], report)
    assert error.value.status == 400
    assert status_of(service) is None
    assert service.session_view(session["session_id"])["face_received"] is False


@pytest.mark.parametrize("report", [None, [], "x", 5])
def test_non_object_reports_are_refused(report):
    service, _, _ = build()
    session = start(service)
    with pytest.raises(IdentityError):
        service.record_face(session["session_id"], report)


def test_transcript_matching_with_digit_words():
    sentence = "Please say the code one two three to confirm this application is mine."
    digits = ["one", "two", "three"]
    assert match(sentence, digits, sentence) == (1.0, True)
    assert match(sentence, digits, "please say the code 1 2 3 to confirm this application is mine")[1] is True
    assert match(sentence, digits, "the code is 123")[1] is True
    assert match(sentence, digits, sentence + " " + sentence)[1] is True
    assert match(sentence, digits, sentence.replace("two", "four"))[1] is False
    assert match(sentence, digits, "please say the code three two one to confirm this application is mine")[1] is False
    assert match(sentence, digits, sentence.replace("three", ""))[1] is False
    ratio, code = match(sentence, digits, "mine is application this confirm to three two one code the say please")
    assert code is False and ratio < 0.5
    ratio, _ = match(sentence, digits, "")
    assert ratio == 0.0
    partial, code = match(sentence, digits, "please say the code one two three")
    assert code is True and 0.4 < partial < 0.7


def test_client_transcript_is_used_without_asr_and_labelled_browser():
    service, _, _ = build(asr=None)
    session = start(service)
    service.record_voice(session["session_id"], HUMAN, spoken_for(session))
    voice = status_of(service)["voice"]
    assert voice["transcript_source"] == "browser" and voice["code_matched"] is True and voice["sentence_match"] == 1.0
    assert voice["is_real_model"] is False and voice["model"] == "heuristic-v1"


def test_wrong_client_code_asks_for_a_live_check():
    service, _, _ = build(asr=None)
    session = start(service)
    service.record_voice(session["session_id"], HUMAN, "hello there")
    result = status_of(service)
    assert result["voice"]["code_matched"] is False
    assert result["advisory"] == "ask_for_live_check"


def test_no_transcript_is_not_held_against_the_candidate():
    service, _, _ = build(asr=None)
    session = start(service)
    service.record_voice(session["session_id"], HUMAN, None)
    voice = status_of(service)["voice"]
    assert voice["code_matched"] is None and voice["sentence_match"] is None and voice["transcript_source"] is None
    assert status_of(service)["advisory"] == "none"


def test_server_side_asr_ignores_client_text():
    asr = FakeAsr()
    service, _, _ = build(asr=asr)
    session = start(service)
    asr.text = spoken_for(session)
    service.record_voice(session["session_id"], HUMAN, "totally different words")
    voice = status_of(service)["voice"]
    assert asr.calls == 1
    assert voice["transcript_source"] == "server" and voice["code_matched"] is True


def test_server_asr_mismatch_beats_client_text():
    asr = FakeAsr("nothing useful")
    service, _, _ = build(asr=asr)
    session = start(service)
    service.record_voice(session["session_id"], HUMAN, spoken_for(session))
    voice = status_of(service)["voice"]
    assert voice["transcript_source"] == "server" and voice["code_matched"] is False


def test_asr_failure_never_falls_back_to_client_text():
    service, _, _ = build(asr=FakeAsr(fail=True))
    session = start(service)
    service.record_voice(session["session_id"], HUMAN, spoken_for(session))
    voice = status_of(service)["voice"]
    assert voice["transcript_source"] is None and voice["code_matched"] is None


def test_long_client_transcript_is_refused():
    service, _, _ = build()
    session = start(service)
    with pytest.raises(IdentityError) as error:
        service.record_voice(session["session_id"], HUMAN, "a" * 301)
    assert error.value.status == 400


def test_voice_states():
    cases = [
        (HUMAN, "human_like"),
        (SYNTHETIC, "synthetic_suspected"),
        (wav_bytes(sine(0.05)), "too_short"),
        (wav_bytes(noise()), None),
    ]
    for data, expected in cases:
        service, _, _ = build()
        session = start(service)
        service.record_voice(session["session_id"], data, None)
        state = status_of(service)["voice"]["state"]
        if expected is not None:
            assert state == expected
        assert state in {"human_like", "synthetic_suspected", "replay_suspected", "unclear", "too_short"}


@pytest.mark.parametrize(
    "face_state, voice_data, text, advisory",
    [
        ("pass", HUMAN, "ok", "none"),
        ("pass", SYNTHETIC, "ok", "ask_for_live_check"),
        ("seen_no", HUMAN, "ok", "ask_for_live_check"),
        ("pass", HUMAN, "bad", "ask_for_live_check"),
        ("incomplete", HUMAN, "ok", "none"),
        ("pass", wav_bytes(sine(0.05)), "ok", "none"),
    ],
)
def test_advisory_table(face_state, voice_data, text, advisory):
    service, _, _ = build()
    session = start(service)
    report = good_face(session)
    if face_state == "seen_no":
        report["frames_with_face"] = 10
    if face_state == "incomplete":
        report["steps"][0]["passed"] = False
    service.record_face(session["session_id"], report)
    service.record_voice(session["session_id"], voice_data, spoken_for(session) if text == "ok" else "wrong words")
    assert status_of(service)["advisory"] == advisory


def test_missing_part_is_not_held_against_anyone():
    service, _, _ = build()
    session = start(service)
    service.record_face(session["session_id"], good_face(session))
    result = status_of(service)
    assert result["status"] == "partial" and result["advisory"] == "none"
    assert result["voice"]["state"] == "missing" and result["completed_at"] is None
    assert any("not held against" in note for note in result["notes"])


def test_stored_result_shape_and_plain_wording():
    service, _, audit = build()
    session = start(service)
    service.record_face(session["session_id"], good_face(session))
    service.record_voice(session["session_id"], HUMAN, spoken_for(session))
    result = status_of(service)
    assert set(result) == {"status", "face", "voice", "photo", "advisory", "summary", "notes", "completed_at"}
    assert set(result["face"]) == {"state", "steps_done", "steps_total", "client_measured"}
    assert set(result["voice"]) == {"state", "code_matched", "sentence_match", "transcript_source", "indicators", "model", "is_real_model"}
    assert result["status"] == "complete" and result["completed_at"] is not None
    for text in [result["summary"], *result["notes"]]:
        assert text and not FORBIDDEN.search(text), text
    joined = " ".join(result["notes"]).lower()
    assert "not a trained model" in joined and "forged" in joined and "advice only" in joined
    assert audit.events == [("identity_check_completed", "alice", "app-1")]


def test_later_session_replaces_only_its_own_part():
    service, _, _ = build()
    first = start(service)
    service.record_face(first["session_id"], good_face(first))
    service.record_voice(first["session_id"], HUMAN, spoken_for(first))
    second = start(service)
    service.record_voice(second["session_id"], SYNTHETIC, None)
    result = status_of(service)
    assert result["status"] == "complete" and result["face"]["state"] == "passed"
    assert result["voice"]["state"] == "synthetic_suspected"


def test_no_media_or_text_is_stored():
    results = Recorder()
    service, _, _ = build(results=results)
    session = start(service)
    sid = session["session_id"]
    secret = "zebraquartz marker words"
    service.record_face(sid, good_face(session))
    service.record_voice(sid, HUMAN, secret)
    written = json.dumps(results.written) + json.dumps(service.sessions.written)
    assert "zebraquartz" not in written
    assert "UklGR" not in written and "RIFF" not in written
    assert all(not isinstance(item, (bytes, bytearray)) for item in results.written)
    allowed = (int, float, str, bool, type(None), list, dict)
    assert all(isinstance(item, dict) for item in results.written)

    def walk(value):
        assert isinstance(value, allowed)
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        if isinstance(value, list):
            for item in value:
                walk(item)

    walk(results.written)
    assert len(json.dumps(results.written)) < 6000


@pytest.mark.parametrize("part", ["face", "voice"])
def test_two_threads_same_part_exactly_one_succeeds(part):
    service, _, _ = build()
    session = start(service)
    sid = session["session_id"]
    barrier = threading.Barrier(4)
    outcomes: list[int] = []
    lock = threading.Lock()

    def work():
        barrier.wait()
        try:
            if part == "face":
                service.record_face(sid, good_face(session))
            else:
                service.record_voice(sid, HUMAN, None)
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


def test_status_reports_honestly():
    service, _, _ = build(asr=None)
    assert service.status() == {"voice_model": "heuristic-v1", "is_real_model": False, "asr_available": False, "face": "measured in the browser", "face_match_available": False, "face_match_model": "sface-2021dec"}
    service, _, _ = build(asr=FakeAsr())
    assert service.status()["asr_available"] is True


def test_asr_adapter_does_not_download(monkeypatch, tmp_path):
    from firewall.identity import asr

    monkeypatch.delenv("FIREWALL_ASR_MODEL_DIR", raising=False)
    monkeypatch.setenv("FIREWALL_ASR_CACHE_DIR", str(tmp_path / "empty"))
    assert asr.model_location() is None and asr.asr_available() is False and asr.default_adapter() is None
    monkeypatch.setenv("FIREWALL_ASR_MODEL_DIR", str(tmp_path))
    assert asr.model_location() == str(tmp_path)
    monkeypatch.setenv("FIREWALL_ASR_MODEL_DIR", str(tmp_path / "missing"))
    assert asr.model_location() is None
    monkeypatch.delenv("FIREWALL_ASR_MODEL_DIR")
    snap = tmp_path / "cache" / asr.CACHE_FOLDER / "snapshots" / "abc"
    snap.mkdir(parents=True)
    monkeypatch.setenv("FIREWALL_ASR_CACHE_DIR", str(tmp_path / "cache"))
    assert asr.model_location() == "tiny.en"


def test_auto_adapter_is_not_loaded_just_to_report_status(monkeypatch):
    from firewall.identity import asr

    monkeypatch.setattr(asr, "_library_present", lambda: False)
    monkeypatch.setattr("firewall.identity.service.asr_available", asr.asr_available)
    service = IdentityService(results=Recorder(), sessions=Recorder(), counter=Counter(Clock()), asr="auto")
    assert service.status()["asr_available"] is False


def test_package_has_no_comments_docstrings_or_process_calls():
    root = pathlib.Path(__file__).resolve().parents[1] / "firewall"
    files = list((root / "identity").glob("*.py")) + [root / "identity_routes.py"]
    for path in files:
        text = path.read_text()
        assert '"""' not in text, path
        assert not re.search(r"^\s*#", text, re.M), path
        assert "subprocess" not in text and "os.system" not in text, path
