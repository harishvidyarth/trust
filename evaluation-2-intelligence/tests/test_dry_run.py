from fastapi.testclient import TestClient

from firewall.api import MOCK_ATS, STORE, app
from firewall.config import Config
from firewall.engine import evaluate
from firewall.store import InMemoryApplicationStore

client = TestClient(app)
SAMPLE = "firewall/resume/samples/honest_ai_polished.pdf"
JOB = '{"must_have_skills":["python"],"nice_to_have":[],"min_years":0}'


def upload(application_id, dry_run):
    with open(SAMPLE, "rb") as handle:
        return client.post(
            "/v1/applications/upload",
            files={"file": ("resume.pdf", handle)},
            data={
                "job_json": JOB,
                "device_id": "dry-run-device",
                "application_id": application_id,
                "session_seconds": "140",
                "dry_run": "true" if dry_run else "false",
            },
        ).json()


def codes(decision):
    return {reason["code"] for reason in decision["reasons"]}


def test_dry_run_is_not_remembered_so_repeats_stay_clean():
    STORE.clear()
    MOCK_ATS.clear()
    first = upload("dry-1", True)
    second = upload("dry-2", True)
    assert codes(first) == codes(second)
    assert not codes(second) & {"DUP_SAME_JOB", "DUP_EMAIL", "DUP_PHONE"}
    assert client.get("/v1/stats").json()["total_received"] == 0
    assert MOCK_ATS.applications() == ()
    STORE.clear()


def test_real_submission_is_remembered_and_repeat_is_flagged():
    STORE.clear()
    MOCK_ATS.clear()
    upload("real-1", False)
    repeat = upload("real-2", False)
    assert {"DUP_SAME_JOB", "DUP_EMAIL", "DUP_PHONE"} <= codes(repeat)
    STORE.clear()
    MOCK_ATS.clear()


def test_dry_run_still_detects_duplicates_of_stored_applications():
    STORE.clear()
    MOCK_ATS.clear()
    upload("stored-1", False)
    probe = upload("probe-1", True)
    assert "DUP_SAME_JOB" in codes(probe)
    assert client.get("/v1/stats").json()["total_received"] == 1
    STORE.clear()
    MOCK_ATS.clear()


def test_engine_persist_flag_controls_saving(application_factory, job_factory):
    store = InMemoryApplicationStore()
    evaluate(application_factory(), job_factory(), store, Config(), persist=False)
    assert store.applications() == ()
    evaluate(application_factory(application_id="kept"), job_factory(), store, Config())
    assert len(store.applications()) == 1
