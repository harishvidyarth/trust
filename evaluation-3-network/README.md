# TR∩ST · Evaluation 3: Network

Rounds 1 and 2 protect a single employer. Round 3 adds cross-employer memory: a bot caught at one employer can be recognized by others, without any employer sharing a candidate's personal data.

Everything from [Evaluation 2](../evaluation-2-intelligence/README.md) is included and unchanged. Team QUARTET: Harish Vidyarth N, Keerthisri D, Madhumitha N, Nakshatra PA.

## What is new in this round

A federation package (`federation/`): independent firewall nodes exchange keyed, non-reversible fingerprints and signed reports.

- **Fingerprints, not data.** Each node sends only HMAC-SHA256 digests of the normalized email, normalized phone, device identifier, network prefix and four banded hashes of the resume text. Near-copy resumes collide on at least one band. Raw names, emails, phones, IPs and resume text never leave a node.
- **Signed reports.** Every report is signed with Ed25519. Unknown signers, tampered reports, replays and expired reports are rejected, and only the originating node can revoke a report.
- **Confirmation, not accusation.** A single node's report is advisory only. A fingerprint becomes confirmed when at least two independent nodes report it, or when the confidence-weighted score crosses a threshold.
- **Gossip.** Valid reports are forwarded to peers, with loop protection.

## Demo: three employers, one bot

Run on one machine with three nodes acting as three employers:

```
t=   0.0 ms  employer-a local detector caught bot (raw PII stays at A)
t=  46.9 ms  employer-b received gossip: FED_ADVISORY (1 node, not blocked)
t=  90.1 ms  employer-c sees FED_CONFIRMED after B corroborates (2 nodes)
MEASURED confirmed propagation B->C: 35.5 ms
rogue single-node false report: FED_ADVISORY at C (not blocked)
DEMO PASS: no raw PII crossed nodes; corroborated bot blocked; lone rogue stayed advisory
```

These timings are localhost measurements, not an Ethernet measurement. To run across real laptops, see [federation/README.md](federation/README.md).

```bash
python3 -m venv federation/.venv
federation/.venv/bin/pip install -r federation/requirements.txt
federation/.venv/bin/python federation/scripts/demo_three_nodes.py
federation/.venv/bin/python -m pytest federation/tests -q
```

## Bugs found and fixed this round

A review of the federation package found problems that its own passing tests had missed. Each is fixed and has a regression test.

| Problem | Effect | Fix |
|---|---|---|
| The report gate compared the trust score the wrong way and required a weight no reason can reach | The firewall would never have reported anything | Report only a Manual Review with a low trust score and a strong high-severity reason |
| Report confidence was computed from the trust score | Worse applications produced lower confidence | Confidence now comes from how low the trust score is |
| A match on the network prefix or on a device alone could confirm a block | One bot behind a campus network could flag every honest student there | Network-prefix-only matches are ignored, and confirmation needs an email or phone match, or at least two distinct strong kinds. Device alone stays advisory. |
| The shared secret had a default value | A deployment could silently use a public secret | The secret is required. An explicit `FED_ALLOW_INSECURE_DEV=1` is needed for local demos. |
| Invalid IP values (empty, "unknown", forwarded-for lists) crashed fingerprinting | An odd request could break a decision | Invalid values are skipped, and the first address of a list is used |

## Verified

| Check | Result |
|---|---|
| Federation tests | 27 passed (15 original, 12 new) |
| All test directories, run from inside this folder | 216 passed in total |
| Three-node demo, run from inside this folder | Passed, no raw personal data crossed nodes |
| Comments and docstrings in the Python and JavaScript sources | None |

## Known limitations

- The federation package is tested on its own but is not yet wired into the firewall's live decision path. The integration functions exist and are tested.
- Fingerprints are pseudonymous, not anonymous. If the shared secret leaks, low-entropy fields such as phone numbers can be dictionary-attacked. This is a thresholded lookup, not private set intersection. Rotate the secret as a coordinated event.
- Independence is counted per node identifier, not per organization, so a ring that registers several nodes could outvote an honest candidate. Counting distinct organizations is the next hardening step.
- The `check` and blocklist endpoints are not authenticated, and reports are not yet purged from memory over time.
- A revocation that arrives before its report is not retried.
- Latency was measured on one machine. It has not been measured across real laptops.
- Two further modules, an operator-farm graph and signed session tokens, exist in the workspace but have no tests yet, so they are not published.

The incremental plan for this round is in [PLAN.md](PLAN.md).

License: MIT.
