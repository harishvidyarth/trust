from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _import_models():
    for attempt in range(5):
        try:
            from firewall import models

            return models
        except ImportError:
            if attempt == 4:
                raise
            time.sleep(60)
    raise RuntimeError("unreachable")


models = _import_models()

CLASS_NAMES = (
    "honest",
    "honest_campus",
    "ai_assisted_honest",
    "bot_naive",
    "bot_evasive",
    "duplicate",
    "fabricated",
    "farm",
)
LEGIT_CLASSES = frozenset({"honest", "honest_campus", "ai_assisted_honest"})

PROFILES = {
    "dev": {
        "seed": 1,
        "first_names": ["Aarav", "Diya", "Kabir", "Meera", "Rohan", "Tara", "Vikram", "Zoya"],
        "last_names": ["Rao", "Iyer", "Shah", "Nair", "Kapoor", "Joshi", "Menon", "Bose"],
        "companies": ["BluePeak Labs", "Kite Systems", "Riverstone Tech", "Cedar Analytics"],
        "skills": ["Python", "FastAPI", "PostgreSQL", "Docker", "Redis", "React"],
        "templates": [
            "Built a reliable {kind} service with measurable latency and test coverage",
            "Designed a {kind} workflow that improved review quality for operators",
            "Delivered a maintainable {kind} platform with documented tradeoffs",
        ],
        "kinds": ["payments", "inventory", "support", "analytics", "scheduling"],
        "verbs": ["Built", "Designed", "Shipped", "Refactored", "Automated", "Migrated", "Profiled", "Instrumented", "Scaled", "Hardened", "Prototyped", "Documented"],
        "objects": [
            "a billing reconciliation pipeline", "an order tracking dashboard", "a customer ticket router",
            "a nightly data quality checker", "a feature flag service", "a mobile push gateway",
            "an internal search index", "a role based access layer", "a report scheduling engine",
            "a fraud scoring prototype", "a warehouse stock sync", "an audit trail exporter",
            "a rate limited public api", "a document ingestion worker", "a shipment eta estimator",
            "a multi tenant settings portal",
        ],
        "outcomes": [
            "cutting p95 latency by a third", "reducing manual effort for the support team",
            "after a month of careful load testing", "which removed two recurring incidents",
            "with unit and integration coverage above eighty percent", "adopted by three neighbouring teams",
            "saving roughly six engineer hours every week", "while keeping the rollout fully reversible",
            "and presented the tradeoffs at a team review", "using only open source components",
            "with clear runbooks for the on call rotation", "within a tight two week deadline",
            "improving onboarding time for new analysts", "and cleaned up years of legacy configuration",
        ],
        "closers": [
            "Wrote the design notes and mentored a junior colleague through the rollout",
            "Partnered with product to prioritise the backlog and trim scope",
            "Presented the results to leadership and incorporated their feedback",
        ],
        "titles": ["Software Engineer", "Associate Engineer", "Backend Developer", "Platform Engineer"],
    },
    "heldout": {
        "seed": 2,
        "first_names": ["Ananya", "Dev", "Ishaan", "Kavya", "Neel", "Pooja", "Sahil", "Yamini"],
        "last_names": ["Banerjee", "Chawla", "Desai", "Gill", "Kulkarni", "Pillai", "Sethi", "Verma"],
        "companies": ["AmberWorks", "Nimbus Data", "Orchid Software", "Summit Logic"],
        "skills": ["Java", "Spring", "MySQL", "Kubernetes", "TypeScript", "Angular"],
        "templates": [
            "Implemented a resilient {kind} system and validated production-facing safeguards",
            "Led delivery of a {kind} product using observable and repeatable engineering practices",
            "Created a scalable {kind} solution with clear ownership and operational controls",
        ],
        "kinds": ["logistics", "identity", "reporting", "commerce", "monitoring"],
        "verbs": ["Implemented", "Led", "Created", "Optimised", "Rebuilt", "Integrated", "Stabilised", "Containerised", "Benchmarked", "Simplified", "Standardised", "Delivered"],
        "objects": [
            "a route planning microservice", "an identity verification flow", "a self service analytics portal",
            "a catalogue synchronisation job", "a log based anomaly alerting tool", "a payment retry orchestrator",
            "an event driven notification hub", "a compliance report generator", "a vendor onboarding workflow",
            "a session management library", "an inventory forecasting notebook", "a deployment verification suite",
            "a graph based recommendation prototype", "a configuration drift detector", "a partner facing webhook layer",
            "a tenant isolation test harness",
        ],
        "outcomes": [
            "that shortened release cycles noticeably", "after profiling slow database queries",
            "and lifted customer satisfaction scores", "which simplified a brittle manual process",
            "supported by dashboards and clear service level objectives", "reviewed by the security team",
            "enabling faster experiments for the growth group", "with a staged canary rollout",
            "and documented the decisions for future maintainers", "reducing cloud spend by a measurable margin",
            "handling several times the previous traffic", "alongside a cross functional squad",
            "eliminating a class of intermittent failures", "and trained colleagues to operate it independently",
        ],
        "closers": [
            "Coordinated with stakeholders across engineering and operations to land the change",
            "Gathered feedback from users and iterated on the interface over several sprints",
            "Owned the on call handover and wrote a concise operational guide",
        ],
        "titles": ["Software Developer", "Systems Engineer", "Application Engineer", "Junior Architect"],
    },
}


