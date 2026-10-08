# Evaluation 1 plan: Foundation

Goal: a working pre-ATS firewall that a recruiter and a judge can run and see end to end.

Builds on: nothing. This is the base for Rounds 2 and 3.

## Incremental steps

| Step | What | Files | Check | Status |
|---|---|---|---|---|
| S1 | Data contract for applications, decisions and reasons | `firewall/models.py` | `pytest tests` | Done |
| S2 | Deterministic engine with reason codes and configurable routes | `firewall/engine.py`, `firewall/config.py`, `firewall/signals/` | `pytest tests` | Done |
| S3 | Fix measured defects: honest false positives, flat qualification penalty, skill-name variants, future end dates, per-person velocity, shared-network bursts | `firewall/signals/` | `pytest tests` | Done |
| S4 | Indexed store for near-linear scaling | `firewall/store.py`, `firewall/index_keys.py` | `python scripts/benchmark.py` | Done, 5,000 in 1.75 s |
| S5 | API: evaluate, webhook with HMAC, decisions list, stats, CORS, security headers, optional API key, no double-forwarding, timestamp sanitizing | `firewall/api.py` | `pytest tests`, ZAP scan | Done |
| S6 | Resume integrity: hidden text, injection, stuffing, parse divergence; upload and X-ray routes | `firewall/resume/` | `pytest tests_resume` | Done |
| S7 | ATS connectors: Greenhouse and Lever boards, forwarders, sandbox ATS and naive ranker | `firewall/connectors/`, `ats/` | `pytest tests_ats` | Done |
| S8 | Recruiter dashboard, career page with behavioral SDK, Resume X-ray, light theme | `web/` | Open the three pages | Done; screenshots reviewed, no automated browser test |
| S9 | Security scans | `docs/security/` | ZAP report, Semgrep | Done |

## Demo script

See the three-minute demo in [README.md](README.md).

## Verified in this round

- `pytest tests`: 68 passed. `pytest tests_resume`: 24 passed. `pytest tests_ats`: 13 passed.
- ZAP API scan: 118 passed, 0 failed.

## Not in this round

Measurement and red-teaming, profile enrichment, adaptive learning (Round 2). Cross-employer federation (Round 3).

## Open items carried forward

- Per-person hourly window to catch slow identity rotation.
- Embedding similarity for paraphrased copies.
- A browser-automated test of the three pages.
