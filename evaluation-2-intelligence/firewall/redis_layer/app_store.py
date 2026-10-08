from __future__ import annotations

import json
from typing import Any

import redis

from firewall.index_keys import (
    candidate_identity_key,
    normalize_email,
    normalize_name,
    normalize_phone,
    resume_bucket_keys,
    resume_content_hash,
    template_hash,
    template_text,
)
from firewall.models import Application, Decision
from firewall.redis_layer.client import KEY_PREFIX, RedisLink
from firewall.signals.identity_links import link_index_keys, name_index_keys, signature_index_keys
from firewall.store import ApplicationStore, InMemoryApplicationStore


STORE_PREFIX = f"{KEY_PREFIX}store:"
DEFAULT_WINDOW_TTL_S = 604_800
_SEP = "\x1f"
_RETRIES = 8


def _encode_application(application: Application) -> str:
    return json.dumps(
        {
            "a": application.model_dump(mode="json"),
            "q": bool(application.candidate._qualification_only_experience),
        },
        separators=(",", ":"),
    )


def _decode_application(raw: str | None) -> Application | None:
    if raw is None:
        return None
    try:
        envelope = json.loads(raw)
        application = Application.model_validate(envelope["a"])
        application.candidate._qualification_only_experience = bool(envelope.get("q", False))
        return application
    except (ValueError, KeyError, TypeError):
        return None


def _decode_decision(raw: str | None) -> Decision | None:
    if raw is None:
        return None
    try:
        return Decision.model_validate_json(raw)
    except ValueError:
        return None


