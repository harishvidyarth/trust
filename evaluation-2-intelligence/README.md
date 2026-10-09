# TR∩ST · Evaluation 2: Intelligence

Round 1 built a working pre-ATS firewall. Round 2 adds AI reasoning that never decides a route, many small checks instead of a few big ones, measurement, login with roles, optional Redis, and one console for candidates, recruiters and admins.

Everything from [Evaluation 1](../evaluation-1-foundation/README.md) is included unless listed below. Team QUARTET: Harish Vidyarth N, Keerthisri D, Madhumitha N, Nakshatra PA.

## The rule for AI in this project

The language model never decides a route and never changes a weight. Every model answer is checked by plain code first. Temperature is 0, answers must match a JSON schema, the timeout is short, and any failure falls back silently to the rules. Anything the model quotes must appear word for word in the resume or it is thrown away. Resume text is treated as untrusted data and is never followed as instructions.

## What is new in this round

| Area | What was added | Where |
|---|---|---|
| Many small checks | Resume integrity is now 18 small checks in four families (hidden text, injection, stuffing, divergence), each with a family cap. The four old codes stay at weight 0 for compatibility. | `firewall/resume/integrity.py` |
| LLM parsing | The model helps only when the rules find no skills and no experience. Values must appear verbatim in the text. | `firewall/resume/parse.py` |
| Hidden text intent | Hidden text is labelled keyword stuffing, screener instruction or harmless, with the exact quote. Weight unchanged. | `firewall/resume/intent.py` |
| Style judge | Heuristic and model blended 0.4 and 0.6 only when the model answer is valid. Quotes verified. Weight 0. | `firewall/resume/style.py` |
| Plain reasoning | A recruiter paragraph, a candidate fix list and a plain sentence for every reason. A test checks every reason code against the wording rule. | `firewall/reasoning.py` |
| Paraphrase detection | Embedding similarity with nomic-embed-text, off by default. | `firewall/signals/duplicates.py` |
| New identity checks | Link reuse, fuzzy identity and email alias, using store indexes so the engine never scans every application. | `firewall/signals/identity_links.py` |
| Corroboration | GitHub, DOI and scholarly checks plus role profiles (general, finance, hardware, sales and design), run only for scores 41 to 69 and only when `FIREWALL_ENRICH=1`. | `firewall/enrichment/`, `firewall/enrichment/roles/` |
| Connectors | Lever style webhook, `FIREWALL_ROUTES` per route destinations, queued delivery with dead letters and replay, SSRF guard. | `firewall/webhooks.py`, `firewall/delivery.py` |
| Login and roles | Candidate, recruiter and admin. Argon2 passwords, cookie sessions with CSRF, lockout, audit log, overrides kept separate from the stored decision. | `firewall/auth/` |
| Redis | Optional persistent store, counters, cache and queue. Falls back to memory when Redis is down. | `firewall/redis_layer/` |
| Intake and intel | Candidate supplied links and a LinkedIn Save to PDF export, with consent and a dispute flow. No LinkedIn scraping. | `firewall/intake_routes.py`, `firewall/intel/` |
| Console | One app for all three roles in the original warm theme, with My applications and Fix and resubmit for candidates and a Destinations tab for recruiters. | `web/console/` |
| Candidate self service | A list of your own applications, a progress card after you fix and resend, file checks before upload, job presets. A resubmit is not counted as a duplicate of itself. | `firewall/candidate_routes.py` |
| Final destinations | Pass goes to the ATS, Additional verification to a verification inbox, Manual review to a review inbox, with an optional Slack message. | `firewall/delivery.py` |
| Registries | ORCID lookup is live. Patent lookup needs a free key. Formats that could not be verified no longer penalise anyone. | `firewall/enrichment/roles/` |
| LinkedIn export | Reads two column Save to PDF exports and says how much it could read. | `firewall/intel/linkedin.py` |
| Identity check | An optional check after the form. Face prompts in the browser, a spoken one time code transcribed on the server with a local Whisper model, and an optional ID photo compared with camera frames using local OpenCV models. The server issues the prompts, limits every upload, stores no media and never changes a score or route. The candidate sees only that it was received. Recruiters see plain notes with the limits stated. | `firewall/identity/`, `web/console/identity.js` |

## Results

All data is synthetic and the attacker is our own. These numbers show behaviour under stress, not production accuracy.

### Held-out evaluation, 2,400 applications

| Metric | Start of Round 2 | End of Round 2 |
|---|---:|---:|
| Precision | 100.0% | 100.0% |
| Recall | 83.5% | 87.7% |
| Honest candidates flagged | 0.0% | 0.0% |
| AI assisted honest flagged | 0.0% | 0.0% |
| Honest campus candidates flagged | 0.0% | 0.0% |
| Duplicates caught | 57.3% | 78.7% |

On the development profile, precision is 99.9% and 0.3% of honest campus candidates are flagged.

The gain in recall comes from the new identity checks. The dataset generator creates alias and name variants, so this gain is partly circular and should not be read as a production figure.

### Ablation, held out (reasons removed before scoring and routing)

