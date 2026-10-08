from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from firewall.enrichment.crossref import CrossrefConnector
from firewall.enrichment.domain import DomainConnector
from firewall.enrichment.github import GitHubConnector
from firewall.enrichment.identity import IdentityConnector
from firewall.enrichment.models import Claims


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def codes(signals) -> set[str]:
    return {signal.code for signal in signals}


def test_github_corroborates_repo_and_sets_request_headers() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/users/octocat":
            return httpx.Response(200, json={"created_at": "2011-01-25T18:44:36Z"})
        if request.url.path == "/repos/octocat/hello-world/commits":
            return httpx.Response(200, json=[{"commit": {"author": {"date": "2020-01-03T00:00:00Z"}}}])
        return httpx.Response(200, json={"created_at": "2019-12-31T00:00:00Z", "html_url": "https://github.com/octocat/hello-world"})

    claims = Claims(
        github_username="octocat",
        project_repos=[{"name": "hello-world", "claimed_start": "2020-01-01", "claimed_end": "2020-12-31"}],
    )
    signals = GitHubConnector(transport=client(handler)).check(claims)

    assert codes(signals) == {"GITHUB_CORROBORATED"}
    assert all(request.headers["User-Agent"].startswith("AI-Application-Firewall/") for request in seen)


def test_github_reports_explicit_not_found_and_date_contradictions() -> None:
    recent = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat().replace("+00:00", "Z")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/users/new-user":
            return httpx.Response(200, json={"created_at": recent})
        if request.url.path.endswith("/missing"):
            return httpx.Response(404)
        if request.url.path.endswith("/late/commits"):
            return httpx.Response(200, json=[{"commit": {"author": {"date": "2024-06-01T00:00:00Z"}}}])
        return httpx.Response(200, json={"created_at": "2024-05-01T00:00:00Z"})

    claims = Claims(
        github_username="new-user",
        project_repos=[
            {"name": "missing", "claimed_start": "2020-01", "claimed_end": "2021-01"},
            {"name": "late", "claimed_start": "2020-01", "claimed_end": "2021-01"},
        ],
    )
    assert codes(GitHubConnector(transport=client(handler)).check(claims)) == {
        "GITHUB_ACCOUNT_NEW",
        "GITHUB_DATES_MISMATCH",
        "GITHUB_REPO_NOT_FOUND",
    }


def test_github_uses_last_page_as_first_commit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/users/octocat":
            return httpx.Response(200, json={"created_at": "2011-01-01T00:00:00Z"})
        if request.url.path.endswith("/commits") and request.url.params.get("page") == "99":
            return httpx.Response(200, json=[{"commit": {"author": {"date": "2020-01-03T00:00:00Z"}}}])
        if request.url.path.endswith("/commits"):
            return httpx.Response(
                200,
                headers={"Link": '<https://api.github.com/repos/octocat/history/commits?per_page=1&page=99>; rel="last"'},
                json=[{"commit": {"author": {"date": "2025-01-01T00:00:00Z"}}}],
            )
        return httpx.Response(200, json={"created_at": "2019-12-31T00:00:00Z"})

    claims = Claims(
        github_username="octocat",
        project_repos=[{"name": "history", "claimed_start": "2020-01-01", "claimed_end": "2020-12-31"}],
    )
    assert codes(GitHubConnector(transport=client(handler)).check(claims)) == {"GITHUB_CORROBORATED"}


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=request)),
        lambda request: httpx.Response(200, content=b"not-json"),
        lambda request: httpx.Response(200, json={"unexpected": "shape"}),
    ],
)
def test_github_failures_and_malformed_data_are_neutral(handler) -> None:
    claims = Claims(github_username="octocat")
    assert GitHubConnector(transport=client(handler)).check(claims) == []


def test_github_absent_claim_is_neutral() -> None:
    assert GitHubConnector(transport=client(lambda request: httpx.Response(500))).check(Claims()) == []


def test_crossref_positive_mismatch_and_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        doi = request.url.path.removeprefix("/works/")
        if doi.endswith("verified"):
            return httpx.Response(200, json={"message": {"title": ["Reliable AI Firewalls"]}})
        if doi.endswith("mismatch"):
            return httpx.Response(200, json={"message": {"title": ["Marine Biology"]}})
        return httpx.Response(404)

    claims = Claims(
        dois=[
            {"doi": "10.1/verified", "claimed_title": "Reliable AI Firewalls"},
            {"doi": "10.1/mismatch", "claimed_title": "Quantum Compiler Design"},
            {"doi": "10.1/missing", "claimed_title": "Missing Paper"},
        ]
    )
    assert codes(CrossrefConnector(transport=client(handler)).check(claims)) == {
        "DOI_VERIFIED",
        "DOI_TITLE_MISMATCH",
        "DOI_NOT_FOUND",
    }


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: (_ for _ in ()).throw(httpx.ConnectTimeout("slow", request=request)),
        lambda request: httpx.Response(200, content=b"{"),
        lambda request: httpx.Response(200, json={"message": {"title": []}}),
    ],
)
def test_crossref_failures_and_malformed_data_are_neutral(handler) -> None:
    claims = Claims(dois=[{"doi": "10.1/test", "claimed_title": "A title"}])
    assert CrossrefConnector(transport=client(handler)).check(claims) == []


