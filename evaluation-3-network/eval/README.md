# Offline evaluation harness

This directory evaluates the current firewall engine against deterministic synthetic application streams. It does not use an AI-generated-text detector and does not call the network. The held-out profile deliberately uses different names, resume language, companies, and skills from the development profile.

## Setup

From the repository root:

```bash
python3 -m venv eval/.venv
eval/.venv/bin/python -m pip install -r requirements.txt -r eval/requirements.txt
```

The root requirements are installed into the **evaluation venv only** because importing the public firewall models requires Pydantic and running the requested checks requires pytest. Neither the root venv nor the root requirements file is modified.

## Generate the two profiles

```bash
eval/.venv/bin/python eval/generate_dataset.py --profile dev
eval/.venv/bin/python eval/generate_dataset.py --profile heldout
```

Each profile defaults to 300 records per class (2,400 records total). Override it with `--count-per-class N` or the deterministic profile seed with `--seed N`.

To use local, consented resume rows for only the `honest` class:

```bash
eval/.venv/bin/python eval/generate_dataset.py \
  --profile heldout \
  --honest-csv /absolute/path/to/resumes.csv
```

Supported CSV headers are `name`, `email`, `phone`, `skills`, and either `resume_text` or `summary`. `skills` may be a JSON list or comma/semicolon/pipe-separated. Missing columns fall back to synthetic values. The hook never downloads data.

## Run evaluation and tests

```bash
eval/.venv/bin/python eval/run_eval.py
eval/.venv/bin/python -m pytest -q eval/tests
```

Outputs are written to `eval/results/`:

- `metrics.json`
- `REPORT.md`
- `route_by_class.png`
- `ablation.png`
- `threshold_sweep.png`

The evaluator replays each profile in `submitted_at` order with a fresh `InMemoryApplicationStore`. It also starts with a fresh store for every threshold point and every signal-group ablation.

