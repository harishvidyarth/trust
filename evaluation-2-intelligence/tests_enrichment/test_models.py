from __future__ import annotations

import pytest
from pydantic import ValidationError

from firewall.enrichment.models import Claims, EnrichmentSignal


def test_claims_accept_structured_claims() -> None:
    claims = Claims(
        application_name="Candidate",
        github_username="candidate",
        project_repos=[{"name": "demo", "claimed_start": "2024-01", "claimed_end": "2024-12"}],
        dois=[{"doi": "10.1/example", "claimed_title": "Example"}],
        employers=["Example Ltd"],
    )
    assert claims.project_repos[0].name == "demo"


def test_signal_confidence_is_bounded() -> None:
    with pytest.raises(ValidationError):
        EnrichmentSignal(code="BAD", polarity="negative", severity="low", confidence=1.1, source="test", detail="Generic")
