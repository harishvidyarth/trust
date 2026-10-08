from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RegistryRecord:
    found: bool
    name: str | None = None
    url: str | None = None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class PatentRecord:
    found: bool
    title: str | None = None
    inventors: tuple[str, ...] = ()
    url: str | None = None


class RoleRegistry(Protocol):
    def membership(self, body: str, number: str) -> RegistryRecord | None: ...

    def directorship(self, person: str, company: str) -> RegistryRecord | None: ...

    def regulator(self, registration_id: str) -> RegistryRecord | None: ...

    def patent(self, number: str) -> PatentRecord | None: ...

    def orcid(self, orcid_id: str) -> RegistryRecord | None: ...


class NullRegistry:
    def membership(self, body: str, number: str) -> RegistryRecord | None:
        return None

    def directorship(self, person: str, company: str) -> RegistryRecord | None:
        return None

    def regulator(self, registration_id: str) -> RegistryRecord | None:
        return None

    def patent(self, number: str) -> PatentRecord | None:
        return None

    def orcid(self, orcid_id: str) -> RegistryRecord | None:
        return None