class RedisApplicationStore(ApplicationStore):
    def __init__(
        self,
        link: RedisLink,
        *,
        fallback: ApplicationStore | None = None,
        window_ttl_s: int | None = DEFAULT_WINDOW_TTL_S,
    ) -> None:
        self._link = link
        self._fallback = fallback or InMemoryApplicationStore()
        self._window_ttl = window_ttl_s

    @staticmethod
    def _k(name: str) -> str:
        return f"{STORE_PREFIX}{name}"

    def _load(self, client: Any, identifiers: set[str] | list[str]) -> tuple[Application, ...]:
        if not identifiers:
            return ()
        ids = list(identifiers)
        pipe = client.pipeline(transaction=False)
        pipe.zmscore(self._k("order"), ids)
        pipe.mget([self._k(f"app:{item}") for item in ids])
        scores, raws = pipe.execute()
        rows = []
        for identifier, score, raw in zip(ids, scores, raws):
            application = _decode_application(raw)
            if score is not None and application is not None:
                rows.append((score, identifier, application))
        rows.sort(key=lambda row: (row[0], row[1]))
        return tuple(row[2] for row in rows)

    def applications(self) -> tuple[Application, ...]:
        def operation(client):
            ids = client.zrange(self._k("order"), 0, -1)
            return self._load(client, ids)

        return self._link.call(operation, self._fallback.applications)

    def _members(self, key: str):
        def operation(client):
            return self._load(client, client.smembers(self._k(key)))

        return operation

    def by_email(self, email: str) -> tuple[Application, ...]:
        return self._link.call(self._members(f"em:{email}"), lambda: self._fallback.by_email(email))

    def by_phone(self, phone: str) -> tuple[Application, ...]:
        return self._link.call(self._members(f"ph:{phone}"), lambda: self._fallback.by_phone(phone))

    def by_job(self, job_id: str) -> tuple[Application, ...]:
        return self._link.call(self._members(f"job:{job_id}"), lambda: self._fallback.by_job(job_id))

    def by_link(self, key: str) -> tuple[Application, ...]:
        return self._link.call(self._members(f"lk:{key}"), lambda: self._fallback.by_link(key))

    def by_name_token(self, token: str) -> tuple[Application, ...]:
        return self._link.call(self._members(f"nt:{token}"), lambda: self._fallback.by_name_token(token))

    def by_signature(self, key: str) -> tuple[Application, ...]:
        return self._link.call(self._members(f"sg:{key}"), lambda: self._fallback.by_signature(key))

    def _window(self, key: str, start: float, end: float):
        def operation(client):
            return self._load(client, client.zrangebyscore(self._k(key), start, end))

        return operation

    def recent(self, start: float, end: float) -> tuple[Application, ...]:
        return self._link.call(self._window("tm", start, end), lambda: self._fallback.recent(start, end))

    def recent_by_device(self, device_id: str, start: float, end: float) -> tuple[Application, ...]:
        return self._link.call(
            self._window(f"tdev:{device_id}", start, end),
            lambda: self._fallback.recent_by_device(device_id, start, end),
        )

    def recent_by_ip(self, ip: str, start: float, end: float) -> tuple[Application, ...]:
        return self._link.call(
            self._window(f"tip:{ip}", start, end),
            lambda: self._fallback.recent_by_ip(ip, start, end),
        )

    def recent_by_identity(self, email: str, phone: str, start: float, end: float) -> tuple[Application, ...]:
        def operation(client):
            identifiers: set[str] = set()
            if email:
                identifiers.update(client.zrangebyscore(self._k(f"tem:{email}"), start, end))
            if phone:
                identifiers.update(client.zrangebyscore(self._k(f"tph:{phone}"), start, end))
            return self._load(client, identifiers)

        return self._link.call(operation, lambda: self._fallback.recent_by_identity(email, phone, start, end))

    def resume_candidates(self, application: Application, include_all: bool = False) -> tuple[Application, ...]:
        if include_all:
            return self.applications()

        def operation(client):
            buckets = resume_bucket_keys(application)
            if not buckets:
                return ()
            pipe = client.pipeline(transaction=False)
            for bucket in buckets:
                pipe.smembers(self._k(f"rbk:{bucket}"))
            pairs = [
                (bucket, content)
                for bucket, contents in zip(buckets, pipe.execute())
                for content in contents
            ]
            if not pairs:
                return ()
            pipe = client.pipeline(transaction=False)
            for bucket, content in pairs:
                pipe.smembers(self._k(f"rb:{bucket}:{content}"))
            chosen = {min(ids) for ids in pipe.execute() if ids}
            return self._load(client, chosen)

        return self._link.call(operation, lambda: self._fallback.resume_candidates(application, include_all))

    def has_exact_resume_from_other_identity(self, application: Application) -> bool:
        content_hash = resume_content_hash(application)
        if not content_hash:
            return False
        name = normalize_name(application.candidate.name)
        email = normalize_email(application.candidate.email)
        phone = normalize_phone(application.candidate.phone)

        def operation(client):
            pipe = client.pipeline(transaction=False)
            pipe.hget(self._k("xc"), content_hash)
            pipe.hget(self._k("xne"), _SEP.join((content_hash, name, email)))
            pipe.hget(self._k("xnp"), _SEP.join((content_hash, name, phone)))
            pipe.hget(self._k("xid"), _SEP.join((content_hash, name, email, phone)))
            total, name_email, name_phone, identity = (int(item or 0) for item in pipe.execute())
            same = 0
            if email:
                same += name_email
            if phone:
                same += name_phone
            if email and phone:
                same -= identity
            return total > same

        return self._link.call(operation, lambda: self._fallback.has_exact_resume_from_other_identity(application))

    def template_identity_count(self, normalized_text: str, current_identity: str) -> int:
        def operation(client):
            counts = client.hgetall(self._k(f"tp:{template_hash(normalized_text)}"))
            identities = {name for name, value in counts.items() if int(value) > 0}
            return len(identities) + (current_identity not in identities)

        return self._link.call(
            operation,
            lambda: self._fallback.template_identity_count(normalized_text, current_identity),
        )

    def _index_commands(self, pipe: Any, application: Application, sign: int) -> None:
        application_id = application.application_id
        email = normalize_email(application.candidate.email)
        phone = normalize_phone(application.candidate.phone)
        stamp = application.signals.submitted_at
        timed = [
            ("tm", ""),
            ("tdev", application.signals.device_id),
            ("tip", application.signals.ip),
        ]
        sets = [("job", application.job_id)]
        sets.extend(("lk", key) for key in sorted(link_index_keys(application)))
        sets.extend(("nt", key) for key in sorted(name_index_keys(application.candidate.name)))
        sets.extend(("sg", key) for key in sorted(signature_index_keys(application)))
        if email:
            sets.append(("em", email))
            timed.append(("tem", email))
        if phone:
            sets.append(("ph", phone))
            timed.append(("tph", phone))
        for prefix, value in sets:
            key = self._k(f"{prefix}:{value}")
            if sign > 0:
                pipe.sadd(key, application_id)
            else:
                pipe.srem(key, application_id)
        for prefix, value in timed:
            key = self._k(prefix if prefix == "tm" else f"{prefix}:{value}")
            if sign > 0:
                pipe.zadd(key, {application_id: stamp})
                if self._window_ttl:
                    pipe.expire(key, int(self._window_ttl))
            else:
                pipe.zrem(key, application_id)
        content_hash = resume_content_hash(application)
        name = normalize_name(application.candidate.name)
        for bucket in resume_bucket_keys(application):
            members = self._k(f"rb:{bucket}:{content_hash}")
            index = self._k(f"rbk:{bucket}")
            if sign > 0:
                pipe.sadd(members, application_id)
                pipe.sadd(index, content_hash)
            else:
                pipe.srem(members, application_id)
        if content_hash:
            pipe.hincrby(self._k("xc"), content_hash, sign)
            if email:
                pipe.hincrby(self._k("xne"), _SEP.join((content_hash, name, email)), sign)
            if phone:
                pipe.hincrby(self._k("xnp"), _SEP.join((content_hash, name, phone)), sign)
            if email and phone:
                pipe.hincrby(self._k("xid"), _SEP.join((content_hash, name, email, phone)), sign)
        normalized = template_text(application)
        if normalized:
            pipe.hincrby(self._k(f"tp:{template_hash(normalized)}"), candidate_identity_key(application), sign)

    def _save(self, client: Any, application: Application, decision: Decision) -> None:
        application_id = application.application_id
        app_key = self._k(f"app:{application_id}")
        sequence = None
        for _ in range(_RETRIES):
            with client.pipeline(transaction=True) as pipe:
                try:
                    pipe.watch(app_key)
                    previous = _decode_application(pipe.get(app_key))
                    if previous is None and sequence is None:
                        sequence = int(client.incr(self._k("seq")))
                    pipe.multi()
                    if previous is not None:
                        self._index_commands(pipe, previous, -1)
                    else:
                        pipe.zadd(self._k("order"), {application_id: sequence}, nx=True)
                    pipe.set(app_key, _encode_application(application))
                    pipe.set(self._k(f"dec:{application_id}"), decision.model_dump_json())
                    self._index_commands(pipe, application, 1)
                    pipe.execute()
                    return
                except redis.WatchError:
                    continue
        raise redis.RedisError("save contention")

    def save(self, application: Application, decision: Decision) -> None:
        self._link.call(lambda client: self._save(client, application, decision), lambda: self._fallback.save(application, decision))

    def get_decision(self, application_id: str) -> Decision | None:
        return self._link.call(
            lambda client: _decode_decision(client.get(self._k(f"dec:{application_id}"))),
            lambda: self._fallback.get_decision(application_id),
        )

    def decisions(self) -> tuple[Decision, ...]:
        def operation(client):
            ids = client.zrange(self._k("order"), 0, -1)
            if not ids:
                return ()
            raws = client.mget([self._k(f"dec:{item}") for item in ids])
            return tuple(item for item in (_decode_decision(raw) for raw in raws) if item is not None)

        return self._link.call(operation, self._fallback.decisions)

    def clear(self) -> None:
        def operation(client):
            keys = list(client.scan_iter(match=f"{STORE_PREFIX}*", count=1000))
            for index in range(0, len(keys), 500):
                client.delete(*keys[index : index + 500])

        self._link.call(operation, lambda: None)
        self._fallback.clear()
