from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from firewall.config import Config
from firewall.intel import (
    ResumeFacts,
    active_signals,
    agreeing_facts,
    dispute,
    extract_facts,
    ground_facts,
    pending_rechecks,
    recruiter_view,
    render_findings,
    run_passive_intel,
    should_run,
    trust_bonus,
)
from firewall.intel.models import Hit
from firewall.intel.shrink import keep_decision
from firewall.models import Candidate, Experience

from tests_intel.conftest import mock_client

ENV = {"FIREWALL_ENRICH": "1"}
NOW = datetime(2026, 1, 15, tzinfo=timezone.utc)


def facts(**overrides):
    values = dict(
        name="Ada Synthetic",
        employers=["Analytical Engines"],
        colleges=["University of Example"],
        cities=["London"],
        links=["https://github.com/ada-real"],
        repos=["ada-real/scheduler"],
        dois=["10.1234/abc"],
        orcids=["0000-0002-1825-0097"],
    )
    values.update(overrides)
    return ResumeFacts(**values)


class Router:
    def __init__(self):
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        host = request.url.host
        if host == "api.github.com":
            if path == "/search/users":
                return httpx.Response(200, json={"items": [{"login": "ada-real"}, {"login": "ada-namesake"}]})
            if path == "/users/ada-real":
                return httpx.Response(
                    200,
                    json={
                        "login": "ada-real",
                        "name": "Ada Synthetic",
                        "company": "@Analytical Engines",
                        "location": "London",
                        "blog": "https://ada.example.dev",
                        "html_url": "https://github.com/ada-real",
                        "created_at": "2015-01-01T00:00:00Z",
                    },
                )
            if path == "/users/ada-namesake":
                return httpx.Response(
                    200,
                    json={
                        "login": "ada-namesake",
                        "name": "Ada Synthetic",
                        "company": "Unrelated Holdings",
                        "location": "Paris",
                        "html_url": "https://github.com/ada-namesake",
                    },
                )
            if path == "/repos/ada-real/scheduler":
                return httpx.Response(200, json={"owner": {"login": "ada-real"}, "fork": False, "html_url": "https://github.com/ada-real/scheduler"})
        if host == "api.openalex.org":
            if path == "/authors":
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "id": "https://openalex.org/A1",
                                "display_name": "Ada Synthetic",
                                "orcid": "https://orcid.org/0000-0002-1825-0097",
                                "last_known_institutions": [{"display_name": "University of Example"}],
                            },
                            {"id": "https://openalex.org/A2", "display_name": "Ada Synthetic", "last_known_institutions": [{"display_name": "Faraway Polytechnic"}]},
                        ]
                    },
                )
            if path == "/works":
                if "A1" in request.url.params.get("filter", ""):
                    return httpx.Response(200, json={"results": [{"doi": "https://doi.org/10.1234/abc"}]})
                return httpx.Response(200, json={"results": [{"doi": "https://doi.org/10.9999/zzz"}]})
        if host == "api.crossref.org":
            return httpx.Response(
                200,
                json={
                    "message": {
                        "items": [
                            {
                                "DOI": "10.1234/abc",
                                "URL": "https://doi.org/10.1234/abc",
                                "author": [{"given": "Ada", "family": "Synthetic", "affiliation": [{"name": "University of Example"}]}],
                            },
                            {
                                "DOI": "10.5555/other",
                                "URL": "https://doi.org/10.5555/other",
                                "author": [{"given": "Ada", "family": "Synthetic", "affiliation": [{"name": "Nowhere Institute"}]}],
                            },
                        ]
                    }
                },
            )
        return httpx.Response(404)


def run(router, resume_facts=None, **kwargs):
    defaults = dict(application_id="app-1", consent_given=True, transport=mock_client(router), env=ENV, now=NOW)
    defaults.update(kwargs)
    return run_passive_intel(resume_facts or facts(), **defaults)


def github_finding(result):
    return next(item for item in result.findings if item.source.startswith("github"))


def test_shrink_keeps_matching_and_discards_namesakes():
    router = Router()
    result = run(router)
    urls = {finding.source_url for finding in result.findings}
    assert "https://github.com/ada-real" in urls
    assert "https://github.com/ada-namesake" not in urls
    assert "https://openalex.org/A2" not in urls
    assert "https://doi.org/10.5555/other" not in urls
    assert result.consent.discarded == 3
    assert result.consent.kept == len(result.findings)


def test_discarded_hits_are_never_stored():
    dump = run(Router()).model_dump_json()
    for leaked in ("ada-namesake", "Unrelated Holdings", "Paris", "Faraway Polytechnic", "Nowhere Institute", "10.5555/other"):
        assert leaked not in dump


def test_kept_finding_records_provenance_and_matched_facts():
    result = run(Router())
    github = github_finding(result)
    assert github.fetched_at.tzinfo is not None
    assert github.source_url == "https://github.com/ada-real"
    assert set(github.matched_facts) >= {"name:Ada Synthetic", "employer:Analytical Engines", "city:London", "link:https://github.com/ada-real"}


def test_unverified_match_has_weight_zero_without_confirmation():
    result = run(Router(), facts(repos=[], dois=[], orcids=[]))
    github = github_finding(result)
    assert github.status == "unverified_match"
    assert github.weight == 0
    assert result.signals_by_finding[github.finding_id] == []
    assert github.confirmation is None


def test_github_repo_ownership_confirms_and_emits_signal():
    result = run(Router())
    github = github_finding(result)
    assert github.status == "confirmed" and github.confirmation == "github repo ownership"
    signals = result.signals_by_finding[github.finding_id]
    assert [item.code for item in signals] == ["GITHUB_CORROBORATED"]
    assert signals[0].polarity == "positive"
    assert github.weight == 0