| Removed group | Recall | Change in F1 |
|---|---:|---:|
| none | 87.7% | 0.0 |
| duplicates | 60.0% | minus 18.5 |
| automation | 54.5% | minus 22.9 |
| identity links (link reuse, fuzzy identity, email alias) | 83.5% | minus 2.5 |
| qualification, consistency, resume checks | 87.7% | 0.0 |

The resume integrity checks show no effect here because the generated records contain no resume files. Those checks are measured by the sample resumes and by the red-team instead.

### Red-team, share of attack applications that reached the ATS

| Attack | Round 1 | Start of Round 2 | End of Round 2 |
|---|---:|---:|---:|
| Rotating identity | 100% | 21.4% | 21.4% |
| Paraphrased copy | 100% | 60% | 60% (20% with embeddings on) |
| Slow and low, many identities | 83% | 50% | 50% |
| Email alias abuse | 40% | 30% | 10% |
| Naive flood | 33% | 33% | 33% |
| Resume farm | 33% | 33% | 33% |

Honest controls: the campus burst is flagged 0% of the time. The AI polished control is flagged 30%, which is a flaw in the control. All ten resumes share one sentence skeleton, so some pairs look like a resume farm. Flagged applications go to verification, never to rejection.

### Embedding similarity

| Measure | Off | On |
|---|---:|---:|
| Paraphrase evasion, 10 template paraphrases | 60% | 20% |
| Honest resumes flagged, held out, 900 | 0 | 11 (1.22 points) |

The 0.95 threshold was tuned on the development split. A first try at 0.85 flagged 655 of 900 honest resumes and was rejected. All 11 extra flags go to Additional verification.

### Writing style judge, 45 synthetic resumes

| Mode | Precision | Recall | Human flagged as AI |
|---|---:|---:|---:|
| Heuristic only | 100% | 50% | 0 of 15 |
| Hybrid | 97% | 97% | 1 of 15 |

The resumes are template written and the labels come from how they were built. The model may find its own style easier to spot. This does not support an accuracy claim, and the style estimate never changes a decision.

### Model off, on and unreachable

Four samples were sent to three servers: model off, model on, and model on but unreachable.

| Sample | Score and route, off, on and unreachable | Style mode on |
|---|---|---|
| Clean senior resume | 100 pass, same in all three | hybrid, 3 verified quotes |
| Hidden injection attack | 51 manual review, same in all three | hybrid, 3 verified quotes |
| Generic AI style resume | 95 pass, same in all three | hybrid, 3 verified quotes |
| Messy resume the rules cannot read | 45 off, 65 on, 45 unreachable, route additional verification in all three | hybrid, 3 verified quotes |

On the messy resume the model finds skills that appear word for word in the text, so a false missing skill penalty is lifted. The route did not change here. With the model unreachable the result equals the rules result, as designed. The model adds 5 to 13 seconds per resume, so it stays off by default.

### Speed of the identity checks

2,400 applications through the engine: 134 s with a full scan, 13 s with indexes, identical results.

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
```

Start everything in one command (API on 8000, console on 8081, both on this machine only):

```bash
FIREWALL_ADMIN_USER=you FIREWALL_ADMIN_PASSWORD='choose-a-long-password' python3 scripts/run_all.py
```

Open `http://localhost:8081/console/`. Settings are in `scripts/README_run.md`.

To serve other laptops on the same Wi-Fi, add `--lan`. It switches to HTTPS, because browsers block the camera on plain http addresses. Steps are in `scripts/README_lan.md`. Without Redis a restart signs everyone out.

The launcher looks for Ollama on this machine. If it finds the model, it switches the language model on and warms it, then prints "Language model: on". If not, it prints why it stays off. Set `FIREWALL_LLM=0` to force it off.

## Known limits and open items

- DAST has not been independently verified. A third party must run and accept it. One scan was run by us by mistake and is not counted.
- ICAI, ACCA, CFA, SEBI and MCA have no free public lookup, so they give no signal. Their formats are unverified and cannot penalise anyone. The patent lookup is untested live because it needs a free key.
- The LinkedIn export parser was built from the known layout and tested on synthetic two column files only. A real export may still break it.
- The verification and review inboxes live in memory, so they empty when the API restarts. Decisions are kept.
- Uploading with the model on takes 5 to 13 seconds.
- Name search and corroboration have only been tested with fakes. They have not been run against the live services.
- Passive name search needs a consent flag and is limited to scores 41 to 69.
- The identity check is advice only. The face prompts are measured in the candidate's browser and can be forged. The voice estimate is rule based and called all six machine made macOS voices human, so only the spoken code matters, and that needs the local speech model. The ID photo match uses the published SFace threshold of 0.363. We tested it on one sample photo made darker, blurred and smaller (closeness 0.85 to 1.0) and on a different person (0.13). We have not measured two real photos of the same person, nor accuracy across skin tones, ages or genders. It can pass a look alike or a photo held to the camera. Details are in `firewall/identity/FAIRNESS.md`.
- Video recording is not built, on purpose. The face and speech models are not in git. Each machine downloads them into `.state/` (see `scripts/README_run.md`), and without them those two parts report that they were not available.
- A cold model load can pass the 5 second timeout and the first request then falls back to the rules.
- Every figure above is synthetic and partly circular.
- Round 3 was not rebuilt and has not been checked against these changes.

The step by step status is in [PLAN.md](PLAN.md).

License: MIT.
