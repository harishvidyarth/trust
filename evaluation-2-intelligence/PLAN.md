# Evaluation 2 plan: Intelligence

Goal: add AI reasoning that never decides a route, measure the system honestly, and turn it into one app with login and roles.

Builds on: [Evaluation 1](../evaluation-1-foundation/PLAN.md). Everything there is included.

## Steps and real status

| Step | What | Check | Status and result |
|---|---|---|---|
| 1 | Fix four bugs: Ollama host and model, unused packages, ablation method | tests, eval tests | Done. Parser uses the shared client. Ablation now removes reasons before scoring and routing. |
| 2 | LLM assisted parsing when rules find nothing | `tests_resume` | Done. Live check on a messy resume: zero skills before, grounded skills after (model warm). |
| 3 | Many small integrity checks in families | `tests_resume`, eval | Done. 18 checks, family caps, old codes kept at weight 0. |
| 4 | Style judge and labeled set | `eval/run_style_eval.py` | Done. 45 synthetic resumes. Hybrid precision 97%, recall 97%, 1 of 15 human flagged. Heuristic recall 50%. Synthetic data only. |
| 5 | Hidden text intent labels | `tests_resume` | Done. Weight and route unchanged. |
| 6 | Plain English reasoning | `tests` | Done. Test covers every reason code and the wording rule. |
| 7 | Embedding similarity for paraphrases | `eval/measure_semantic_duplicates.py` | Done, off by default. Evasion 60% to 20%. Honest flagged 0 to 11 of 900 held out. |
| 8 | Corroboration for scores 41 to 69 | `tests/test_corroboration.py` | Done and tested with fakes. Never run against live services. |
| 9 | Connectors, Lever webhook, FIREWALL_ROUTES, delivery | `tests_ats`, live fake destination | Done. Checked live against a fake destination. |
| 10 | Agreement meter | live console | Done. Shown in the candidate X-ray. |
| 11 | Final eval and red-team | `eval/run_eval.py`, `redteam/` | Done. Recall 83.5% to 87.7%, honest flagged 0%. Email alias evasion 30% to 10%. |
| 12 | Login and roles | `tests_auth`, live server | Done. Checked live: refused when anonymous, CSRF enforced, overrides separate. |
| 13 | Redis layer | `tests_redis`, live Redis | Done. Decisions survive a restart. |
| 14 | New identity checks with store indexes | `tests`, `tests_redis` | Done. 134 s down to 13 s for 2,400 applications, same results. |
| 15 | Intake routes with consent and dispute | `tests_intake` | Done in tests. Real browser upload not tested. |
| 16 | Role profiles and passive name search | `tests_intel` | Built and tested with fakes. Not wired into the live flow, needs a consent flag. Registry formats are our assumptions. |
| 17 | One console in the original theme | live Chrome | Done for the candidate flow. Recruiter and admin views checked in demo mode only. |
| 18 | Face, voice and video verification | none | Not started. Parked by decision. |
| 19 | Independent DAST | none | Not done. A third party must run and accept it. |

## Verified in this round

See the per folder counts printed by the build script. The last full run of every folder passed. Semgrep with the CI rule sets found no errors and two accepted warnings.

## Not in this round

Face and voice checks, real registry lookups, live testing of corroboration, Round 3 changes.

## Open items carried forward

- Run corroboration and name search against live services with real consent.
- Check role profile number formats against real ones.
- Test the LinkedIn parser on real exports.
- Independent DAST by a third party.
- A more realistic honest AI polished control for the red-team.
