from __future__ import annotations

from collections.abc import Collection
from typing import Any

from firewall.models import Application


class IgnoringStore:
    def __init__(self, inner: Any, ignored_ids: Collection[str]) -> None:
        self._inner = inner
        self._ignored = frozenset(ignored_ids)

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._inner, name)
        if not callable(attribute):
            return attribute

        def call(*arguments: Any, **keywords: Any) -> Any:
            result = attribute(*arguments, **keywords)
            if isinstance(result, tuple) and all(isinstance(item, Application) for item in result):
                return tuple(item for item in result if item.application_id not in self._ignored)
            return result

        return call
