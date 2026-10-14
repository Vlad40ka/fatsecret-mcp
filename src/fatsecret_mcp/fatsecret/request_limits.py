"""Pace all calls from one FatSecret client and stop sending during an upstream throttle.

The interval and cooldown are this application's policy, not FatSecret's undocumented thresholds.
The request holds its slot until the response is checked, so queued calls see a throttle before
they send anything. Waiting is cancellable, and no operation is retried here.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from time import monotonic

import anyio

from fatsecret_mcp.fatsecret.errors import TooManyActions

REQUEST_INTERVAL_SECONDS = 1.0
THROTTLE_COOLDOWN_SECONDS = 30.0


class RequestLimits:
    def __init__(
        self,
        *,
        interval: float = REQUEST_INTERVAL_SECONDS,
        cooldown: float = THROTTLE_COOLDOWN_SECONDS,
        now: Callable[[], float] = monotonic,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    ) -> None:
        if interval < 0 or cooldown <= 0:
            raise ValueError("Request interval must be non-negative and cooldown must be positive.")

        self._interval = interval
        self._cooldown = cooldown
        self._now = now
        self._sleep = sleep
        self._last_started: float | None = None
        self._blocked_until = 0.0
        self._lock = anyio.Lock()

    @asynccontextmanager
    async def request(self) -> AsyncGenerator[None]:
        """One paced request, or error 12 immediately if the upstream cooldown hasn't elapsed."""
        async with self._lock:
            now = self._now()

            if now < self._blocked_until:
                raise TooManyActions()

            if self._last_started is not None:
                wait = self._last_started + self._interval - now

                if wait > 0:
                    await self._sleep(wait)

            self._last_started = self._now()

            try:
                yield
            except TooManyActions:
                self._blocked_until = self._now() + self._cooldown

                raise
