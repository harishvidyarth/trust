# AI Application Firewall Evaluation

Generated: 2026-10-08T16:25:57.169001+00:00

Flagged means any route other than `PASS_TO_ATS`. `LEGIT` comprises honest, campus-honest, and AI-assisted-honest applications. All figures are produced by the current engine without relabelling or tuning the generated records to improve results.

## Headline metrics

| Profile | Precision | Recall | F1 | Honest FPR | Campus FPR | AI-assisted-honest FPR |
|---|---:|---:|---:|---:|---:|---:|
| dev | 99.9% | 87.7% | 93.4% | 0.0% | 0.3% | 0.0% |
| heldout | 100.0% | 87.7% | 93.5% | 0.0% | 0.0% | 0.0% |

## Per-class results (heldout)

| Class | Label | Pass | Additional verification | Manual review | Recall / FPR | Mean score |
|---|---|---:|---:|---:|---:|---:|
| ai_assisted_honest | LEGIT | 100.0% | 0.0% | 0.0% | FPR 0.0% | 100.0 |
| bot_evasive | ABUSE | 30.0% | 69.7% | 0.3% | recall 70.0% | 75.4 |
| bot_naive | ABUSE | 3.3% | 0.0% | 96.7% | recall 96.7% | 41.0 |
| duplicate | ABUSE | 21.3% | 21.3% | 57.3% | recall 78.7% | 37.1 |
| fabricated | ABUSE | 0.0% | 0.0% | 100.0% | recall 100.0% | 9.9 |
| farm | ABUSE | 6.7% | 53.3% | 40.0% | recall 93.3% | 52.1 |
| honest | LEGIT | 100.0% | 0.0% | 0.0% | FPR 0.0% | 100.0 |
| honest_campus | LEGIT | 100.0% | 0.0% | 0.0% | FPR 0.0% | 88.4 |

## Observed weaknesses

- Missed abuse classes (<50% recall): none.
- Over-flagged legitimate classes (>10% FPR): none.
- These are synthetic stress-test findings. The held-out profile uses different names, templates, and skill vocabulary from development, but it is not a substitute for a consented production sample.

## Signal-group ablation (held-out)

Each configuration removes that signal group's reasons before scoring and hard-routing rules are applied.

| Configuration | Precision | Recall | F1 | Δ recall | Δ F1 |
|---|---:|---:|---:|---:|---:|
| baseline | 100.0% | 87.7% | 93.5% | +0.000 | +0.000 |
| without_duplicates | 100.0% | 60.0% | 75.0% | -0.277 | -0.185 |
| without_automation | 100.0% | 54.5% | 70.5% | -0.333 | -0.229 |
| without_identity_links | 100.0% | 83.5% | 91.0% | -0.043 | -0.025 |
| without_qualification | 100.0% | 87.7% | 93.5% | +0.000 | +0.000 |
| without_consistency | 100.0% | 87.7% | 93.5% | +0.000 | +0.000 |
| without_resume_hidden | 100.0% | 87.7% | 93.5% | +0.000 | +0.000 |
| without_resume_injection | 100.0% | 87.7% | 93.5% | +0.000 | +0.000 |
| without_resume_stuffing | 100.0% | 87.7% | 93.5% | +0.000 | +0.000 |
| without_resume_divergence | 100.0% | 87.7% | 93.5% | +0.000 | +0.000 |

## Threshold sweep

Labels below are `review_max/pass_min`. Note that hard-escalation rules remain active, so some threshold points can coincide.

| review_max | pass_min | Precision | Recall | F1 | FPR |
|---:|---:|---:|---:|---:|---:|
| 20 | 60 | 100.0% | 77.1% | 87.0% | 0.0% |
| 20 | 70 | 100.0% | 87.7% | 93.5% | 0.0% |
| 20 | 80 | 100.0% | 88.4% | 93.8% | 0.0% |
| 20 | 90 | 82.1% | 88.4% | 85.1% | 32.2% |
| 40 | 60 | 100.0% | 77.1% | 87.0% | 0.0% |
| 40 | 70 | 100.0% | 87.7% | 93.5% | 0.0% |
| 40 | 80 | 100.0% | 88.4% | 93.8% | 0.0% |
| 40 | 90 | 82.1% | 88.4% | 85.1% | 32.2% |
| 60 | 70 | 100.0% | 87.7% | 93.5% | 0.0% |
| 60 | 80 | 100.0% | 88.4% | 93.8% | 0.0% |
| 60 | 90 | 82.1% | 88.4% | 85.1% | 32.2% |

## Charts

- `route_by_class.png` — stacked route distribution.
- `ablation.png` — precision, recall, and F1 with each signal group's reasons removed.
- `threshold_sweep.png` — precision-recall view of routing thresholds.
