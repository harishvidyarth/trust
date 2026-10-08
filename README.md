# TR∩ST: the AI Application Firewall

**TR** is the text a parser reads. **ST** is the text a person sees. The inverted U is the intersection: trust is the part both agree on, and anything outside it is hidden content.

**A pre-ATS trust layer that lets honest AI-assisted candidates through, stops bots, duplicates and fabricated resumes, and gives a reason for every decision.**

Built by team **QUARTET** (Harish Vidyarth N, Keerthisri D, Madhumitha N, Nakshatra PA) for problem **SW-05, AI Application Firewall**, KERNEL PRIME'26.

## The problem

AI tools now discover openings, tailor resumes, write screening answers and submit hundreds of applications a day. Applicant tracking systems were built for human-paced volume, so recruiters drown in three kinds of noise: duplicates, low-intent spray, and fraudulent or misleading profiles. The catch is that a candidate who uses AI to polish a resume is legitimate. A firewall that punishes AI use fails the people it is meant to protect.

## The approach

TR∩ST sits in front of the ATS and routes every application to **Pass to ATS**, **Additional Verification** or **Manual Review**.

- **Provenance, not AI-text detection.** It never decides on writing style. An informational estimate is shown for context, with a weight of zero. A fluent, AI-polished honest resume produced zero flags in our tests.
- **Caught before the ATS reads it.** Each resume is read twice, the way a parser extracts it and the way a person sees it. The difference exposes hidden white text, instructions aimed at AI screeners, and keyword stuffing.
- **Explainable and cautious.** Every decision carries reason codes. Nothing is rejected automatically.
- **Deterministic core.** Rules decide, so every decision can be audited. Models and an optional local LLM only assist.

## Repository layout

Each folder is self-contained and runnable. A later folder includes everything in the one before it and adds more.

| Folder | Theme | Status |
|---|---|---|
| [`evaluation-1-foundation`](evaluation-1-foundation) | A working pre-ATS firewall: engine, API, resume integrity, Resume X-ray with 23 samples, ATS connectors, security scans | Complete, 117 tests passing |
| [`evaluation-2-intelligence`](evaluation-2-intelligence) | AI reasoning and measurement: many small checks, model assisted parsing that never decides a route, login and roles, optional Redis, one console | Complete, 932 tests passing (4 skipped) |
| [`evaluation-3-network`](evaluation-3-network) | Cross-employer memory: federated fingerprints with signed reports and a three-node demo | Complete, 216 tests passing |

Rounds 2 and 3 are older snapshots that predate the latest Round 1 changes. They are being rebuilt on top of the final Round 1. Start with [`evaluation-1-foundation/README.md`](evaluation-1-foundation/README.md). Every folder has a `PLAN.md` describing its incremental steps and what was verified.

## Highlights measured so far

| Result | Value |
|---|---|
| Naive keyword ATS score for a keyword-stuffed resume versus what a human sees | 71.9 versus 20.3, so the fraudulent resume ranks first without the firewall |
| Firewall on that resume | Four integrity flags, score 0, Manual Review, never reaches the ATS |
| Honest AI-polished resume | No flags, passes |
| Engine throughput | 5,000 applications in 1.75 s on one laptop, engine only |
| Held-out evaluation (synthetic) | Precision 100.0%, recall 87.7% (83.5% at the start of Round 2), honest candidates flagged 0.0% |
| Red-team: rotating-identity attack reaching the ATS | 100% before, 21.4% after the device-rotation signal |
| Federation demo (three nodes, one machine) | Bot caught at employer A is confirmed at employer C in about 90 ms, with no raw personal data shared and a lone rogue report kept advisory |
| OWASP ZAP API scan | 118 rules passed, 0 failed |

## How it compares

In our GitHub search we found no open-source project covering this problem. The closest prototype, VerifyHire, had no stars and about three commits, covers roughly three of the eight required capabilities, and relies on AI-text scoring that penalizes honest AI users. Commercial identity tools such as Greenhouse with CLEAR and Persona verify who the applicant is, per vendor. TR∩ST adds content-level checks and an ATS-agnostic layer. This comparison comes from public product descriptions and keyword searches, not hands-on testing of the commercial tools.

## Honest status

This is a hackathon prototype. Behavioral signals come from the browser and can be spoofed. Slow, rotating-identity submission and paraphrased copies are only partly caught. The datasets used for evaluation are synthetic, so reported metrics are partly circular. Each round's README lists its own known limitations.

## License

MIT. See [LICENSE](LICENSE).