def _parse_skills(value: str) -> list[str]:
    value = value.strip()
    if not value:
        return []
    if value.startswith("["):
        parsed = json.loads(value)
        return [str(item).strip() for item in parsed if str(item).strip()]
    normalized = value.replace("|", ",").replace(";", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


def load_honest_csv(path: Path | None) -> list[dict[str, str]]:
    if path is None:
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"honest CSV contains no data rows: {path}")
    return rows


def _job(profile: dict[str, Any], index: int) -> dict[str, Any]:
    skills = profile["skills"]
    offset = index % 3
    must_have = [skills[offset], skills[(offset + 1) % len(skills)]]
    return {
        "must_have_skills": must_have,
        "nice_to_have": [skills[(offset + 2) % len(skills)]],
        "min_years": 2.0,
    }


_PREFIX_CODES: dict[str, int] = {}


def _identity(profile: dict[str, Any], index: int, prefix: str) -> tuple[str, str, str]:
    first = profile["first_names"][index % len(profile["first_names"])]
    last = profile["last_names"][(index // len(profile["first_names"])) % len(profile["last_names"])]
    name = f"{first} {last}"
    email = f"{first.lower()}.{last.lower()}.{prefix}{index}@example.org"
    code = _PREFIX_CODES.setdefault(prefix, len(_PREFIX_CODES) + 10)
    phone = f"9{code:02d}{index:07d}"[-10:]
    return name, email, phone


def _resume(
    profile: dict[str, Any],
    rng: random.Random,
    index: int,
    required: list[str],
    *,
    polished: bool = False,
    description: str | None = None,
) -> dict[str, Any]:
    kind = profile["kinds"][(index * 3 + rng.randrange(len(profile["kinds"]))) % len(profile["kinds"])]
    if description is not None:
        project_text = description
    else:
        sentence_count = 3 if polished else 2
        sentences = [
            f"{rng.choice(profile['verbs'])} {rng.choice(profile['objects'])} {rng.choice(profile['outcomes'])}"
            for _ in range(sentence_count)
        ]
        if polished:
            sentences.append(rng.choice(profile["closers"]))
        project_text = "; ".join(sentences)
    skills = list(dict.fromkeys(required + rng.sample(profile["skills"], k=min(2, len(profile["skills"])))))
    start_index = (2016 + rng.randrange(7)) * 12 + rng.randrange(12)
    end_index = min(start_index + 30 + rng.randrange(60), 2025 * 12 + 8)
    start = f"{start_index // 12}-{start_index % 12 + 1:02d}"
    end = f"{end_index // 12}-{end_index % 12 + 1:02d}"
    return {
        "skills": skills,
        "experience": [
            {
                "company": rng.choice(profile["companies"]),
                "title": rng.choice(profile["titles"]),
                "start": start,
                "end": end,
            }
        ],
        "projects": [{"name": f"{kind.title()} Project {index}", "description": project_text}],
        "claimed_experience_years": round((end_index - start_index) / 12, 1),
    }


def _record(
    application_id: str,
    job_id: str,
    candidate: dict[str, Any],
    job: dict[str, Any],
    class_name: str,
    timestamp: float,
    *,
    device_id: str,
    ip: str,
    session_seconds: float,
    paste_char_ratio: float,
) -> dict[str, Any]:
    application = {
        "application_id": application_id,
        "job_id": job_id,
        "candidate": candidate,
        "signals": {
            "device_id": device_id,
            "ip": ip,
            "session_seconds": session_seconds,
            "paste_char_ratio": paste_char_ratio,
            "submitted_at": timestamp,
        },
    }
    models.Application.model_validate(application)
    models.JobRequirements.model_validate(job)
    return {
        "application": application,
        "job": job,
        "label": "LEGIT" if class_name in LEGIT_CLASSES else "ABUSE",
        "class": class_name,
    }


def generate_records(
    profile_name: str,
    count_per_class: int = 300,
    seed: int | None = None,
    honest_rows: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    if profile_name not in PROFILES:
        raise ValueError(f"unknown profile: {profile_name}")
    if count_per_class <= 0:
        raise ValueError("count_per_class must be positive")
    profile = PROFILES[profile_name]
    rng = random.Random(profile["seed"] if seed is None else seed)
    honest_rows = honest_rows or []
    records: list[dict[str, Any]] = []
    base = 1_767_225_600.0 + (0 if profile_name == "dev" else 10_000_000)
    block_span = max(100_000.0, count_per_class * 300.0)

    for class_index, class_name in enumerate(CLASS_NAMES):
        block = base + class_index * block_span
        for index in range(count_per_class):
            job = _job(profile, index)
            required = job["must_have_skills"]
            job_id = f"{profile_name}-job-{index % 12}"
            timestamp = block + index * 180.0
            name, email, phone = _identity(profile, index, class_name)
            candidate = {"name": name, "email": email, "phone": phone}
            candidate.update(_resume(profile, rng, index, required))
            device = f"device-{class_name}-{index}"
            ip = f"198.51.{class_index}.{(index % 240) + 10}"
            session = rng.uniform(90, 900)
            paste = rng.uniform(0.05, 0.75)

            if class_name == "honest" and honest_rows:
                row = honest_rows[index % len(honest_rows)]
                candidate["name"] = row.get("name") or candidate["name"]
                candidate["email"] = row.get("email") or candidate["email"]
                candidate["phone"] = row.get("phone") or candidate["phone"]
                csv_skills = _parse_skills(row.get("skills", ""))
                if csv_skills:
                    candidate["skills"] = list(dict.fromkeys(required + csv_skills))
                resume_text = row.get("resume_text") or row.get("summary")
                if resume_text:
                    candidate["projects"][0]["description"] = resume_text

            elif class_name == "honest_campus":
                timestamp = block + index * 2.0
                ip = "203.0.113.25"
                session = rng.uniform(120, 800)
                paste = rng.uniform(0.05, 0.70)

            elif class_name == "ai_assisted_honest":
                candidate.update(_resume(profile, rng, index + 20_000, required, polished=True))
                session = rng.uniform(180, 1_200)
                paste = rng.uniform(0.45, 0.88)

            elif class_name == "bot_naive":
                timestamp = block + index * 1.5
                device = "automator-device-naive"
                ip = "192.0.2.44"
                session = rng.uniform(3, 15)
                paste = rng.uniform(0.94, 1.0)

            elif class_name == "bot_evasive":
                timestamp = block + index * 3.0
                operator = index % max(1, count_per_class // 10)
                op_name, op_email, op_phone = _identity(profile, operator, "evasive-person")
                candidate["name"] = op_name
                candidate["email"] = op_email
                candidate["phone"] = op_phone
                candidate.update(_resume(profile, rng, operator + 30_000, required))
                job_id = f"{profile_name}-evasive-job-{index}"
                device = f"rotated-device-{index}"
                ip = f"100.64.{index // 240}.{(index % 240) + 10}"
                session = rng.uniform(75, 420)
                paste = rng.uniform(0.2, 0.8)

            elif class_name == "duplicate":
                group = index // 3
                variant = index % 3
                base_name, _, base_phone = _identity(profile, group, "duplicate-person")
                compact = "".join(character for character in base_name.lower() if character.isalpha())
                candidate["name"] = base_name if variant < 2 else f"{base_name.split()[0]} {base_name.split()[-1][0]}."
                candidate["email"] = (
                    f"{compact[:4]}.{compact[4:]}+attempt{variant}@gmail.com"
                    if variant == 0
                    else f"{compact}+attempt{variant}@googlemail.com"
                )
                candidate["phone"] = base_phone if variant < 2 else f"7{index:09d}"[-10:]
                candidate.update(_resume(profile, rng, group + 40_000, required))
                job_id = f"{profile_name}-duplicate-job-{group % 8}"
                timestamp = block + group * 120.0 + variant * 10.0

            elif class_name == "fabricated":
                candidate["skills"] = [profile["skills"][-1]]
                candidate["experience"] = [
                    {
                        "company": "Unverifiable Ventures",
                        "title": "Principal Engineer",
                        "start": "2025-09",
                        "end": "2021-02",
                    }
                ]
                candidate["claimed_experience_years"] = 12.0

            elif class_name == "farm":
                group = index // 15
                shared = (
                    f"Built scalable candidate onboarding and reporting services with automated tests, "
                    f"monitoring dashboards, deployment controls, and operator documentation for cohort {group}"
                )
                suffix = ["improved reliability", "reduced manual effort", "supported daily operations"][index % 3]
                candidate.update(_resume(profile, rng, group + 50_000, required, description=f"{shared}; {suffix}"))
                timestamp = block + index * 4.0
                device = f"farm-operator-{group % 2}"
                ip = f"172.20.0.{20 + group % 3}"
                session = rng.uniform(45, 140)
                paste = rng.uniform(0.70, 0.89)

            records.append(
                _record(
                    f"{profile_name}-{class_name}-{index:04d}",
                    job_id,
                    candidate,
                    job,
                    class_name,
                    timestamp,
                    device_id=device,
                    ip=ip,
                    session_seconds=session,
                    paste_char_ratio=paste,
                )
            )

    records.sort(key=lambda item: item["application"]["signals"]["submitted_at"])
    return records


def write_jsonl(records: list[dict[str, Any]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate deterministic firewall evaluation JSONL.")
    parser.add_argument("--profile", choices=sorted(PROFILES), required=True)
    parser.add_argument("--count-per-class", type=int, default=300)
    parser.add_argument("--seed", type=int, help="Override the profile seed.")
    parser.add_argument("--honest-csv", type=Path, help="Optional local CSV used only for honest records.")
    parser.add_argument("--output", type=Path, help="Default: eval/data/<profile>.jsonl")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = args.output or Path(__file__).resolve().parent / "data" / f"{args.profile}.jsonl"
    records = generate_records(
        args.profile,
        count_per_class=args.count_per_class,
        seed=args.seed,
        honest_rows=load_honest_csv(args.honest_csv),
    )
    write_jsonl(records, output)
    print(f"wrote {len(records)} records to {output}")
    print(f"seed={PROFILES[args.profile]['seed'] if args.seed is None else args.seed} profile={args.profile}")


if __name__ == "__main__":
    main()
