from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any
from urllib.request import Request, urlopen


Transport = Callable[[Request, float], bytes]


def _urllib_transport(request: Request, timeout: float) -> bytes:
    if request.type not in {"http", "https"}:
        raise ValueError("Ollama URL must use http or https")
    with urlopen(request, timeout=timeout) as response:
        body = response.read(65_537)
    if len(body) > 65_536:
        raise ValueError("Ollama response exceeded 64 KiB")
    return body


class OllamaClient:
    def __init__(self, transport: Transport | None = None) -> None:
        self._transport = transport or _urllib_transport
        self._base_url = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        self._model = os.getenv("FIREWALL_LLM_MODEL", "qwen2.5:7b-instruct")
        self._timeout = 5.0

    def _generate(self, prompt: str) -> str:
        payload = json.dumps(
            {"model": self._model, "prompt": prompt, "stream": False},
            separators=(",", ":"),
        ).encode()
        request = Request(
            f"{self._base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        decoded: Any = json.loads(self._transport(request, self._timeout))
        if not isinstance(decoded, dict) or not isinstance(decoded.get("response"), str):
            raise ValueError("Ollama response did not contain text")
        response = decoded["response"].strip()
        if not response:
            raise ValueError("Ollama returned empty text")
        return response

    def rewrite_summary(self, reason_codes: list[str], skill_names: list[str]) -> str:
        prompt = (
            "Write one concise recruiter-facing sentence based only on these JSON fields. "
            "Do not infer identity, fraud, score, or route.\n"
            + json.dumps(
                {"reason_codes": reason_codes, "skill_names": skill_names},
                separators=(",", ":"),
            )
        )
        return self._generate(prompt)

    def suggest_skill_canonicalizations(self, skill_names: list[str]) -> list[str]:
        prompt = (
            "Return only a JSON list of canonical skill-name suggestions for this JSON field.\n"
            + json.dumps({"skill_names": skill_names}, separators=(",", ":"))
        )
        suggestions: Any = json.loads(self._generate(prompt))
        if not isinstance(suggestions, list) or not all(isinstance(item, str) for item in suggestions):
            raise ValueError("Ollama suggestions were not a string list")
        return suggestions
