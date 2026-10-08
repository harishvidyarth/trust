
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal

import httpx
import uvicorn
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator


FraudClass = Literal["bot", "duplicate", "farm", "fabricated"]
HEX_64 = r"^[0-9a-f]{64}$"


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def public_key_text(key: Ed25519PublicKey) -> str:
    raw = key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def parse_public_key(value: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(value, validate=True))


def generate_private_key(path: str | Path) -> Ed25519PrivateKey:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        loaded = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(loaded, Ed25519PrivateKey):
            raise ValueError(f"{path} is not an Ed25519 private key")
        return loaded
    key = Ed25519PrivateKey.generate()
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    path.chmod(0o600)
    return key


class SignedReport(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    report_id: str = Field(pattern=HEX_64)
    fingerprints: dict[str, str]
    classification: FraudClass = Field(alias="class")
    confidence: float = Field(ge=0, le=1)
    ttl: int = Field(ge=1, le=2_592_000)
    node_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    timestamp: float = Field(gt=0)
    signature: str

    @field_validator("fingerprints")
    @classmethod
    def validate_fingerprints(cls, value: dict[str, str]) -> dict[str, str]:
        if not value or len(value) > 16:
            raise ValueError("one to sixteen fingerprints are required")
        for kind, digest in value.items():
            if not kind or len(kind) > 64 or not digest or len(digest) != 64:
                raise ValueError("invalid fingerprint")
            try:
                bytes.fromhex(digest)
            except ValueError as exc:
                raise ValueError("fingerprints must be hex digests") from exc
        return value

    def unsigned(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, exclude={"signature"})

    def id_source(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, exclude={"report_id", "signature"})


class SignedRevocation(BaseModel):
    report_id: str = Field(pattern=HEX_64)
    node_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    timestamp: float = Field(gt=0)
    signature: str

    def unsigned(self) -> dict[str, object]:
        return self.model_dump(exclude={"signature"})


class FingerprintRequest(BaseModel):
    fingerprints: dict[str, str]

    @field_validator("fingerprints")
    @classmethod
    def validate_fingerprints(cls, value: dict[str, str]) -> dict[str, str]:
        SignedReport.validate_fingerprints(value)
        return value


@dataclass(slots=True)
class NodeConfig:
    node_id: str
    peers: list[str]
    secret: str
    key_file: Path
    peers_file: Path | None
    confirmation_k: int = 2
    score_threshold: float = 1.5
    public_keys: dict[str, str] = field(default_factory=dict)
    clock_skew_seconds: float = 30.0

    @classmethod
    def from_env(cls, require_secret: bool = True) -> "NodeConfig":
        node_id = os.getenv("NODE_ID", "local-node")
        secret = os.getenv("FED_SECRET")
        if not secret:
            if os.getenv("FED_ALLOW_INSECURE_DEV") == "1":
                secret = "development-consortium-secret"
            elif require_secret:
                raise RuntimeError("FED_SECRET is required; set FED_ALLOW_INSECURE_DEV=1 only for local demos")
            else:
                secret = ""
        peer_text = os.getenv("PEERS", "")
        return cls(
            node_id=node_id,
            peers=[value.strip().rstrip("/") for value in peer_text.split(",") if value.strip()],
            secret=secret,
            key_file=Path(os.getenv("FED_KEY_FILE", f"federation/.state/{node_id}.pem")),
            peers_file=Path(os.environ["FED_PEERS_FILE"]) if os.getenv("FED_PEERS_FILE") else None,
            confirmation_k=int(os.getenv("FED_CONFIRMATION_K", "2")),
            score_threshold=float(os.getenv("FED_SCORE_THRESHOLD", "1.5")),
        )


def _peer_records(path: Path | None) -> tuple[dict[str, str], list[str]]:
    if path is None:
        return {}, []
    document = json.loads(path.read_text(encoding="utf-8"))
    records = document.get("nodes", document)
    keys: dict[str, str] = {}
    urls: list[str] = []
    for node_id, record in records.items():
        if isinstance(record, str):
            keys[node_id] = record
        else:
            keys[node_id] = record["public_key"]
            if record.get("url"):
                urls.append(record["url"].rstrip("/"))
    return keys, urls


class ProtocolError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class NodeState:
    def __init__(self, config: NodeConfig) -> None:
        if config.confirmation_k < 1:
            raise ValueError("confirmation_k must be positive")
        if config.score_threshold <= 0:
            raise ValueError("score_threshold must be positive")
        self.config = config
        self.private_key = generate_private_key(config.key_file)
        file_keys, file_urls = _peer_records(config.peers_file)
        all_key_text = {**file_keys, **config.public_keys}
        all_key_text[config.node_id] = public_key_text(self.private_key.public_key())
        self.public_keys = {node_id: parse_public_key(value) for node_id, value in all_key_text.items()}
        self.peers = list(dict.fromkeys([*config.peers, *file_urls]))
        self.reports: dict[str, SignedReport] = {}
        self.received_at: dict[str, float] = {}
        self.revocations: set[str] = set()
        self.seen_revocations: set[tuple[str, str, float]] = set()
        self.lock = asyncio.Lock()
        self.metrics = {
            "reports_accepted": 0,
            "replays_rejected": 0,
            "revocations_accepted": 0,
            "gossip_attempts": 0,
            "gossip_failures": 0,
        }

    def _verify(self, node_id: str, unsigned: dict[str, object], signature: str) -> None:
        key = self.public_keys.get(node_id)
        if key is None:
            raise ProtocolError(status.HTTP_403_FORBIDDEN, "unknown federation node")
        try:
            key.verify(base64.b64decode(signature, validate=True), canonical_json(unsigned))
        except (InvalidSignature, ValueError) as exc:
            raise ProtocolError(status.HTTP_401_UNAUTHORIZED, "invalid signature") from exc

    def _validate_time(self, timestamp: float, ttl: int | None = None) -> None:
        now = time.time()
        if timestamp > now + self.config.clock_skew_seconds:
            raise ProtocolError(status.HTTP_400_BAD_REQUEST, "timestamp too far in the future")
        if ttl is not None and timestamp + ttl <= now:
            raise ProtocolError(status.HTTP_410_GONE, "report expired")

    async def accept_report(self, report: SignedReport) -> None:
        expected_id = hashlib.sha256(canonical_json(report.id_source())).hexdigest()
        if expected_id != report.report_id:
            raise ProtocolError(status.HTTP_401_UNAUTHORIZED, "report id does not match payload")
        self._verify(report.node_id, report.unsigned(), report.signature)
        self._validate_time(report.timestamp, report.ttl)
        async with self.lock:
            if report.report_id in self.reports:
                self.metrics["replays_rejected"] += 1
                raise ProtocolError(status.HTTP_409_CONFLICT, "report already seen")
            self.reports[report.report_id] = report
            self.received_at[report.report_id] = time.time()
            self.metrics["reports_accepted"] += 1

    async def accept_revocation(self, revocation: SignedRevocation) -> None:
        self._verify(revocation.node_id, revocation.unsigned(), revocation.signature)
        self._validate_time(revocation.timestamp)
        replay_key = (revocation.report_id, revocation.node_id, revocation.timestamp)
        async with self.lock:
            if replay_key in self.seen_revocations:
                raise ProtocolError(status.HTTP_409_CONFLICT, "revocation already seen")
            report = self.reports.get(revocation.report_id)
            if report is None:
                raise ProtocolError(status.HTTP_404_NOT_FOUND, "report not found")
            if report.node_id != revocation.node_id:
                raise ProtocolError(status.HTTP_403_FORBIDDEN, "only the origin node may revoke")
            self.seen_revocations.add(replay_key)
            self.revocations.add(revocation.report_id)
            self.metrics["revocations_accepted"] += 1

    def active_reports(self) -> list[SignedReport]:
        now = time.time()
        return [
            report
            for report_id, report in self.reports.items()
            if report_id not in self.revocations and report.timestamp + report.ttl > now
        ]

    def check(self, fingerprints: dict[str, str]) -> list[dict[str, object]]:
        query_pairs = set(fingerprints.items())
        matching: dict[str, tuple[SignedReport, set[str]]] = {}
        for report in self.active_reports():
            kinds = {kind for kind, digest in report.fingerprints.items() if (kind, digest) in query_pairs}
            if kinds - {"ip24"}:
                matching[report.report_id] = (report, kinds)
        if not matching:
            return []

        confidence_by_node: dict[str, float] = {}
        matched_kinds: set[str] = set()
        classes: set[str] = set()
        for report, kinds in matching.values():
            confidence_by_node[report.node_id] = max(
                confidence_by_node.get(report.node_id, 0.0), report.confidence
            )
            matched_kinds.update(kinds)
            classes.add(report.classification)
        independent_nodes = len(confidence_by_node)
        score = sum(confidence_by_node.values())
        strong_kinds = matched_kinds - {"ip24"}
        confirmed = (
            independent_nodes >= self.config.confirmation_k
            or score >= self.config.score_threshold
        ) and (len(strong_kinds) >= 2 or bool(strong_kinds & {"email", "phone"}))
        code = "FED_CONFIRMED" if confirmed else "FED_ADVISORY"
        qualifier = "confirmed" if confirmed else "advisory"
        return [
            {
                "code": code,
                "severity": "high" if confirmed else "low",
                "detail": (
                    f"Federated {qualifier} match from {independent_nodes} independent "
                    f"node(s); classes: {', '.join(sorted(classes))}."
                ),
                "weight_hint": 80 if confirmed else 20,
                "matches": sorted(matched_kinds),
                "independent_nodes": independent_nodes,
                "confidence_score": round(score, 4),
            }
        ]

    async def _post_to_peers(self, path: str, payload: dict[str, object]) -> None:
        async with httpx.AsyncClient(timeout=2.0) as client:
            for peer in self.peers:
                self.metrics["gossip_attempts"] += 1
                try:
                    response = await client.post(f"{peer}{path}", json=payload)
                    if response.status_code not in (202, 409):
                        self.metrics["gossip_failures"] += 1
                except httpx.HTTPError:
                    self.metrics["gossip_failures"] += 1

    async def gossip_report(self, report: SignedReport) -> None:
        await self._post_to_peers("/fed/report", report.model_dump(by_alias=True))

    async def gossip_revocation(self, revocation: SignedRevocation) -> None:
        await self._post_to_peers("/fed/revoke", revocation.model_dump())


def create_app(config: NodeConfig | None = None) -> FastAPI:
    state_obj = NodeState(config or NodeConfig.from_env())
    app = FastAPI(title="Application Firewall Federation Node", version="1.0.0")
    app.state.federation = state_obj

    @app.post("/fed/report", status_code=status.HTTP_202_ACCEPTED)
    async def report(payload: SignedReport, background: BackgroundTasks) -> dict[str, object]:
        try:
            await state_obj.accept_report(payload)
        except ProtocolError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc
        background.add_task(state_obj.gossip_report, payload)
        return {"accepted": True, "report_id": payload.report_id}

    @app.post("/fed/revoke", status_code=status.HTTP_202_ACCEPTED)
    async def revoke(payload: SignedRevocation, background: BackgroundTasks) -> dict[str, object]:
        try:
            await state_obj.accept_revocation(payload)
        except ProtocolError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc
        background.add_task(state_obj.gossip_revocation, payload)
        return {"accepted": True, "report_id": payload.report_id, "revoked": True}

    @app.get("/fed/blocklist")
    async def blocklist(since: Annotated[float, Query(ge=0)] = 0.0) -> dict[str, object]:
        reports = [
            report.model_dump(by_alias=True)
            for report in state_obj.active_reports()
            if state_obj.received_at[report.report_id] >= since
        ]
        return {"generated_at": time.time(), "reports": reports}

    @app.get("/fed/status")
    async def fed_status() -> dict[str, object]:
        return {
            "node_id": state_obj.config.node_id,
            "known_nodes": sorted(state_obj.public_keys),
            "peers": state_obj.peers,
            "reports_total": len(state_obj.reports),
            "reports_active": len(state_obj.active_reports()),
            "revocations": len(state_obj.revocations),
            "confirmation_k": state_obj.config.confirmation_k,
            "score_threshold": state_obj.config.score_threshold,
            "metrics": dict(state_obj.metrics),
        }

    @app.post("/fed/check")
    async def check(payload: FingerprintRequest) -> dict[str, object]:
        return {"hits": state_obj.check(payload.fingerprints), "checked_at": time.time()}

    return app


def _arguments() -> argparse.Namespace:
    defaults = NodeConfig.from_env(require_secret=False)
    parser = argparse.ArgumentParser(description="Run a privacy-preserving federation node")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--node-id", default=defaults.node_id)
    parser.add_argument("--peers", default=",".join(defaults.peers))
    parser.add_argument("--secret", default=defaults.secret)
    parser.add_argument("--key-file", type=Path, default=defaults.key_file)
    parser.add_argument("--peers-file", type=Path, default=defaults.peers_file)
    parser.add_argument("--k", type=int, default=defaults.confirmation_k)
    parser.add_argument("--score-threshold", type=float, default=defaults.score_threshold)
    return parser.parse_args()


def main() -> None:
    args = _arguments()
    if not args.secret:
        raise SystemExit("a federation secret is required: pass --secret or set FED_SECRET")
    config = NodeConfig(
        node_id=args.node_id,
        peers=[value.strip().rstrip("/") for value in args.peers.split(",") if value.strip()],
        secret=args.secret,
        key_file=args.key_file,
        peers_file=args.peers_file,
        confirmation_k=args.k,
        score_threshold=args.score_threshold,
    )
    uvicorn.run(create_app(config), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
