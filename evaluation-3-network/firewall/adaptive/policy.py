from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from firewall.models import Route


class PolicySimulator:
    @staticmethod
    def replay(
        records: Iterable[Mapping[str, Any]],
        weights: Mapping[str, int],
        pass_min: int,
        review_max: int,
    ) -> dict[str, Any]:
        PolicySimulator._validate_thresholds(pass_min, review_max)
        rows = list(records)
        route_counts: Counter[str] = Counter({route.value: 0 for route in Route})
        baseline_counts: Counter[str] = Counter({route.value: 0 for route in Route})
        transitions: Counter[str] = Counter()
        predictions: list[tuple[str, str]] = []
        moved = 0
        details: list[dict[str, Any]] = []
        for index, record in enumerate(rows):
            baseline_score = PolicySimulator._score(record, None)
            proposed_score = PolicySimulator._score(record, weights)
            baseline_route = PolicySimulator._route(record, baseline_score, pass_min, review_max)
            proposed_route = PolicySimulator._route(record, proposed_score, pass_min, review_max)
            route_counts[proposed_route.value] += 1
            baseline_counts[baseline_route.value] += 1
            if proposed_route != baseline_route:
                moved += 1
                transitions[f"{baseline_route.value}->{proposed_route.value}"] += 1
            label = record.get("label")
            if label in {"legit", "bad"}:
                predictions.append((str(label), proposed_route.value))
            details.append(
                {
                    "index": index,
                    "baseline_score": baseline_score,
                    "score": proposed_score,
                    "baseline_route": baseline_route.value,
                    "route": proposed_route.value,
                }
            )
        result: dict[str, Any] = {
            "total": len(rows),
            "route_counts": dict(route_counts),
            "baseline_route_counts": dict(baseline_counts),
            "moved": moved,
            "transitions": dict(sorted(transitions.items())),
            "records": details,
        }
        if predictions:
            result["metrics"] = PolicySimulator._metrics(predictions)
        return result

    @staticmethod
    def shadow_mode_report(records: Iterable[Mapping[str, Any]], policy: Mapping[str, Any]) -> dict[str, Any]:
        replay = PolicySimulator.replay(
            records,
            policy.get("weights", {}),
            int(policy["pass_min"]),
            int(policy["review_max"]),
        )
        return {"mode": "shadow", "effect": "none", "would_have_done": replay, "policy": dict(policy)}

    @staticmethod
    def threshold_sweep(
        records: Iterable[Mapping[str, Any]],
        weights: Mapping[str, int],
        pass_min_values: Sequence[int],
        review_max_values: Sequence[int],
    ) -> list[dict[str, Any]]:
        rows = list(records)
        points: list[dict[str, Any]] = []
        for pass_min in sorted(set(pass_min_values)):
            for review_max in sorted(set(review_max_values)):
                if not 0 <= review_max < pass_min <= 100:
                    continue
                replay = PolicySimulator.replay(rows, weights, pass_min, review_max)
                point = {
                    "pass_min": pass_min,
                    "review_max": review_max,
                    "route_counts": replay["route_counts"],
                    "moved": replay["moved"],
                }
                if "metrics" in replay:
                    point["metrics"] = replay["metrics"]
                points.append(point)
        return points

    @staticmethod
    def _score(record: Mapping[str, Any], weights: Mapping[str, int] | None) -> int:
        total = 0
        for reason in record.get("reasons", []):
            code = reason["code"] if isinstance(reason, Mapping) else reason.code
            original = reason.get("weight", 0) if isinstance(reason, Mapping) else reason.weight
            total += int(weights.get(code, original) if weights is not None else original)
        return max(0, min(100, 100 - total))

    @staticmethod
    def _route(record: Mapping[str, Any], score: int, pass_min: int, review_max: int) -> Route:
        reason_codes = {
            reason["code"] if isinstance(reason, Mapping) else reason.code
            for reason in record.get("reasons", [])
        }
        if reason_codes.intersection(record.get("hard_escalation_codes", [])):
            return Route.MANUAL_REVIEW
        if score >= pass_min:
            return Route.PASS_TO_ATS
        if score <= review_max:
            return Route.MANUAL_REVIEW
        return Route.ADDITIONAL_VERIFICATION

    @staticmethod
    def _metrics(predictions: Sequence[tuple[str, str]]) -> dict[str, float | int]:
        tp = sum(label == "bad" and route != Route.PASS_TO_ATS.value for label, route in predictions)
        fp = sum(label == "legit" and route != Route.PASS_TO_ATS.value for label, route in predictions)
        fn = sum(label == "bad" and route == Route.PASS_TO_ATS.value for label, route in predictions)
        tn = sum(label == "legit" and route == Route.PASS_TO_ATS.value for label, route in predictions)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        false_positive_rate = fp / (fp + tn) if fp + tn else 0.0
        return {
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "false_positive_rate": round(false_positive_rate, 6),
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "true_negatives": tn,
        }

    @staticmethod
    def _validate_thresholds(pass_min: int, review_max: int) -> None:
        if not 0 <= review_max < pass_min <= 100:
            raise ValueError("thresholds must satisfy 0 <= review_max < pass_min <= 100")
