from __future__ import annotations

import re
from difflib import SequenceMatcher
from importlib.resources import files

from firewall.enrichment.models import Claims, EnrichmentSignal


def _normalise_name(value: str) -> str:
    return " ".join(re.findall(r"[a-z]+", value.casefold()))


class IdentityConnector:
    name = "identity"

    def __init__(self, disposable_domains: set[str] | None = None) -> None:
        if disposable_domains is None:
            content = files("firewall.enrichment").joinpath("disposable_domains.txt").read_text(encoding="utf-8")
            disposable_domains = {
                line.strip().casefold() for line in content.splitlines() if line.strip() and not line.startswith("#")
            }
        self.disposable_domains = disposable_domains

    def check(self, claims: Claims) -> list[EnrichmentSignal]:
        signals: list[EnrichmentSignal] = []
        application_name = _normalise_name(claims.application_name or "")
        verified_name = _normalise_name(claims.oidc_verified_name or "")
        if application_name and verified_name:
            app_tokens = set(application_name.split())
            oidc_tokens = set(verified_name.split())
            token_overlap = len(app_tokens & oidc_tokens) / max(1, min(len(app_tokens), len(oidc_tokens)))
            similarity = max(SequenceMatcher(None, application_name, verified_name).ratio(), token_overlap)
            if similarity < 0.55:
                signals.append(
                    EnrichmentSignal(
                        code="IDENTITY_NAME_MISMATCH",
                        polarity="negative",
                        severity="high",
                        confidence=min(0.98, 1.0 - similarity / 2),
                        source=self.name,
                        detail="The OIDC-verified name materially differs from the application name.",
                        matched_claim=claims.application_name,
                    )
                )
            else:
                signals.append(
                    EnrichmentSignal(
                        code="IDENTITY_OIDC_VERIFIED",
                        polarity="positive",
                        severity="info",
                        confidence=max(0.7, similarity),
                        source=self.name,
                        detail="The OIDC-verified identity is consistent with the application name.",
                        matched_claim=claims.application_name,
                    )
                )

        email = (claims.oidc_verified_email or claims.application_email or "").strip().casefold()
        if email.count("@") == 1:
            domain = email.rsplit("@", 1)[1].rstrip(".")
            if domain in self.disposable_domains:
                signals.append(
                    EnrichmentSignal(
                        code="EMAIL_DISPOSABLE",
                        polarity="negative",
                        severity="low",
                        confidence=0.9,
                        source=self.name,
                        detail="The supplied email uses a known disposable-email provider.",
                        matched_claim=claims.oidc_verified_email or claims.application_email,
                    )
                )
        return signals
