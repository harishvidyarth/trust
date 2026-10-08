from __future__ import annotations

import re

from firewall.enrichment.claims import DOI_RE, GITHUB_RE
from firewall.intel.models import ResumeFacts
from firewall.models import Candidate
from firewall.signals.identity_links import CERT_ID_RE, COLLEGE_RE, URL_RE, link_key


ORCID_RE = re.compile(r"\b\d{4}-\d{4}-\d{4}-\d{3}[\dX]\b")
CITY_RE = re.compile(r"(?:Location|City|Based in|Based at)\s*[:\-]?\s*(?P<city>[A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+)?)")


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def extract_facts(text: str, candidate: Candidate) -> ResumeFacts:
    source = text or ""
    links = [match.group(0).rstrip(".,;:") for match in URL_RE.finditer(source)]
    repos = [
        f"{match.group('username')}/{match.group('repo')}"
        for match in GITHUB_RE.finditer(source)
        if match.group("repo")
    ]
    facts = ResumeFacts(
        name=candidate.name,
        employers=_unique([item.company for item in candidate.experience]),
        colleges=_unique([" ".join(match.group(0).split()) for match in COLLEGE_RE.finditer(source)]),
        cities=_unique([match.group("city") for match in CITY_RE.finditer(source)]),
        links=_unique([link for link in links if link_key(link) is not None]),
        certs=_unique([match.group("id") for match in CERT_ID_RE.finditer(source)]),
        orcids=_unique(ORCID_RE.findall(source)),
        dois=_unique([match.group("doi").rstrip(".,;:)]}") for match in DOI_RE.finditer(source)]),
        repos=_unique(repos),
    )
    return ground_facts(facts, source)


def ground_facts(facts: ResumeFacts, text: str) -> ResumeFacts:
    haystack = " ".join((text or "").split()).casefold()

    def present(value: str) -> bool:
        return bool(value) and " ".join(value.split()).casefold() in haystack

    return ResumeFacts(
        name=facts.name if present(facts.name) else "",
        employers=[value for value in facts.employers if present(value)],
        colleges=[value for value in facts.colleges if present(value)],
        cities=[value for value in facts.cities if present(value)],
        links=[value for value in facts.links if present(value)],
        certs=[value for value in facts.certs if present(value)],
        orcids=[value for value in facts.orcids if present(value)],
        dois=[value for value in facts.dois if present(value)],
        repos=[value for value in facts.repos if present(value)],
    )
