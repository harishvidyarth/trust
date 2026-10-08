# Federated fraud intelligence

This package lets independent Application Firewall deployments exchange fraud
signals without exchanging names, email addresses, phone numbers, IP addresses,
device IDs, or resume text. Nodes exchange only consortium-keyed HMAC-SHA256
fingerprints and signed fraud reports.

## Protocol

```text
 raw application                   signed HMAC fingerprints only
 at Employer A                              (no raw PII)
       |                                           |
       v                                           v
 [fingerprint locally] -> [A: POST /fed/report] -> [B] -> [C]
                                |                   |      |
                       Ed25519 signature       dedup/verify |
                                |                   +------+
                                v                  loop-safe gossip
                     one signer = ADVISORY
                     K signers / score threshold = CONFIRMED
```

`POST /fed/check` compares a locally generated fingerprint set with active
reports. `POST /fed/report` accepts an immutable signed report. `GET
/fed/blocklist?since=<unix-seconds>` supports incremental pulls, and `GET
/fed/status` exposes non-secret health/counters. `POST /fed/revoke` accepts a
signed revocation only from the report's origin. Reports expire at `timestamp +
ttl`; replayed IDs and signatures from unknown nodes are rejected.

The default poisoning policy is conservative: a match is `FED_CONFIRMED` only
after reports from two independent node IDs, or an aggregate per-node confidence
score of 1.5. Only the strongest report from each node contributes to that score,
so one node cannot confirm a fingerprint by submitting repeatedly. A lone report
is `FED_ADVISORY` with low severity.

## Install, test, and demo

Create and use this package's isolated environment (all commands run from the
repository root):

```bash
python3 -m venv federation/.venv
federation/.venv/bin/pip install -r federation/requirements.txt
federation/.venv/bin/python -m pytest federation/tests -q
federation/.venv/bin/python federation/scripts/demo_three_nodes.py
```

The demo launches three real Uvicorn processes on free localhost ports. Employer
A first reports a bot, B receives an advisory, B independently corroborates it,
and C receives a confirmed match. It then proves that one malicious employer's
false report about an honest candidate remains advisory.

## Node configuration

Environment variables and equivalent CLI flags are supported:

```bash
NODE_ID=employer-a \
PEERS=http://192.168.1.12:8102,http://192.168.1.13:8103 \
FED_SECRET='replace-with-a-long-random-shared-secret' \
FED_KEY_FILE=federation/.state/employer-a.pem \
FED_PEERS_FILE=federation/peers.json \
FED_CONFIRMATION_K=2 \
FED_SCORE_THRESHOLD=1.5 \
federation/.venv/bin/python -m federation.node --host 0.0.0.0 --port 8101
```

The private Ed25519 key is created with mode `0600` on first run. Distribute only
the public keys in the peers file:

```json
{
  "nodes": {
    "employer-a": {"public_key": "BASE64_RAW_ED25519_KEY", "url": "http://192.168.1.11:8101"},
    "employer-b": {"public_key": "BASE64_RAW_ED25519_KEY", "url": "http://192.168.1.12:8102"}
  }
}
```

For laptops on Ethernet, use stable LAN IPs, pass `--host 0.0.0.0`, list every
other laptop in `PEERS`, and allow the selected TCP ports through each host
firewall. Verify connectivity with `/fed/status`. Keep system clocks synchronized
(NTP); reports more than 30 seconds in the future are rejected. Plain HTTP is for
the isolated demo LAN only—use mutually authenticated TLS or a private WireGuard
network outside that setting.

## Threat model and honest limitations

The design protects raw PII against peer nodes and passive observers of stored
messages. HMAC prevents useful offline guessing while the consortium secret is
safe; Ed25519 proves which enrolled node authored a report; quorum/weighted
confirmation limits a single malicious node; TTL and origin-only revocation
limit stale harm.

It does **not** solve every privacy or trust problem:

- These values are pseudonymous and intentionally linkable across consortium
  members. Matching itself reveals that two nodes saw the same identifier.
- If `FED_SECRET` leaks, low-entropy fields—especially phone numbers and IPv4
  `/24` prefixes—can be dictionary-attacked. Rotate the secret across all nodes,
  flush old fingerprints, and rebuild reports after a suspected leak. Rotation
  temporarily prevents old/new fingerprints from matching.
- A stolen node signing key can issue valid reports as that employer. Remove its
  public key, rotate its key, and raise `K` during incident response.
- Colluding nodes can satisfy `K`; governance, enrollment controls, audit logs,
  rate limits, and contractual remedies remain necessary.
- MinHash bands are probabilistic: near copies can miss, unrelated resumes can
  collide, and four band digests disclose limited similarity/linkability. A
  federated match must remain one signal, never the sole basis for rejection.
- The in-memory store is demonstration-grade and is lost on restart. Production
  deployment needs durable storage, bounded replay history, TLS, authentication
  for local callers, rate limits, monitoring, and safe secret/key management.
- Bloom filters reduce synchronization bandwidth but introduce false positives;
  use them only to decide what to fetch, never as final blocking evidence.
