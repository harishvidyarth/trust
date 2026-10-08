# Keys and what they unlock

Put each key in the file named `.env` in the project folder, one per line, like `GITHUB_TOKEN=your_value`.
The launcher reads that file, prints only the names it found, and never prints a value.
The file is ignored by git, so it is never published.

| Key name | What it unlocks | Cost | How to get it |
|---|---|---|---|
| `GITHUB_TOKEN` | GitHub checks go from 60 to 5,000 lookups an hour. Needed once more than a few people are checked. | Free | GitHub, Settings, Developer settings, Personal access tokens, Fine grained tokens. Choose public repositories only and tick no permissions. |
| `FIREWALL_USPTO_ODP_API_KEY` | Patent lookups for hardware roles. | Free | Create a USPTO.gov account, then ask for an Open Data Portal API key. Identity checks by USPTO may apply. |
| `SLACK_REVIEW_HOOK` | A Slack message when an application needs a person. | Free | In Slack, make an app, turn on Incoming Webhooks, add one to a channel and copy the link. Then set `FIREWALL_ROUTES` to use it. |
| `SEMANTIC_SCHOLAR_API_KEY` | Fewer slow or refused paper lookups. | Free | Request a key on the Semantic Scholar API page. |
| `LEVER_API_KEY` or `GREENHOUSE_API_KEY` | Send passed applications to a real hiring system. | Needs a paid account | Create an API key in the hiring system settings. |

## Keys that do not exist for free

ICAI, ACCA, CFA, SEBI and MCA do not offer a free public lookup. Their sites use forms, logins or CAPTCHAs, and TR∩ST does not bypass those.
To use them you need written permission and a data file, or a paid company data provider. Until then they give no signal and nobody is penalised.

## No key needed

ORCID, Crossref, OpenAlex, arXiv and DBLP work without a key. ORCID asks that the free public service is used for non commercial work only.
Ollama runs on this machine and needs no key.
