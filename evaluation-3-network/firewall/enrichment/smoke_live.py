from __future__ import annotations

import argparse
import json

from firewall.enrichment.crossref import CrossrefConnector
from firewall.enrichment.domain import DomainConnector
from firewall.enrichment.github import GitHubConnector
from firewall.enrichment.models import Claims
from firewall.enrichment.runner import TTLCache, enrich
from firewall.enrichment.scholar import ScholarConnector


def main() -> int:
    parser = argparse.ArgumentParser(description="Opt-in live smoke test for public enrichment APIs")
    parser.add_argument("--live", action="store_true", help="perform real public network requests")
    args = parser.parse_args()
    if not args.live:
        parser.error("network calls are disabled unless --live is supplied")

    claims = Claims(
        github_username="octocat",
        project_repos=[{"name": "Hello-World", "claimed_start": "2011-01-01", "claimed_end": "2012-12-31"}],
        dois=[
            {
                "doi": "10.1038/nature14539",
                "claimed_title": "Deep learning",
            }
        ],
        portfolio_url="https://example.com",
        papers=[{"title": "Attention Is All You Need", "authors": ["Vaswani"], "year": 2017}],
    )
    signals, summary = enrich(
        claims,
        connectors=[GitHubConnector(), CrossrefConnector(), DomainConnector(), ScholarConnector()],
        per_connector_timeout=6.0,
        cache=TTLCache(ttl_seconds=0),
    )
    output = {
        "signals": [signal.model_dump(mode="json") for signal in signals],
        "summary": summary.model_dump(mode="json"),
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
