# Consent-based enrichment

This package performs best-effort corroboration for applications already routed
to a **gray-zone / additional-verification** path. It is not a candidate search,
an identity-discovery tool, or a standalone rejection engine.

## Invariants

- Only use claims the candidate supplied or data sources the candidate explicitly
  authorized.
- Missing accounts, missing optional fields, private activity, unavailable APIs,
  malformed responses, and timeouts are neutral. They do not reduce a score.
- Only an explicit contradiction creates a negative signal. A matching public
  record creates a positive trust signal.
- Every connector is isolated. A slow or failed connector returns no signal and
  cannot block the other checks or reject an application.
- Signal `detail` text is deliberately generic and contains no candidate PII.
  Evidence URLs can identify a candidate-supplied public artifact and therefore
  must receive the same access controls and retention policy as the application.
- Positive trust is returned separately and capped at 15. It must never cancel a
  hard fraud rule, eligibility failure, or required manual review.

## Inputs and connectors

`Claims` holds candidate-supplied GitHub, repository, DOI, DOI-less paper,
portfolio, OIDC, and employer claims. `application_name` is included for OIDC
comparison and conservative paper-author mismatch checks. Employer claims are
reserved for a future consented provider; this package does not contact employers.

- `GitHubConnector`: public GitHub REST metadata, optionally authenticated with
  `GITHUB_TOKEN` to increase rate limits. It checks explicitly claimed repositories
  and compares public account/repository/commit dates with claimed timelines.
- `CrossrefConnector`: DOI existence and fuzzy title consistency through Crossref.
- `ScholarConnector`: DOI-less paper corroboration through a fallback chain of
  Crossref bibliographic search, OpenAlex, Semantic Scholar, arXiv, and DBLP. It
  requires a close title, a claimed author surname, and a year within one year
  when supplied. It never scrapes Google Scholar and treats outages or absence as
  neutral. `SEMANTIC_SCHOLAR_API_KEY` is optional.
- `DomainConnector`: RDAP registration age and whether a candidate-supplied public
  portfolio URL responds. Loopback and literal private-network targets are refused.
- `IdentityConnector`: compares the application name with the name returned by a
  consented OIDC login and checks the verified email domain against a deliberately
  small bundled disposable-domain list.

LinkedIn is **not scraped**. LinkedIn support means only accepting the verified
name/email fields returned after the candidate chooses **Sign in with LinkedIn
OIDC**. This package performs no LinkedIn page automation and no third-party
email-to-account reconnaissance.

Google Programmable Search is disabled by default. If both
`FIREWALL_GOOGLE_CSE_KEY` and `FIREWALL_GOOGLE_CSE_CX` are set, it is queried only
as a last-resort evidence-URL lookup and can never corroborate a paper by itself.
Google's Custom Search JSON API is closed to new customers and is scheduled to
sunset on 1 January 2027, so this path is best-effort only.

## Gray-zone wiring

Call enrichment only after the primary engine chooses its additional-verification
route. Run it in the API's background job/task system so request latency is not
tied to public providers:

```python
claims = Claims(...)  # built only from consented application inputs
signals, run_summary = enrich(claims, per_connector_timeout=4.0)
reasons, trust_bonus = to_reasons(signals)
```

Append `reasons` using the engine's normal `Reason` validation. Apply
`trust_bonus` only to the gray-zone soft-risk calculation, after preserving any
hard-fraud/manual-review decision. Persist `run_summary` for operational
observability; do not interpret `timed_out` or `failed` as candidate evidence.

The cache is in-process and keyed by a hash of the claims. Its default TTL is five
minutes. It avoids duplicate provider calls but is not durable across processes.

## Legal and ethics boundary

Obtain specific, informed candidate consent and state the purposes, sources,
retention period, appeal path, and human-review process. Minimize collection and
restrict access in line with applicable GDPR and Indian DPDP obligations. If this
is used for employment background reporting in the United States, obtain counsel
on FCRA disclosure, authorization, accuracy, adverse-action, and dispute duties;
these connectors are not an FCRA-compliant consumer-reporting workflow by
themselves. Also follow every provider's terms, including LinkedIn's prohibition
on unauthorized scraping. This is engineering guidance, not legal advice.

## Tests and opt-in smoke check

```bash
.venv/bin/python -m pytest tests_enrichment -q
.venv/bin/python -m firewall.enrichment.smoke_live --live
```

The smoke module does nothing network-facing without the explicit `--live` flag.
