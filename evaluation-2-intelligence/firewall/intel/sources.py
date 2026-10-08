from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Iterator
from urllib.parse import quote

from firewall.enrichment._http import HttpConnector
from firewall.intel.models import Hit


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _strip_prefix(value: str, prefixes: tuple[str, ...]) -> str:
    for prefix in prefixes:
        if value.startswith(prefix):
            return value[len(prefix):]
    return value


class PublicSources(HttpConnector):
    github_url = "https://api.github.com"
    openalex_url = "https://api.openalex.org"
    crossref_url = "https://api.crossref.org/works"

    def _github_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        token = os.getenv("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def github_profile(self, login: str, source: str = "github_user_search") -> Hit | None:
        url = f"{self.github_url}/users/{quote(login, safe='')}"
        response = self._get(url, headers=self._github_headers())
        if response is None or response.status_code != 200:
            return None
        payload = self._json(response)
        if not isinstance(payload, dict):
            return None
        company = _text(payload.get("company")).lstrip("@")
        blog = _text(payload.get("blog"))
        html_url = _text(payload.get("html_url")) or f"https://github.com/{login}"
        return Hit(
            source=source,
            source_url=html_url,
            fetched_at=datetime.now(timezone.utc),
            name=_text(payload.get("name")),
            org=tuple(item for item in (company, _text(payload.get("bio"))) if item),
            location=tuple(item for item in (_text(payload.get("location")),) if item),
            links=tuple(item for item in (html_url, blog) if item),
            login=_text(payload.get("login")) or login,
            extra={"created_at": _text(payload.get("created_at"))},
        )

    def github_users(self, name: str, limit: int = 5) -> Iterator[Hit]:
        response = self._get(
            f"{self.github_url}/search/users",
            params={"q": f"{name} in:fullname", "per_page": limit},
            headers=self._github_headers(),
        )
        if response is None or response.status_code != 200:
            return
        payload = self._json(response)
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return
        for item in items[:limit]:
            login = _text(item.get("login")) if isinstance(item, dict) else ""
            if not login:
                continue
            hit = self.github_profile(login)
            if hit is not None:
                yield hit

    def github_repo_owner(self, login: str, repo: str) -> tuple[str, bool, str] | None:
        url = f"{self.github_url}/repos/{quote(login, safe='')}/{quote(repo, safe='')}"
        response = self._get(url, headers=self._github_headers())
        if response is None or response.status_code != 200:
            return None
        payload = self._json(response)
        if not isinstance(payload, dict):
            return None
        owner = payload.get("owner")
        owner_login = _text(owner.get("login")) if isinstance(owner, dict) else ""
        return owner_login, bool(payload.get("fork")), _text(payload.get("html_url")) or url

    def openalex_authors(self, name: str, limit: int = 5) -> Iterator[Hit]:
        response = self._get(f"{self.openalex_url}/authors", params={"search": name, "per-page": limit})
        if response is None or response.status_code != 200:
            return
        payload = self._json(response)
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            return
        for item in results[:limit]:
            if not isinstance(item, dict):
                continue
            author_id = _text(item.get("id"))
            institutions: list[str] = []
            for entry in item.get("last_known_institutions") or []:
                if isinstance(entry, dict) and _text(entry.get("display_name")):
                    institutions.append(_text(entry.get("display_name")))
            for entry in item.get("affiliations") or []:
                institution = entry.get("institution") if isinstance(entry, dict) else None
                if isinstance(institution, dict) and _text(institution.get("display_name")):
                    institutions.append(_text(institution.get("display_name")))
            dois = self._openalex_dois(author_id) if author_id else ()
            yield Hit(
                source="openalex_author",
                source_url=author_id,
                fetched_at=datetime.now(timezone.utc),
                name=_text(item.get("display_name")),
                org=tuple(dict.fromkeys(institutions)),
                orcid=_strip_prefix(_text(item.get("orcid")), ("https://orcid.org/", "http://orcid.org/")),
                dois=dois,
            )

    def _openalex_dois(self, author_id: str) -> tuple[str, ...]:
        response = self._get(
            f"{self.openalex_url}/works",
            params={"filter": f"author.id:{author_id}", "per-page": 25, "select": "doi"},
        )
        if response is None or response.status_code != 200:
            return ()
        payload = self._json(response)
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            return ()
        found = []
        for work in results:
            doi = _text(work.get("doi")) if isinstance(work, dict) else ""
            if doi:
                found.append(_strip_prefix(doi, ("https://doi.org/", "http://doi.org/")))
        return tuple(found)

    def crossref_works(self, name: str, limit: int = 5) -> Iterator[Hit]:
        response = self._get(self.crossref_url, params={"query.author": name, "rows": limit})
        if response is None or response.status_code != 200:
            return
        payload = self._json(response)
        message = payload.get("message") if isinstance(payload, dict) else None
        items = message.get("items") if isinstance(message, dict) else None
        if not isinstance(items, list):
            return
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            doi = _text(item.get("DOI"))
            for author in item.get("author") or []:
                if not isinstance(author, dict):
                    continue
                full = f"{_text(author.get('given'))} {_text(author.get('family'))}".strip()
                affiliations = tuple(
                    _text(entry.get("name"))
                    for entry in author.get("affiliation") or []
                    if isinstance(entry, dict) and _text(entry.get("name"))
                )
                yield Hit(
                    source="crossref_author",
                    source_url=_text(item.get("URL")) or (f"https://doi.org/{doi}" if doi else ""),
                    fetched_at=datetime.now(timezone.utc),
                    name=full,
                    org=affiliations,
                    orcid=_strip_prefix(_text(author.get("ORCID")), ("https://orcid.org/", "http://orcid.org/")),
                    dois=(doi,) if doi else (),
                )
