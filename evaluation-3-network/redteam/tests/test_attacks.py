from __future__ import annotations

import pytest

from firewall.models import EvaluationRequest
from redteam.attacks import ATTACK_NAMES, CONTROL_NAMES, generate_scenario


ALL_SCENARIOS = (*ATTACK_NAMES, *CONTROL_NAMES)


@pytest.mark.parametrize("scenario_name", ALL_SCENARIOS)
def test_attack_generators_emit_valid_unique_requests(scenario_name: str) -> None:
    payloads = list(generate_scenario(scenario_name, seed=2026, count=5))

    assert len(payloads) == 5
    validated = [EvaluationRequest.model_validate(payload) for payload in payloads]
    application_ids = [item.application.application_id for item in validated]

    assert len(set(application_ids)) == len(application_ids)
    assert all(item.application.candidate.projects for item in validated)
    assert all(item.job.must_have_skills for item in validated)


@pytest.mark.parametrize("scenario_name", ALL_SCENARIOS)
def test_attack_generators_are_deterministic_by_seed(scenario_name: str) -> None:
    first = list(generate_scenario(scenario_name, seed=91, count=6))
    second = list(generate_scenario(scenario_name, seed=91, count=6))

    assert first == second


def test_seed_changes_generated_identity_data() -> None:
    first = list(generate_scenario("resume_farm", seed=1, count=4))
    second = list(generate_scenario("resume_farm", seed=2, count=4))

    assert first != second


def test_unknown_scenario_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown scenario"):
        list(generate_scenario("not-a-scenario"))