def test_crossref_absent_claim_is_neutral() -> None:
    assert CrossrefConnector(transport=client(lambda request: httpx.Response(500))).check(Claims()) == []


def test_domain_reports_recent_age_and_reachability() -> None:
    recent = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "rdap.org":
            return httpx.Response(200, json={"events": [{"eventAction": "registration", "eventDate": recent}]})
        return httpx.Response(200)

    claims = Claims(portfolio_url="https://portfolio.example/work")
    assert codes(DomainConnector(transport=client(handler)).check(claims)) == {
        "DOMAIN_AGE_RECENT",
        "DOMAIN_REACHABLE",
    }


def test_domain_old_site_is_positive_without_age_penalty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "rdap.org":
            return httpx.Response(200, json={"events": [{"eventAction": "registration", "eventDate": "1995-08-14T04:00:00Z"}]})
        return httpx.Response(204)

    assert codes(DomainConnector(transport=client(handler)).check(Claims(portfolio_url="https://example.com"))) == {
        "DOMAIN_REACHABLE"
    }


@pytest.mark.parametrize(
    "url,handler",
    [
        (None, lambda request: httpx.Response(500)),
        ("http://127.0.0.1/private", lambda request: httpx.Response(200)),
        ("https://example.com", lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=request))),
        ("https://example.com", lambda request: httpx.Response(200, content=b"not-json") if request.url.host == "rdap.org" else httpx.Response(503)),
    ],
)
def test_domain_absence_unsafe_urls_failures_and_malformed_data_are_neutral(url, handler) -> None:
    assert DomainConnector(transport=client(handler)).check(Claims(portfolio_url=url)) == []


def test_identity_oidc_name_match_and_disposable_email() -> None:
    claims = Claims(
        application_name="Ada Lovelace",
        oidc_verified_name="Ada Byron Lovelace",
        oidc_verified_email="ada@mailinator.com",
    )
    assert codes(IdentityConnector().check(claims)) == {"IDENTITY_OIDC_VERIFIED", "EMAIL_DISPOSABLE"}


def test_identity_name_mismatch() -> None:
    claims = Claims(application_name="Ada Lovelace", oidc_verified_name="Grace Hopper")
    assert codes(IdentityConnector().check(claims)) == {"IDENTITY_NAME_MISMATCH"}


@pytest.mark.parametrize(
    "claims",
    [Claims(), Claims(application_name="Ada Lovelace"), Claims(application_name=" ", oidc_verified_name="???")],
)
def test_identity_absent_or_malformed_claims_are_neutral(claims) -> None:
    assert IdentityConnector().check(claims) == []


def test_signal_details_never_contain_claimed_pii() -> None:
    pii = ["Private Person", "private@example.com", "secret-user", "secret-repo", "private.example", "10.1/private", "Secret Paper"]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.github.com" and request.url.path.startswith("/users/"):
            return httpx.Response(200, json={"created_at": "2026-01-01T00:00:00Z"})
        if request.url.host == "api.github.com":
            return httpx.Response(404)
        if request.url.host == "api.crossref.org":
            return httpx.Response(404)
        if request.url.host == "rdap.org":
            return httpx.Response(200, json={"events": [{"eventAction": "registration", "eventDate": "2026-09-01T00:00:00Z"}]})
        return httpx.Response(200)

    transport = client(handler)
    claims = Claims(
        application_name="Private Person",
        github_username="secret-user",
        project_repos=[{"name": "secret-repo", "claimed_start": "2020", "claimed_end": "2021"}],
        dois=[{"doi": "10.1/private", "claimed_title": "Secret Paper"}],
        portfolio_url="https://private.example",
        oidc_verified_name="Different Name",
        oidc_verified_email="private@example.com",
    )
    signals = [
        *GitHubConnector(transport=transport).check(claims),
        *CrossrefConnector(transport=transport).check(claims),
        *DomainConnector(transport=transport).check(claims),
        *IdentityConnector().check(claims),
    ]
    assert signals
    details = " ".join(signal.detail for signal in signals).casefold()
    assert all(value.casefold() not in details for value in pii)
