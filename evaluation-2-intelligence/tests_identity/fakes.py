from __future__ import annotations

import threading

from firewall.redis_layer.cache import JsonCache


class Clock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Counter:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.entries: dict[str, tuple[float, int]] = {}
        self.lock = threading.Lock()

    def hit(self, key: str, window_s: int) -> int:
        with self.lock:
            expires, count = self.entries.get(key, (0.0, 0))
            if expires <= self.clock():
                expires, count = self.clock() + window_s, 0
            count += 1
            self.entries[key] = (expires, count)
            return count


class Recorder(JsonCache):
    def __init__(self) -> None:
        super().__init__(max_entries=10000)
        self.written: list[object] = []

    def set(self, source, value, ttl_s=None):
        self.written.append(value)
        return super().set(source, value, ttl_s)


class Audit:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, str]] = []

    def append(self, event, actor, target="", ip="", detail=None):
        self.events.append((event, actor, target))

    def __call__(self):
        return self


class FakeAsr:
    def __init__(self, text: str = "", fail: bool = False) -> None:
        self.text = text
        self.fail = fail
        self.calls = 0

    def transcribe(self, samples, sample_rate):
        self.calls += 1
        if self.fail:
            raise RuntimeError("no model")
        return self.text


class FakeMatcher:
    def __init__(self, state: str = "match", similarity: float | None = 0.61, ready: bool = True) -> None:
        self.state = state
        self.similarity = similarity
        self.ready = ready
        self.calls: list[tuple[bytes, list[bytes]]] = []
        self.lock = threading.Lock()

    def available(self) -> bool:
        return self.ready

    def compare(self, id_image_bytes, live_images):
        with self.lock:
            self.calls.append((id_image_bytes, list(live_images)))
        if id_image_bytes == b"unreadable":
            return {"state": "unreadable", "similarity": None, "threshold": 0.363, "model": "sface-2021dec", "frames_checked": 0}
        if not self.ready:
            return {"state": "not_available", "similarity": None, "threshold": 0.363, "model": "sface-2021dec", "frames_checked": 0}
        return {"state": self.state, "similarity": self.similarity, "threshold": 0.363, "model": "sface-2021dec", "frames_checked": len(live_images)}
