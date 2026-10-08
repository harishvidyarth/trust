from __future__ import annotations

import json
from urllib.error import HTTPError

from firewall.config import Config
from firewall.engine import evaluate
from firewall.llm.ollama_client import OllamaClient
from firewall.store import InMemoryApplicationStore


def test_llm_rewrites_only_summary_without_changing_decision(monkeypatch, application_factory, job_factory):
    captured: list[bytes] = []

    def transport(request, timeout):
        captured.append(request.data)
        assert timeout == 5.0
        return json.dumps({"response": "Review the listed qualification concerns with the candidate."}).encode()

    application = application_factory(
        name="Private Candidate",
        email="private.person@example.com",
        phone="9123456789",
        skills=["Python"],
        device_id="private-device",
        ip="192.0.2.44",
    )
    monkeypatch.delenv("FIREWALL_LLM", raising=False)
    deterministic = evaluate(application, job_factory(), InMemoryApplicationStore(), Config())
    monkeypatch.setenv("FIREWALL_LLM", "1")
    assisted = evaluate(
        application,
        job_factory(),
        InMemoryApplicationStore(),
        Config(),
        llm_client=OllamaClient(transport=transport),
    )

    assert (assisted.score, assisted.route, assisted.reasons) == (
        deterministic.score,
        deterministic.route,
        deterministic.reasons,
    )
    assert assisted.summary == "Review the listed qualification concerns with the candidate."
    assert assisted.llm_used is True
    request_text = captured[0].decode()
    assert "QUAL_MISSING_MUST_HAVE" in request_text
    assert "Python" in request_text
    for pii in ("Private Candidate", "private.person@example.com", "9123456789", "private-device", "192.0.2.44"):
        assert pii not in request_text


def test_llm_timeout_falls_back_to_identical_deterministic_decision(
    monkeypatch, application_factory, job_factory
):
    application = application_factory(skills=["Python"])
    monkeypatch.delenv("FIREWALL_LLM", raising=False)
    deterministic = evaluate(application, job_factory(), InMemoryApplicationStore(), Config())

    def timeout_transport(request, timeout):
        raise TimeoutError

    monkeypatch.setenv("FIREWALL_LLM", "1")
    fallback = evaluate(
        application,
        job_factory(),
        InMemoryApplicationStore(),
        Config(),
        llm_client=OllamaClient(transport=timeout_transport),
    )

    assert fallback == deterministic
    assert fallback.llm_used is False


def test_llm_skill_suggestions_are_advisory_and_parsed_from_mocked_transport():
    def transport(request, timeout):
        return json.dumps({"response": '["react", "node.js"]'}).encode()

    client = OllamaClient(transport=transport)
    assert client.suggest_skill_canonicalizations(["React.js", "NodeJS"]) == ["react", "node.js"]


def test_embed_uses_modern_ollama_endpoint_with_caps_and_keep_alive():
    captured = []

    def transport(request, timeout):
        captured.append((request, timeout))
        return json.dumps({"embeddings": [[0.25, -0.5, 0.75]]}).encode()

    vector = OllamaClient(transport=transport, timeout=20).embed("x" * 50_000)

    assert vector == (0.25, -0.5, 0.75)
    request, timeout = captured[0]
    payload = json.loads(request.data)
    assert request.full_url.endswith("/api/embed")
    assert timeout == 3.0
    assert payload["model"] == "nomic-embed-text"
    assert payload["keep_alive"] == "10m"
    assert payload["truncate"] is True
    assert len(payload["input"].encode()) <= 16_384


def test_embed_falls_back_to_legacy_ollama_endpoint():
    paths = []

    def transport(request, timeout):
        paths.append(request.full_url)
        if request.full_url.endswith("/api/embed"):
            raise HTTPError(request.full_url, 404, "not found", {}, None)
        return json.dumps({"embedding": [1, 2, 3]}).encode()

    assert OllamaClient(transport=transport).embed("resume") == (1.0, 2.0, 3.0)
    assert [path.rsplit("/", 1)[-1] for path in paths] == ["embed", "embeddings"]


def test_embed_failure_is_silent():
    def transport(request, timeout):
        raise TimeoutError

    assert OllamaClient(transport=transport).embed("resume") is None
