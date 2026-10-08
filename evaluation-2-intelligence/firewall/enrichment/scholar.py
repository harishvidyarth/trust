from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Callable
from urllib.parse import quote
from defusedxml import ElementTree

from firewall.enrichment._http import HttpConnector
from firewall.enrichment.models import Claims, EnrichmentSignal, PaperClaim


@dataclass(frozen=True)
class _Candidate:
    title: str
    authors: tuple[str, ...]
    year: int | None
    venue: str | None
    evidence_url: str | None


def _normalise(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    unaccented = "".join(character for character in decomposed if not unicodedata.combining(character))
    return " ".join(re.findall(r"[a-z0-9]+", unaccented.casefold()))


def _title_similarity(left: str, right: str) -> float:
    normalised_left = _normalise(left)
    normalised_right = _normalise(right)
    if not normalised_left or not normalised_right:
        return 0.0
    character_ratio = SequenceMatcher(None, normalised_left, normalised_right).ratio()
    token_ratio = SequenceMatcher(None, normalised_left.split(), normalised_right.split()).ratio()
    return (character_ratio + token_ratio) / 2


def _surname(value: str) -> str:
    name = value.split(",", 1)[0] if "," in value else value
    parts = _normalise(name).split()
    while parts and parts[-1] in {"jr", "sr", "ii", "iii", "iv"}:
        parts.pop()
    return parts[-1] if parts else ""


def _year(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if 1000 <= result <= 9999 else None


def _first_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                return item.strip()
    return None


def _date_parts_year(value: Any) -> int | None:
    if not isinstance(value, dict):
        return None
    parts = value.get("date-parts")
    if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
        return _year(parts[0][0])
    return None


class ScholarConnector(HttpConnector):
    name = "scholar"
    crossref_url = "https://api.crossref.org/works"
    openalex_url = "https://api.openalex.org/works"
    semantic_scholar_url = "https://api.semanticscholar.org/graph/v1/paper/search/match"
    arxiv_url = "http://export.arxiv.org/api/query"
    dblp_url = "https://dblp.org/search/publ/api"

    def check(self, claims: Claims) -> list[EnrichmentSignal]:
        signals: list[EnrichmentSignal] = []
        for claim in claims.papers:
            try:
                signal = self._check_paper(claim, claims.application_name)
            except Exception:
                signal = None
            if signal is not None:
                signals.append(signal)
        return signals

    def _check_paper(self, claim: PaperClaim, application_name: str | None) -> EnrichmentSignal | None:
        if not _normalise(claim.title) or not any(_surname(author) for author in claim.authors):
            return None
        mismatch: tuple[str, _Candidate, float] | None = None
        sources: tuple[tuple[str, Callable[[PaperClaim], list[_Candidate]]], ...] = (
            ("crossref", self._crossref),
            ("openalex", self._openalex),
            ("semantic_scholar", self._semantic_scholar),
            ("arxiv", self._arxiv),
            ("dblp", self._dblp),
        )
        for source, load in sources:
            try:
                candidates = load(claim)
            except Exception:
                candidates = []
            accepted = self._accepted_candidate(claim, candidates)
            if accepted is not None:
                candidate, similarity = accepted
                return EnrichmentSignal(
                    code="PAPER_CORROBORATED",
                    polarity="positive",
                    severity="info",
                    confidence=similarity,
                    source=source,
                    detail="A scholarly source corroborates the supplied publication claim.",
                    evidence_url=candidate.evidence_url,
                )
            source_mismatch = self._author_mismatch(claim, application_name, candidates)
            if source_mismatch is not None and (mismatch is None or source_mismatch[1] > mismatch[2]):
                candidate, similarity = source_mismatch
                mismatch = (source, candidate, similarity)
        if mismatch is None:
            return None
        source, candidate, similarity = mismatch
        return EnrichmentSignal(
            code="PAPER_AUTHOR_MISMATCH",
            polarity="negative",
            severity="low",
            confidence=min(0.5, similarity / 2),
            source=source,
            detail="A strong title match lists authors inconsistent with the supplied publication claim.",
            evidence_url=candidate.evidence_url,
        )

    @staticmethod
    def _accepted_candidate(claim: PaperClaim, candidates: list[_Candidate]) -> tuple[_Candidate, float] | None:
        claimed_surnames = {_surname(author) for author in claim.authors} - {""}
        accepted: list[tuple[_Candidate, float]] = []
        for candidate in candidates:
            similarity = _title_similarity(claim.title, candidate.title)
            real_surnames = {_surname(author) for author in candidate.authors} - {""}
            year_matches = claim.year is None or candidate.year is not None and abs(claim.year - candidate.year) <= 1
            if similarity >= 0.9 and claimed_surnames & real_surnames and year_matches:
                accepted.append((candidate, similarity))
        return max(accepted, key=lambda item: item[1], default=None)

    @staticmethod
    def _author_mismatch(
        claim: PaperClaim,
        application_name: str | None,
        candidates: list[_Candidate],
    ) -> tuple[_Candidate, float] | None:
        claimed_surnames = {_surname(author) for author in claim.authors} - {""}
        application_surname = _surname(application_name or "")
        mismatches: list[tuple[_Candidate, float]] = []
        for candidate in candidates:
            similarity = _title_similarity(claim.title, candidate.title)
            real_surnames = {_surname(author) for author in candidate.authors} - {""}
            if similarity >= 0.95 and real_surnames and not claimed_surnames & real_surnames:
                if not application_surname or application_surname not in real_surnames:
                    mismatches.append((candidate, similarity))
        return max(mismatches, key=lambda item: item[1], default=None)

    def _crossref(self, claim: PaperClaim) -> list[_Candidate]:
        response = self._get(self.crossref_url, params={"query.bibliographic": claim.title, "rows": 5})
        if response is None or response.status_code != 200:
            return []
        payload = self._json(response)
        message = payload.get("message") if isinstance(payload, dict) else None
        items = message.get("items") if isinstance(message, dict) else None
        if not isinstance(items, list):
            return []
        candidates: list[_Candidate] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = _first_string(item.get("title"))
            if not title:
                continue
            authors = []
            for author in item.get("author", []):
                if isinstance(author, dict):
                    name = _first_string(author.get("family")) or _first_string(author.get("name"))
                    if name:
                        authors.append(name)
            year = next(
                (
                    parsed
                    for parsed in (
                        _date_parts_year(item.get("published-print")),
                        _date_parts_year(item.get("published-online")),
                        _date_parts_year(item.get("issued")),
                    )
                    if parsed is not None
                ),
                None,
            )
            doi = _first_string(item.get("DOI"))
            evidence_url = f"https://doi.org/{quote(doi, safe='/')}" if doi else _first_string(item.get("URL"))
            candidates.append(_Candidate(title, tuple(authors), year, _first_string(item.get("container-title")), evidence_url))
        return candidates

    def _openalex(self, claim: PaperClaim) -> list[_Candidate]:
        response = self._get(self.openalex_url, params={"search": claim.title, "per-page": 5})
        if response is None or response.status_code != 200:
            return []
        payload = self._json(response)
        items = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return []
        candidates: list[_Candidate] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = _first_string(item.get("display_name")) or _first_string(item.get("title"))
            if not title:
                continue
            authors = []
            for authorship in item.get("authorships", []):
                if not isinstance(authorship, dict):
                    continue
                author = authorship.get("author")
                name = _first_string(author.get("display_name")) if isinstance(author, dict) else None
                name = name or _first_string(authorship.get("raw_author_name"))
                if name:
                    authors.append(name)
            location = item.get("primary_location")
            source = location.get("source") if isinstance(location, dict) else None
            venue = _first_string(source.get("display_name")) if isinstance(source, dict) else None
            evidence_url = _first_string(item.get("doi")) or _first_string(item.get("id"))
            candidates.append(_Candidate(title, tuple(authors), _year(item.get("publication_year")), venue, evidence_url))
        return candidates

    def _semantic_scholar(self, claim: PaperClaim) -> list[_Candidate]:
        headers = {}
        api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY")
        if api_key:
            headers["x-api-key"] = api_key
        response = self._get(
            self.semantic_scholar_url,
            params={"query": claim.title, "fields": "title,authors,year,venue,url,externalIds"},
            headers=headers,
        )
        if response is None or response.status_code != 200:
            return []
        payload = self._json(response)
        items = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(payload, dict) and items is None and payload.get("title"):
            items = [payload]
        if not isinstance(items, list):
            return []
        candidates: list[_Candidate] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = _first_string(item.get("title"))
            if not title:
                continue
            authors = [
                name
                for author in item.get("authors", [])
                if isinstance(author, dict)
                for name in [_first_string(author.get("name"))]
                if name
            ]
            paper_id = _first_string(item.get("paperId"))
            evidence_url = _first_string(item.get("url"))
            if not evidence_url and paper_id:
                evidence_url = f"https://www.semanticscholar.org/paper/{quote(paper_id, safe='')}"
            candidates.append(
                _Candidate(title, tuple(authors), _year(item.get("year")), _first_string(item.get("venue")), evidence_url)
            )
        return candidates

    def _arxiv(self, claim: PaperClaim) -> list[_Candidate]:
        response = self._get(
            self.arxiv_url,
            params={"search_query": f'ti:"{claim.title}"', "start": 0, "max_results": 5},
        )
        if response is None or response.status_code != 200:
            return []
        try:
            root = ElementTree.fromstring(response.content)
        except (ElementTree.ParseError, TypeError, ValueError):
            return []
        namespace = {"atom": "http://www.w3.org/2005/Atom"}
        candidates: list[_Candidate] = []
        for entry in root.findall("atom:entry", namespace):
            title = entry.findtext("atom:title", default="", namespaces=namespace).strip()
            if not title:
                continue
            authors = tuple(
                name.text.strip()
                for name in entry.findall("atom:author/atom:name", namespace)
                if name.text and name.text.strip()
            )
            published = entry.findtext("atom:published", default="", namespaces=namespace)
            venue = entry.findtext("atom:journal_ref", default="", namespaces=namespace) or None
            evidence_url = entry.findtext("atom:id", default="", namespaces=namespace) or None
            candidates.append(_Candidate(title, authors, _year(published[:4]), venue, evidence_url))
        return candidates

    def _dblp(self, claim: PaperClaim) -> list[_Candidate]:
        response = self._get(self.dblp_url, params={"q": claim.title, "format": "json", "h": 5})
        if response is None or response.status_code != 200:
            return []
        payload = self._json(response)
        result = payload.get("result") if isinstance(payload, dict) else None
        hits = result.get("hits") if isinstance(result, dict) else None
        items = hits.get("hit") if isinstance(hits, dict) else None
        if not isinstance(items, list):
            return []
        candidates: list[_Candidate] = []
        for item in items:
            info = item.get("info") if isinstance(item, dict) else None
            if not isinstance(info, dict):
                continue
            title = _first_string(info.get("title"))
            if not title:
                continue
            raw_authors = info.get("authors")
            raw_authors = raw_authors.get("author") if isinstance(raw_authors, dict) else []
            if not isinstance(raw_authors, list):
                raw_authors = [raw_authors]
            authors = []
            for author in raw_authors:
                name = _first_string(author)
                if isinstance(author, dict):
                    name = _first_string(author.get("text")) or _first_string(author.get("name"))
                if name:
                    authors.append(name)
            evidence_url = _first_string(info.get("ee")) or _first_string(info.get("url"))
            candidates.append(
                _Candidate(title, tuple(authors), _year(info.get("year")), _first_string(info.get("venue")), evidence_url)
            )
        return candidates

