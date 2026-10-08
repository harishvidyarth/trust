from __future__ import annotations

import httpx

from firewall.enrichment.github_profile import GitHubProfileConnector
from firewall.enrichment.models import Claims, ProjectRepoClaim

RECENT = "2026-09-01T10:00:00Z"
OLD = "2022-01-10T10:00:00Z"


def commit(date: str) -> dict:
    return {"commit": {"author": {"date": date}}}


def repo(name: str, language: str | None, fork: bool = False) -> dict:
    return {"name": name, "fork": fork, "language": language, "created_at": OLD, "pushed_at": RECENT, "stargazers_count": 2}


def make_handler(repos, commits_by_repo, languages=None, user=None, limit=False):
    languages = languages or {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if limit:
            return httpx.Response(403, headers={"x-ratelimit-remaining": "0"}, json={"message": "rate limit"})
        if path == "/users/octo":
            return httpx.Response(200, json=user or {"created_at": OLD, "public_repos": len(repos), "followers": 4})
        if path == "/users/octo/repos":
            return httpx.Response(200, json=repos)
        parts = path.strip("/").split("/")
        if parts[0] == "repos" and len(parts) == 3:
            match = next((item for item in repos if item["name"] == parts[2]), None)
            if match is None:
                return httpx.Response(404, json={})
            detail = dict(match)
            if match["fork"]:
                detail["parent"] = {"full_name": "someone/" + match["name"]}
            return httpx.Response(200, json=detail)
        if parts[0] == "repos" and parts[3] == "languages":
            return httpx.Response(200, json=languages.get(parts[2], {}))
        if parts[0] == "repos" and parts[3] == "commits":
            count = commits_by_repo.get(parts[2])
            if count is None:
                return httpx.Response(404, json={})
            if count == 0:
                return httpx.Response(200, json=[])
            page = request.url.params.get("page")
            if count > 1 and page is None:
                link = f'<https://api.github.com/repos/octo/{parts[2]}/commits?author=octo&per_page=1&page={count}>; rel="last"'
                return httpx.Response(200, json=[commit(RECENT)], headers={"Link": link})
            return httpx.Response(200, json=[commit(OLD)])
        return httpx.Response(404, json={})

    return handler


def run(repos, commits, skills=(), claimed=(), languages=None, limit=False):
    connector = GitHubProfileConnector(skills=list(skills), transport=httpx.MockTransport(make_handler(repos, commits, languages, limit=limit)))
    signals = connector.check(Claims(github_username="octo", project_repos=[ProjectRepoClaim(name=name) for name in claimed]))
    return connector, {item.code: item for item in signals}, GitHubProfileConnector.summary_for("octo")


def test_commits_and_languages_are_reported():
    repos = [repo("alpha", "Python"), repo("beta", "JavaScript"), repo("gamma", "Python")]
    _, signals, summary = run(
        repos,
        {"alpha": 12, "beta": 3, "gamma": 1},
        skills=["Python", "FastAPI", "Rust"],
        languages={"alpha": {"Python": 9000, "Shell": 1000}},
    )
    assert "GITHUB_COMMITS_VERIFIED" in signals and "GITHUB_LANGUAGES_MATCH" in signals
    assert summary["original_repos"] == 3 and summary["active_last_year"] is True
    alpha = next(row for row in summary["repositories"] if row["name"] == "alpha")
    assert alpha["commits_by_you"] == 12 and alpha["first_commit"] and alpha["last_commit"]
    assert alpha["languages"][0] == {"language": "Python", "percent": 90}
    by_skill = {row["skill"]: row for row in summary["skills"]}
    assert by_skill["Python"]["found"] and by_skill["FastAPI"]["found"] and not by_skill["Rust"]["found"]
    assert summary["top_languages"][0]["language"] == "Python"


def test_claimed_project_that_is_a_fork_is_flagged():
    repos = [repo("alpha", "Python"), repo("copied", "Python", fork=True)]
    _, signals, summary = run(repos, {"alpha": 8, "copied": 20}, claimed=["copied"])
    assert "GITHUB_CLAIMED_REPO_IS_FORK" in signals
    row = next(item for item in summary["repositories"] if item["name"] == "copied")
    assert row["fork"] is True and row["parent"] == "someone/copied"


def test_claimed_project_with_no_commits_by_the_user_is_flagged():
    _, signals, _ = run([repo("alpha", "Python")], {"alpha": 0}, claimed=["alpha"])
    assert "GITHUB_NO_COMMITS_BY_USER" in signals and "GITHUB_COMMITS_VERIFIED" not in signals


def test_profile_of_only_forks_is_flagged():
    repos = [repo("a", "Go", True), repo("b", "Go", True), repo("c", "Go", True)]
    _, signals, summary = run(repos, {})
    assert "GITHUB_ALL_FORKS" in signals and summary["original_repos"] == 0


def test_no_language_evidence_needs_several_public_repositories():
    many = [repo("a", "Go"), repo("b", "Go"), repo("c", "Go")]
    _, signals, _ = run(many, {"a": 1, "b": 1, "c": 1}, skills=["Python"])
    assert "GITHUB_NO_LANGUAGE_EVIDENCE" in signals
    _, few, _ = run([repo("a", "Go")], {"a": 1}, skills=["Python"])
    assert "GITHUB_NO_LANGUAGE_EVIDENCE" not in few


def test_rate_limit_is_reported_not_punished():
    connector, signals, summary = run([repo("a", "Python")], {}, limit=True)
    assert signals == {} and connector.rate_limited is True and summary["rate_limited"] is True


def test_unknown_user_gives_no_signals():
    connector = GitHubProfileConnector(transport=httpx.MockTransport(lambda request: httpx.Response(404, json={})))
    assert connector.check(Claims(github_username="nobody-here")) == []
    assert GitHubProfileConnector.summary_for("nobody-here")["found"] is False


def test_no_github_link_means_nothing_to_check():
    assert GitHubProfileConnector(transport=httpx.MockTransport(lambda request: httpx.Response(404))).check(Claims()) == []
