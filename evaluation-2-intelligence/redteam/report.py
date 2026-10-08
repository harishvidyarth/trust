from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", str(Path(__file__).resolve().parent / ".matplotlib"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from redteam.attacks import ATTACK_NAMES, CONTROL_NAMES


RESULTS_DIR = Path(__file__).resolve().parent / "results"


def _load_results() -> list[dict[str, Any]]:
    results = []
    for path in sorted(RESULTS_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") == 1 and isinstance(data.get("scenarios"), list):
            results.append(data)
    if not results:
        raise RuntimeError(f"no result JSON files found in {RESULTS_DIR}")
    return results


def _scenario_map(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {scenario["name"]: scenario for scenario in result["scenarios"]}


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _first_flag(scenario: dict[str, Any]) -> str:
    seconds = scenario.get("time_to_first_flag_seconds")
    index = scenario.get("first_flag_application_index")
    return "Never" if seconds is None else f"{seconds:.1f}s (application {index})"


def _plot_grouped(
    results: list[dict[str, Any]],
    names: tuple[str, ...],
    metric: str,
    title: str,
    output: Path,
) -> None:
    labels = [str(result["label"]) for result in results]
    width = 0.8 / max(1, len(labels))
    positions = list(range(len(names)))
    figure_width = max(9.0, len(names) * 1.7)
    fig, axis = plt.subplots(figsize=(figure_width, 5.8))
    for label_index, result in enumerate(results):
        scenarios = _scenario_map(result)
        offsets = [position - 0.4 + width / 2 + label_index * width for position in positions]
        values = [float(scenarios.get(name, {}).get(metric) or 0.0) * 100.0 for name in names]
        bars = axis.bar(offsets, values, width=width, label=labels[label_index])
        axis.bar_label(bars, fmt="%.1f", padding=2, fontsize=8)
    axis.set_title(title)
    axis.set_ylabel("Rate (%)")
    axis.set_ylim(0, 110)
    axis.set_xticks(positions, [name.replace("_", "\n") for name in names])
    axis.grid(axis="y", alpha=0.25)
    axis.legend(title="Label")
    fig.tight_layout()
    fig.savefig(output, dpi=160)
    plt.close(fig)


def _markdown(results: list[dict[str, Any]]) -> str:
    lines = [
        "# Firewall Robustness Report",
        "",
        "A flag means the route was `ADDITIONAL_VERIFICATION` or `MANUAL_REVIEW`; reason codes on a "
        "`PASS_TO_ATS` decision are observations, not detections for these metrics.",
        "",
        "## Attack evasion",
        "",
        "| Label | Attack | Applications | Evasion | Detection | First flag | Reason codes seen |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for result in results:
        for scenario in result["scenarios"]:
            if scenario["kind"] != "attack":
                continue
            reasons = ", ".join(
                f"{code} ({count})" for code, count in scenario["reason_codes_seen"].items()
            ) or "None"
            lines.append(
                f"| {result['label']} | `{scenario['name']}` | {scenario['application_count']} | "
                f"{_percent(scenario['evasion_rate'])} | {_percent(scenario['detection_rate'])} | "
                f"{_first_flag(scenario)} | {reasons} |"
            )

    lines.extend(
        [
            "",
            "## Control false positives",
            "",
            "| Label | Control | Applications | Passed | False-positive rate | Reason codes seen |",
            "|---|---|---:|---:|---:|---|",
        ]
    )
    for result in results:
        for scenario in result["scenarios"]:
            if scenario["kind"] != "control":
                continue
            reasons = ", ".join(
                f"{code} ({count})" for code, count in scenario["reason_codes_seen"].items()
            ) or "None"
            lines.append(
                f"| {result['label']} | `{scenario['name']}` | {scenario['application_count']} | "
                f"{scenario['passed_count']} | {_percent(scenario['control_false_positive_rate'])} | {reasons} |"
            )

    lines.extend(["", "## Gaps surfaced", ""])
    gaps: list[str] = []
    for result in results:
        for scenario in result["scenarios"]:
            if scenario["kind"] == "attack" and (scenario["evasion_rate"] or 0) > 0:
                gaps.append(
                    f"- **{result['label']} / {scenario['name']}:** "
                    f"{_percent(scenario['evasion_rate'])} of attack submissions reached the ATS."
                )
            if scenario["kind"] == "control" and (scenario["control_false_positive_rate"] or 0) > 0:
                gaps.append(
                    f"- **{result['label']} / {scenario['name']}:** "
                    f"{_percent(scenario['control_false_positive_rate'])} of legitimate controls were flagged."
                )
    lines.extend(gaps or ["- No route-level evasions or control false positives were observed."])
    lines.extend(
        [
            "",
            "## Charts",
            "",
            "![Attack evasion rates](evasion_rate.png)",
            "",
            "![Control false-positive rates](control_false_positive_rate.png)",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    results = _load_results()
    _plot_grouped(
        results,
        ATTACK_NAMES,
        "evasion_rate",
        "Attack evasion rate by scenario and result label",
        RESULTS_DIR / "evasion_rate.png",
    )
    _plot_grouped(
        results,
        CONTROL_NAMES,
        "control_false_positive_rate",
        "Control false-positive rate by scenario and result label",
        RESULTS_DIR / "control_false_positive_rate.png",
    )
    report = RESULTS_DIR / "ROBUSTNESS.md"
    report.write_text(_markdown(results), encoding="utf-8")
    print(f"Wrote {report}")


if __name__ == "__main__":
    main()
