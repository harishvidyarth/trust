from __future__ import annotations

import re
from difflib import SequenceMatcher
from urllib.parse import quote

from firewall.enrichment._http import HttpConnector
from firewall.enrichment.models import Claims, EnrichmentSignal


def _normalise_title(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


class CrossrefConnector(HttpConnector):
    name = "crossref"
    base_url = "https://api.crossref.org/works"

    def check(self, claims: Claims) -> list[EnrichmentSignal]:
        signals: list[EnrichmentSignal] = []
        for claim in claims.dois:
            doi = claim.doi.strip()
            title = _normalise_title(claim.claimed_title)
            if not doi or not title:
                continue
            response = self._get(f"{self.base_url}/{quote(doi, safe='')}")
            evidence_url = f"https://doi.org/{quote(doi, safe='/')}"
            if response is None:
                continue
            if response.status_code in {404, 410}:
                signals.append(
                    EnrichmentSignal(
                        code="DOI_NOT_FOUND",
                        polarity="negative",
                        severity="high",
                        confidence=0.95,
                        source=self.name,
                        detail="The specifically claimed DOI was not found in the scholarly registry.",
                        matched_claim=f"{doi} | {claim.claimed_title}",
                        evidence_url=evidence_url,
                    )
                )
                continue
            if response.status_code != 200:
                continue
            payload = self._json(response)
            message = payload.get("message") if isinstance(payload, dict) else None
            titles = message.get("title") if isinstance(message, dict) else None
            registered_title = titles[0] if isinstance(titles, list) and titles and isinstance(titles[0], str) else None
            if not registered_title:
                continue
            ratio = SequenceMatcher(None, title, _normalise_title(registered_title)).ratio()
            if ratio < 0.65:
                signals.append(
                    EnrichmentSignal(
                        code="DOI_TITLE_MISMATCH",
                        polarity="negative",
                        severity="high",
                        confidence=min(0.98, 1.0 - ratio / 2),
                        source=self.name,
                        detail="The registered publication title materially differs from the supplied title.",
                        matched_claim=f"{doi} | {claim.claimed_title}",
                        evidence_url=evidence_url,
                    )
                )
            else:
                signals.append(
                    EnrichmentSignal(
                        code="DOI_VERIFIED",
                        polarity="positive",
                        severity="info",
                        confidence=max(0.7, ratio),
                        source=self.name,
                        detail="The DOI exists and its registered title is consistent with the supplied title.",
                        matched_claim=f"{doi} | {claim.claimed_title}",
                        evidence_url=evidence_url,
                    )
                )
        return signals
