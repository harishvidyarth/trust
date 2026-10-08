from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eval.run_eval import load_jsonl, replay
from firewall.config import Config
from firewall.llm.ollama_client import OllamaClient


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    routes = Counter(str(row["route"]) for row in rows)
    return {
        "count": len(rows),
        "flagged": sum(row["route"] != "PASS_TO_ATS" for row in rows),
        "routes": {
            route: routes[route]
            for route in ("PASS_TO_ATS", "ADDITIONAL_VERIFICATION", "MANUAL_REVIEW")
        },
    }


def measure_honest(records: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    honest = [record for record in records if record["label"] == "LEGIT"]
    before = replay(honest, Config(semantic_dup_enabled=False))
    after = replay(
        honest,
        Config(
            semantic_dup_enabled=True,
            semantic_dup_similarity_threshold=threshold,
        ),
    )
    return {
        "threshold": threshold,
        "before": _summary(before),
        "after": _summary(after),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=Config().semantic_dup_similarity_threshold)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if OllamaClient().embed("synthetic resume embedding warmup") is None:
        print(json.dumps({"status": "not measured", "reason": "live Ollama embedding unavailable"}))
        raise SystemExit(2)
    result = measure_honest(load_jsonl(args.input), args.threshold)
    result["status"] = "measured"
    result["input"] = str(args.input)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
