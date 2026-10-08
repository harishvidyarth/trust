# Evaluation 2 plan: Intelligence

Goal: measure the firewall honestly, attack it, close the gaps found, and add the building blocks for corroboration and learning.

Builds on: [Evaluation 1](../evaluation-1-foundation/PLAN.md). Everything there is included.

## Incremental steps

| Step | What | Files | Check | Status |
|---|---|---|---|---|
| S1 | Seeded dataset generator with development and held-out profiles | `eval/generate_dataset.py` | `pytest eval/tests` | Done. Running it exposed two generator bugs, fixed before any result was used. |
| S2 | Evaluation harness: precision, recall, per-class, honest false-positive rate, threshold sweep, ablation, charts | `eval/run_eval.py` | `eval/.venv/bin/python eval/run_eval.py` | Done |
| S3 | Red-team harness with six attacks and two honest controls | `redteam/` | `pytest redteam/tests`, `python -m redteam.run` | Done |
| S4 | Close measured gaps: shared-network bursts (honest campus flagged 44% to 0%), slow identity rotation (100% to 21.4% evasion) | `firewall/signals/automation.py`, `firewall/engine.py` | `pytest tests` | Done, 4 new tests |
| S5 | Corroboration connectors: GitHub, Crossref DOI, RDAP domain age, OIDC identity | `firewall/enrichment/` | `pytest tests_enrichment` | Done |
| S6 | Scholarly paper fallback chain with strict acceptance rules | `firewall/enrichment/scholar.py` | `pytest tests_enrichment` | Done, 8 new tests. Dead Google search call removed. |
| S7 | Adaptive modules: feedback learner, policy simulator, drift, capacity, ROI, impact ratio, audit log | `firewall/adaptive/` | `pytest tests_adaptive` | Done, not wired into the API |
| S8 | Security: replace the standard XML parser with `defusedxml` for arXiv, validate URL schemes | `firewall/enrichment/scholar.py`, `firewall/llm/`, `firewall/resume/parse.py` | Semgrep | Done |

## Verified in this round

- 189 tests pass across all directories, run from inside this folder.
- Held-out evaluation: precision 100.0%, recall 83.5%, honest false positives 0.0%.

## Not in this round

Wiring enrichment and adaptive learning into the live decision path. Embedding similarity for paraphrased copies. Cross-employer federation (Round 3).

## Open items carried forward

- Wire corroboration into the engine for uncertain cases only.
- Replace the ablation method with one that removes reasons from the decision.
- A more realistic honest AI-polished control for the red-team.
