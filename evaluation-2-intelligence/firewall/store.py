from __future__ import annotations

from abc import ABC, abstractmethod
from bisect import bisect_left, bisect_right, insort
from collections import OrderedDict, defaultdict
from threading import RLock

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
from firewall.signals.identity_links import link_index_keys, name_index_keys, signature_index_keys


class ApplicationStore(ABC):

    @abstractmethod
    def applications(self) -> tuple[Application, ...]:
        raise NotImplementedError

    @abstractmethod
    def by_email(self, email: str) -> tuple[Application, ...]:
        raise NotImplementedError

    @abstractmethod
    def by_phone(self, phone: str) -> tuple[Application, ...]:
        raise NotImplementedError

    @abstractmethod
    def by_job(self, job_id: str) -> tuple[Application, ...]:
        raise NotImplementedError

    @abstractmethod
    def by_link(self, key: str) -> tuple[Application, ...]:
        raise NotImplementedError

    @abstractmethod
    def by_name_token(self, token: str) -> tuple[Application, ...]:
        raise NotImplementedError

    @abstractmethod
    def by_signature(self, key: str) -> tuple[Application, ...]:
        raise NotImplementedError

    @abstractmethod
    def recent(self, start: float, end: float) -> tuple[Application, ...]:
        raise NotImplementedError

    @abstractmethod
    def recent_by_device(self, device_id: str, start: float, end: float) -> tuple[Application, ...]:
        raise NotImplementedError

    @abstractmethod
    def recent_by_ip(self, ip: str, start: float, end: float) -> tuple[Application, ...]:
        raise NotImplementedError

    @abstractmethod
    def recent_by_identity(self, email: str, phone: str, start: float, end: float) -> tuple[Application, ...]:
        raise NotImplementedError

    @abstractmethod
    def resume_candidates(self, application: Application, include_all: bool = False) -> tuple[Application, ...]:
        raise NotImplementedError

    @abstractmethod
    def has_exact_resume_from_other_identity(self, application: Application) -> bool:
        raise NotImplementedError

    @abstractmethod
    def template_identity_count(self, normalized_text: str, current_identity: str) -> int:
        raise NotImplementedError

    @abstractmethod
    def save(self, application: Application, decision: Decision) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_decision(self, application_id: str) -> Decision | None:
        raise NotImplementedError

    @abstractmethod
    def decisions(self) -> tuple[Decision, ...]:
        raise NotImplementedError

    @abstractmethod
    def clear(self) -> None:
        raise NotImplementedError


