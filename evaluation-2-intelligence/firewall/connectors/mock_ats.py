from __future__ import annotations

from collections import OrderedDict
from threading import RLock

from firewall.models import Application


class MockATS:
    def __init__(self) -> None:
        self._applications: OrderedDict[str, Application] = OrderedDict()
        self._lock = RLock()

    def receive(self, application: Application) -> None:
        with self._lock:
            self._applications[application.application_id] = application

    def applications(self) -> tuple[Application, ...]:
        with self._lock:
            return tuple(self._applications.values())

    def clear(self) -> None:
        with self._lock:
            self._applications.clear()
