from firewall.config import Config
from firewall.engine import evaluate
from firewall.models import Project, Route
from firewall.store import InMemoryApplicationStore


def codes(decision):
    return {reason.code for reason in decision.reasons}


def run(application_factory, job_factory, device_for, count, spacing, config=None):
    store = InMemoryApplicationStore()
    decisions = []
    for index in range(count):
        decisions.append(
            evaluate(
                application_factory(
                    application_id=f"rot-{index}",
                    job_id=f"job-{index}",
                    device_id=device_for(index),
                    ip=f"203.0.113.{index}",
                    submitted_at=1_767_225_600 + index * spacing,
                    projects=[Project(name="Proj", description=f"Distinct write up number {index} covering topic {index * 13}")],
                ),
                job_factory(),
                store,
                config or Config(),
            )
        )
    return decisions


def test_slow_rotation_across_many_devices_is_flagged(application_factory, job_factory):
    decisions = run(application_factory, job_factory, lambda index: f"device-{index}", 10, 120)
    assert "IDENTITY_DEVICE_ROTATION" not in codes(decisions[2])
    flagged = [decision for decision in decisions if "IDENTITY_DEVICE_ROTATION" in codes(decision)]
    assert len(flagged) >= 6
    assert all(decision.route != Route.PASS_TO_ATS for decision in flagged)


def test_person_using_phone_and_laptop_is_not_flagged(application_factory, job_factory):
    decisions = run(application_factory, job_factory, lambda index: "phone" if index % 2 else "laptop", 10, 300)
    assert all("IDENTITY_DEVICE_ROTATION" not in codes(decision) for decision in decisions)
    assert all(decision.route == Route.PASS_TO_ATS for decision in decisions)


def test_rotation_limit_is_configurable(application_factory, job_factory):
    strict = run(application_factory, job_factory, lambda index: f"device-{index}", 4, 120, Config(identity_device_rotation_limit=3))
    relaxed = run(application_factory, job_factory, lambda index: f"device-{index}", 4, 120, Config(identity_device_rotation_limit=6))
    assert "IDENTITY_DEVICE_ROTATION" in codes(strict[-1])
    assert all("IDENTITY_DEVICE_ROTATION" not in codes(decision) for decision in relaxed)


def test_rotation_outside_the_window_is_ignored(application_factory, job_factory):
    decisions = run(application_factory, job_factory, lambda index: f"device-{index}", 6, 4000)
    assert all("IDENTITY_DEVICE_ROTATION" not in codes(decision) for decision in decisions)
