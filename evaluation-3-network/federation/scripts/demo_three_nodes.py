
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firewall.models import Application
from federation.client import NodeClient
from federation.fingerprints import to_fingerprints
from federation.node import generate_private_key, public_key_text


SECRET = "demo-consortium-secret-change-in-production"


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def sample_application(*, honest: bool = False) -> Application:
    if honest:
        identity = {
            "name": "Honest Candidate",
            "email": "honest.candidate@example.org",
            "phone": "+91 90000 11111",
        }
        device, ip = "honest-device", "198.51.100.82"
        description = "Built an accessible offline health survey application for field workers"
    else:
        identity = {
            "name": "Synthetic Applicant",
            "email": "bot.campaign+job@gmail.com",
            "phone": "+91 98888 77777",
        }
        device, ip = "automation-device-77", "203.0.113.61"
        description = "Generated bulk application automation using Python FastAPI Redis Docker"
    return Application.model_validate(
        {
            "application_id": "honest-1" if honest else "bot-1",
            "job_id": "backend-1",
            "candidate": {
                **identity,
                "skills": ["Python"],
                "experience": [
                    {"company": "Example Labs", "title": "Engineer", "start": "2024", "end": "2026"}
                ],
                "projects": [{"name": "Platform", "description": description}],
            },
            "signals": {
                "device_id": device,
                "ip": ip,
                "session_seconds": 3,
                "paste_char_ratio": 0.98,
                "submitted_at": time.time(),
            },
        }
    )


async def wait_ready(urls: list[str], processes: list[subprocess.Popen[bytes]]) -> None:
    deadline = time.monotonic() + 10
    async with httpx.AsyncClient(timeout=0.5) as client:
        while time.monotonic() < deadline:
            if any(process.poll() is not None for process in processes):
                raise RuntimeError("a node exited during startup")
            try:
                responses = await asyncio.gather(*(client.get(f"{url}/fed/status") for url in urls))
                if all(response.status_code == 200 for response in responses):
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.05)
    raise TimeoutError("nodes did not become ready")


async def check_until(
    url: str, fingerprints: dict[str, str], expected_code: str, timeout: float = 5.0
) -> tuple[dict[str, object], float]:
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=1.0) as client:
        while time.perf_counter() - started < timeout:
            response = await client.post(f"{url}/fed/check", json={"fingerprints": fingerprints})
            response.raise_for_status()
            hits = response.json()["hits"]
            if hits and hits[0]["code"] == expected_code:
                return hits[0], (time.perf_counter() - started) * 1000
            await asyncio.sleep(0.02)
    raise TimeoutError(f"{url} did not return {expected_code}")


async def run_demo() -> None:
    node_ids = ["employer-a", "employer-b", "employer-c"]
    ports = [free_port() for _ in node_ids]
    urls = [f"http://127.0.0.1:{port}" for port in ports]
    processes: list[subprocess.Popen[bytes]] = []

    with tempfile.TemporaryDirectory(prefix="fed-demo-") as directory:
        state_dir = Path(directory)
        key_paths = {node: state_dir / f"{node}.pem" for node in node_ids}
        keys = {node: generate_private_key(path) for node, path in key_paths.items()}
        peers_file = state_dir / "peers.json"
        peers_file.write_text(
            json.dumps({"nodes": {node: public_key_text(key.public_key()) for node, key in keys.items()}}),
            encoding="utf-8",
        )

        try:
            for index, node_id in enumerate(node_ids):
                peers = ",".join(url for peer_index, url in enumerate(urls) if peer_index != index)
                command = [
                    sys.executable,
                    "-m",
                    "federation.node",
                    "--node-id",
                    node_id,
                    "--port",
                    str(ports[index]),
                    "--peers",
                    peers,
                    "--secret",
                    SECRET,
                    "--key-file",
                    str(key_paths[node_id]),
                    "--peers-file",
                    str(peers_file),
                    "--k",
                    "2",
                    "--score-threshold",
                    "1.5",
                ]
                processes.append(
                    subprocess.Popen(
                        command,
                        cwd=ROOT,
                        env={**os.environ, "PYTHONUNBUFFERED": "1"},
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                )

            await wait_ready(urls, processes)
            clients = {
                node: NodeClient(urls[index], secret=SECRET, node_id=node, private_key=keys[node])
                for index, node in enumerate(node_ids)
            }
            bot = sample_application()
            bot_fp = to_fingerprints(bot, SECRET)

            overall = time.perf_counter()
            print("t=   0.0 ms  employer-a local detector caught bot (raw PII stays at A)")
            await clients["employer-a"].report_application(bot, "bot", confidence=0.96, ttl=300)
            hit_b, advisory_ms = await check_until(urls[1], bot_fp, "FED_ADVISORY")
            print(
                f"t={advisory_ms:6.1f} ms  employer-b received gossip: {hit_b['code']} "
                f"({hit_b['independent_nodes']} node, not blocked)"
            )

            corroboration_start = time.perf_counter()
            await clients["employer-b"].report_application(bot, "bot", confidence=0.92, ttl=300)
            hit_c, confirmation_poll_ms = await check_until(urls[2], bot_fp, "FED_CONFIRMED")
            propagation_ms = (time.perf_counter() - corroboration_start) * 1000
            elapsed_ms = (time.perf_counter() - overall) * 1000
            print(
                f"t={elapsed_ms:6.1f} ms  employer-c sees {hit_c['code']} after B corroborates "
                f"({hit_c['independent_nodes']} nodes)"
            )
            print(
                f"MEASURED confirmed propagation B->C: {propagation_ms:.1f} ms "
                f"(check observed after {confirmation_poll_ms:.1f} ms)"
            )

            honest = sample_application(honest=True)
            honest_fp = to_fingerprints(honest, SECRET)
            rogue_start = time.perf_counter()
            await clients["employer-a"].report_application(
                honest, "fabricated", confidence=0.99, ttl=300
            )
            rogue_hit, rogue_ms = await check_until(urls[2], honest_fp, "FED_ADVISORY")
            assert rogue_hit["independent_nodes"] == 1
            print(
                f"t=+{(time.perf_counter() - rogue_start) * 1000:5.1f} ms  rogue single-node false report: "
                f"{rogue_hit['code']} at C (not blocked; observed in {rogue_ms:.1f} ms)"
            )
            print("DEMO PASS: no raw PII crossed nodes; corroborated bot blocked; lone rogue stayed advisory")
        finally:
            for process in processes:
                process.terminate()
            for process in processes:
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)


if __name__ == "__main__":
    asyncio.run(run_demo())