def test_orcid_and_doi_author_confirmation():
    result = run(Router())
    codes = {item.code for signals in result.signals_by_finding.values() for item in signals}
    assert {"ORCID_REGISTRY_MATCH", "DOI_VERIFIED", "GITHUB_CORROBORATED"} <= codes
    assert trust_bonus(result) > 0
    assert trust_bonus(result, cap=3) == 3


def test_name_and_city_alone_is_not_enough():
    weak = Hit(source="github_user_search", source_url="u", fetched_at=NOW, name="Ada Synthetic", location=("London",))
    assert keep_decision(weak, facts()) is None
    assert agreeing_facts(weak, facts()) == ["name:Ada Synthetic", "city:London"]


def test_two_strong_facts_without_name_match_kept():
    hit = Hit(source="x", source_url="u", fetched_at=NOW, name="A. Different", org=("Analytical Engines", "University of Example"))
    assert keep_decision(hit, facts()) is not None


def test_single_fact_discarded():
    hit = Hit(source="x", source_url="u", fetched_at=NOW, name="Ada Synthetic")
    assert keep_decision(hit, facts()) is None


def test_gate_requires_env_and_consent_and_makes_no_requests():
    router = Router()
    off = run_passive_intel(facts(), application_id="a", consent_given=True, transport=mock_client(router), env={}, now=NOW)
    no_consent = run_passive_intel(facts(), application_id="a", consent_given=False, transport=mock_client(router), env=ENV, now=NOW)
    assert router.requests == []
    assert off.findings == [] and off.consent.skipped_reason == "FIREWALL_ENRICH is not 1"
    assert no_consent.findings == [] and no_consent.consent.skipped_reason == "candidate consent not recorded"


def test_queries_contain_name_only_and_consent_record_lists_searches():
    router = Router()
    result = run(router)
    for request in router.requests:
        query = str(request.url.params)
        for private in ("Analytical", "University", "London", "0000-0002"):
            assert private not in query
    assert any("github user search" in item for item in result.consent.searched)
    assert result.consent.purpose and result.consent.started_at == NOW


def test_network_failures_are_swallowed():
    def boom(request):
        raise httpx.ConnectError("down")

    result = run_passive_intel(facts(), application_id="a", consent_given=True, transport=mock_client(boom), env=ENV, now=NOW)
    assert result.findings == []


def test_should_run_band_and_env():
    assert should_run(41, env=ENV) and should_run(69, env=ENV)
    assert not should_run(40, env=ENV) and not should_run(70, env=ENV)
    assert not should_run(55, env={})
    assert should_run(45, Config(corroboration_min=45, corroboration_max=50), ENV)
    assert not should_run(44, Config(corroboration_min=45, corroboration_max=50), ENV)


def test_render_dispute_and_recruiter_view():
    result = run(Router())
    text = render_findings(result)
    assert "What we looked up" in text and "https://github.com/ada-real" in text and "matched your resume on" in text
    target = github_finding(result)
    disputed = dispute(result, target.finding_id, "not me", now=NOW)
    assert not any(item.finding_id == target.finding_id for item in recruiter_view(disputed))
    assert any(item.finding_id == target.finding_id for item in recruiter_view(result))
    assert [item.finding_id for item in pending_rechecks(disputed)] == [target.finding_id]
    assert len(active_signals(disputed)) < len(active_signals(result))
    assert trust_bonus(disputed, cap=100) < trust_bonus(result, cap=100)
    assert "disputed, awaiting human re-check" in render_findings(disputed)
    with pytest.raises(KeyError):
        dispute(result, "missing", "x")


def test_render_when_nothing_kept():
    result = run_passive_intel(facts(name="", links=[]), application_id="a", consent_given=True, transport=mock_client(Router()), env=ENV, now=NOW)
    assert "No public records matched" in render_findings(result)


def test_extract_facts_verbatim_only():
    text = (
        "Ada Synthetic\nLocation: London, UK\nBSc, University of Example, graduated 2017\n"
        "Code: https://github.com/ada-real/scheduler\nORCID 0000-0002-1825-0097\ndoi: 10.1234/abc\n"
        "Certificate ID: CERT123456\n"
    )
    candidate = Candidate(
        name="Ada Synthetic",
        email="a@example.com",
        phone="1",
        experience=[Experience(company="Analytical Engines", title="Eng", start="2020-01", end="2021-01"), Experience(company="Ghost Corp", title="x", start="2019-01", end="2019-12")],
    )
    extracted = extract_facts(text, candidate)
    assert extracted.employers == []
    assert extracted.colleges == ["University of Example"]
    assert extracted.cities == ["London"]
    assert extracted.repos == ["ada-real/scheduler"]
    assert extracted.orcids == ["0000-0002-1825-0097"]
    assert extracted.dois == ["10.1234/abc"]
    assert extracted.certs == ["CERT123456"]
    assert extracted.name == "Ada Synthetic"


def test_ground_facts_drops_hallucinated_values():
    text = "Ada Synthetic worked at Analytical Engines in London"
    claimed = facts(employers=["Analytical Engines", "Imaginary Labs"], cities=["London", "Atlantis"], links=["https://github.com/fake"])
    grounded = ground_facts(claimed, text)
    assert grounded.employers == ["Analytical Engines"]
    assert grounded.cities == ["London"]
    assert grounded.links == []
    assert grounded.colleges == []
