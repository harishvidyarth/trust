# Evaluation 3 plan: Network

Goal: let independent employers recognize a bad actor together, without sharing any personal data.

Builds on: [Evaluation 2](../evaluation-2-intelligence/PLAN.md). Everything there is included.

## Incremental steps

| Step | What | Files | Check | Status |
|---|---|---|---|---|
| S1 | Keyed fingerprints of email, phone, device, network prefix and banded resume hashes | `federation/fingerprints.py` | `pytest federation/tests` | Done |
| S2 | Signed reports, signature and replay rejection, TTL expiry, origin-only revocation | `federation/node.py`, `federation/client.py` | `pytest federation/tests` | Done |
| S3 | Gossip between nodes with loop safety | `federation/node.py` | `pytest federation/tests` | Done |
| S4 | Confirmation rules: advisory for one node, confirmed for two independent nodes or a weighted score | `federation/node.py` | `pytest federation/tests` | Done |
| S5 | Adapter functions for the firewall pipeline | `federation/integration.py` | `pytest federation/tests` | Done |
| S6 | Review the package and fix what it found: inverted report gate, unreachable threshold, confidence direction, weak-kind confirmation, default secret, invalid IP crashes | `federation/` | `pytest federation/tests` | Done, 12 new tests |
| S7 | Three-node demo with measured propagation | `federation/scripts/demo_three_nodes.py` | Run the demo | Done, on one machine |
| S8 | Wire federation into the live decision path | `firewall/` | Not started | Not done |
| S9 | Run the demo across real laptops over Ethernet | Hardware | Not started | Not done |
| S10 | Operator-farm graph and signed session tokens | `firewall/graph/`, `firewall/attest/` | No tests yet | Not published |

## Verified in this round

- 27 federation tests pass, and 216 tests pass across every directory, run from inside this folder.
- The three-node demo passes from inside this folder.

## Open items carried forward

- Count independent organizations instead of node identifiers.
- Authenticate the check and blocklist endpoints, and purge expired reports.
- Wire federation, corroboration and adaptive learning into the live decision path.
- Measure propagation latency across real laptops.
- Test, then publish, the operator-farm graph and the signed session tokens.
