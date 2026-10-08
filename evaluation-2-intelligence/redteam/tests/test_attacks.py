from __future__ import annotations

import pytest

from firewall.models import EvaluationRequest
from redteam import attacks
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


def test_live_paraphrase_skips_embedding_models_and_varies_strategy(monkeypatch) -> None:
    requests = []
    monkeypatch.delenv("FIREWALL_REDTEAM_MODEL", raising=False)

    class Response:
        def __init__(self, value):
            self.value = value

        def raise_for_status(self):
            return None

        def json(self):
            return self.value

    class Client:
        def __init__(self, **values):
            self.values = values

        def __enter__(self):
            return self

        def __exit__(self, *values):
            return None

        def get(self, path):
            return Response({"models": [{"name": "nomic-embed-text:latest"}, {"name": "qwen:test"}]})

        def post(self, path, json, timeout):
            requests.append(json)
            return Response({"response": "rewritten"})

    monkeypatch.setattr(attacks.httpx, "Client", Client)

    assert attacks._ollama_paraphrase("source", 1, "http://localhost:11434") == "rewritten"
    assert attacks._ollama_paraphrase("source", 2, "http://localhost:11434") == "rewritten"
    assert all(item["model"] == "qwen:test" for item in requests)
    assert all(item["keep_alive"] == "10m" for item in requests)
    assert requests[0]["prompt"] != requests[1]["prompt"]
