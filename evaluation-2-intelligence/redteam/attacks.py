from __future__ import annotations

import os
import random
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import urlparse

import httpx


Payload = dict[str, Any]
BASE_TIME = 1_767_225_600.0

ATTACK_NAMES = (
    "naive_flood",
    "rotating_identity",
    "slow_and_low",
    "paraphrase_copy",
    "resume_farm",
    "email_alias_abuse",
)
CONTROL_NAMES = ("honest_ai_polished", "honest_campus_burst")

DEFAULT_COUNTS = {
    "naive_flood": 18,
    "rotating_identity": 14,
    "slow_and_low": 12,
    "paraphrase_copy": 10,
    "resume_farm": 12,
    "email_alias_abuse": 10,
    "honest_ai_polished": 10,
    "honest_campus_burst": 18,
}

FIRST_NAMES = (
    "Aarav",
    "Aditi",
    "Ananya",
    "Dev",
    "Ishaan",
    "Kavya",
    "Meera",
    "Nila",
    "Rohan",
    "Sara",
    "Tara",
    "Vikram",
)
LAST_NAMES = ("Bose", "Das", "Iyer", "Jain", "Menon", "Nair", "Rao", "Sen", "Shah", "Singh")

BASE_DESCRIPTION = (
    "Built a resilient application service in Python and FastAPI. "
    "Designed PostgreSQL schemas for reliable audit records. "
    "Implemented automated tests and monitored production latency. "
    "Collaborated with product engineers to deliver secure releases."
)


def _job(job_id: str = "backend-platform") -> Payload:
    return {
        "must_have_skills": ["Python", "FastAPI", "PostgreSQL"],
        "nice_to_have": ["Docker", "Redis"],
        "min_years": 2.0,
    }


def _experience(company: str = "Northstar Labs", title: str = "Software Engineer") -> list[Payload]:
    return [{"company": company, "title": title, "start": "2021-06", "end": "2025-08"}]


def _candidate(
    *,
    name: str,
    email: str,
    phone: str,
    description: str,
    project_name: str = "Hiring Platform",
    company: str = "Northstar Labs",
    title: str = "Software Engineer",
) -> Payload:
    return {
        "name": name,
        "email": email,
        "phone": phone,
        "skills": ["Python", "FastAPI", "PostgreSQL", "Docker"],
        "experience": _experience(company, title),
        "projects": [{"name": project_name, "description": description}],
        "claimed_experience_years": 4.0,
    }


def _payload(
    *,
    application_id: str,
    job_id: str,
    candidate: Payload,
    device_id: str,
    ip: str,
    session_seconds: float,
    paste_char_ratio: float,
    submitted_at: float,
) -> Payload:
    return {
        "application": {
            "application_id": application_id,
            "job_id": job_id,
            "candidate": candidate,
            "signals": {
                "device_id": device_id,
                "ip": ip,
                "session_seconds": session_seconds,
                "paste_char_ratio": paste_char_ratio,
                "submitted_at": submitted_at,
            },
        },
        "job": _job(job_id),
    }


def _identity(rng: random.Random, index: int, prefix: str) -> tuple[str, str, str]:
    first = rng.choice(FIRST_NAMES)
    last = rng.choice(LAST_NAMES)
    serial = rng.randrange(1000, 9999)
    name = f"{first} {last}"
    email = f"{prefix}.{first.lower()}.{last.lower()}.{serial}.{index}@example.test"
    phone = f"+91 9{rng.randrange(100_000_000, 999_999_999):09d}"
    return name, email, phone


def _fallback_paraphrase(text: str, rng: random.Random, index: int) -> str:
    substitutions = (
        ("Built", "Created"),
        ("resilient", "fault-tolerant"),
        ("application service", "backend system"),
        ("Designed", "Modeled"),
        ("reliable", "dependable"),
        ("Implemented", "Developed"),
        ("automated tests", "repeatable test suites"),
        ("monitored", "observed"),
        ("Collaborated", "Partnered"),
        ("deliver", "ship"),
        ("secure releases", "hardened deployments"),
    )
    changed = text
    for offset, (source, replacement) in enumerate(substitutions):
        if (index + offset) % 3 != 0:
            changed = changed.replace(source, replacement)
    sentences = [sentence.strip() for sentence in changed.split(".") if sentence.strip()]
    rng.shuffle(sentences)
    return ". ".join(sentences) + "."


