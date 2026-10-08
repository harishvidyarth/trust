# ATS integration and sandbox

This directory demonstrates the boundary between the application firewall and an applicant tracking system (ATS). The local service is explicitly a **sandbox simulator**. It is not Greenhouse, Lever, or a copy of either vendor's proprietary ranking system.

## What is real

- `GreenhouseJobBoardClient` performs read-only calls to the documented public Job Board paths: `/v1/boards/{board_token}/jobs` and `/v1/boards/{board_token}/jobs/{id}?questions=true`.
- `LeverJobBoardClient` performs read-only calls to the public postings path: `/v0/postings/{company}?mode=json` and its posting detail path.
- `GreenhouseHarvestForwarder` uses HTTP Basic authentication, `On-Behalf-Of`, and a candidate payload with an application/job association.
- `LeverForwarder` uses a configurable Lever-style opportunity endpoint. Real Lever accounts differ in authentication and authorization setup, so confirm the account's API configuration before enabling it.
- Every outbound forwarder uses the firewall application ID as its idempotency key, retries with exponential backoff, and retains exhausted deliveries in a dead-letter list.

Public job-board fixtures in `tests_ats/fixtures/` are small recorded examples used without network access. Job parsing uses the documented finite `SKILL_TAXONOMY` in `firewall/connectors/boards.py`. Preferred/bonus/nice-to-have sections map to `nice_to_have`; other matches map to `must_have_skills`. The largest lower bound matching `X+ years` or `X-Y years` becomes `min_years`. This is deterministic extraction, not semantic understanding.

## What is simulated

- `ats.sandbox` stores candidates and applications in process memory.
- Its Greenhouse-shaped and Lever-like routes implement only the subset needed by this demo.
- `ats.ranker` is deliberately naive: it ranks by the fraction of job keywords found anywhere in resume text. It is a teaching baseline, not a claim about how Greenhouse, Lever, or any other vendor ranks candidates.

That naive baseline makes the firewall's value visible. A fraudulent resume that repeats every keyword ranks over an honest resume when both reach the sandbox. When routing is enabled, suspicious applications go to verification or manual review and are never sent to the ATS forwarder, so they cannot pollute downstream ranking.

## Run locally

```bash
ATS_SANDBOX_API_KEY=sandbox-key .venv/bin/python -m ats.sandbox --port 8090
```

Use the key as the HTTP Basic username and an empty password. Point a Greenhouse forwarder at the simulator with `base_url=http://127.0.0.1:8090`; point a Lever forwarder at it with `base_url=http://127.0.0.1:8090/lever/v1`.

The only live-network utility is opt-in:

```bash
.venv/bin/python -m ats.smoke_live --live --board discord
```

Without `--live`, it makes no request.

## Point at a real Greenhouse sandbox

Obtain a Harvest API key and a valid user ID from the Greenhouse test/sandbox account, grant only the candidate-write permissions needed, and keep both values outside source control. Construct the forwarder with the real Harvest base URL (the default), the API key, and the user ID as `on_behalf_of`. Do not send fixture candidates to a production tenant. Start with a non-production Greenhouse tenant, verify field mappings and permissions, and monitor `dead_letters` before enabling routing.

The public Job Board API needs only a board token and is unrelated to Harvest write credentials.
