from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any, Mapping, Protocol

import httpx

from firewall.models import JobRequirements


SKILL_TAXONOMY: dict[str, tuple[str, ...]] = {
    "AWS": ("aws", "amazon web services"),
    "Azure": ("azure",),
    "C#": ("c#", "c sharp"),
    "C++": ("c++",),
    "Django": ("django",),
    "Docker": ("docker", "containers"),
    "FastAPI": ("fastapi",),
    "GCP": ("gcp", "google cloud"),
    "Go": ("golang", "go language"),
    "GraphQL": ("graphql",),
    "Java": ("java",),
    "JavaScript": ("javascript",),
    "Kafka": ("kafka",),
    "Kotlin": ("kotlin",),
    "Kubernetes": ("kubernetes", "k8s"),
    "Machine Learning": ("machine learning", "ml models"),
    "MongoDB": ("mongodb",),
    "Node.js": ("node.js", "nodejs"),
    "PostgreSQL": ("postgresql", "postgres"),
    "Python": ("python",),
    "React": ("react", "react.js", "reactjs"),
    "Redis": ("redis",),
    "Ruby": ("ruby",),
    "Rust": ("rust",),
    "SQL": ("sql",),
    "Terraform": ("terraform",),
    "TypeScript": ("typescript",),
}

_NICE_MARKERS = (
    "bonus",
    "desired",
    "nice to have",
    "nice-to-have",
    "preferred",
    "plus",
)
_REQUIRED_MARKERS = (
    "basic qualification",
    "minimum qualification",
    "must have",
    "must-have",
    "required",
    "requirements",
    "what you need",
)
_YEARS_RE = re.compile(
    r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:[-\u2013\u2014]\s*\d+(?:\.\d+)?)?\s*\+?\s*(?:years?|yrs?)\b",
    re.IGNORECASE,
)


class HTTPTransport(Protocol):
    def get(self, url: str, **kwargs: Any) -> httpx.Response: ...


class _TextExtractor(HTMLParser):
    _BLOCK_TAGS = {"br", "div", "h1", "h2", "h3", "h4", "li", "ol", "p", "section", "ul"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _html_to_lines(value: str) -> list[str]:
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    text = "".join(parser.parts)
    return [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]


def _description_parts(job: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("content", "description", "descriptionPlain", "additionalPlain"):
        value = job.get(key)
        if isinstance(value, str):
            values.append(value)

    lists = job.get("lists")
    if isinstance(lists, list):
        for section in lists:
            if not isinstance(section, Mapping):
                continue
            heading = section.get("text")
            if isinstance(heading, str):
                values.append(f"<h3>{heading}</h3>")
            items = section.get("content")
            if isinstance(items, str):
                values.append(items)
            elif isinstance(items, list):
                values.extend(str(item) for item in items)
    return values


def _contains_alias(line: str, alias: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", line, re.IGNORECASE) is not None


def to_job_requirements(job: Mapping[str, Any]) -> JobRequirements:

    lines: list[str] = []
    for part in _description_parts(job):
        lines.extend(_html_to_lines(part))

    required: set[str] = set()
    nice: set[str] = set()
    section = "required"
    for line in lines:
        lower = line.casefold()
        if any(marker in lower for marker in _NICE_MARKERS):
            section = "nice"
        elif any(marker in lower for marker in _REQUIRED_MARKERS):
            section = "required"

        for canonical, aliases in SKILL_TAXONOMY.items():
            if any(_contains_alias(line, alias) for alias in aliases):
                (nice if section == "nice" else required).add(canonical)

    nice.difference_update(required)
    year_values = [float(match.group(1)) for line in lines for match in _YEARS_RE.finditer(line)]
    return JobRequirements(
        must_have_skills=sorted(required),
        nice_to_have=sorted(nice),
        min_years=max(year_values, default=0.0),
    )


class GreenhouseJobBoardClient:

    def __init__(
        self,
        board_token: str,
        *,
        transport: HTTPTransport | None = None,
        base_url: str = "https://boards-api.greenhouse.io/v1/boards",
        timeout: float = 10.0,
    ) -> None:
        self.board_token = board_token
        self.transport = transport or httpx.Client()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def list_jobs(self) -> list[dict[str, Any]]:
        response = self.transport.get(
            f"{self.base_url}/{self.board_token}/jobs",
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        return list(payload.get("jobs", []))

    def get_job(self, job_id: str | int) -> dict[str, Any]:
        response = self.transport.get(
            f"{self.base_url}/{self.board_token}/jobs/{job_id}",
            params={"questions": "true"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return dict(response.json())


class LeverJobBoardClient:

    def __init__(
        self,
        company: str,
        *,
        transport: HTTPTransport | None = None,
        base_url: str = "https://api.lever.co/v0/postings",
        timeout: float = 10.0,
    ) -> None:
        self.company = company
        self.transport = transport or httpx.Client()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def list_jobs(self) -> list[dict[str, Any]]:
        response = self.transport.get(
            f"{self.base_url}/{self.company}",
            params={"mode": "json"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return list(response.json())

    def get_job(self, job_id: str | int) -> dict[str, Any]:
        response = self.transport.get(
            f"{self.base_url}/{self.company}/{job_id}",
            params={"mode": "json"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return dict(response.json())


GreenhouseBoardClient = GreenhouseJobBoardClient
LeverBoardClient = LeverJobBoardClient