def _ollama_paraphrase(text: str, seed: int, base_url: str) -> str | None:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return None
    try:
        with httpx.Client(base_url=base_url.rstrip("/"), timeout=2.0, trust_env=False) as client:
            tags = client.get("/api/tags")
            tags.raise_for_status()
            models = tags.json().get("models", [])
            if not models:
                return None
            requested_model = os.getenv("FIREWALL_REDTEAM_MODEL", "").strip()
            model_names = [str(item.get("name", "")) for item in models]
            model = requested_model if requested_model in model_names else next(
                (name for name in model_names if name and "embed" not in name.lower()),
                "",
            )
            if not model:
                return None
            strategies = (
                "Reverse the sentence order and replace wording with close synonyms.",
                "Combine the facts into two longer sentences with different syntax.",
                "Use concise clauses and mostly passive voice.",
                "Start with collaboration, then describe testing, data, and service work.",
                "Use achievement-style prose with varied sentence openings.",
                "Use formal nominal phrasing while preserving every fact.",
                "Put operational outcomes before the tools and implementation details.",
                "Use plain language and split the facts into short sentences.",
                "Lead with database work and end with the application service.",
                "Lead with release security and end with team collaboration.",
            )
            strategy = strategies[seed % len(strategies)]
            response = client.post(
                "/api/generate",
                json={
                    "model": model,
                    "prompt": (
                        "Paraphrase this synthetic resume paragraph without adding facts. "
                        f"{strategy} Return only the paragraph:\n{text}"
                    ),
                    "stream": False,
                    "keep_alive": "10m",
                    "options": {"temperature": 0, "seed": seed},
                },
                timeout=20.0,
            )
            response.raise_for_status()
            result = response.json().get("response", "").strip()
            return result or None
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return None


def naive_flood(*, seed: int = 2026, count: int = 18, **_: Any) -> Iterator[Payload]:
    rng = random.Random(seed)
    name, email, phone = _identity(rng, 0, "flood")
    candidate = _candidate(name=name, email=email, phone=phone, description=BASE_DESCRIPTION)
    for index in range(count):
        yield _payload(
            application_id=f"naive-{seed}-{index:03d}",
            job_id=f"flood-job-{index:03d}",
            candidate=candidate,
            device_id=f"flood-device-{seed}",
            ip="198.51.100.20",
            session_seconds=4.0 + rng.random() * 4.0,
            paste_char_ratio=0.96,
            submitted_at=BASE_TIME + index,
        )


def rotating_identity(*, seed: int = 2026, count: int = 14, **_: Any) -> Iterator[Payload]:
    rng = random.Random(seed)
    name, email, phone = _identity(rng, 0, "rotating")
    candidate = _candidate(name=name, email=email, phone=phone, description=BASE_DESCRIPTION)
    for index in range(count):
        yield _payload(
            application_id=f"rotating-{seed}-{index:03d}",
            job_id=f"rotating-job-{index:03d}",
            candidate=candidate,
            device_id=f"rotating-device-{seed}-{index}",
            ip=f"203.0.113.{20 + index}",
            session_seconds=180.0 + rng.random() * 240.0,
            paste_char_ratio=0.15 + rng.random() * 0.25,
            submitted_at=BASE_TIME + index * 12.0,
        )


def slow_and_low(*, seed: int = 2026, count: int = 12, **_: Any) -> Iterator[Payload]:
    rng = random.Random(seed)
    for index in range(count):
        name, email, phone = _identity(rng, index, "slow")
        description = _fallback_paraphrase(BASE_DESCRIPTION, rng, index)
        yield _payload(
            application_id=f"slow-{seed}-{index:03d}",
            job_id="backend-platform",
            candidate=_candidate(
                name=name,
                email=email,
                phone=phone,
                description=description,
                project_name=f"Service Reliability Initiative {index + 1}",
            ),
            device_id=f"slow-device-{seed}-{index}",
            ip=f"198.51.100.{80 + index}",
            session_seconds=300.0 + rng.random() * 300.0,
            paste_char_ratio=0.08 + rng.random() * 0.20,
            submitted_at=BASE_TIME + index * 240.0,
        )


