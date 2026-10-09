from __future__ import annotations

import random
import secrets
import threading
import time
from collections.abc import Callable
from typing import Any

from firewall.identity import face as face_check
from firewall.identity import transcript as spoken
from firewall.identity.asr import asr_available, default_adapter
from firewall.identity.challenge import CONSENT_TEXT, pick_sentence, pick_steps, public_steps
from firewall.identity.errors import IdentityError
from firewall.identity.voice import MODEL_NAME, REJECT_CODES, MESSAGES, analyze_wav, decode_for_asr

SESSION_SECONDS = 600
SESSION_STORE_SECONDS = 1800
RESULT_TTL_SECONDS = 90 * 24 * 3600
MAX_VOICE_SECONDS = 12
APP_PER_DAY = 3
USER_PER_HOUR = 5
DAY = 86400
HOUR = 3600
VOICE_STATES = {"HUMAN": "human_like", "SYNTHETIC": "synthetic_suspected", "REPLAY": "replay_suspected"}
ASK = "ask_for_live_check"
STANDING_NOTES = (
    "The voice check is a rough rule based test and not a trained model. It can be wrong in both directions.",
    "The face check was measured in the candidate's browser so it can be forged.",
    "This note is advice only. It did not change the score or the route.",
)


def missing_face() -> dict[str, Any]:
    return {"state": "missing", "steps_done": 0, "steps_total": 0, "client_measured": True}


def missing_voice() -> dict[str, Any]:
    return {
        "state": "missing",
        "code_matched": None,
        "sentence_match": None,
        "transcript_source": None,
        "indicators": {},
        "model": MODEL_NAME,
        "is_real_model": False,
    }


def advisory_for(face: dict[str, Any], voice: dict[str, Any]) -> str:
    risky = (
        voice["state"] in {"synthetic_suspected", "replay_suspected"}
        or face["state"] in {"not_seen", "implausible"}
        or voice["code_matched"] is False
    )
    return ASK if risky else "none"


