"""Simple sliding-window rate limits kept in memory (FR-68, FR-72).

Good for our single API process. Each key keeps at most `max_events`
timestamps, so an attacker hammering one key cannot fill the memory.
With several API processes this would move to the database or Redis;
the interface would stay the same.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque


class SlidingWindow:
    def __init__(
        self, limit: int, window_s: float, clock=time.monotonic, max_events: int | None = None
    ) -> None:
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        size = max_events or limit
        self._events: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=size))

    def _trim(self, key: str) -> deque[float]:
        events = self._events.get(key)
        if events is None:
            return deque()  # unknown key: nothing stored, nothing to remember
        cutoff = self._clock() - self.window_s
        while events and events[0] <= cutoff:
            events.popleft()
        if not events:
            del self._events[key]  # forget quiet keys, so the dict does not grow forever
        return events

    def blocked(self, key: str) -> bool:
        return len(self._trim(key)) >= self.limit

    def hit(self, key: str) -> None:
        self._trim(key)
        self._events[key].append(self._clock())

    def retry_after(self, key: str) -> int:
        events = self._trim(key)
        if not events:
            return 0
        return max(1, int(events[0] + self.window_s - self._clock()) + 1)

    def reset(self, key: str) -> None:
        self._events.pop(key, None)

    def clear(self) -> None:
        self._events.clear()
