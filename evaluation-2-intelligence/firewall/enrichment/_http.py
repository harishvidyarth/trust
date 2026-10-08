from __future__ import annotations

from typing import Any

import httpx


USER_AGENT = "AI-Application-Firewall/1.0 (consent-based verification)"


class HttpConnector:
    def __init__(self, transport: Any | None = None, timeout: float = 3.0) -> None:
        if isinstance(transport, httpx.Client):
            self._client = transport
        else:
            self._client = httpx.Client(transport=transport) if transport is not None else httpx.Client()
        self.timeout = timeout

    def _get(self, url: str, **kwargs: Any) -> httpx.Response | None:
        headers = dict(kwargs.pop("headers", {}))
        headers.setdefault("User-Agent", USER_AGENT)
        try:
            return self._client.get(url, headers=headers, timeout=self.timeout, **kwargs)
        except (httpx.HTTPError, OSError, ValueError):
            return None

    @staticmethod
    def _json(response: httpx.Response) -> Any | None:
        try:
            return response.json()
        except (ValueError, TypeError):
            return None
