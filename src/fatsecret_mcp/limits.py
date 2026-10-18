"""A count of recent events, shared by the server's in-memory limits."""

from __future__ import annotations

import math
from collections import deque


class RollingWindow:
    """At most `limit` events in any `seconds`; each event leaves the window `seconds` after it.

    Refused events aren't counted, so refusals never extend the wait.
    """

    def __init__(self, limit: int, seconds: float) -> None:
        self._limit = limit
        self._seconds = seconds
        self._events: deque[float] = deque()

    def admit(self, now: float) -> float | None:
        """Count an event at `now`, or the seconds until the oldest leaves the window when it's full."""
        while self._events and self._events[0] <= now - self._seconds:
            self._events.popleft()

        if len(self._events) >= self._limit:
            return self._events[0] + self._seconds - now

        self._events.append(now)

        return None

    def retry_after(self, now: float) -> int | None:
        """Count an event at `now`, or the whole seconds to wait, for a `Retry-After` header."""
        wait = self.admit(now)

        if wait is None:
            return None

        return math.ceil(wait)

    def forget(self, event: float) -> None:
        """Stop counting an event admitted at `event`, as if it hadn't happened."""
        self._events.remove(event)
