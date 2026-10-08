from __future__ import annotations

import json
import math
import os
from collections.abc import Callable
from typing import Any
from urllib.request import Request, urlopen


Transport = Callable[[Request, float], bytes]
MAX_EMBED_TEXT_BYTES = 16_384
MAX_EMBED_DIMENSIONS = 8_192


def _urllib_transport(request: Request, timeout: float) -> bytes:
    if request.type not in {"http", "https"}:
        raise ValueError("Ollama URL must use http or https")
    with urlopen(request, timeout=timeout) as response:
        body = response.read(65_537)
    if len(body) > 65_536:
        raise ValueError("Ollama response exceeded 64 KiB")
    return body


class OllamaClient:
    def __init__(self, transport: Transport | None = None, timeout: float = 5.0) -> None:
        self._transport = transport or _urllib_transport
        self._base_url = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        self._model = os.getenv("FIREWALL_LLM_MODEL", "qwen2.5:7b-instruct")
        self._timeout = timeout

    def _generate(
        self,
        prompt: str,
        response_format: dict[str, Any] | str | None = None,
        temperature: float | None = None,
    ) -> str:
        values: dict[str, Any] = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": "10m",
        }
        if response_format is not None:
            values["format"] = response_format
        if temperature is not None:
            values["options"] = {"temperature": temperature}
        payload = json.dumps(values, separators=(",", ":")).encode()
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

    def generate_json(self, prompt: str, schema: dict[str, Any]) -> Any:
        return json.loads(self._generate(prompt, response_format=schema, temperature=0))

    def embed(self, text: str) -> tuple[float, ...] | None:
        encoded = text.encode()[:MAX_EMBED_TEXT_BYTES]
        bounded_text = encoded.decode(errors="ignore")
        if not bounded_text.strip():
            return None
        requests = (
            (
                "/api/embed",
                {
                    "model": "nomic-embed-text",
                    "input": bounded_text,
                    "truncate": True,
                    "keep_alive": "10m",
                },
                "embeddings",
            ),
            (
                "/api/embeddings",
                {
                    "model": "nomic-embed-text",
                    "prompt": bounded_text,
                    "keep_alive": "10m",
                },
                "embedding",
            ),
        )
        for path, values, key in requests:
            try:
                payload = json.dumps(values, separators=(",", ":")).encode()
                request = Request(
                    f"{self._base_url}{path}",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                decoded: Any = json.loads(self._transport(request, min(self._timeout, 3.0)))
                vector = decoded.get(key) if isinstance(decoded, dict) else None
                if key == "embeddings" and isinstance(vector, list) and len(vector) == 1:
                    vector = vector[0]
                if not isinstance(vector, list) or not 0 < len(vector) <= MAX_EMBED_DIMENSIONS:
                    continue
                if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in vector):
                    continue
                result = tuple(float(value) for value in vector)
                if all(math.isfinite(value) for value in result):
                    return result
            except Exception:
                continue
        return None

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
