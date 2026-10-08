# Local firewall red-team harness

This directory measures the local AI Application Firewall without changing it. Every scenario starts a fresh Uvicorn process, waits for `/healthz`, submits only to a loopback address, and terminates the process afterward. Synthetic submission timestamps model human or automated pacing; the harness does not sleep between applications.

## Setup

Create the isolated environment and install only the harness dependencies:

```bash
python3 -m venv redteam/.venv
redteam/.venv/bin/python -m pip install -r redteam/requirements.txt
```

The spawned firewall process always uses the repository's `.venv/bin/python`, keeping its dependencies separate from the red-team environment.

## Run scenarios

Run the full deterministic baseline:

```bash
redteam/.venv/bin/python -m redteam.run --label baseline --base-url http://127.0.0.1
```

Run one scenario or override its submission count:

```bash
redteam/.venv/bin/python -m redteam.run --label alias-check --scenario email_alias_abuse --count 20
```

`paraphrase_copy` uses deterministic synonym replacement and sentence reordering by default. To try a model already available from a local Ollama service, use `--use-ollama --ollama-url http://localhost:11434`; a connection/model failure automatically falls back to deterministic paraphrasing. No external model or network endpoint is used.

Each invocation writes `redteam/results/<label>.json`. Labels may coexist for before/after comparisons.

## Generate the report

```bash
redteam/.venv/bin/python -m redteam.report
```

This reads every result JSON and writes:

- `redteam/results/ROBUSTNESS.md`
- `redteam/results/evasion_rate.png`
- `redteam/results/control_false_positive_rate.png`

An attack evades when it receives `PASS_TO_ATS`; detection is either other route. A control false positive is a legitimate control receiving either non-pass route. `time_to_first_flag_seconds` is measured from synthetic submission timestamps, not harness wall-clock time.

## Generator tests

The repository environment already contains pytest and the firewall models used for schema validation:

```bash
.venv/bin/python -m pytest -q redteam/tests
```

The tests do not start a server or contact Ollama.

## Scenario intent

- `naive_flood`: fast, pasted submissions from one device and IP across many jobs.
- `rotating_identity`: one person rotates device/IP while applying to many jobs in minutes.
- `slow_and_low`: fake identities submit a shared resume skeleton at human pace.
- `paraphrase_copy`: fake candidates paraphrase and reorder another candidate's resume.
- `resume_farm`: many identities use a shared skeleton with light variations.
- `email_alias_abuse`: Gmail dot, plus-tag, and `googlemail.com` aliases with name variations.
- `honest_ai_polished`: distinct, truthful candidates use fluent AI-polished prose at normal pace.
- `honest_campus_burst`: distinct students submit from one campus NAT in a short window.
