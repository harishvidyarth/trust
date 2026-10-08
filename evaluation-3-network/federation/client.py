
from __future__ import annotations

import base64
import hashlib
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from firewall.models import Application

from .fingerprints import to_fingerprints
from .node import SignedReport, SignedRevocation, canonical_json, generate_private_key


@dataclass
class BloomFilter:
    size: int
    hashes: int
    bits: bytearray

    @classmethod
    def create(cls, capacity: int = 10_000, false_positive_rate: float = 0.01) -> "BloomFilter":
        if capacity < 1 or not 0 < false_positive_rate < 1:
            raise ValueError("invalid Bloom filter parameters")
        size = max(8, math.ceil(-capacity * math.log(false_positive_rate) / math.log(2) ** 2))
        hashes = max(1, round(size / capacity * math.log(2)))
        return cls(size=size, hashes=hashes, bits=bytearray(math.ceil(size / 8)))

    def _positions(self, value: str) -> Iterable[int]:
        digest = hashlib.sha256(value.encode("ascii")).digest()
        first = int.from_bytes(digest[:16], "big")
        second = int.from_bytes(digest[16:], "big") or 1
        for index in range(self.hashes):
            yield (first + index * second) % self.size

    def add(self, value: str) -> None:
        for position in self._positions(value):
            self.bits[position // 8] |= 1 << (position % 8)

    def __contains__(self, value: str) -> bool:
        return all(self.bits[position // 8] & (1 << (position % 8)) for position in self._positions(value))

    def to_dict(self) -> dict[str, object]:
        return {
            "size": self.size,
            "hashes": self.hashes,
            "bits": base64.b64encode(bytes(self.bits)).decode("ascii"),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "BloomFilter":
        size = int(value["size"])
        hashes = int(value["hashes"])
        bits = bytearray(base64.b64decode(str(value["bits"]), validate=True))
        if size < 8 or hashes < 1 or len(bits) != math.ceil(size / 8):
            raise ValueError("invalid Bloom filter payload")
        return cls(size=size, hashes=hashes, bits=bits)


class NodeClient:
    def __init__(
        self,
        base_url: str,
        *,
        secret: str,
        node_id: str | None = None,
        private_key: Ed25519PrivateKey | None = None,
        private_key_path: str | Path | None = None,
        timeout: float = 3.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.secret = secret
        self.node_id = node_id
        self.timeout = timeout
        if private_key is not None and private_key_path is not None:
            raise ValueError("provide a private key or path, not both")
        self.private_key = private_key or (
            generate_private_key(private_key_path) if private_key_path is not None else None
        )

    def _sign(self, payload: dict[str, object]) -> str:
        if self.private_key is None or not self.node_id:
            raise ValueError("reporting requires node_id and an Ed25519 private key")
        return base64.b64encode(self.private_key.sign(canonical_json(payload))).decode("ascii")

    def build_report(
        self,
        fingerprints: dict[str, str],
        classification: str,
        confidence: float,
        ttl: int,
        *,
        timestamp: float | None = None,
    ) -> SignedReport:
        if not self.node_id:
            raise ValueError("node_id is required")
        base: dict[str, object] = {
            "fingerprints": fingerprints,
            "class": classification,
            "confidence": confidence,
            "ttl": ttl,
            "node_id": self.node_id,
            "timestamp": time.time() if timestamp is None else timestamp,
        }
        report_id = hashlib.sha256(canonical_json(base)).hexdigest()
        unsigned = {"report_id": report_id, **base}
        return SignedReport.model_validate({**unsigned, "signature": self._sign(unsigned)})

    def build_revocation(self, report_id: str, *, timestamp: float | None = None) -> SignedRevocation:
        if not self.node_id:
            raise ValueError("node_id is required")
        unsigned: dict[str, object] = {
            "report_id": report_id,
            "node_id": self.node_id,
            "timestamp": time.time() if timestamp is None else timestamp,
        }
        return SignedRevocation.model_validate({**unsigned, "signature": self._sign(unsigned)})

    async def _post(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}{path}", json=payload)
        response.raise_for_status()
        return response.json()

    async def check(self, fingerprints: dict[str, str]) -> dict[str, object]:
        return await self._post("/fed/check", {"fingerprints": fingerprints})

    async def report(
        self, fingerprints: dict[str, str], classification: str, confidence: float, ttl: int = 3600
    ) -> dict[str, object]:
        payload = self.build_report(fingerprints, classification, confidence, ttl)
        return await self._post("/fed/report", payload.model_dump(by_alias=True))

    async def report_application(
        self, application: Application, classification: str, confidence: float, ttl: int = 3600
    ) -> dict[str, object]:
        return await self.report(to_fingerprints(application, self.secret), classification, confidence, ttl)

    async def revoke(self, report_id: str) -> dict[str, object]:
        payload = self.build_revocation(report_id)
        return await self._post("/fed/revoke", payload.model_dump())

    @staticmethod
    def export_bloom(values: Iterable[str], capacity: int = 10_000) -> BloomFilter:
        bloom = BloomFilter.create(capacity=capacity)
        for value in values:
            bloom.add(value)
        return bloom

    @staticmethod
    def import_bloom(payload: dict[str, object]) -> BloomFilter:
        return BloomFilter.from_dict(payload)
