from __future__ import annotations

import re
from urllib.parse import urlsplit

from firewall.enrichment.models import Claims, DoiClaim, PaperClaim, ProjectRepoClaim
from firewall.models import Candidate


GITHUB_RE = re.compile(
    r"(?:(?:https?://)?(?:www\.)?github\.com/)(?P<username>[A-Z0-9-]{1,39})(?:/(?P<repo>[A-Z0-9_.-]+))?",
    re.IGNORECASE,
)
DOI_RE = re.compile(
    r"(?:https?://(?:dx\.)?doi\.org/|\bdoi\s*:\s*)(?P<doi>10\.\d{4,9}/[-._;()/:A-Z0-9]+)",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
PORTFOLIO_RE = re.compile(
    r"\b(?:portfolio|personal\s+(?:site|website)|website)\s*[:\-]\s*(?P<url>https?://[^\s<>]+)",
    re.IGNORECASE,
)
EMPLOYER_DOMAIN_RE = re.compile(
    r"\b(?:employer|company)\s+(?:domain|website)\s*[:\-]\s*(?P<domain>(?:https?://)?[A-Z0-9.-]+\.[A-Z]{2,}(?:/[^\s<>]*)?)",
    re.IGNORECASE,
)
PAPER_RE = re.compile(r"^\s*(?:paper|publication|title)\s*[:\-]\s*(?P<title>.+?)\s*$", re.IGNORECASE)
AUTHORS_RE = re.compile(r"\bauthors?\s*[:\-]\s*(?P<authors>[^|]+)", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
DATE_RANGE_RE = re.compile(
    r"(?P<start>(?:19|20)\d{2}(?:-\d{2}(?:-\d{2})?)?)\s*(?:-|–|—|to)\s*(?P<end>(?:19|20)\d{2}(?:-\d{2}(?:-\d{2})?)?|present|current|now)",
    re.IGNORECASE,
)


def _clean_url(value: str) -> str:
    return value.rstrip(".,;:)]}")


def _line_title(line: str, match: re.Match[str]) -> str:
    prefix = line[: match.start()]
    prefix = re.sub(r"^\s*(?:paper|publication|title)\s*[:\-]\s*", "", prefix, flags=re.IGNORECASE)
    return prefix.strip(" \t|:;-–—")


def _paper_parts(line: str, fallback_name: str | None) -> tuple[str, list[str], int | None]:
    title_match = PAPER_RE.match(line)
    if title_match is None:
        return "", [], None
    title = re.split(r"\s*(?:\||\bdoi\s*:|\bauthors?\s*:|\byear\s*:|\bvenue\s*:)", title_match.group("title"), 1, flags=re.IGNORECASE)[0]
    title = title.strip(" \t|:;-–—")
    authors_match = AUTHORS_RE.search(line)
    authors = []
    if authors_match:
        authors = [item.strip() for item in re.split(r",|;|\band\b", authors_match.group("authors"), flags=re.IGNORECASE) if item.strip()]
    elif fallback_name and fallback_name in line:
        authors = [fallback_name]
    year_match = YEAR_RE.search(line)
    return title, authors, int(year_match.group(0)) if year_match else None


def extract_claims(text: str, candidate: Candidate) -> Claims:
    source = text or ""
    lines = [line.strip() for line in source.replace("\r", "\n").split("\n") if line.strip()]
    github_matches = list(GITHUB_RE.finditer(source))
    github_username = github_matches[0].group("username") if github_matches else None
    repos: list[ProjectRepoClaim] = []
    seen_repos: set[str] = set()
    for line in lines:
        for match in GITHUB_RE.finditer(line):
            username = match.group("username")
            repo = match.group("repo")
            if not repo or github_username is None or username.casefold() != github_username.casefold():
                continue
            key = repo.casefold()
            if key in seen_repos:
                continue
            date_match = DATE_RANGE_RE.search(line)
            repos.append(
                ProjectRepoClaim(
                    name=repo,
                    claimed_start=date_match.group("start") if date_match else None,
                    claimed_end=date_match.group("end") if date_match else None,
                )
            )
            seen_repos.add(key)

    papers: list[PaperClaim] = []
    paper_by_title: dict[str, PaperClaim] = {}
    for line in lines:
        title, authors, year = _paper_parts(line, candidate.name if candidate.name in line else None)
        if not title:
            continue
        key = title.casefold()
        if key not in paper_by_title:
            paper_by_title[key] = PaperClaim(title=title, authors=authors, year=year)
            papers.append(paper_by_title[key])

    dois: list[DoiClaim] = []
    seen_dois: set[str] = set()
    for index, line in enumerate(lines):
        for match in DOI_RE.finditer(line):
            doi = match.group("doi").rstrip(".,;:)]}")
            if doi.casefold() in seen_dois:
                continue
            title = _line_title(line, match)
            if not title and index > 0:
                prior = lines[index - 1]
                prior_title, _, _ = _paper_parts(prior, candidate.name if candidate.name in prior else None)
                title = prior_title
            if title:
                dois.append(DoiClaim(doi=doi, claimed_title=title))
                seen_dois.add(doi.casefold())

    portfolio_match = PORTFOLIO_RE.search(source)
    portfolio_url = _clean_url(portfolio_match.group("url")) if portfolio_match else None
    employer_domains: list[str] = []
    for match in EMPLOYER_DOMAIN_RE.finditer(source):
        raw = _clean_url(match.group("domain"))
        parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
        domain = parsed.hostname or ""
        if domain and domain not in employer_domains:
            employer_domains.append(domain)

    employers = [item.company for item in candidate.experience if item.company and item.company in source]
    name = candidate.name if candidate.name and candidate.name in source else None
    email = candidate.email if candidate.email and candidate.email in source else None
    return Claims(
        application_name=name,
        application_email=email,
        github_username=github_username,
        project_repos=repos,
        dois=dois,
        papers=papers,
        portfolio_url=portfolio_url,
        employers=list(dict.fromkeys(employers)),
        employer_domains=employer_domains,
    )