class InMemoryApplicationStore(ApplicationStore):
    def __init__(self) -> None:
        self._applications: OrderedDict[str, Application] = OrderedDict()
        self._decisions: OrderedDict[str, Decision] = OrderedDict()
        self._sequence: dict[str, int] = {}
        self._next_sequence = 0
        self._by_email: dict[str, set[str]] = defaultdict(set)
        self._by_phone: dict[str, set[str]] = defaultdict(set)
        self._by_job: dict[str, set[str]] = defaultdict(set)
        self._by_link: dict[str, set[str]] = defaultdict(set)
        self._by_name_token: dict[str, set[str]] = defaultdict(set)
        self._by_signature: dict[str, set[str]] = defaultdict(set)
        self._by_device_time: dict[str, list[tuple[float, str]]] = defaultdict(list)
        self._by_ip_time: dict[str, list[tuple[float, str]]] = defaultdict(list)
        self._by_email_time: dict[str, list[tuple[float, str]]] = defaultdict(list)
        self._by_phone_time: dict[str, list[tuple[float, str]]] = defaultdict(list)
        self._time_ordered: list[tuple[float, str]] = []
        self._resume_buckets: dict[str, dict[str, set[str]]] = defaultdict(dict)
        self._exact_resume_counts: dict[str, int] = defaultdict(int)
        self._exact_resume_name_email: dict[tuple[str, str, str], int] = defaultdict(int)
        self._exact_resume_name_phone: dict[tuple[str, str, str], int] = defaultdict(int)
        self._exact_resume_identity: dict[tuple[str, str, str, str], int] = defaultdict(int)
        self._template_identity_counts: dict[str, dict[str, int]] = defaultdict(dict)
        self._lock = RLock()

    def applications(self) -> tuple[Application, ...]:
        with self._lock:
            return tuple(self._applications.values())

    def _ordered(self, application_ids: set[str]) -> tuple[Application, ...]:
        return tuple(
            self._applications[application_id]
            for application_id in sorted(application_ids, key=self._sequence.__getitem__)
            if application_id in self._applications
        )

    def by_email(self, email: str) -> tuple[Application, ...]:
        with self._lock:
            return self._ordered(set(self._by_email.get(email, ())))

    def by_phone(self, phone: str) -> tuple[Application, ...]:
        with self._lock:
            return self._ordered(set(self._by_phone.get(phone, ())))

    def by_job(self, job_id: str) -> tuple[Application, ...]:
        with self._lock:
            return self._ordered(set(self._by_job.get(job_id, ())))

    def by_link(self, key: str) -> tuple[Application, ...]:
        with self._lock:
            return self._ordered(set(self._by_link.get(key, ())))

    def by_name_token(self, token: str) -> tuple[Application, ...]:
        with self._lock:
            return self._ordered(set(self._by_name_token.get(token, ())))

    def by_signature(self, key: str) -> tuple[Application, ...]:
        with self._lock:
            return self._ordered(set(self._by_signature.get(key, ())))

    @staticmethod
    def _time_slice(items: list[tuple[float, str]], start: float, end: float) -> list[tuple[float, str]]:
        lower = bisect_left(items, (start, ""))
        upper = bisect_right(items, (end, "\U0010ffff"))
        return items[lower:upper]

    def _recent_from_index(
        self,
        index: dict[str, list[tuple[float, str]]],
        key: str,
        start: float,
        end: float,
    ) -> tuple[Application, ...]:
        identifiers = {application_id for _, application_id in self._time_slice(index.get(key, []), start, end)}
        return self._ordered(identifiers)

    def recent(self, start: float, end: float) -> tuple[Application, ...]:
        with self._lock:
            identifiers = {item[1] for item in self._time_slice(self._time_ordered, start, end)}
            return self._ordered(identifiers)

    def recent_by_device(self, device_id: str, start: float, end: float) -> tuple[Application, ...]:
        with self._lock:
            return self._recent_from_index(self._by_device_time, device_id, start, end)

    def recent_by_ip(self, ip: str, start: float, end: float) -> tuple[Application, ...]:
        with self._lock:
            return self._recent_from_index(self._by_ip_time, ip, start, end)

    def recent_by_identity(self, email: str, phone: str, start: float, end: float) -> tuple[Application, ...]:
        with self._lock:
            identifiers: set[str] = set()
            if email:
                identifiers.update(item[1] for item in self._time_slice(self._by_email_time.get(email, []), start, end))
            if phone:
                identifiers.update(item[1] for item in self._time_slice(self._by_phone_time.get(phone, []), start, end))
            return self._ordered(identifiers)

    def resume_candidates(self, application: Application, include_all: bool = False) -> tuple[Application, ...]:
        with self._lock:
            if include_all:
                return tuple(self._applications.values())
            identifiers: set[str] = set()
            for bucket in resume_bucket_keys(application):
                for content_ids in self._resume_buckets.get(bucket, {}).values():
                    identifiers.add(next(iter(content_ids)))
            return self._ordered(identifiers)

    def has_exact_resume_from_other_identity(self, application: Application) -> bool:
        with self._lock:
            content_hash = resume_content_hash(application)
            if not content_hash:
                return False
            name = normalize_name(application.candidate.name)
            email = normalize_email(application.candidate.email)
            phone = normalize_phone(application.candidate.phone)
            same_identity_count = 0
            if email:
                same_identity_count += self._exact_resume_name_email.get((content_hash, name, email), 0)
            if phone:
                same_identity_count += self._exact_resume_name_phone.get((content_hash, name, phone), 0)
            if email and phone:
                same_identity_count -= self._exact_resume_identity.get((content_hash, name, email, phone), 0)
            return self._exact_resume_counts.get(content_hash, 0) > same_identity_count

    def template_identity_count(self, normalized_text: str, current_identity: str) -> int:
        with self._lock:
            identities = self._template_identity_counts.get(template_hash(normalized_text), {})
            return len(identities) + (current_identity not in identities)

    @staticmethod
    def _discard(index: dict[str, set[str]], key: str, application_id: str) -> None:
        values = index.get(key)
        if values is None:
            return
        values.discard(application_id)
        if not values:
            index.pop(key, None)

    @staticmethod
    def _discard_time(index: dict[str, list[tuple[float, str]]], key: str, item: tuple[float, str]) -> None:
        values = index.get(key)
        if values is None:
            return
        position = bisect_left(values, item)
        if position < len(values) and values[position] == item:
            values.pop(position)
        if not values:
            index.pop(key, None)

    @staticmethod
    def _decrement(index: dict[object, int], key: object) -> None:
        remaining = index.get(key, 0) - 1
        if remaining > 0:
            index[key] = remaining
        else:
            index.pop(key, None)

    def _remove_indexes(self, application: Application) -> None:
        application_id = application.application_id
        email = normalize_email(application.candidate.email)
        phone = normalize_phone(application.candidate.phone)
        timestamped = (application.signals.submitted_at, application_id)
        if email:
            self._discard(self._by_email, email, application_id)
            self._discard_time(self._by_email_time, email, timestamped)
        if phone:
            self._discard(self._by_phone, phone, application_id)
            self._discard_time(self._by_phone_time, phone, timestamped)
        self._discard(self._by_job, application.job_id, application_id)
        for key in link_index_keys(application):
            self._discard(self._by_link, key, application_id)
        for key in name_index_keys(application.candidate.name):
            self._discard(self._by_name_token, key, application_id)
        for key in signature_index_keys(application):
            self._discard(self._by_signature, key, application_id)
        self._discard_time(self._by_device_time, application.signals.device_id, timestamped)
        self._discard_time(self._by_ip_time, application.signals.ip, timestamped)
        position = bisect_left(self._time_ordered, timestamped)
        if position < len(self._time_ordered) and self._time_ordered[position] == timestamped:
            self._time_ordered.pop(position)
        content_hash = resume_content_hash(application)
        for bucket in resume_bucket_keys(application):
            bucket_contents = self._resume_buckets.get(bucket)
            if bucket_contents is None:
                continue
            content_ids = bucket_contents.get(content_hash)
            if content_ids is not None:
                content_ids.discard(application_id)
                if not content_ids:
                    bucket_contents.pop(content_hash, None)
            if not bucket_contents:
                self._resume_buckets.pop(bucket, None)
        name = normalize_name(application.candidate.name)
        if content_hash:
            self._decrement(self._exact_resume_counts, content_hash)
            if email:
                self._decrement(self._exact_resume_name_email, (content_hash, name, email))
            if phone:
                self._decrement(self._exact_resume_name_phone, (content_hash, name, phone))
            if email and phone:
                self._decrement(self._exact_resume_identity, (content_hash, name, email, phone))
        normalized_template = template_text(application)
        if normalized_template:
            hashed_template = template_hash(normalized_template)
            identity = candidate_identity_key(application)
            identity_counts = self._template_identity_counts[hashed_template]
            remaining = identity_counts.get(identity, 0) - 1
            if remaining > 0:
                identity_counts[identity] = remaining
            else:
                identity_counts.pop(identity, None)
            if not identity_counts:
                self._template_identity_counts.pop(hashed_template, None)

    def _add_indexes(self, application: Application) -> None:
        application_id = application.application_id
        email = normalize_email(application.candidate.email)
        phone = normalize_phone(application.candidate.phone)
        timestamped = (application.signals.submitted_at, application_id)
        if email:
            self._by_email[email].add(application_id)
            insort(self._by_email_time[email], timestamped)
        if phone:
            self._by_phone[phone].add(application_id)
            insort(self._by_phone_time[phone], timestamped)
        self._by_job[application.job_id].add(application_id)
        for key in link_index_keys(application):
            self._by_link[key].add(application_id)
        for key in name_index_keys(application.candidate.name):
            self._by_name_token[key].add(application_id)
        for key in signature_index_keys(application):
            self._by_signature[key].add(application_id)
        insort(self._by_device_time[application.signals.device_id], timestamped)
        insort(self._by_ip_time[application.signals.ip], timestamped)
        insort(self._time_ordered, timestamped)
        content_hash = resume_content_hash(application)
        for bucket in resume_bucket_keys(application):
            bucket_contents = self._resume_buckets[bucket]
            bucket_contents.setdefault(content_hash, set()).add(application_id)
        name = normalize_name(application.candidate.name)
        if content_hash:
            self._exact_resume_counts[content_hash] += 1
            if email:
                self._exact_resume_name_email[(content_hash, name, email)] += 1
            if phone:
                self._exact_resume_name_phone[(content_hash, name, phone)] += 1
            if email and phone:
                self._exact_resume_identity[(content_hash, name, email, phone)] += 1
        normalized_template = template_text(application)
        if normalized_template:
            hashed_template = template_hash(normalized_template)
            identity = candidate_identity_key(application)
            identity_counts = self._template_identity_counts[hashed_template]
            identity_counts[identity] = identity_counts.get(identity, 0) + 1

    def save(self, application: Application, decision: Decision) -> None:
        with self._lock:
            previous = self._applications.get(application.application_id)
            if previous is not None:
                self._remove_indexes(previous)
            else:
                self._sequence[application.application_id] = self._next_sequence
                self._next_sequence += 1
            self._applications[application.application_id] = application
            self._decisions[application.application_id] = decision
            self._add_indexes(application)

    def get_decision(self, application_id: str) -> Decision | None:
        with self._lock:
            return self._decisions.get(application_id)

    def decisions(self) -> tuple[Decision, ...]:
        with self._lock:
            return tuple(self._decisions.values())

    def clear(self) -> None:
        with self._lock:
            self._applications.clear()
            self._decisions.clear()
            self._sequence.clear()
            self._next_sequence = 0
            self._by_email.clear()
            self._by_phone.clear()
            self._by_job.clear()
            self._by_link.clear()
            self._by_name_token.clear()
            self._by_signature.clear()
            self._by_device_time.clear()
            self._by_ip_time.clear()
            self._by_email_time.clear()
            self._by_phone_time.clear()
            self._time_ordered.clear()
            self._resume_buckets.clear()
            self._exact_resume_counts.clear()
            self._exact_resume_name_email.clear()
            self._exact_resume_name_phone.clear()
            self._exact_resume_identity.clear()
            self._template_identity_counts.clear()
