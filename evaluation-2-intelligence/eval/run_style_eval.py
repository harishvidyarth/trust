from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from firewall.llm.ollama_client import OllamaClient
from firewall.resume.style import analyze_style


MODEL = "qwen2.5:7b-instruct"
OLLAMA_URL = "http://localhost:11434"
WARM_SCHEMA = {
    "type": "object",
    "properties": {"ready": {"type": "boolean"}},
    "required": ["ready"],
    "additionalProperties": False,
}


def predicted_label(score: int | float | None) -> str:
    if score is None or score < 35:
        return "human"
    if score < 65:
        return "ai_polished"
    return "ai_generated"


def metrics(records: list[dict[str, Any]], predictions: list[str]) -> dict[str, Any]:
    actual_ai = [item["label"] != "human" for item in records]
    predicted_ai = [label != "human" for label in predictions]
    true_positive = sum(actual and predicted for actual, predicted in zip(actual_ai, predicted_ai))
    false_positive = sum(not actual and predicted for actual, predicted in zip(actual_ai, predicted_ai))
    false_negative = sum(actual and not predicted for actual, predicted in zip(actual_ai, predicted_ai))
    human_count = sum(not item for item in actual_ai)
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    confusion = {label: dict(Counter()) for label in ("human", "ai_polished", "ai_generated")}
    for item, prediction in zip(records, predictions):
        row = confusion[item["label"]]
        row[prediction] = row.get(prediction, 0) + 1
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "false_positives": false_positive,
        "false_positive_rate": round(false_positive / human_count, 4) if human_count else 0.0,
        "confusion_matrix": confusion,
    }


def available_models() -> set[str]:
    request = Request(f"{OLLAMA_URL}/api/tags", method="GET")
    with urlopen(request, timeout=2.0) as response:
        payload = json.loads(response.read(1_048_577))
    models = payload.get("models", []) if isinstance(payload, dict) else []
    names = set()
    for item in models:
        if isinstance(item, dict):
            for key in ("name", "model"):
                if isinstance(item.get(key), str):
                    names.add(item[key])
    return names


def warm_model() -> None:
    client = OllamaClient(timeout=120.0)
    value = client.generate_json('Return {"ready":true}.', WARM_SCHEMA)
    if not isinstance(value, dict) or value.get("ready") is not True:
        raise ValueError("warmup response was invalid")


def evaluate(dataset: dict[str, Any], allow_live: bool = True) -> dict[str, Any]:
    records = dataset["records"]
    heuristic_rows = [analyze_style(item["text"], use_llm=False) for item in records]
    heuristic_predictions = [predicted_label(row["score"]) for row in heuristic_rows]
    result = {
        "dataset": dataset["metadata"],
        "class_counts": dict(Counter(item["label"] for item in records)),
        "modes": {
            "heuristic": {
                "status": "measured",
                **metrics(records, heuristic_predictions),
            },
            "hybrid": {
                "status": "not measured",
                "model": MODEL,
                "reason": "live Ollama evaluation was disabled",
            },
        },
        "heuristic_predictions": [
            {
                "id": item["id"],
                "actual": item["label"],
                "predicted": prediction,
                "score": row["score"],
            }
            for item, prediction, row in zip(records, heuristic_predictions, heuristic_rows)
        ],
        "limits": [
            "The resumes are synthetic and template-written.",
            "The dataset is small.",
            "Labels are assigned by construction, not independent annotation.",
            "The LLM judge may find patterns resembling its own writing easier to classify.",
            "These measurements do not support a production accuracy claim.",
        ],
    }
    if not allow_live:
        return result
    previous_host = os.environ.get("OLLAMA_HOST")
    previous_model = os.environ.get("FIREWALL_LLM_MODEL")
    os.environ["OLLAMA_HOST"] = OLLAMA_URL
    os.environ["FIREWALL_LLM_MODEL"] = MODEL
    try:
        models = available_models()
        if MODEL not in models:
            result["modes"]["hybrid"]["reason"] = f"{MODEL} is not installed in local Ollama"
            return result
        warm_model()
        client = OllamaClient()
        hybrid_rows = [
            analyze_style(item["text"], llm_client=client, use_llm=True) for item in records
        ]
        hybrid_predictions = [predicted_label(row["score"]) for row in hybrid_rows]
        valid = sum(row["mode"] == "hybrid" for row in hybrid_rows)
        result["modes"]["hybrid"] = {
            "status": "measured",
            "model": MODEL,
            "valid_judgments": valid,
            "fallbacks": len(records) - valid,
            **metrics(records, hybrid_predictions),
        }
        result["hybrid_predictions"] = [
            {
                "id": item["id"],
                "actual": item["label"],
                "predicted": prediction,
                "score": row["score"],
                "mode": row["mode"],
                "llm_label": row["llm_label"],
                "llm_confidence": row["llm_confidence"],
                "verified_quotes": row["verified_quotes"],
            }
            for item, prediction, row in zip(records, hybrid_predictions, hybrid_rows)
        ]
    except Exception as exc:
        result["modes"]["hybrid"]["reason"] = (
            f"local Ollama unavailable or warmup failed ({type(exc).__name__})"
        )
    finally:
        if previous_host is None:
            os.environ.pop("OLLAMA_HOST", None)
        else:
            os.environ["OLLAMA_HOST"] = previous_host
        if previous_model is None:
            os.environ.pop("FIREWALL_LLM_MODEL", None)
        else:
            os.environ["FIREWALL_LLM_MODEL"] = previous_model
    return result


def report_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Writing-style evaluation",
        "",
        f"Dataset: {result['dataset']['count']} synthetic resumes, seed {result['dataset']['seed']}.",
        "",
        "## Results",
        "",
        "| Mode | Status | Precision | Recall | Human false positives |",
        "|---|---|---:|---:|---:|",
    ]
    for name in ("heuristic", "hybrid"):
        mode = result["modes"][name]
        if mode["status"] == "measured":
            false_positive = f"{mode['false_positives']} ({mode['false_positive_rate']:.2%})"
            lines.append(
                f"| {name} | measured | {mode['precision']:.4f} | {mode['recall']:.4f} | {false_positive} |"
            )
        else:
            lines.append(f"| {name} | not measured | — | — | — |")
            lines.extend(["", f"Hybrid reason: {mode['reason']}."])
    hybrid = result["modes"]["hybrid"]
    if hybrid["status"] == "measured":
        lines.extend(
            [
                "",
                f"Hybrid valid judgments: {hybrid['valid_judgments']}; heuristic fallbacks: {hybrid['fallbacks']}.",
            ]
        )
    lines.extend(
        [
            "",
            "The heuristic maps scores below 35 to human, 35–64 to AI-polished, and 65–100 to AI-generated. "
            "The hybrid maps LLM labels to 0, 60, and 100 before applying 0.4 × heuristic + 0.6 × LLM.",
            "",
            "## LIMITS",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in result["limits"])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("eval/style_set.json"))
    parser.add_argument("--output", type=Path, default=Path("eval/results/style_eval.json"))
    parser.add_argument("--report", type=Path, default=Path("eval/results/STYLE_REPORT.md"))
    parser.add_argument("--no-live-ollama", action="store_true")
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    result = evaluate(dataset, allow_live=not args.no_live_ollama)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    args.report.write_text(report_markdown(result), encoding="utf-8")
    print(json.dumps(result["modes"], indent=2))


if __name__ == "__main__":
    main()
