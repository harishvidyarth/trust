from __future__ import annotations

import json
from collections import Counter

from eval.generate_dataset import CLASS_NAMES, generate_records
from firewall import models


def test_generation_is_deterministic_by_seed():
    first = generate_records("dev", count_per_class=2, seed=77)
    second = generate_records("dev", count_per_class=2, seed=77)
    different = generate_records("dev", count_per_class=2, seed=78)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert json.dumps(first, sort_keys=True) != json.dumps(different, sort_keys=True)


def test_class_and_label_balance():
    records = generate_records("heldout", count_per_class=3)
    classes = Counter(item["class"] for item in records)
    labels = Counter(item["label"] for item in records)

    assert classes == Counter({name: 3 for name in CLASS_NAMES})
    assert labels == {"LEGIT": 9, "ABUSE": 15}


def test_records_are_ordered_and_validate_against_public_models():
    records = generate_records("dev", count_per_class=3)
    timestamps = []
    for item in records:
        models.Application.model_validate(item["application"])
        models.JobRequirements.model_validate(item["job"])
        timestamps.append(item["application"]["signals"]["submitted_at"])

    assert timestamps == sorted(timestamps)

