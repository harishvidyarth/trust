from __future__ import annotations

import importlib.util
from pathlib import Path

import httpx
import pytest

from firewall.config import Config
from firewall.store import InMemoryApplicationStore


_spec = importlib.util.spec_from_file_location("_shared_tests_conftest", Path(__file__).resolve().parents[1] / "tests" / "conftest.py")
_shared = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_shared)

make_application = _shared.make_application
make_job = _shared.make_job
BASE_TS = _shared.BASE_TS


def mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def application_factory():
    return make_application


@pytest.fixture
def store():
    return InMemoryApplicationStore()


@pytest.fixture
def config():
    return Config()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("live network access attempted")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.delenv("FIREWALL_ENRICH", raising=False)
