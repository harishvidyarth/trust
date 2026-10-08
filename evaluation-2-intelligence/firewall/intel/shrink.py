from __future__ import annotations

from typing import Callable, Iterable, Iterator

from firewall.index_keys import normalize_name
from firewall.intel.models import Hit, ResumeFacts
from firewall.signals.identity_links import link_key, name_similarity


NAME_THRESHOLD = 0.9
MIN_AGREEING_TYPES = 2
STRONG_TYPES = frozenset({"employer", "college", "link", "cert", "orcid", "doi"})
MIN_TOKEN_CHARS = 4


def _contains(needle: str, hay: str) -> bool:
    left = normalize_name(needle)
    right = normalize_name(hay)
    if len(left) < MIN_TOKEN_CHARS or len(right) < MIN_TOKEN_CHARS:
        return left == right and bool(left)
    return f" {left} " in f" {right} " or f" {right} " in f" {left} "


def _first_match(values: Iterable[str], hay: Iterable[str]) -> str | None:
    pool = list(hay)
    for value in values:
        if any(_contains(value, item) for item in pool):
            return value
    return None


def agreeing_facts(hit: Hit, facts: ResumeFacts) -> list[str]:
    agreed: list[str] = []
    if facts.name and hit.name and name_similarity(facts.name, hit.name) >= NAME_THRESHOLD:
        agreed.append(f"name:{facts.name}")
    employer = _first_match(facts.employers, hit.org)
    if employer:
        agreed.append(f"employer:{employer}")
    college = _first_match(facts.colleges, hit.org)
    if college:
        agreed.append(f"college:{college}")
    city = _first_match(facts.cities, hit.location)
    if city:
        agreed.append(f"city:{city}")
    hit_keys = {key for key in (link_key(link) for link in hit.links) if key is not None}
    for link in facts.links:
        if link_key(link) in hit_keys:
            agreed.append(f"link:{link}")
            break
    if hit.orcid and any(hit.orcid.casefold() == value.casefold() for value in facts.orcids):
        agreed.append(f"orcid:{hit.orcid}")
    resume_dois = {value.casefold() for value in facts.dois}
    for doi in hit.dois:
        if doi.casefold() in resume_dois:
            agreed.append(f"doi:{doi}")
            break
    return agreed


def keep_decision(hit: Hit, facts: ResumeFacts) -> list[str] | None:
    agreed = agreeing_facts(hit, facts)
    kinds = {item.split(":", 1)[0] for item in agreed}
    if len(kinds) >= MIN_AGREEING_TYPES and kinds & STRONG_TYPES:
        return agreed
    return None


def shrink(
    hits: Iterable[Hit],
    facts: ResumeFacts,
    on_discard: Callable[[], None] | None = None,
) -> Iterator[tuple[Hit, list[str]]]:
    for hit in hits:
        agreed = keep_decision(hit, facts)
        if agreed is None:
            if on_discard is not None:
                on_discard()
            continue
        yield hit, agreed
