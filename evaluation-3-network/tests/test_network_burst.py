from firewall.config import Config
from firewall.engine import evaluate
from firewall.models import Project, Route
from firewall.store import InMemoryApplicationStore


def codes(decision):
    return {reason.code for reason in decision.reasons}


def test_campus_burst_of_distinct_students_is_not_escalated(application_factory, job_factory):
    store = InMemoryApplicationStore()
    final = None
    for index in range(15):
        final = evaluate(
            application_factory(
                application_id=f"student-{index}",
                name=f"Student {index}",
                email=f"student{index}@uni.edu",
                phone=f"98000000{index:02d}",
                device_id=f"device-{index}",
                ip="10.0.0.1",
                submitted_at=1_767_225_600 + index * 2,
                projects=[Project(name=f"P{index}", description=f"Unique capstone {index} about topic {index * 7}")],
            ),
            job_factory(),
            store,
            Config(),
        )
    assert final is not None
    assert "NETWORK_BURST" in codes(final)
    assert "VELOCITY_HIGH" not in codes(final)
    assert final.route == Route.PASS_TO_ATS


def test_one_identity_rotating_devices_still_escalates(application_factory, job_factory):
    store = InMemoryApplicationStore()
    final = None
    for index in range(12):
        final = evaluate(
            application_factory(
                application_id=f"rotate-{index}",
                job_id=f"job-{index}",
                device_id=f"rot-{index}",
                ip=f"203.0.113.{index}",
                submitted_at=1_767_225_600 + index * 3,
                projects=[Project(name="Proj", description=f"Rewritten description variant {index} of my work")],
            ),
            job_factory(),
            store,
            Config(),
        )
    assert final is not None
    assert "VELOCITY_HIGH" in codes(final)
    assert final.route == Route.MANUAL_REVIEW


def test_network_burst_with_bot_behaviour_is_still_caught(application_factory, job_factory):
    store = InMemoryApplicationStore()
    final = None
    for index in range(15):
        final = evaluate(
            application_factory(
                application_id=f"farm-{index}",
                name=f"Farm {index}",
                email=f"farm{index}@example.com",
                phone=f"97000000{index:02d}",
                device_id=f"farm-device-{index}",
                ip="198.51.100.7",
                session_seconds=4,
                paste_char_ratio=0.99,
                submitted_at=1_767_225_600 + index,
                projects=[Project(name=f"P{index}", description=f"Distinct automated text number {index} for farm {index * 11}")],
            ),
            job_factory(),
            store,
            Config(),
        )
    assert final is not None
    assert {"NETWORK_BURST", "FAST_SUBMIT", "PASTE_BULK"} <= codes(final)
    assert final.route != Route.PASS_TO_ATS
