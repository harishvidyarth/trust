from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any, Mapping
from urllib.parse import urlencode

import httpx

from firewall.enrichment._http import USER_AGENT
from firewall.enrichment.roles import formats
from firewall.enrichment.roles.registry import NullRegistry, PatentRecord, RegistryRecord


TIMEOUT_SECONDS = 4.0
MAX_BYTES = 262_144
CACHE_TTL_SECONDS = 3600.0
CACHE_MAX_ENTRIES = 512
ALLOWED_HOSTS = frozenset({"pub.orcid.org", "api.uspto.gov"})
ORCID_BASE = "https://pub.orcid.org/v3.0/"
USPTO_SEARCH = "https://api.uspto.gov/api/v1/patent/applications/search"
USPTO_KEY_ENV = "FIREWALL_USPTO_ODP_API_KEY"
US_GRANTED_RE = re.compile(r"US(?P<digits>\d{7,8})(?:B\d)?")
MAX_ALIASES = 10


class SafeFetcher:
    def __init__(self, transport: Any | None = None, ttl: float = CACHE_TTL_SECONDS, clock: Any = time.monotonic) -> None:
        if isinstance(transport, httpx.Client):
            self._client = transport
        elif transport is not None:
            self._client = httpx.Client(transport=transport, follow_redirects=False)
        else:
            self._client = httpx.Client(follow_redirects=False)
        self._ttl = ttl
        self._clock = clock
        self._cache: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def cached(self, key: str) -> Any | None:
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if entry[0] < self._clock():
                del self._cache[key]
                return None
            return entry[1]

    def remember(self, key: str, value: Any) -> None:
        with self._lock:
            if len(self._cache) >= CACHE_MAX_ENTRIES:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = (self._clock() + self._ttl, value)

    def get_json(self, url: str, headers: Mapping[str, str] | None = None) -> tuple[int, Any] | None:
        parsed = httpx.URL(url)
        if parsed.scheme != "https" or parsed.host not in ALLOWED_HOSTS:
            return None
        merged = {"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}
        try:
            with self._client.stream("GET", url, headers=merged, timeout=TIMEOUT_SECONDS, follow_redirects=False) as response:
                if response.status_code in (404, 410):
                    return response.status_code, None
                if response.status_code != 200:
                    return None
                declared = response.headers.get("content-length")
                if declared is not None and declared.isdigit() and int(declared) > MAX_BYTES:
                    return None
                if "json" not in response.headers.get("content-type", "").lower():
                    return None
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        return None
        except (httpx.HTTPError, OSError, ValueError):
            return None
        try:
            return 200, json.loads(bytes(body))
        except (ValueError, UnicodeDecodeError):
            return None


def _value(node: Any) -> str | None:
    if isinstance(node, dict) and isinstance(node.get("value"), str) and node["value"].strip():
        return node["value"].strip()
    return None


class OrcidRegistry:
    def __init__(self, fetcher: SafeFetcher) -> None:
        self._fetcher = fetcher

    def orcid(self, orcid_id: str) -> RegistryRecord | None:
        token = orcid_id.strip().upper()
        if not formats.valid_orcid(token):
            return None
        key = "orcid:" + token
        hit = self._fetcher.cached(key)
        if hit is not None:
            return hit
        result = self._fetcher.get_json(ORCID_BASE + token + "/person")
        if result is None:
            return None
        status, payload = result
        url = "https://orcid.org/" + token
        if status in (404, 410):
            record = RegistryRecord(found=False, url=url)
        else:
            record = self._parse(payload, url)
        if record is not None:
            self._fetcher.remember(key, record)
        return record

    @staticmethod
    def _parse(payload: Any, url: str) -> RegistryRecord | None:
        if not isinstance(payload, dict):
            return None
        name = payload.get("name")
        if name is not None and not isinstance(name, dict):
            return None
        given = _value(name.get("given-names")) if name else None
        family = _value(name.get("family-name")) if name else None
        credit = _value(name.get("credit-name")) if name else None
        full = " ".join(part for part in (given, family) if part) or credit
        aliases: list[str] = []
        if credit and credit != full:
            aliases.append(credit)
        other = payload.get("other-names")
        entries = other.get("other-name") if isinstance(other, dict) else None
        if isinstance(entries, list):
            for entry in entries[:MAX_ALIASES]:
                if isinstance(entry, dict) and isinstance(entry.get("content"), str) and entry["content"].strip():
                    aliases.append(entry["content"].strip())
        return RegistryRecord(found=True, name=full, url=url, aliases=tuple(aliases[:MAX_ALIASES]))


class UsptoOdpRegistry:
    def __init__(self, fetcher: SafeFetcher, api_key: str) -> None:
        self._fetcher = fetcher
        self._api_key = api_key

    def patent(self, number: str) -> PatentRecord | None:
        match = US_GRANTED_RE.fullmatch(number.strip().upper())
        if match is None or not self._api_key:
            return None
        digits = match.group("digits")
        key = "uspto:" + digits
        hit = self._fetcher.cached(key)
        if hit is not None:
            return hit
        query = urlencode({"q": f"applicationMetaData.patentNumber:{digits}", "limit": "1"})
        result = self._fetcher.get_json(USPTO_SEARCH + "?" + query, headers={"X-API-KEY": self._api_key})
        if result is None or result[0] != 200:
            return None
        record = self._parse(result[1], digits)
        if record is not None:
            self._fetcher.remember(key, record)
        return record

    @staticmethod
    def _parse(payload: Any, digits: str) -> PatentRecord | None:
        if not isinstance(payload, dict):
            return None
        bag = payload.get("patentFileWrapperDataBag")
        if not isinstance(bag, list):
            return None
        for entry in bag:
            meta = entry.get("applicationMetaData") if isinstance(entry, dict) else None
            if not isinstance(meta, dict):
                continue
            returned = str(meta.get("patentNumber") or "").replace(",", "").strip()
            if returned != digits:
                continue
            inventors: list[str] = []
            people = meta.get("inventorBag")
            if isinstance(people, list):
                for person in people[:50]:
                    if not isinstance(person, dict):
                        continue
                    text = person.get("inventorNameText")
                    if not isinstance(text, str) or not text.strip():
                        text = " ".join(str(person.get(part)) for part in ("firstName", "lastName") if isinstance(person.get(part), str))
                    if text.strip():
                        inventors.append(text.strip())
            title = meta.get("inventionTitle")
            return PatentRecord(
                found=True,
                title=title if isinstance(title, str) else None,
                inventors=tuple(inventors),
                url=f"https://patents.google.com/patent/US{digits}",
            )
        return None


class LiveRegistry(NullRegistry):
    def __init__(self, transport: Any | None = None, env: Mapping[str, str] | None = None) -> None:
        self._fetcher = SafeFetcher(transport)
        source = os.environ if env is None else env
        key = (source.get(USPTO_KEY_ENV) or "").strip()
        self._orcid = OrcidRegistry(self._fetcher)
        self._uspto = UsptoOdpRegistry(self._fetcher, key) if key else None

    def orcid(self, orcid_id: str) -> RegistryRecord | None:
        return self._orcid.orcid(orcid_id)

    def patent(self, number: str) -> PatentRecord | None:
        return self._uspto.patent(number) if self._uspto is not None else None
