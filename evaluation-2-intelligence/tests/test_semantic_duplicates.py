from __future__ import annotations

import pytest

from firewall.config import Config, load_config
from firewall.engine import evaluate
from firewall.models import Project
from firewall.signals import duplicates
from firewall.store import InMemoryApplicationStore


class FakeEmbedder:
    def __init__(self, vectors):
        self.vectors = vectors
        self.calls = []

    def embed(self, text):
        self.calls.append(text)
        value = self.vectors.get(text)
        if isinstance(value, Exception):
            raise value
        return value


def _application(application_factory, application_id, identity, description):
    return application_factory(
        application_id=application_id,
        name=f"Candidate {identity}",
        email=f"candidate-{identity}@example.test",
        phone=f"900000{identity:04d}",
        projects=[Project(name=f"Project {identity}", description=description)],
    )


def _stored(store, application, job_factory):
    evaluate(application, job_factory(), store, Config())


@pytest.mark.parametrize(
    ("current_text", "current_vector", "expected"),
    [
        ("Created fault tolerant services and verified dependable releases", (1.0, 0.0), True),
        ("Implemented robust backends and tested reliable deployments", (0.98, 0.2), True),
        ("Studied botanical field samples and catalogued native plants", (0.0, 1.0), False),
    ],
    ids=["identical-vector", "paraphrase-like-vector", "unrelated-vector"],
)
def test_semantic_duplicate_uses_deterministic_fake_vectors(
    application_factory,
    job_factory,
    current_text,
    current_vector,
    expected,
):
    duplicates.clear_embedding_cache()
    source_text = "Built resilient APIs and validated secure production releases"
    source = _application(application_factory, "source", 1, source_text)
    current = _application(application_factory, "current", 2, current_text)
    store = InMemoryApplicationStore()
    _stored(store, source, job_factory)
    embedder = FakeEmbedder({source_text: (1.0, 0.0), current_text: current_vector})

    reasons = duplicates.detect_duplicates(
        current,
        store,
        Config(semantic_dup_enabled=True, semantic_dup_similarity_threshold=0.95),
        embedder=embedder,
    )

    assert ("DUP_RESUME_NEAR" in {item.code for item in reasons}) is expected


def test_semantic_duplicate_chunks_long_text_and_caches_by_text_hash(application_factory, job_factory):
    duplicates.clear_embedding_cache()
    source_text = " ".join(f"sourceword{index}" for index in range(80))
    current_text = " ".join(f"currentword{index}" for index in range(80))
    source = _application(application_factory, "source-long", 3, source_text)
    current = _application(application_factory, "current-long", 4, current_text)
    store = InMemoryApplicationStore()
    _stored(store, source, job_factory)
    embedder = FakeEmbedder({})
    embedder.embed = lambda text: embedder.calls.append(text) or (1.0, 0.0)
    config = Config(
        semantic_dup_enabled=True,
        semantic_dup_similarity_threshold=0.95,
        semantic_dup_chunk_chars=120,
        semantic_dup_max_chunks=20,
    )

    first = duplicates.detect_duplicates(current, store, config, embedder=embedder)
    first_call_count = len(embedder.calls)
    second = duplicates.detect_duplicates(current, store, config, embedder=embedder)

    assert "DUP_RESUME_NEAR" in {item.code for item in first}
    assert second == first
    assert first_call_count >= 4
    assert len(embedder.calls) == first_call_count
    assert all(len(text) <= 120 for text in embedder.calls)


def test_semantic_duplicate_embed_failure_falls_back_without_reason(application_factory, job_factory):
    duplicates.clear_embedding_cache()
    source_text = "Built resilient APIs and validated secure production releases"
    current_text = "Created dependable services and checked hardened deployments"
    source = _application(application_factory, "source-failure", 5, source_text)
    current = _application(application_factory, "current-failure", 6, current_text)
    store = InMemoryApplicationStore()
    _stored(store, source, job_factory)
    embedder = FakeEmbedder({current_text: None, source_text: (1.0, 0.0)})

    reasons = duplicates.detect_duplicates(
        current,
        store,
        Config(semantic_dup_enabled=True),
        embedder=embedder,
    )

    assert "DUP_RESUME_NEAR" not in {item.code for item in reasons}


def test_semantic_switch_off_preserves_decision_bytes(application_factory, job_factory, monkeypatch):
    class UnexpectedEmbedder:
        def __init__(self):
            raise AssertionError("embedder constructed while semantic duplicates disabled")

    monkeypatch.setattr(duplicates, "OllamaClient", UnexpectedEmbedder)
    source = _application(
        application_factory,
        "source-disabled",
        7,
        "Built resilient APIs and validated secure production releases",
    )
    current = _application(
        application_factory,
        "current-disabled",
        8,
        "Created dependable services and checked hardened deployments",
    )
    first_store = InMemoryApplicationStore()
    second_store = InMemoryApplicationStore()
    _stored(first_store, source, job_factory)
    _stored(second_store, source, job_factory)

    implicit = evaluate(current, job_factory(), first_store, Config())
    explicit = evaluate(current, job_factory(), second_store, Config(semantic_dup_enabled=False))

    assert implicit.model_dump_json() == explicit.model_dump_json()


def test_semantic_switch_env_override(monkeypatch):
    monkeypatch.setenv("FIREWALL_SEMANTIC_DUP", "1")
    assert load_config().semantic_dup_enabled is True
    monkeypatch.setenv("FIREWALL_SEMANTIC_DUP", "0")
    assert load_config().semantic_dup_enabled is False


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"semantic_dup_similarity_threshold": 1.01}, "semantic duplicate similarity"),
        ({"semantic_dup_min_chars": 0}, "semantic duplicate minimum"),
        ({"semantic_dup_chunk_chars": 0}, "semantic duplicate chunk"),
        ({"semantic_dup_max_chunks": 0}, "semantic duplicate maximum"),
    ],
)
def test_semantic_config_rejects_invalid_thresholds(values, message):
    with pytest.raises(ValueError, match=message):
        Config(**values)
