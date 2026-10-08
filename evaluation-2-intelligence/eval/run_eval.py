from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _import_firewall_surface():
    for attempt in range(5):
        try:
            from firewall.config import Config
            from firewall.engine import evaluate
            from firewall.models import Application, JobRequirements, Route
            from firewall.store import InMemoryApplicationStore

            return Config, evaluate, Application, JobRequirements, Route, InMemoryApplicationStore
        except ImportError:
            if attempt == 4:
                raise
            time.sleep(60)
    raise RuntimeError("unreachable")


Config, evaluate, Application, JobRequirements, Route, InMemoryApplicationStore = _import_firewall_surface()

ROUTES = ("PASS_TO_ATS", "ADDITIONAL_VERIFICATION", "MANUAL_REVIEW")
LEGIT_LABEL = "LEGIT"
SIGNAL_GROUPS = {
    "duplicates": {"DUP_EMAIL", "DUP_PHONE", "DUP_RESUME_NEAR", "DUP_SAME_JOB"},
    "automation": {
        "VELOCITY_HIGH",
        "NETWORK_BURST",
        "IDENTITY_DEVICE_ROTATION",
        "FAST_SUBMIT",
        "PASTE_BULK",
        "TEMPLATE_REUSE",
    },
    "identity_links": {"LINK_REUSE", "FUZZY_IDENTITY", "EMAIL_ALIAS"},
    "qualification": {"QUAL_MISSING_MUST_HAVE", "QUAL_UNDER_EXPERIENCE"},
    "consistency": {"TIMELINE_INVALID", "TIMELINE_OVERLAP"},
    "resume_hidden": {
        "RESUME_HIDDEN_TEXT",
        "RESUME_HIDDEN_NEAR_WHITE",
        "RESUME_HIDDEN_TINY_FONT",
        "RESUME_HIDDEN_OUTSIDE_BOUNDS",
        "RESUME_HIDDEN_ZERO_WIDTH",
        "RESUME_HIDDEN_OVERLAPPING_DUPLICATE",
        "RESUME_HIDDEN_VANISHED_DOCX",
    },
    "resume_injection": {
        "RESUME_PROMPT_INJECTION",
        "RESUME_INJECTION_INSTRUCTION_PHRASE",
        "RESUME_INJECTION_ROLE_PLAY_MARKER",
        "RESUME_INJECTION_IGNORE_PREVIOUS",
        "RESUME_INJECTION_SCREENER_ADDRESSED",
        "RESUME_INJECTION_HIDDEN_LOCATION",
    },
    "resume_stuffing": {
        "RESUME_KEYWORD_STUFFING",
        "RESUME_STUFFING_OVERALL_DENSITY",
        "RESUME_STUFFING_CONCENTRATED_LINE",
        "RESUME_STUFFING_REPEATED_SKILL_BLOCK",
        "RESUME_STUFFING_UNSUPPORTED_SKILLS",
    },
    "resume_divergence": {
        "RESUME_PARSE_DIVERGENCE",
        "RESUME_DIVERGENCE_LOW",
        "RESUME_DIVERGENCE_MEDIUM",
        "RESUME_DIVERGENCE_HIGH",
    },
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            try:
                Application.model_validate(value["application"])
                JobRequirements.model_validate(value["job"])
            except Exception as exc:
                raise ValueError(f"invalid record at {path}:{line_number}: {exc}") from exc
            if value.get("label") not in {"LEGIT", "ABUSE"}:
                raise ValueError(f"invalid label at {path}:{line_number}")
            records.append(value)
    records.sort(key=lambda item: item["application"]["signals"]["submitted_at"])
    return records


def replay(
    records: list[dict[str, Any]],
    config: Any,
    excluded_reason_codes: set[str] | None = None,
) -> list[dict[str, Any]]:
    store = InMemoryApplicationStore()
    evaluated: list[dict[str, Any]] = []
    for record in records:
        application = Application.model_validate(record["application"])
        job = JobRequirements.model_validate(record["job"])
        decision = evaluate(
            application,
            job,
            store,
            config,
            excluded_reason_codes=excluded_reason_codes,
        )
        evaluated.append(
            {
                "application_id": application.application_id,
                "class": record["class"],
                "label": record["label"],
                "route": decision.route.value,
                "score": decision.score,
                "reasons": [reason.code for reason in decision.reasons],
            }
        )
    return evaluated


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def compute_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    tp = sum(row["label"] != LEGIT_LABEL and row["route"] != "PASS_TO_ATS" for row in rows)
    fp = sum(row["label"] == LEGIT_LABEL and row["route"] != "PASS_TO_ATS" for row in rows)
    fn = sum(row["label"] != LEGIT_LABEL and row["route"] == "PASS_TO_ATS" for row in rows)
    tn = sum(row["label"] == LEGIT_LABEL and row["route"] == "PASS_TO_ATS" for row in rows)
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    return {
        "count": len(rows),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": _ratio(2 * precision * recall, precision + recall),
        "false_positive_rate": _ratio(fp, fp + tn),
    }


def summarize_classes(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["class"]].append(row)
    result: dict[str, dict[str, Any]] = {}
    for class_name in sorted(grouped):
        items = grouped[class_name]
        counts = Counter(item["route"] for item in items)
        flagged = sum(route != "PASS_TO_ATS" for route in (item["route"] for item in items))
        label = items[0]["label"]
        result[class_name] = {
            "label": label,
            "count": len(items),
            "route_counts": {route: counts[route] for route in ROUTES},
            "route_rates": {route: _ratio(counts[route], len(items)) for route in ROUTES},
            "recall": _ratio(flagged, len(items)) if label != LEGIT_LABEL else None,
            "false_positive_rate": _ratio(flagged, len(items)) if label == LEGIT_LABEL else None,
            "mean_score": sum(item["score"] for item in items) / len(items),
        }
    return result


def _headline(metrics: dict[str, Any], classes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        **metrics,
        "honest_false_positive_rate": classes["honest"]["false_positive_rate"],
        "honest_campus_false_positive_rate": classes["honest_campus"]["false_positive_rate"],
        "ai_assisted_honest_false_positive_rate": classes["ai_assisted_honest"]["false_positive_rate"],
    }


def threshold_sweep(records: list[dict[str, Any]], base_config: Any) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for review_max in (20, 40, 60):
        for pass_min in (60, 70, 80, 90):
            if review_max >= pass_min:
                continue
            config = replace(base_config, review_max=review_max, pass_min=pass_min)
            rows = replay(records, config)
            metrics = compute_metrics(rows)
            points.append({"review_max": review_max, "pass_min": pass_min, **metrics})
    return points


def run_ablations(records: list[dict[str, Any]], base_config: Any, baseline: dict[str, Any]) -> list[dict[str, Any]]:
    results = [{"group": "baseline", **baseline, "delta_f1": 0.0, "delta_recall": 0.0}]
    for group, codes in SIGNAL_GROUPS.items():
        rows = replay(records, base_config, excluded_reason_codes=codes)
        metrics = compute_metrics(rows)
        results.append(
            {
                "group": f"without_{group}",
                **metrics,
                "delta_f1": metrics["f1"] - baseline["f1"],
                "delta_recall": metrics["recall"] - baseline["recall"],
            }
        )
    return results


def _setup_matplotlib():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def plot_routes(class_metrics: dict[str, dict[str, Any]], output: Path) -> None:
    plt = _setup_matplotlib()
    classes = list(class_metrics)
    bottoms = [0.0] * len(classes)
    colors = {"PASS_TO_ATS": "#4c9f70", "ADDITIONAL_VERIFICATION": "#e6a23c", "MANUAL_REVIEW": "#d9534f"}
    fig, axis = plt.subplots(figsize=(12, 6.5))
    for route in ROUTES:
        values = [class_metrics[name]["route_rates"][route] * 100 for name in classes]
        axis.bar(classes, values, bottom=bottoms, label=route, color=colors[route])
        bottoms = [bottom + value for bottom, value in zip(bottoms, values)]
    axis.set_title("Route distribution by application class (held-out profile)")
    axis.set_ylabel("Applications (%)")
    axis.set_ylim(0, 100)
    axis.tick_params(axis="x", rotation=28)
    axis.legend(loc="upper right")
    fig.tight_layout()
    fig.savefig(output, dpi=180)
    plt.close(fig)


def plot_ablations(ablations: list[dict[str, Any]], output: Path) -> None:
    plt = _setup_matplotlib()
    labels = [item["group"].replace("without_", "-") for item in ablations]
    x = list(range(len(labels)))
    width = 0.25
    fig, axis = plt.subplots(figsize=(11, 6))
    axis.bar([item - width for item in x], [row["precision"] for row in ablations], width, label="Precision")
    axis.bar(x, [row["recall"] for row in ablations], width, label="Recall")
    axis.bar([item + width for item in x], [row["f1"] for row in ablations], width, label="F1")
    axis.set_title("Signal-group removal ablation (held-out profile)")
    axis.set_ylabel("Metric")
    axis.set_ylim(0, 1.05)
    axis.set_xticks(x, labels, rotation=22)
    axis.legend()
    fig.tight_layout()
    fig.savefig(output, dpi=180)
    plt.close(fig)


def plot_thresholds(points: list[dict[str, Any]], output: Path) -> None:
    plt = _setup_matplotlib()
    fig, axis = plt.subplots(figsize=(8, 6.5))
    scatter = axis.scatter(
        [point["recall"] for point in points],
        [point["precision"] for point in points],
        c=[point["pass_min"] for point in points],
        s=[45 + point["review_max"] for point in points],
        cmap="viridis",
    )
    for point in points:
        axis.annotate(
            f"{point['review_max']}/{point['pass_min']}",
            (point["recall"], point["precision"]),
            xytext=(4, 4),
            textcoords="offset points",
            fontsize=7,
        )
    axis.set_title("Threshold sweep: precision vs recall (held-out profile)")
    axis.set_xlabel("Recall")
    axis.set_ylabel("Precision")
    axis.set_xlim(0, 1.03)
    axis.set_ylim(0, 1.03)
    colorbar = fig.colorbar(scatter, ax=axis)
    colorbar.set_label("pass_min")
    fig.tight_layout()
    fig.savefig(output, dpi=180)
    plt.close(fig)


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _markdown_report(payload: dict[str, Any], primary_name: str) -> str:
    lines = [
        "# AI Application Firewall Evaluation",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        "Flagged means any route other than `PASS_TO_ATS`. `LEGIT` comprises honest, campus-honest, and AI-assisted-honest applications. All figures are produced by the current engine without relabelling or tuning the generated records to improve results.",
        "",
        "## Headline metrics",
        "",
        "| Profile | Precision | Recall | F1 | Honest FPR | Campus FPR | AI-assisted-honest FPR |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, result in payload["profiles"].items():
        head = result["headline"]
        lines.append(
            f"| {name} | {_pct(head['precision'])} | {_pct(head['recall'])} | {_pct(head['f1'])} | "
            f"{_pct(head['honest_false_positive_rate'])} | {_pct(head['honest_campus_false_positive_rate'])} | "
            f"{_pct(head['ai_assisted_honest_false_positive_rate'])} |"
        )

    classes = payload["profiles"][primary_name]["classes"]
    lines.extend(
        [
            "",
            f"## Per-class results ({primary_name})",
            "",
            "| Class | Label | Pass | Additional verification | Manual review | Recall / FPR | Mean score |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for name, result in classes.items():
        rates = result["route_rates"]
        rate = result["false_positive_rate"] if result["label"] == LEGIT_LABEL else result["recall"]
        rate_name = "FPR" if result["label"] == LEGIT_LABEL else "recall"
        lines.append(
            f"| {name} | {result['label']} | {_pct(rates['PASS_TO_ATS'])} | "
            f"{_pct(rates['ADDITIONAL_VERIFICATION'])} | {_pct(rates['MANUAL_REVIEW'])} | "
            f"{rate_name} {_pct(rate)} | {result['mean_score']:.1f} |"
        )

    missed = [name for name, item in classes.items() if item["label"] != LEGIT_LABEL and (item["recall"] or 0) < 0.5]
    overflagged = [
        name for name, item in classes.items() if item["label"] == LEGIT_LABEL and (item["false_positive_rate"] or 0) > 0.1
    ]
    lines.extend(["", "## Observed weaknesses", ""])
    lines.append("- Missed abuse classes (<50% recall): " + (", ".join(missed) if missed else "none") + ".")
    lines.append("- Over-flagged legitimate classes (>10% FPR): " + (", ".join(overflagged) if overflagged else "none") + ".")
    lines.append(
        "- These are synthetic stress-test findings. The held-out profile uses different names, templates, and skill vocabulary from development, but it is not a substitute for a consented production sample."
    )

    lines.extend(
        [
            "",
            "## Signal-group ablation (held-out)",
            "",
            "Each configuration removes that signal group's reasons before scoring and hard-routing rules are applied.",
            "",
            "| Configuration | Precision | Recall | F1 | Δ recall | Δ F1 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for result in payload["ablations"]:
        lines.append(
            f"| {result['group']} | {_pct(result['precision'])} | {_pct(result['recall'])} | {_pct(result['f1'])} | "
            f"{result['delta_recall']:+.3f} | {result['delta_f1']:+.3f} |"
        )

    lines.extend(
        [
            "",
            "## Threshold sweep",
            "",
            "Labels below are `review_max/pass_min`. Note that hard-escalation rules remain active, so some threshold points can coincide.",
            "",
            "| review_max | pass_min | Precision | Recall | F1 | FPR |",
            "|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for result in payload["threshold_sweep"]:
        lines.append(
            f"| {result['review_max']} | {result['pass_min']} | {_pct(result['precision'])} | "
            f"{_pct(result['recall'])} | {_pct(result['f1'])} | {_pct(result['false_positive_rate'])} |"
        )
    lines.extend(
        [
            "",
            "## Charts",
            "",
            "- `route_by_class.png` — stacked route distribution.",
            "- `ablation.png` — precision, recall, and F1 with each signal group's reasons removed.",
            "- `threshold_sweep.png` — precision-recall view of routing thresholds.",
            "",
        ]
    )
    return "\n".join(lines)


def run(inputs: list[Path], output_dir: Path) -> dict[str, Any]:
    config = Config()
    profile_results: dict[str, Any] = {}
    loaded: dict[str, list[dict[str, Any]]] = {}
    for path in inputs:
        name = path.stem
        records = load_jsonl(path)
        loaded[name] = records
        started = time.perf_counter()
        rows = replay(records, config)
        classes = summarize_classes(rows)
        metrics = compute_metrics(rows)
        profile_results[name] = {
            "source": str(path),
            "duration_seconds": time.perf_counter() - started,
            "headline": _headline(metrics, classes),
            "classes": classes,
        }
        print(
            f"baseline {name}: n={len(rows)} precision={metrics['precision']:.4f} "
            f"recall={metrics['recall']:.4f} f1={metrics['f1']:.4f}"
        )

    primary_name = "heldout" if "heldout" in loaded else next(reversed(loaded))
    primary_records = loaded[primary_name]
    baseline = profile_results[primary_name]["headline"]
    print(f"running threshold sweep on {primary_name} ({len(primary_records)} records per point)")
    sweep = threshold_sweep(primary_records, config)
    print(f"running signal-group ablations on {primary_name}")
    ablations = run_ablations(primary_records, config, baseline)

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "positive_definition": "label != LEGIT and route != PASS_TO_ATS",
        "primary_profile": primary_name,
        "config": {
            "pass_min": config.pass_min,
            "review_max": config.review_max,
            "weights": dict(config.weights),
        },
        "profiles": profile_results,
        "threshold_sweep": sweep,
        "ablations": ablations,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = output_dir / "metrics.json"
    report_path = output_dir / "REPORT.md"
    metrics_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_path.write_text(_markdown_report(payload, primary_name), encoding="utf-8")
    plot_routes(profile_results[primary_name]["classes"], output_dir / "route_by_class.png")
    plot_ablations(ablations, output_dir / "ablation.png")
    plot_thresholds(sweep, output_dir / "threshold_sweep.png")
    print(f"wrote metrics and report to {output_dir}")
    return payload


def parse_args() -> argparse.Namespace:
    eval_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Replay firewall evaluation datasets and report metrics.")
    parser.add_argument(
        "--input",
        type=Path,
        nargs="+",
        default=[eval_dir / "data" / "dev.jsonl", eval_dir / "data" / "heldout.jsonl"],
    )
    parser.add_argument("--output-dir", type=Path, default=eval_dir / "results")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run(args.input, args.output_dir)


if __name__ == "__main__":
    main()
