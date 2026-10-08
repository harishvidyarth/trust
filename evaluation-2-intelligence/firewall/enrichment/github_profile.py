from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from firewall.enrichment._dates import parse_datetime
from firewall.enrichment.github import GitHubConnector
from firewall.enrichment.models import Claims, EnrichmentSignal

SUMMARY_TTL_SECONDS = 3600
LANGUAGE_NAMES = {
    "python": ("Python",),
    "javascript": ("JavaScript",),
    "js": ("JavaScript",),
    "typescript": ("TypeScript",),
    "ts": ("TypeScript",),
    "java": ("Java",),
    "go": ("Go",),
    "golang": ("Go",),
    "rust": ("Rust",),
    "c": ("C",),
    "c++": ("C++",),
    "cpp": ("C++",),
    "c#": ("C#",),
    "csharp": ("C#",),
    "kotlin": ("Kotlin",),
    "swift": ("Swift",),
    "php": ("PHP",),
    "ruby": ("Ruby",),
    "scala": ("Scala",),
    "dart": ("Dart",),
    "r": ("R",),
    "shell": ("Shell",),
    "bash": ("Shell",),
    "html": ("HTML",),
    "css": ("CSS", "SCSS"),
    "fastapi": ("Python",),
    "django": ("Python",),
    "flask": ("Python",),
    "pandas": ("Python", "Jupyter Notebook"),
    "numpy": ("Python", "Jupyter Notebook"),
    "pytorch": ("Python", "Jupyter Notebook"),
    "tensorflow": ("Python", "Jupyter Notebook"),
    "scikit-learn": ("Python", "Jupyter Notebook"),
    "react": ("JavaScript", "TypeScript"),
    "vue": ("JavaScript", "TypeScript", "Vue"),
    "angular": ("TypeScript", "JavaScript"),
    "node": ("JavaScript", "TypeScript"),
    "nodejs": ("JavaScript", "TypeScript"),
    "express": ("JavaScript", "TypeScript"),
    "next.js": ("JavaScript", "TypeScript"),
    "spring": ("Java", "Kotlin"),
    "spring boot": ("Java", "Kotlin"),
    "rails": ("Ruby",),
    "laravel": ("PHP",),
    "flutter": ("Dart",),
}
_LOCK = threading.Lock()
_SUMMARIES: dict[str, tuple[float, dict[str, Any]]] = {}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class GitHubProfileConnector(GitHubConnector):
    name = "github_profile"

    def __init__(self, skills: list[str] | tuple[str, ...] = (), transport: Any | None = None, timeout: float = 4.0, max_repos: int = 4) -> None:
        super().__init__(transport, timeout)
        self.skills = [str(item) for item in skills]
        self.max_repos = max(1, max_repos)
        self.rate_limited = False

    @staticmethod
    def summary_for(username: str | None) -> dict[str, Any] | None:
        if not username:
            return None
        with _LOCK:
            entry = _SUMMARIES.get(username.lower())
        if entry is None or time.time() - entry[0] > SUMMARY_TTL_SECONDS:
            return None
        return entry[1]

    def _fetch(self, url: str, **kwargs: Any) -> Any | None:
        response = self._get(url, headers=self._headers(), **kwargs)
        if response is None:
            return None
        if response.status_code in {403, 429} and response.headers.get("x-ratelimit-remaining") == "0":
            self.rate_limited = True
            return None
        return response

    def _commit_stats(self, username: str, repo: str) -> tuple[int, datetime | None, datetime | None] | None:
        base = f"{self.base_url}/repos/{quote(username, safe='')}/{quote(repo, safe='')}/commits"
        first_page = self._fetch(base, params={"author": username, "per_page": 1})
        if first_page is None or first_page.status_code != 200:
            return None
        items = self._json(first_page)
        if not isinstance(items, list):
            return None
        if not items:
            return 0, None, None
        last_url = first_page.links.get("last", {}).get("url")
        count = len(items)
        oldest = items[0]
        if last_url:
            query = parse_qs(urlparse(last_url).query)
            try:
                count = int(query.get("page", ["1"])[0])
            except ValueError:
                count = len(items)
            last_page = self._fetch(last_url)
            if last_page is not None and last_page.status_code == 200:
                last_items = self._json(last_page)
                if isinstance(last_items, list) and last_items:
                    oldest = last_items[0]

        def when(item: Any) -> datetime | None:
            commit = item.get("commit") if isinstance(item, dict) else None
            author = commit.get("author") if isinstance(commit, dict) else None
            return parse_datetime(author.get("date")) if isinstance(author, dict) else None

        return count, when(oldest), when(items[0])

    def _languages(self, username: str, repo: str) -> list[dict[str, Any]]:
        response = self._fetch(f"{self.base_url}/repos/{quote(username, safe='')}/{quote(repo, safe='')}/languages")
        data = self._json(response) if response is not None and response.status_code == 200 else None
        if not isinstance(data, dict) or not data:
            return []
        total = sum(value for value in data.values() if isinstance(value, int)) or 1
        rows = [
            {"language": str(name), "percent": round(100 * value / total)}
            for name, value in data.items()
            if isinstance(value, int)
        ]
        return sorted(rows, key=lambda row: -row["percent"])[:6]

    def _one_repo(self, username: str, name: str) -> dict[str, Any] | None:
        response = self._fetch(f"{self.base_url}/repos/{quote(username, safe='')}/{quote(name, safe='')}")
        if response is None or response.status_code != 200:
            return None
        data = self._json(response)
        return data if isinstance(data, dict) else None

    def check(self, claims: Claims) -> list[EnrichmentSignal]:
        username = (claims.github_username or "").strip()
        if not username:
            return []
        signals: list[EnrichmentSignal] = []
        profile_url = f"https://github.com/{quote(username, safe='')}"
        summary: dict[str, Any] = {
            "username": username,
            "profile_url": profile_url,
            "found": False,
            "rate_limited": False,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        user_response = self._fetch(f"{self.base_url}/users/{quote(username, safe='')}")
        user = self._json(user_response) if user_response is not None and user_response.status_code == 200 else None
        if not isinstance(user, dict):
            summary["rate_limited"] = self.rate_limited
            self._remember(username, summary)
            return signals
        summary.update(
            {
                "found": True,
                "account_created": _iso(parse_datetime(user.get("created_at"))),
                "public_repos": int(user.get("public_repos") or 0),
                "followers": int(user.get("followers") or 0),
            }
        )
        listing = self._fetch(
            f"{self.base_url}/users/{quote(username, safe='')}/repos",
            params={"per_page": 100, "sort": "pushed", "type": "owner"},
        )
        repos = self._json(listing) if listing is not None and listing.status_code == 200 else []
        repos = [item for item in repos if isinstance(item, dict) and item.get("name")] if isinstance(repos, list) else []
        originals = [item for item in repos if not item.get("fork")]
        forks = [item for item in repos if item.get("fork")]
        summary["original_repos"] = len(originals)
        summary["fork_repos"] = len(forks)
        cutoff = datetime.now(timezone.utc) - timedelta(days=365)
        pushed = [parse_datetime(item.get("pushed_at")) for item in repos]
        pushed = [value for value in pushed if value is not None]
        summary["last_pushed"] = _iso(max(pushed)) if pushed else None
        summary["active_last_year"] = bool(pushed and max(pushed) >= cutoff)

        counts: dict[str, int] = {}
        for item in originals:
            language = item.get("language")
            if isinstance(language, str) and language:
                counts[language] = counts.get(language, 0) + 1
        total_counted = sum(counts.values()) or 1
        summary["top_languages"] = [
            {"language": name, "repos": number, "share_percent": round(100 * number / total_counted)}
            for name, number in sorted(counts.items(), key=lambda entry: -entry[1])[:6]
        ]

        claimed_names = [repo.name.strip() for repo in claims.project_repos if repo.name.strip()]
        by_name = {str(item["name"]).lower(): item for item in repos}
        chosen: list[tuple[str, dict[str, Any] | None, bool]] = []
        for name in claimed_names:
            chosen.append((name, by_name.get(name.lower()), True))
        for item in originals:
            if len(chosen) >= self.max_repos:
                break
            if str(item["name"]).lower() not in {entry[0].lower() for entry in chosen}:
                chosen.append((str(item["name"]), item, False))

        rows: list[dict[str, Any]] = []
        best_commit_repo: tuple[int, str] | None = None
        for name, listed, claimed in chosen[: self.max_repos]:
            detail = self._one_repo(username, name) if (claimed or listed is None) else listed
            if detail is None:
                continue
            is_fork = bool(detail.get("fork"))
            parent = detail.get("parent") if isinstance(detail.get("parent"), dict) else None
            stats = self._commit_stats(username, str(detail["name"]))
            languages = self._languages(username, str(detail["name"]))
            url = f"https://github.com/{quote(username, safe='')}/{quote(str(detail['name']), safe='')}"
            row = {
                "name": str(detail["name"]),
                "url": url,
                "claimed": claimed,
                "fork": is_fork,
                "parent": str(parent.get("full_name")) if parent and parent.get("full_name") else None,
                "created": _iso(parse_datetime(detail.get("created_at"))),
                "last_pushed": _iso(parse_datetime(detail.get("pushed_at"))),
                "stars": int(detail.get("stargazers_count") or 0),
                "language": detail.get("language"),
                "languages": languages,
                "commits_by_you": stats[0] if stats else None,
                "first_commit": _iso(stats[1]) if stats else None,
                "last_commit": _iso(stats[2]) if stats else None,
            }
            rows.append(row)
            if claimed and is_fork:
                signals.append(
                    EnrichmentSignal(
                        code="GITHUB_CLAIMED_REPO_IS_FORK",
                        polarity="negative",
                        severity="medium",
                        confidence=0.7,
                        source=self.name,
                        detail=f"The claimed project {row['name']} is a fork of {row['parent'] or 'another repository'}.",
                        matched_claim=url,
                        evidence_url=url,
                    )
                )
            if claimed and not is_fork and stats is not None and stats[0] == 0:
                signals.append(
                    EnrichmentSignal(
                        code="GITHUB_NO_COMMITS_BY_USER",
                        polarity="negative",
                        severity="high",
                        confidence=0.75,
                        source=self.name,
                        detail=f"The account {username} authored no commits in the claimed project {row['name']}.",
                        matched_claim=url,
                        evidence_url=url,
                    )
                )
            if not is_fork and stats is not None and stats[0] >= 5 and (best_commit_repo is None or stats[0] > best_commit_repo[0]):
                best_commit_repo = (stats[0], url)
        summary["repositories"] = rows
        if best_commit_repo is not None:
            signals.append(
                EnrichmentSignal(
                    code="GITHUB_COMMITS_VERIFIED",
                    polarity="positive",
                    severity="info",
                    confidence=0.8,
                    source=self.name,
                    detail=f"The account authored {best_commit_repo[0]} commits in an original project.",
                    matched_claim=best_commit_repo[1],
                    evidence_url=best_commit_repo[1],
                )
            )
        if summary["public_repos"] >= 3 and repos and not originals:
            signals.append(
                EnrichmentSignal(
                    code="GITHUB_ALL_FORKS",
                    polarity="negative",
                    severity="low",
                    confidence=0.5,
                    source=self.name,
                    detail="Every public repository on the account is a fork of someone else's work.",
                    matched_claim=username,
                    evidence_url=profile_url,
                )
            )

        seen_languages = set(counts)
        for row in rows:
            seen_languages.update(item["language"] for item in row["languages"])
        skill_rows = []
        for skill in self.skills:
            targets = LANGUAGE_NAMES.get(skill.strip().lower())
            if not targets:
                continue
            matching = [item for item in originals if item.get("language") in targets]
            found = bool(matching) or any(target in seen_languages for target in targets)
            skill_rows.append({"skill": skill, "language": targets[0], "found": found, "repos": len(matching)})
        summary["skills"] = skill_rows
        if skill_rows:
            hit = sum(1 for item in skill_rows if item["found"])
            if hit / len(skill_rows) >= 0.5:
                signals.append(
                    EnrichmentSignal(
                        code="GITHUB_LANGUAGES_MATCH",
                        polarity="positive",
                        severity="info",
                        confidence=0.7,
                        source=self.name,
                        detail=f"{hit} of {len(skill_rows)} listed programming skills appear in public code.",
                        matched_claim=", ".join(item["skill"] for item in skill_rows if item["found"]),
                        evidence_url=profile_url,
                    )
                )
            elif hit == 0 and len(originals) >= 3:
                signals.append(
                    EnrichmentSignal(
                        code="GITHUB_NO_LANGUAGE_EVIDENCE",
                        polarity="negative",
                        severity="low",
                        confidence=0.4,
                        source=self.name,
                        detail="None of the listed programming skills appear in the public repositories, although several exist.",
                        matched_claim=", ".join(item["skill"] for item in skill_rows),
                        evidence_url=profile_url,
                    )
                )
        summary["rate_limited"] = self.rate_limited
        self._remember(username, summary)
        return signals

    def _remember(self, username: str, summary: dict[str, Any]) -> None:
        with _LOCK:
            _SUMMARIES[username.lower()] = (time.time(), summary)
