from __future__ import annotations

import ipaddress
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit

from firewall.enrichment._dates import parse_datetime
from firewall.enrichment._http import HttpConnector
from firewall.enrichment.models import Claims, EnrichmentSignal


def _public_host(url: str) -> tuple[str, str] | None:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").rstrip(".").casefold()
        if parsed.scheme not in {"http", "https"} or not host or host == "localhost" or host.endswith(".local"):
            return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            return None
        return host, url
    except (TypeError, ValueError):
        return None


class DomainConnector(HttpConnector):
    name = "domain"
    rdap_url = "https://rdap.org/domain"

    def check(self, claims: Claims) -> list[EnrichmentSignal]:
        signals: list[EnrichmentSignal] = []
        raw_targets = []
        if claims.portfolio_url:
            raw_targets.append(claims.portfolio_url)
        raw_targets.extend(f"https://{domain}" for domain in claims.employer_domains)
        seen_hosts: set[str] = set()
        for raw_target in raw_targets:
            target = _public_host(raw_target)
            if target is None:
                continue
            host, site_url = target
            if host in seen_hosts:
                continue
            seen_hosts.add(host)
            rdap_response = self._get(f"{self.rdap_url}/{quote(host, safe='')}")
            if rdap_response is not None and rdap_response.status_code == 200:
                payload = self._json(rdap_response)
                events = payload.get("events") if isinstance(payload, dict) else None
                created = None
                if isinstance(events, list):
                    for event in events:
                        if isinstance(event, dict) and event.get("eventAction") in {"registration", "registered"}:
                            created = parse_datetime(event.get("eventDate"))
                            if created:
                                break
                if created and datetime.now(timezone.utc) - created < timedelta(days=180):
                    signals.append(
                        EnrichmentSignal(
                            code="DOMAIN_AGE_RECENT",
                            polarity="negative",
                            severity="low",
                            confidence=0.65,
                            source=self.name,
                            detail="The supplied domain was registered recently.",
                            matched_claim=raw_target,
                            evidence_url=f"https://rdap.org/domain/{quote(host, safe='')}",
                        )
                    )

            site_response = self._get(site_url, follow_redirects=True)
            if site_response is not None and 200 <= site_response.status_code < 400:
                signals.append(
                    EnrichmentSignal(
                        code="DOMAIN_REACHABLE",
                        polarity="positive",
                        severity="info",
                        confidence=0.5,
                        source=self.name,
                        detail="The candidate-supplied site responded successfully.",
                        matched_claim=raw_target,
                        evidence_url=site_url,
                    )
                )
        return signals