def paraphrase_copy(
    *,
    seed: int = 2026,
    count: int = 10,
    use_ollama: bool = False,
    ollama_url: str = "http://localhost:11434",
    **_: Any,
) -> Iterator[Payload]:
    rng = random.Random(seed)
    for index in range(count):
        name, email, phone = _identity(rng, index, "copy")
        description = None
        if use_ollama:
            description = _ollama_paraphrase(BASE_DESCRIPTION, seed + index, ollama_url)
        if not description:
            description = _fallback_paraphrase(BASE_DESCRIPTION, rng, index + 11)
        yield _payload(
            application_id=f"copy-{seed}-{index:03d}",
            job_id="copied-resume-target",
            candidate=_candidate(
                name=name,
                email=email,
                phone=phone,
                description=description,
                project_name=f"Candidate Delivery System {index + 1}",
            ),
            device_id=f"copy-device-{seed}-{index}",
            ip=f"203.0.113.{100 + index}",
            session_seconds=160.0 + rng.random() * 160.0,
            paste_char_ratio=0.25 + rng.random() * 0.30,
            submitted_at=BASE_TIME + 600.0 + index * 75.0,
        )


def resume_farm(*, seed: int = 2026, count: int = 12, **_: Any) -> Iterator[Payload]:
    rng = random.Random(seed)
    variants = (
        "Reduced endpoint latency by twelve percent.",
        "Added dashboards for operational visibility.",
        "Documented recovery procedures for the team.",
        "Improved release checks for safer deployments.",
    )
    for index in range(count):
        name, email, phone = _identity(rng, index, "farm")
        description = f"{BASE_DESCRIPTION} {variants[(seed + index) % len(variants)]}"
        yield _payload(
            application_id=f"farm-{seed}-{index:03d}",
            job_id="backend-platform",
            candidate=_candidate(name=name, email=email, phone=phone, description=description),
            device_id=f"farm-device-{seed}-{index % 6}",
            ip=f"198.51.100.{130 + index % 6}",
            session_seconds=90.0 + rng.random() * 80.0,
            paste_char_ratio=0.65 + rng.random() * 0.20,
            submitted_at=BASE_TIME + index * 8.0,
        )


def email_alias_abuse(*, seed: int = 2026, count: int = 10, **_: Any) -> Iterator[Payload]:
    rng = random.Random(seed)
    aliases = (
        "ananya.rao+backend@gmail.com",
        "a.n.a.n.y.a.r.a.o+jobs@googlemail.com",
        "ananyarao+campus@gmail.com",
        "ananya.r.a.o+apply@googlemail.com",
    )
    names = ("Ananya Rao", "A Rao", "Ananya R", "Ana Rao")
    for index in range(count):
        description = _fallback_paraphrase(BASE_DESCRIPTION, rng, index + 31)
        candidate = _candidate(
            name=names[index % len(names)],
            email=aliases[index % len(aliases)],
            phone=f"+91 8{(seed * 10_000 + index):09d}"[-14:],
            description=description,
            project_name=f"Backend Portfolio {index + 1}",
        )
        yield _payload(
            application_id=f"alias-{seed}-{index:03d}",
            job_id="backend-platform",
            candidate=candidate,
            device_id=f"alias-device-{seed}-{index}",
            ip=f"203.0.113.{150 + index}",
            session_seconds=130.0 + rng.random() * 120.0,
            paste_char_ratio=0.20 + rng.random() * 0.30,
            submitted_at=BASE_TIME + index * 90.0,
        )


