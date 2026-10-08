from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from firewall.enrichment._dates import parse_datetime
from firewall.enrichment._http import HttpConnector
from firewall.enrichment.models import Claims, EnrichmentSignal


class GitHubConnector(HttpConnector):
    name = "github"
    base_url = "https://api.github.com"

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        token = os.getenv("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def check(self, claims: Claims) -> list[EnrichmentSignal]:
        username = (claims.github_username or "").strip()
        signals: list[EnrichmentSignal] = []
        for link in claims.invalid_code_links:
            signals.append(
                EnrichmentSignal(
                    code="GITHUB_LINK_INVALID",
                    polarity="negative",
                    severity="medium",
                    confidence=0.8,
                    source=self.name,
                    detail=f"The code link {link} does not point to github.com, so it cannot be a real GitHub profile or project.",
                    matched_claim=link,
                    evidence_url=None,
                )
            )
        if not username:
            return signals

        user_url = f"{self.base_url}/users/{quote(username, safe='')}"
        user_response = self._get(user_url, headers=self._headers())
        if user_response is not None and user_response.status_code == 404:
            signals.append(
                EnrichmentSignal(
                    code="GITHUB_ACCOUNT_NOT_FOUND",
                    polarity="negative",
                    severity="high",
                    confidence=0.85,
                    source=self.name,
                    detail=f"No GitHub account named {username} exists.",
                    matched_claim=f"https://github.com/{username}",
                    evidence_url=f"https://github.com/{quote(username, safe='')}",
                )
            )
            return signals
        account_created = None
        if user_response is not None and user_response.status_code == 200:
            payload = self._json(user_response)
            if isinstance(payload, dict):
                account_created = parse_datetime(payload.get("created_at"))

        starts = [parse_datetime(repo.claimed_start) for repo in claims.project_repos]
        starts = [value for value in starts if value is not None]
        account_is_new = bool(
            account_created
            and (
                datetime.now(timezone.utc) - account_created < timedelta(days=180)
                or (starts and account_created > min(starts) + timedelta(days=30))
            )
        )
        if account_is_new:
            signals.append(
                EnrichmentSignal(
                    code="GITHUB_ACCOUNT_NEW",
                    polarity="negative",
                    severity="low",
                    confidence=0.45,
                    source=self.name,
                    detail="The public account timeline is unusually recent relative to the supplied project timeline.",
                    matched_claim=username,
                    evidence_url=user_url,
                )
            )

        for repo in claims.project_repos:
            repo_name = repo.name.strip()
            if not repo_name:
                continue
            api_url = f"{self.base_url}/repos/{quote(username, safe='')}/{quote(repo_name, safe='')}"
            response = self._get(api_url, headers=self._headers())
            if response is None:
                continue
            evidence_url = f"https://github.com/{quote(username, safe='')}/{quote(repo_name, safe='')}"
            if response.status_code == 404:
                signals.append(
                    EnrichmentSignal(
                        code="GITHUB_REPO_NOT_FOUND",
                        polarity="negative",
                        severity="medium",
                        confidence=0.85,
                        source=self.name,
                        detail="A specifically claimed public repository was not found under the authorized account.",
                        matched_claim=f"https://github.com/{username}/{repo_name}",
                        evidence_url=evidence_url,
                    )
                )
                continue
            if response.status_code != 200:
                continue
            payload = self._json(response)
            if not isinstance(payload, dict):
                continue
            repo_created = parse_datetime(payload.get("created_at"))
            if repo_created is None:
                continue

            first_commit = self._first_commit_date(username, repo_name)
            claimed_start = parse_datetime(repo.claimed_start)
            claimed_end = parse_datetime(repo.claimed_end, end=True)
            mismatch = bool(
                (claimed_end and repo_created > claimed_end + timedelta(days=30))
                or (claimed_end and first_commit and first_commit > claimed_end + timedelta(days=30))
                or (
                    claimed_start
                    and min(value for value in (repo_created, first_commit) if value is not None)
                    > claimed_start + timedelta(days=90)
                )
            )
            if mismatch:
                signals.append(
                    EnrichmentSignal(
                        code="GITHUB_DATES_MISMATCH",
                        polarity="negative",
                        severity="high",
                        confidence=0.9,
                        source=self.name,
                        detail="The earliest public repository activity materially postdates the supplied project timeline.",
                        matched_claim=f"https://github.com/{username}/{repo_name}",
                        evidence_url=evidence_url,
                    )
                )
            else:
                signals.append(
                    EnrichmentSignal(
                        code="GITHUB_CORROBORATED",
                        polarity="positive",
                        severity="info",
                        confidence=0.8,
                        source=self.name,
                        detail="Public repository metadata is consistent with the supplied project timeline.",
                        matched_claim=f"https://github.com/{username}/{repo_name}",
                        evidence_url=evidence_url,
                    )
                )
        return signals

    def _first_commit_date(self, username: str, repo_name: str):
        url = f"{self.base_url}/repos/{quote(username, safe='')}/{quote(repo_name, safe='')}/commits"
        response = self._get(url, params={"per_page": 1}, headers=self._headers())
        if response is None or response.status_code != 200:
            return None
        last_url = response.links.get("last", {}).get("url")
        if last_url:
            last_response = self._get(last_url, headers=self._headers())
            if last_response is not None and last_response.status_code == 200:
                response = last_response
        payload = self._json(response)
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
            return None
        commit = payload[0].get("commit")
        if not isinstance(commit, dict):
            return None
        author = commit.get("author")
        if not isinstance(author, dict):
            return None
        return parse_datetime(author.get("date"))
