# TrustGate · Evaluation 1: Foundation

A pre-ATS application firewall. It sits in front of Workday, SuccessFactors, Greenhouse, iCIMS or Oracle, inspects every application before a recruiter sees it, and routes it to **Pass to ATS**, **Additional Verification** or **Manual Review**, with a reason for every decision.

The hard constraint of the problem: candidates who use AI to polish a resume are legitimate. TrustGate never scores writing style. It judges submission behavior, identity and duplicate patterns, qualification, timeline consistency, and whether a resume hides anything from people that a parser can still read.

Team QUARTET: Harish Vidyarth N, Keerthisri D, Madhumitha N, Nakshatra PA. Problem SW-05, KERNEL PRIME'26.

## What this round delivers

| Capability | Where | Status |
|---|---|---|
| Deterministic decision engine with reason codes and configurable routing | `firewall/engine.py`, `firewall/signals/` | Verified, 68 tests |
| Duplicate, velocity, qualification and timeline signals | `firewall/signals/` | Verified |
| Indexed store: 5,000 applications in 1.75 s on one laptop | `firewall/store.py`, `scripts/benchmark.py` | Measured |
| Resume integrity: hidden text, prompt injection, keyword stuffing, parser-versus-human divergence | `firewall/resume/` | Verified, 24 tests |
| ATS integration: Greenhouse and Lever job boards, Greenhouse-style forwarder, signed webhooks, retries, dead-letter list, sandbox ATS with a naive keyword ranker | `firewall/connectors/`, `ats/` | Verified, 13 tests |
| Recruiter dashboard, career page with behavioral SDK, Resume X-ray (light theme) | `web/` | Screenshots reviewed; no automated browser test yet |
| Security: Semgrep and OWASP ZAP | `docs/security/` | ZAP: 118 rules passed, 0 failed |

## How an application flows

```
Application ─► Connector ─► Resume integrity ─► Identity and duplicates ─► Behavior
                                                                              │
        Pass to ATS ◄── Trust score + reason codes ◄── Qualification and timeline
        Additional Verification
        Manual Review
```

Nothing is rejected automatically. The worst outcome for an honest candidate is a lightweight verification step.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q

.venv/bin/uvicorn firewall.api:app --port 8000
cd web && python3 -m http.server 8080
```

Open:

- Recruiter dashboard: `http://localhost:8080/dashboard/index.html`
- Career page with behavioral SDK: `http://localhost:8080/career-page/index.html`
- Resume X-ray: `http://localhost:8080/resume-xray/index.html`
- API docs: `http://localhost:8000/docs`

Optional sandbox ATS: `ATS_SANDBOX_API_KEY=sandbox-key .venv/bin/python -m ats.sandbox --port 8090`

## Three-minute demo

1. Open **Resume X-ray**, upload `firewall/resume/samples/honest_ai_polished.pdf`, and click **Inspect**. Both views match and there are no flags.
2. Upload `firewall/resume/samples/stuffed_hidden_text.pdf`. The human view scores **20.3** against the ATS parser's **71.9**, with the hidden text highlighted and four flags.
3. Click **Send through the firewall**. The stuffed resume scores 0 and goes to **Manual Review**. The honest one scores 88 and passes.
4. Open the **dashboard** to see the funnel, reason-code mix, thresholds and the live feed.
5. On the **career page**, toggle **Simulate bot** and submit to see the contrast with a human-paced submission.

## API

| Method and path | Purpose |
|---|---|
| `POST /v1/applications/evaluate` | Evaluate a structured application |
| `POST /v1/applications/upload` | Upload a PDF, DOCX or TXT resume (5 MB limit), analyze it and decide |
| `POST /v1/resume/inspect` | Return the human view, the parser view, hidden spans, flags and both naive ATS scores |
| `POST /v1/webhooks/greenhouse` | Greenhouse-style webhook, optional HMAC signature |
| `GET /v1/decisions` | Recent decisions for the dashboard |
| `GET /v1/decisions/{id}` | One decision |
| `GET /v1/stats` | Counts per route and top reason codes |
| `GET /v1/mock-ats/applications` | What the simulated ATS actually received |
| `GET /healthz` | Liveness |

## Reason codes

| Group | Codes |
|---|---|
| Duplicates | `DUP_EMAIL`, `DUP_PHONE`, `DUP_SAME_JOB`, `DUP_RESUME_NEAR`, `TEMPLATE_REUSE` |
| Behavior | `VELOCITY_HIGH`, `NETWORK_BURST` (weak, shared networks), `FAST_SUBMIT`, `PASTE_BULK` |
| Qualification and timeline | `QUAL_MISSING_MUST_HAVE`, `QUAL_UNDER_EXPERIENCE`, `TIMELINE_INVALID`, `TIMELINE_OVERLAP` |
| Resume integrity | `RESUME_HIDDEN_TEXT`, `RESUME_PROMPT_INJECTION`, `RESUME_KEYWORD_STUFFING`, `RESUME_PARSE_DIVERGENCE` |

## Configuration

| Variable | Effect |
|---|---|
| `FIREWALL_API_KEY` | When set, every route except `/healthz` requires an `X-API-Key` header |
| `FIREWALL_CORS_ORIGINS` | Allowed browser origins, default `http://localhost:8080,http://127.0.0.1:8080` |
| `FIREWALL_WEBHOOK_SECRET` | When set, the Greenhouse webhook requires an HMAC-SHA256 `Signature` header |
| `FIREWALL_CONFIG` | Path to a JSON file overriding thresholds and weights |
| `FIREWALL_LLM=1` | Enables the optional local Ollama summary writer. It never affects score or route. |

## Verified results

| Check | Result |
|---|---|
| Core tests, resume tests, ATS tests | 68, 24 and 13 passed |
| Benchmark, 5,000 synthetic applications | 1.75 s, about 2,860 per second, engine only, one machine |
| OWASP ZAP API scan, 56 URLs | 118 rules passed, 0 failed, 0 warnings |
| Semgrep | 2 warnings, both `urllib` calls with the URL scheme validated |

Resume samples run through the naive ATS ranker and the firewall:

| Sample | ATS parser score | Human view score | Firewall result |
|---|---|---|---|
| `honest_ai_polished.pdf` | 54.7 | 54.7 | No flags, passes |
| `stuffed_hidden_text.pdf` | 71.9 | 20.3 | Four flags, score 0, Manual Review |
| `visible_injection.docx` | 20.3 | 20.3 | Injection flag, score 32, Manual Review |

## Known limitations

- Behavioral signals come from the browser and can be spoofed. They are treated as weak evidence.
- One person submitting about five applications a minute while rotating device and IP is not yet caught. Paraphrased copies are only partly caught.
- Authentication is optional and off by default. The webhook is unsigned unless a secret is set.
- The store is in memory. The ATS in the demo is a simulator, because a real submission needs an employer API key.
- The sample resumes are hand-built to exercise each detector. They are not a test against evasive resumes in the wild.

The incremental plan for this round is in [PLAN.md](PLAN.md). Later rounds add measurement, red-teaming and cross-employer memory.

License: MIT.