def honest_ai_polished(*, seed: int = 2026, count: int = 10, **_: Any) -> Iterator[Payload]:
    rng = random.Random(seed)
    domains = ("payments", "education", "logistics", "health analytics", "developer tooling")
    outcomes = ("cut review time", "improved uptime", "reduced query cost", "sped up onboarding")
    for index in range(count):
        name, email, phone = _identity(rng, index, "honest")
        domain = domains[(seed + index) % len(domains)]
        outcome = outcomes[(seed * 3 + index) % len(outcomes)]
        description = (
            f"Designed a Python and FastAPI service for {domain}. "
            f"Modeled PostgreSQL data with traceable migrations and {outcome}. "
            f"Wrote integration tests and presented the measured results to stakeholders for cohort {index + 1}."
        )
        yield _payload(
            application_id=f"honest-polished-{seed}-{index:03d}",
            job_id="backend-platform",
            candidate=_candidate(
                name=name,
                email=email,
                phone=phone,
                description=description,
                project_name=f"{domain.title()} Service {index + 1}",
                company=f"Company {chr(65 + index % 26)}",
            ),
            device_id=f"honest-device-{seed}-{index}",
            ip=f"198.51.100.{180 + index}",
            session_seconds=420.0 + rng.random() * 480.0,
            paste_char_ratio=0.35 + rng.random() * 0.30,
            submitted_at=BASE_TIME + index * 900.0,
        )


def honest_campus_burst(*, seed: int = 2026, count: int = 18, **_: Any) -> Iterator[Payload]:
    rng = random.Random(seed)
    for index in range(count):
        name, email, phone = _identity(rng, index, "student")
        description = (
            f"Created campus project {index + 1} using Python, FastAPI, and PostgreSQL for student workflow {seed + index}. "
            f"Tested API behavior with a distinct dataset of {100 + index * 17} records and documented the findings. "
            f"Coordinated demonstration group {index + 1} and resolved deployment issue category {index % 7}."
        )
        yield _payload(
            application_id=f"campus-{seed}-{index:03d}",
            job_id="backend-platform",
            candidate=_candidate(
                name=name,
                email=email,
                phone=phone,
                description=description,
                project_name=f"Campus Capstone {index + 1}",
                company=f"Internship Partner {chr(65 + index % 26)}",
                title="Software Engineering Intern",
            ),
            device_id=f"student-laptop-{seed}-{index}",
            ip="192.0.2.44",
            session_seconds=240.0 + rng.random() * 360.0,
            paste_char_ratio=0.10 + rng.random() * 0.35,
            submitted_at=BASE_TIME + index * 2.0,
        )


GENERATORS: dict[str, Callable[..., Iterator[Payload]]] = {
    "naive_flood": naive_flood,
    "rotating_identity": rotating_identity,
    "slow_and_low": slow_and_low,
    "paraphrase_copy": paraphrase_copy,
    "resume_farm": resume_farm,
    "email_alias_abuse": email_alias_abuse,
    "honest_ai_polished": honest_ai_polished,
    "honest_campus_burst": honest_campus_burst,
}


def scenario_prelude(name: str, *, seed: int) -> list[Payload]:
    if name != "paraphrase_copy":
        return []
    source = _candidate(
        name="Priya Original",
        email=f"priya.original.{seed}@example.test",
        phone=f"+91 7{seed:09d}"[-14:],
        description=BASE_DESCRIPTION,
        project_name="Hiring Platform",
    )
    return [
        _payload(
            application_id=f"copy-source-{seed}",
            job_id="copied-resume-target",
            candidate=source,
            device_id=f"source-device-{seed}",
            ip="192.0.2.10",
            session_seconds=640.0,
            paste_char_ratio=0.12,
            submitted_at=BASE_TIME,
        )
    ]


def generate_scenario(
    name: str,
    *,
    seed: int = 2026,
    count: int | None = None,
    use_ollama: bool = False,
    ollama_url: str = "http://localhost:11434",
) -> Iterator[Payload]:
    try:
        generator = GENERATORS[name]
    except KeyError as exc:
        raise ValueError(f"unknown scenario: {name}") from exc
    requested_count = DEFAULT_COUNTS[name] if count is None else count
    if requested_count < 1:
        raise ValueError("count must be at least 1")
    yield from generator(
        seed=seed,
        count=requested_count,
        use_ollama=use_ollama,
        ollama_url=ollama_url,
    )
