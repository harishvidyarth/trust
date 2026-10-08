from __future__ import annotations

import argparse
import json
import re
import socket
import subprocess
import sys
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

import httpx

from redteam.attacks import (
    ATTACK_NAMES,
    CONTROL_NAMES,
    generate_scenario,
    scenario_prelude,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = Path(__file__).resolve().parent / "results"
EVALUATE_PATH = "/v1/applications/evaluate"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _loopback_host(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname not in LOOPBACK_HOSTS:
        raise ValueError("--base-url must be an http:// loopback address")
    return parsed.hostname or "127.0.0.1"


def _free_port(host: str) -> int:
    bind_host = "127.0.0.1" if host == "localhost" else host
    family = socket.AF_INET6 if ":" in bind_host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as listener:
        listener.bind((bind_host, 0))
        return int(listener.getsockname()[1])


def _url(host: str, port: int) -> str:
    display_host = f"[{host}]" if ":" in host else host
    return f"http://{display_host}:{port}"


def _stop_process(process: subprocess.Popen[str]) -> str:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    output = ""
    if process.stdout is not None:
        output = process.stdout.read()
    return output


@contextmanager
def fresh_firewall(
    base_url: str,
    *,
    attempts: int = 5,
    retry_delay_seconds: float = 60.0,
) -> Iterator[tuple[str, int]]:
    host = _loopback_host(base_url)
    python = REPO_ROOT / ".venv" / "bin" / "python"
    if not python.exists():
        raise RuntimeError(f"firewall interpreter not found: {python}")

    process: subprocess.Popen[str] | None = None
    actual_url = ""
    used_attempt = 0
    startup_errors: list[str] = []
    for attempt in range(1, attempts + 1):
        used_attempt = attempt
        port = _free_port(host)
        actual_url = _url(host, port)
        process = subprocess.Popen(
            [
                str(python),
                "-m",
                "uvicorn",
                "firewall.api:app",
                "--host",
                host,
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.monotonic() + 20.0
        ready = False
        with httpx.Client(timeout=0.5, trust_env=False) as health_client:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                try:
                    response = health_client.get(f"{actual_url}/healthz")
                    if response.status_code == 200:
                        ready = True
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
        if ready:
            break
        startup_errors.append(_stop_process(process).strip() or "process did not become healthy")
        process = None
        if attempt < attempts:
            print(
                f"Firewall startup attempt {attempt}/{attempts} failed; retrying in "
                f"{retry_delay_seconds:g}s.",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(retry_delay_seconds)
    if process is None:
        details = "\n\n".join(startup_errors[-3:])
        raise RuntimeError(f"firewall failed to start after {attempts} attempts:\n{details}")

    try:
        yield actual_url, used_attempt
    finally:
        _stop_process(process)


def _post(client: httpx.Client, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post(EVALUATE_PATH, json=payload)
    response.raise_for_status()
    body = response.json()
    if body.get("route") not in {"PASS_TO_ATS", "ADDITIONAL_VERIFICATION", "MANUAL_REVIEW"}:
        raise RuntimeError(f"unexpected decision route: {body.get('route')!r}")
    return body


def run_scenario(
    name: str,
    *,
    base_url: str,
    seed: int,
    count: int | None,
    use_ollama: bool,
    ollama_url: str,
) -> dict[str, Any]:
    kind = "attack" if name in ATTACK_NAMES else "control"
    payloads = list(
        generate_scenario(
            name,
            seed=seed,
            count=count,
            use_ollama=use_ollama,
            ollama_url=ollama_url,
        )
    )
    route_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    decision_records: list[dict[str, Any]] = []
    first_flag_index: int | None = None
    first_flag_seconds: float | None = None
    first_submitted_at = float(payloads[0]["application"]["signals"]["submitted_at"])

    with fresh_firewall(base_url) as (scenario_url, startup_attempts):
        with httpx.Client(base_url=scenario_url, timeout=10.0, trust_env=False) as client:
            for setup_payload in scenario_prelude(name, seed=seed):
                _post(client, setup_payload)
            started = time.perf_counter()
            for index, payload in enumerate(payloads):
                decision = _post(client, payload)
                route = str(decision["route"])
                route_counts[route] += 1
                codes = [str(reason["code"]) for reason in decision.get("reasons", [])]
                reason_counts.update(codes)
                flagged = route != "PASS_TO_ATS"
                if flagged and first_flag_index is None:
                    first_flag_index = index + 1
                    submitted_at = float(payload["application"]["signals"]["submitted_at"])
                    first_flag_seconds = max(0.0, submitted_at - first_submitted_at)
                decision_records.append(
                    {
                        "application_id": decision["application_id"],
                        "route": route,
                        "score": decision["score"],
                        "reason_codes": codes,
                    }
                )
            wall_time = time.perf_counter() - started

    total = len(payloads)
    passed = route_counts["PASS_TO_ATS"]
    flagged = total - passed
    is_attack = kind == "attack"
    return {
        "name": name,
        "kind": kind,
        "seed": seed,
        "application_count": total,
        "passed_count": passed,
        "flagged_count": flagged,
        "evasion_rate": passed / total if is_attack else None,
        "detection_rate": flagged / total if is_attack else None,
        "control_false_positive_rate": flagged / total if not is_attack else None,
        "time_to_first_flag_seconds": first_flag_seconds,
        "first_flag_application_index": first_flag_index,
        "route_counts": {
            route: route_counts[route]
            for route in ("PASS_TO_ATS", "ADDITIONAL_VERIFICATION", "MANUAL_REVIEW")
        },
        "reason_codes_seen": dict(sorted(reason_counts.items())),
        "wall_time_seconds": round(wall_time, 4),
        "startup_attempts": startup_attempts,
        "decisions": decision_records,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run isolated local red-team scenarios.")
    parser.add_argument("--label", required=True, help="Result label, e.g. baseline or after-v2")
    parser.add_argument("--base-url", default="http://127.0.0.1", help="Loopback host; a fresh port is chosen")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--count", type=int, help="Override the default submission count for every scenario")
    parser.add_argument("--scenario", action="append", choices=(*ATTACK_NAMES, *CONTROL_NAMES))
    parser.add_argument("--use-ollama", action="store_true", help="Try local Ollama for paraphrase_copy")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if not re.fullmatch(r"[A-Za-z0-9._-]+", args.label):
        raise SystemExit("--label may contain only letters, numbers, dot, underscore, and hyphen")
    _loopback_host(args.base_url)
    _loopback_host(args.ollama_url)
    names = args.scenario or [*ATTACK_NAMES, *CONTROL_NAMES]
    scenarios = []
    for name in names:
        print(f"Running {name} in a fresh firewall process...", flush=True)
        scenarios.append(
            run_scenario(
                name,
                base_url=args.base_url,
                seed=args.seed,
                count=args.count,
                use_ollama=args.use_ollama,
                ollama_url=args.ollama_url,
            )
        )

    result = {
        "schema_version": 1,
        "label": args.label,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "requested_base_url": args.base_url,
        "definitions": {
            "flagged": "Decision route is not PASS_TO_ATS.",
            "evasion_rate": "Attack submissions routed PASS_TO_ATS / attack submissions.",
            "detection_rate": "Attack submissions not routed PASS_TO_ATS / attack submissions.",
            "control_false_positive_rate": "Control submissions not routed PASS_TO_ATS / control submissions.",
            "time_to_first_flag_seconds": "Synthetic submitted_at delta from first measured submission; null if never flagged.",
        },
        "scenarios": scenarios,
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    result_path = RESULTS_DIR / f"{args.label}.json"
    result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {result_path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