def notes_for(face: dict[str, Any], voice: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    state = face["state"]
    if state == "passed":
        notes.append(f"The candidate followed all {face['steps_total']} face prompts in the browser.")
    elif state == "steps_incomplete":
        notes.append(f"The candidate completed {face['steps_done']} of {face['steps_total']} face prompts.")
    elif state == "not_seen":
        notes.append("A face was not seen in enough of the camera frames.")
    elif state == "implausible":
        notes.append("The face report did not fit the prompts that were issued. It may be a forged report or a faulty camera.")
    else:
        notes.append("The face check was not done. This is not held against the candidate.")
    state = voice["state"]
    texts = {
        "human_like": "The rule based voice check found no machine like signs. In our own test it said the same about machine made voices, so this is not evidence of a live person.",
        "synthetic_suspected": "The voice recording was very even which can happen with synthetic speech.",
        "replay_suspected": "The voice recording started abruptly and stayed even which can happen with played back audio.",
        "unclear": "The voice recording did not give clear evidence either way.",
        "too_short": "The voice recording was too short to check.",
        "missing": "The voice check was not done. This is not held against the candidate.",
    }
    notes.append(texts[state])
    if voice["code_matched"] is True:
        notes.append("The spoken code matched the code that was issued.")
    elif voice["code_matched"] is False:
        notes.append("The spoken code did not match the code that was issued. Speech to text makes mistakes so this is not proof.")
    if voice["sentence_match"] is not None:
        notes.append(f"About {round(voice['sentence_match'] * 100)} percent of the issued sentence was heard in the right order.")
    if voice["transcript_source"] == "browser":
        notes.append("The transcript came from the candidate's browser and can be faked.")
    elif voice["transcript_source"] == "server":
        notes.append("The transcript was made on the server.")
    return notes + list(STANDING_NOTES)


def summary_for(status: str, advisory: str) -> str:
    if advisory == ASK:
        return "Something in the identity check looked unusual. Consider a short live check with the candidate. This is advice only."
    if status == "complete":
        return "The identity check was finished and the simple checks found nothing unusual. These checks are weak, so a person should still decide."
    return "Part of the identity check was finished and the simple checks found nothing unusual. These checks are weak, so a person should still decide."


class IdentityService:
    def __init__(
        self,
        *,
        results: Any,
        sessions: Any,
        counter: Any,
        clock: Callable[[], float] = time.time,
        rng: random.Random | None = None,
        asr: Any = "auto",
        audit: Callable[[], Any] | None = None,
    ) -> None:
        self.results = results
        self.sessions = sessions
        self.counter = counter
        self.clock = clock
        self.rng = rng or random.SystemRandom()
        self._asr = asr
        self.audit = audit
        self._lock = threading.Lock()

    def adapter(self) -> Any:
        if self._asr == "auto":
            found = default_adapter()
            if found is None:
                return None
            self._asr = found
        return self._asr

    def asr_ready(self) -> bool:
        if self._asr == "auto":
            return asr_available()
        return self._asr is not None

    def status(self) -> dict[str, Any]:
        return {
            "voice_model": MODEL_NAME,
            "is_real_model": False,
            "asr_available": self.asr_ready(),
            "face": "measured in the browser",
        }

    @staticmethod
    def _key(session_id: str) -> str:
        return f"session:{session_id}"

    @staticmethod
    def result_key(application_id: str) -> str:
        return f"identity:{application_id}"

    def create_session(self, application_id: str, username: str, consent: bool) -> dict[str, Any]:
        if consent is not True:
            raise IdentityError(400, "Please agree before you start the check.")
        app_count = self.counter.hit(f"identity:app:{application_id}", DAY)
        user_count = self.counter.hit(f"identity:user:{username}", HOUR)
        if app_count > APP_PER_DAY or user_count > USER_PER_HOUR:
            raise IdentityError(429, "You have started too many checks. Please try again later.")
        now = self.clock()
        steps = pick_steps(self.rng)
        sentence, digits = pick_sentence(self.rng)
        session_id = secrets.token_urlsafe(24)
        record = {
            "id": session_id,
            "application_id": application_id,
            "username": username,
            "created_at": now,
            "expires_at": int(now + SESSION_SECONDS),
            "face_ids": [step["id"] for step in steps],
            "sentence": sentence,
            "digits": digits,
            "face": None,
            "voice": None,
        }
        self.sessions.set(self._key(session_id), record, SESSION_STORE_SECONDS)
        return {
            "session_id": session_id,
            "expires_at": record["expires_at"],
            "face": {"steps": steps},
            "voice": {"sentence": sentence, "max_seconds": MAX_VOICE_SECONDS},
            "consent_text": CONSENT_TEXT,
        }

    def load(self, session_id: str) -> dict[str, Any]:
        record = self.sessions.get(self._key(session_id)) if isinstance(session_id, str) and 0 < len(session_id) <= 64 else None
        if not isinstance(record, dict):
            raise IdentityError(404, "We could not find that check.")
        return record

    def state_of(self, record: dict[str, Any]) -> str:
        if record["face"] is not None and record["voice"] is not None:
            return "complete"
        if self.clock() >= record["expires_at"]:
            return "expired"
        if record["face"] is not None:
            return "face_done"
        if record["voice"] is not None:
            return "voice_done"
        return "created"

    def session_view(self, session_id: str) -> dict[str, Any]:
        record = self.load(session_id)
        state = self.state_of(record)
        return {
            "status": state,
            "face_received": record["face"] is not None,
            "voice_received": record["voice"] is not None,
            "expires_at": record["expires_at"],
            "complete": state == "complete",
        }

    def _open(self, session_id: str, part: str) -> dict[str, Any]:
        record = self.load(session_id)
        if self.state_of(record) == "expired":
            raise IdentityError(410, "This check has run out of time. Please start again.")
        if record[part] is not None:
            raise IdentityError(409, "That part was already received.")
        return record

    def _commit(self, session_id: str, part: str, value: dict[str, Any]) -> None:
        with self._lock:
            record = self._open(session_id, part)
            if self.counter.hit(f"identity:claim:{session_id}:{part}", SESSION_STORE_SECONDS) > 1:
                raise IdentityError(409, "That part was already received.")
            record[part] = value
            self.sessions.set(self._key(session_id), record, SESSION_STORE_SECONDS)
            self._publish(record)

    def record_face(self, session_id: str, report: Any) -> None:
        record = self._open(session_id, "face")
        elapsed = self.clock() - float(record["created_at"])
        outcome = face_check.judge(report, list(record["face_ids"]), elapsed)
        self._commit(session_id, "face", outcome)

    def record_voice(self, session_id: str, wav_bytes: bytes, client_transcript: str | None = None) -> None:
        record = self._open(session_id, "voice")
        text = spoken.clean(client_transcript) if isinstance(client_transcript, str) else ""
        if len(text) > spoken.MAX_TRANSCRIPT_CHARS:
            raise IdentityError(400, "Please keep the transcript under 300 characters.")
        analysis = analyze_wav(wav_bytes)
        if analysis["reason_code"] in REJECT_CODES:
            raise IdentityError(400, MESSAGES[analysis["reason_code"]] + " Please try again.")
        heard: str | None = None
        source: str | None = None
        adapter = self.adapter()
        if adapter is not None:
            source = "server"
            decoded = decode_for_asr(wav_bytes)
            try:
                heard = adapter.transcribe(*decoded) if decoded is not None else None
            except Exception:
                heard = None
            if heard is None:
                source = None
        elif text:
            heard, source = text, "browser"
        match_ratio: float | None = None
        code_matched: bool | None = None
        if heard is not None and heard.strip():
            match_ratio, code_matched = spoken.match(record["sentence"], list(record["digits"]), heard)
        else:
            source = None
        if analysis["verdict"] == "UNKNOWN" and analysis["reason_code"] == "too_short":
            state = "too_short"
        else:
            state = VOICE_STATES.get(analysis["verdict"], "unclear")
        outcome = {
            "state": state,
            "code_matched": code_matched,
            "sentence_match": match_ratio,
            "transcript_source": source,
            "indicators": analysis["indicators"],
            "model": MODEL_NAME,
            "is_real_model": False,
        }
        self._commit(session_id, "voice", outcome)

    def _publish(self, record: dict[str, Any]) -> None:
        application_id = record["application_id"]
        previous = self.result(application_id) or {}
        face = record["face"] or previous.get("face") or missing_face()
        voice = record["voice"] or previous.get("voice") or missing_voice()
        complete = face["state"] != "missing" and voice["state"] != "missing"
        status = "complete" if complete else "partial"
        advisory = advisory_for(face, voice)
        result = {
            "status": status,
            "face": face,
            "voice": voice,
            "advisory": advisory,
            "summary": summary_for(status, advisory),
            "notes": notes_for(face, voice),
            "completed_at": self.clock() if complete else None,
        }
        self.results.set(self.result_key(application_id), result, RESULT_TTL_SECONDS)
        if complete and self.audit is not None:
            try:
                self.audit().append("identity_check_completed", record["username"], application_id)
            except Exception:
                pass

    def result(self, application_id: str) -> dict[str, Any] | None:
        stored = self.results.get(self.result_key(application_id))
        return stored if isinstance(stored, dict) else None

    def summary(self, application_id: str) -> dict[str, str | None]:
        stored = self.result(application_id)
        if stored is None:
            return {"identity_check": None, "identity_advisory": None}
        return {
            "identity_check": stored.get("status"),
            "identity_advisory": ASK if stored.get("advisory") == ASK else None,
        }

    def is_complete(self, application_id: str) -> bool:
        stored = self.result(application_id)
        return bool(stored) and stored.get("status") == "complete"
