from __future__ import annotations

import argparse
import json
import random
from pathlib import Path


NAMES = [
    "Aarav Shah",
    "Meera Nair",
    "Kabir Rao",
    "Diya Sen",
    "Rohan Iyer",
    "Nisha Das",
    "Arjun Bose",
    "Tara Menon",
    "Vikram Jain",
    "Leela Roy",
    "Neel Joshi",
    "Ananya Pillai",
    "Kiran Bhat",
    "Sara Ali",
    "Dev Kapoor",
    "Isha Dutta",
]
ROLES = ["backend engineer", "data analyst", "platform developer", "QA engineer"]
COMPANIES = ["Northstar Labs", "Pine Systems", "MapleWorks", "Riverline Tech"]
TOOLS = [
    ("Python", "FastAPI", "PostgreSQL 15"),
    ("Java", "Spring Boot 3.1", "MySQL 8"),
    ("Go", "Kafka 3.6", "Redis 7"),
    ("TypeScript", "Node.js 20", "MongoDB 7"),
]
QUIRKS = [
    "Frontend is not really my thing, though I can fix a React form when needed.",
    "The first version was rough and we rewrote the retry part after an outage.",
    "I still keep a small checklist because I once missed a timezone bug.",
    "Most of this was maintenance work, not a greenfield build.",
]


def human_text(index: int, rng: random.Random) -> str:
    name = NAMES[index % len(NAMES)]
    role = rng.choice(ROLES)
    company = rng.choice(COMPANIES)
    tool, framework, database = rng.choice(TOOLS)
    customers = rng.randint(11, 47)
    minutes = rng.randint(34, 58)
    team = rng.randint(4, 8)
    return (
        f"{name}\n{role.title()}\n\n"
        f"I have worked mostly with {tool}, plus {framework} when the project called for it.\n"
        f"At {company}, I moved a nightly import into smaller jobs; it used to take about three "
        f"hours and now usually finishes in {minutes} minutes.\n"
        f"Fixed the duplicate retry path after it affected {customers} customer records. That one "
        f"needed a data repair script and a slightly awkward Monday call.\n"
        f"On a team of {team}, I reviewed changes and looked after {database}.\n"
        f"{rng.choice(QUIRKS)}\n"
        "Side project: a bus reminder bot for my family. It runs on an old mini PC."
    )


def polished_text(index: int, rng: random.Random) -> str:
    name = NAMES[index % len(NAMES)]
    role = rng.choice(ROLES)
    company = rng.choice(COMPANIES)
    tool, framework, database = rng.choice(TOOLS)
    latency = rng.choice([18, 22, 27, 33])
    incidents = rng.choice([21, 29, 36, 43])
    return (
        f"{name}\n{role.title()}\n\n"
        f"{role.title()} focused on dependable services and clear collaboration.\n"
        f"Built {tool} services with {framework} and {database}, improving median response time by {latency}%.\n"
        f"Reworked deployment checks at {company}, reducing repeat incidents by {incidents}%.\n"
        "Partnered with product and support teams to translate recurring customer issues into engineering work.\n"
        "Created runbooks for on-call handoffs and coached two graduate developers through their first releases.\n"
        "Improved test coverage around payment and notification workflows while keeping changes easy to review."
    )


def generated_text(index: int, rng: random.Random) -> str:
    name = NAMES[index % len(NAMES)]
    role = rng.choice(ROLES)
    company = rng.choice(COMPANIES)
    gains = rng.sample([20, 25, 30, 35, 40, 45, 50], 4)
    return (
        f"{name}\n{role.title()}\n\n"
        "Results-driven and highly motivated professional with a proven track record of delivering "
        "innovative, cutting-edge solutions in dynamic environments.\n"
        f"Spearheaded strategic initiatives at {company}, streamlining robust workflows and increasing efficiency by {gains[0]}%.\n"
        f"Leveraged best-in-class technologies to foster seamless collaboration and improve productivity by {gains[1]}%.\n"
        f"Orchestrated cross-functional stakeholders, championed agile practices, and elevated delivery quality by {gains[2]}%.\n"
        f"Pioneered scalable solutions that optimized performance, enhanced reliability, and reduced costs by {gains[3]}%.\n"
        "Facilitated a world-class culture of synergy, continuous improvement, and exceptional value-add outcomes."
    )


BUILDERS = {
    "human": human_text,
    "ai_polished": polished_text,
    "ai_generated": generated_text,
}


def build_style_set(seed: int = 20261008, count_per_class: int = 15) -> dict[str, object]:
    if not 10 <= count_per_class <= 16:
        raise ValueError("count_per_class must produce a total between 30 and 50")
    records = []
    for label, builder in BUILDERS.items():
        for index in range(count_per_class):
            item_seed = seed + index + list(BUILDERS).index(label) * 10_000
            text = builder(index, random.Random(item_seed))
            records.append({"id": f"{label}-{index + 1:02d}", "label": label, "text": text})
    random.Random(seed).shuffle(records)
    return {
        "metadata": {
            "seed": seed,
            "count": len(records),
            "count_per_class": count_per_class,
            "construction": "Deterministic templates and rules; no LLM generated or assigned labels.",
        },
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a deterministic template-written style set with labels assigned by construction."
    )
    parser.add_argument("--seed", type=int, default=20261008)
    parser.add_argument("--count-per-class", type=int, default=15)
    parser.add_argument("--output", type=Path, default=Path("eval/style_set.json"))
    args = parser.parse_args()
    dataset = build_style_set(args.seed, args.count_per_class)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dataset, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {dataset['metadata']['count']} records to {args.output}")


if __name__ == "__main__":
    main()
