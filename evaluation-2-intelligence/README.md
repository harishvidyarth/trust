# TR∩ST · Evaluation 2: Intelligence

Round 1 built a working pre-ATS firewall. Round 2 measures it, attacks it, closes the gaps the measurements exposed, and adds the building blocks for corroborating claims and for learning from recruiter feedback.

Everything from [Evaluation 1](../evaluation-1-foundation/README.md) is included and unchanged unless listed below. Team QUARTET: Harish Vidyarth N, Keerthisri D, Madhumitha N, Nakshatra PA.

## What is new in this round

| Area | What was added | Where | Tests |
|---|---|---|---|
| Measurement | Seeded dataset generator with a development and a held-out profile, an evaluation harness with per-class results, threshold sweep and ablation, charts | `eval/` | 4 |
| Red-team | Six attack strategies and two honest controls run against a fresh server per scenario, with before and after comparison | `redteam/` | 18 |
| Engine | `IDENTITY_DEVICE_ROTATION`: one person submitting from four or more devices within an hour is routed to verification, whatever the pace | `firewall/signals/automation.py`, `firewall/engine.py` | 4 |
| Corroboration | Background checks that confirm or contradict claims: GitHub, Crossref DOI, RDAP domain age, OIDC identity, and a scholarly-paper fallback chain | `firewall/enrichment/` | 40 |
| Learning | Recruiter-feedback weight learner with quorum and rollback, what-if policy simulator, drift detection, capacity-aware thresholds, ROI model, impact-ratio report, hash-chained audit log | `firewall/adaptive/` | 18 |

## Results

All results use synthetic data and our own attacker. They show what the system does under stress, not production accuracy.

### Held-out evaluation, 2,400 applications

| Metric | Value |
|---|---|
| Precision | 100.0% |
| Recall | 83.5% |
| Honest candidates flagged | 0.0% |
| AI-assisted honest candidates flagged | 0.0% |
| Honest students behind one campus network flagged | 0.0% |

| Class | Caught |
|---|---|
| Fabricated timelines and claims | 100.0% |
| Naive bots | 96.7% |
| Resume farms | 93.3% |
| Evasive bots (rotating identity) | 70.0% |
| Duplicates (aliases, name variants, copies) | 57.3% |

The first evaluation run reported that 95% of honest candidates were flagged. That was a bug in the dataset generator (templated resumes and phone numbers shared across classes), not in the engine. We fixed the generator and re-ran. The held-out profile uses different names, wording and skills from the development profile to reduce circular evaluation.

### Red-team: share of attack applications that reached the ATS

| Attack | Before | After fixes | After rotation signal |
|---|---:|---:|---:|
| Rotating identity (one person, many devices, slow pace) | 100% | 100% | 21.4% |
| Paraphrased copy | 100% | 60% | 60% |
| Slow and low, many identities | 83% | 50% | 50% |
| Email alias abuse | 40% | 30% | 30% |
| Naive flood | 33% | 33% | 33% |
| Resume farm | 33% | 33% | 33% |

| Honest control | Before | After fixes | After rotation signal |
|---|---:|---:|---:|
| Honest campus burst flagged | 44% | 0% | 0% |
| Honest AI-polished flagged | 0% | 30% | 30% |

The 30% on the AI-polished control is a flaw in the control, not a regression. All ten control resumes share one sentence skeleton with a few swapped words, so some pairs cross the 0.75 similarity threshold. By text alone that is indistinguishable from a resume farm. Flagged applications go to verification, never to rejection. We do not treat this figure as evidence, and the held-out evaluation above is the cleaner measure.

## Corroboration design rules

- Only data the candidate supplied or authorized is checked. There is no scraping of LinkedIn, and LinkedIn is supported only through verified name and email from Sign in with LinkedIn.
- Absence of data is neutral. Only contradictions count against a candidate.
- A slow or failing source never blocks an application. It just contributes no signal.
- Papers are accepted only when the title similarity is at least 0.9, an author surname matches and the year is within one. The first result is never accepted blindly.
- Positive evidence adds a capped trust bonus and cannot override hard fraud signals.

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
```

Evaluation:

```bash
python3 -m venv eval/.venv
eval/.venv/bin/pip install -r requirements.txt -r eval/requirements.txt
eval/.venv/bin/python eval/generate_dataset.py --profile dev
eval/.venv/bin/python eval/generate_dataset.py --profile heldout
eval/.venv/bin/python eval/run_eval.py
```

Red-team (the harness starts the firewall from the root `.venv`):

```bash
python3 -m venv redteam/.venv
redteam/.venv/bin/pip install -r redteam/requirements.txt
redteam/.venv/bin/python -m redteam.run --label mine
redteam/.venv/bin/python -m redteam.report
```

Optional environment variables for corroboration: `GITHUB_TOKEN` (raises the GitHub rate limit from 60 to 5,000 requests an hour) and `SEMANTIC_SCHOLAR_API_KEY`.

## Verified

| Check | Result |
|---|---|
| Core, resume, ATS, enrichment and adaptive tests | 72, 24, 13, 40 and 18 passed |
| Evaluation and red-team tests | 4 and 18 passed |
| Every test directory run from inside this folder | 189 passed in total |
| Comments and docstrings in the Python and JavaScript sources | None |

## Known limitations

- The corroboration connectors and the adaptive modules are tested on their own but are not yet wired into the API. A live decision does not use them yet.
- The ablation table only zeroes reason-code weights. Hard-escalation rules still fire, so use it as a rough indication.
- The rotation signal needs a person to use several devices. One device with many applications is handled by the velocity limits, and paraphrased copies still evade 60% of the time.
- A multi-paper claim can exceed the per-connector time budget because the scholarly sources are tried one after another.
- Evaluation data is synthetic, so the metrics are partly circular.

The incremental plan for this round is in [PLAN.md](PLAN.md).

License: MIT.
