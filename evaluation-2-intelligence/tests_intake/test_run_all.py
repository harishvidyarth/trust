from __future__ import annotations

import importlib.util
import socket
import stat
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_all.py"
spec = importlib.util.spec_from_file_location("run_all", SCRIPT)
run_all = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_all)


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(run_all, "STATE_DIR", tmp_path / ".state")
    for name in ("FIREWALL_USERS_FILE", "FIREWALL_AUDIT_FILE", "FIREWALL_CORS_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path / ".state"


def test_defaults_create_private_state_files_and_console_cors(state):
    environment = run_all.build_environment(8081)
    users = Path(environment["FIREWALL_USERS_FILE"])
    assert users == state / "users.json" and users.read_text() == "[]"
    assert stat.S_IMODE(users.stat().st_mode) == 0o600
    assert stat.S_IMODE(Path(environment["FIREWALL_AUDIT_FILE"]).stat().st_mode) == 0o600
    assert environment["FIREWALL_CORS_ORIGINS"] == "http://localhost:8081,http://127.0.0.1:8081"


def test_existing_settings_and_files_are_kept(state, monkeypatch, tmp_path):
    custom = tmp_path / "mine.json"
    monkeypatch.setenv("FIREWALL_USERS_FILE", str(custom))
    monkeypatch.setenv("FIREWALL_CORS_ORIGINS", "https://console.example.com")
    environment = run_all.build_environment(8081)
    assert environment["FIREWALL_USERS_FILE"] == str(custom) and not custom.exists()
    assert environment["FIREWALL_CORS_ORIGINS"] == "https://console.example.com"
    existing = state / "audit.jsonl"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("keep\n")
    run_all.build_environment(8081)
    assert existing.read_text() == "keep\n"


def test_port_check_and_api_port_validation():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        assert run_all.port_is_busy(port)
    assert not run_all.port_is_busy(port)
    with pytest.raises(SystemExit):
        run_all.start_api(80, {})


def test_model_switch_follows_ollama_availability(monkeypatch):
    from scripts import run_all

    monkeypatch.setattr(run_all, "ollama_status", lambda environment: (True, "qwen2.5:7b-instruct"))
    monkeypatch.delenv("FIREWALL_LLM", raising=False)
    environment = run_all.build_environment(8081)
    assert environment["FIREWALL_LLM"] == "1"
    assert environment["_TRUST_LLM_NOTE"].startswith("on")

    monkeypatch.setattr(run_all, "ollama_status", lambda environment: (False, "Ollama was not found, so the model stays off"))
    environment = run_all.build_environment(8081)
    assert environment["FIREWALL_LLM"] == "0"
    assert environment["_TRUST_LLM_NOTE"].startswith("off")

    monkeypatch.setenv("FIREWALL_LLM", "0")
    environment = run_all.build_environment(8081)
    assert environment["FIREWALL_LLM"] == "0"
    assert "switched off" in environment["_TRUST_LLM_NOTE"]
