from __future__ import annotations

import httpx

from firewall.enrichment.claims import extract_claims, find_invalid_code_links
from firewall.enrichment.github import GitHubConnector
from firewall.enrichment.models import Claims
from firewall.models import Candidate


def person() -> Candidate:
    return Candidate(name="Test Person", email="t@example.com", phone="+91 90000 00000", skills=["Python"], experience=[], projects=[])


def test_fake_hosts_are_found_and_real_ones_are_not():
    assert find_invalid_code_links("GitHub: https://github.example.test/sample") == ["https://github.example.test/sample"]
    assert find_invalid_code_links("Projects at github.example.test/sample") == ["github.example.test/sample"]
    assert find_invalid_code_links("GitHub: github.example.test/sample") == ["github.example.test/sample"]
    assert find_invalid_code_links("github.com/octocat and https://www.github.com/octocat/Hello-World") == []
    assert find_invalid_code_links("my pages are at octocat.github.io") == []
    assert find_invalid_code_links("GitHub: octocat") == []
    assert find_invalid_code_links("mygithubpages.dev is my site") == []


def test_extract_claims_carries_the_invalid_links():
    claims = extract_claims("Maya Rao\nGitHub: https://github.example.test/sample\nPython", person())
    assert claims.github_username is None
    assert claims.invalid_code_links == ["https://github.example.test/sample"]


def test_invalid_link_is_a_problem_without_any_network_call():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(404, json={})

    signals = GitHubConnector(transport=httpx.MockTransport(handler)).check(Claims(invalid_code_links=["github.example.test/sample"]))
    assert [item.code for item in signals] == ["GITHUB_LINK_INVALID"]
    assert signals[0].polarity == "negative" and calls == []


def test_account_that_does_not_exist_is_a_problem():
    handler = lambda request: httpx.Response(404, json={"message": "Not Found"})
    signals = GitHubConnector(transport=httpx.MockTransport(handler)).check(Claims(github_username="nobody-has-this-name"))
    assert [item.code for item in signals] == ["GITHUB_ACCOUNT_NOT_FOUND"]


def test_rate_limit_or_outage_is_not_treated_as_missing():
    limited = lambda request: httpx.Response(403, headers={"x-ratelimit-remaining": "0"}, json={})
    assert GitHubConnector(transport=httpx.MockTransport(limited)).check(Claims(github_username="octocat")) == []
    broken = lambda request: httpx.Response(500, json={})
    assert GitHubConnector(transport=httpx.MockTransport(broken)).check(Claims(github_username="octocat")) == []
