from __future__ import annotations

import argparse
import functools
import http.client
import http.server
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web"
STATE_DIR = ROOT / ".state"
HOST = "127.0.0.1"


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return None


def port_is_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((HOST, port)) == 0


def private_file(path: Path, initial: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(initial)
    os.chmod(path, 0o600)
    return str(path)


def ollama_status(environment: dict[str, str]) -> tuple[bool, str]:
    host = environment.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    model = environment.get("FIREWALL_LLM_MODEL", "qwen2.5:7b-instruct")
    parts = host.split("://", 1)[-1].split(":")
    name = parts[0] or "localhost"
    port = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 11434
    if name not in {"localhost", "127.0.0.1", "::1"}:
        return False, "Ollama host is not on this machine, so the model stays off"
    try:
        connection = http.client.HTTPConnection(name, port, timeout=2)
        connection.request("GET", "/api/tags")
        installed = connection.getresponse().read(1_000_000).decode("utf-8", "replace")
        connection.close()
    except (OSError, http.client.HTTPException):
        return False, "Ollama was not found, so the model stays off"
    if f'"{model}"' not in installed:
        return False, f"Model {model} is not installed in Ollama, so the model stays off"
    return True, model


def warm_model(environment: dict[str, str], model: str) -> None:
    name = environment.get("OLLAMA_HOST", "http://localhost:11434").split("://", 1)[-1].split(":")[0] or "localhost"
    port_text = environment.get("OLLAMA_HOST", "http://localhost:11434").rsplit(":", 1)[-1]
    port = int(port_text) if port_text.isdigit() else 11434
    body = '{"model":"%s","prompt":"hi","stream":false,"keep_alive":"30m","options":{"num_predict":1}}' % model
    try:
        connection = http.client.HTTPConnection(name, port, timeout=120)
        connection.request("POST", "/api/generate", body, {"Content-Type": "application/json"})
        connection.getresponse().read(65_536)
        connection.close()
    except (OSError, http.client.HTTPException):
        return None


def build_environment(web_port: int) -> dict[str, str]:
    environment = dict(os.environ)
    if "FIREWALL_LLM" not in environment:
        available, detail = ollama_status(environment)
        environment["FIREWALL_LLM"] = "1" if available else "0"
        environment["_TRUST_LLM_NOTE"] = ("on, using " + detail) if available else ("off, " + detail)
    else:
        environment["_TRUST_LLM_NOTE"] = "on" if environment["FIREWALL_LLM"] == "1" else "off, switched off by FIREWALL_LLM"
    if not environment.get("FIREWALL_USERS_FILE"):
        environment["FIREWALL_USERS_FILE"] = private_file(STATE_DIR / "users.json", "[]")
    if not environment.get("FIREWALL_AUDIT_FILE"):
        environment["FIREWALL_AUDIT_FILE"] = private_file(STATE_DIR / "audit.jsonl", "")
    if not environment.get("FIREWALL_CORS_ORIGINS"):
        environment["FIREWALL_CORS_ORIGINS"] = f"http://localhost:{web_port},http://{HOST}:{web_port}"
    return environment


def start_api(port: int, environment: dict[str, str]) -> subprocess.Popen:
    if not 1024 <= port <= 65535:
        raise SystemExit("api port must be between 1024 and 65535")
    command = [sys.executable, "-m", "uvicorn", "firewall.api:app", "--host", HOST, "--port", "%d" % port]
    return subprocess.Popen(command, cwd=ROOT, env=environment)


def wait_for_api(process: subprocess.Popen, port: int, seconds: float = 40.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        connection = http.client.HTTPConnection(HOST, port, timeout=1)
        try:
            connection.request("GET", "/healthz")
            if connection.getresponse().status == 200:
                return True
        except OSError:
            time.sleep(0.3)
        finally:
            connection.close()
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the firewall API and the web console together.")
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--web-port", type=int, default=8081)
    arguments = parser.parse_args()
    busy = [port for port in (arguments.api_port, arguments.web_port) if port_is_busy(port)]
    if busy or arguments.api_port == arguments.web_port:
        print("Cannot start: port(s) already in use or equal: " + ", ".join(str(p) for p in busy or [arguments.api_port]))
        return 1
    if not WEB_DIR.is_dir():
        print(f"Cannot start: web folder not found at {WEB_DIR}")
        return 1
    environment = build_environment(arguments.web_port)
    api = start_api(arguments.api_port, environment)
    handler = functools.partial(QuietHandler, directory=str(WEB_DIR))
    server = http.server.ThreadingHTTPServer((HOST, arguments.web_port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    stopping = threading.Event()

    def stop(*_: object) -> None:
        stopping.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    thread.start()
    if environment.get("FIREWALL_LLM") == "1":
        threading.Thread(target=warm_model, args=(environment, environment.get("FIREWALL_LLM_MODEL", "qwen2.5:7b-instruct")), daemon=True).start()
    code = 0
    try:
        if not wait_for_api(api, arguments.api_port):
            print("The API did not start. Check the messages above.")
            code = 1
        else:
            print(f"API:      http://{HOST}:{arguments.api_port}")
            print(f"Console:  http://localhost:{arguments.web_port}/console/")
            print(f"Users saved in: {environment['FIREWALL_USERS_FILE']}")
            print("Language model: " + environment["_TRUST_LLM_NOTE"])
            print("Press Ctrl+C to stop.")
            while not stopping.is_set() and api.poll() is None:
                time.sleep(0.5)
            if api.poll() is not None and not stopping.is_set():
                print("The API stopped unexpectedly.")
                code = 1
    finally:
        server.shutdown()
        server.server_close()
        if api.poll() is None:
            api.terminate()
            try:
                api.wait(timeout=10)
            except subprocess.TimeoutExpired:
                api.kill()
                api.wait()
        print("Stopped.")
    return code


if __name__ == "__main__":
    sys.exit(main())
