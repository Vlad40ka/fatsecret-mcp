"""Pace all calls from one FatSecret client and stop sending during an upstream throttle.

The pacing and pauses are this application's policy, not FatSecret's undocumented thresholds, which
refused about 38 requests in 50 seconds. Requests start at most one a second. Up to ten are held in
reserve and one more is earned every three seconds, so one search stays quick; once the reserve is
used, a request waits for its turn, and the server sustains at most twenty a minute. After error
12 (too many actions) nothing is sent for thirty seconds; after error 11 (the daily quota is used
up) nothing is sent for ten minutes, since FatSecret doesn't say when its day starts. The request
holds its slot until the response is checked, so queued calls see a pause before they send
anything. Waiting is cancellable, and no operation is retried here.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from time import monotonic

import anyio

from fatsecret_mcp.fatsecret.errors import DailyQuotaExceeded, TooManyActions, not_sent
from fatsecret_mcp.limits import TokenBucket

REQUEST_INTERVAL_SECONDS = 1.0
BURST_REQUESTS = 10
REFILL_SECONDS = 3.0
THROTTLE_COOLDOWN_SECONDS = 30.0
QUOTA_PAUSE_SECONDS = 600.0


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
        self._bucket = TokenBucket(
            capacity=BURST_REQUESTS,
            refill_seconds=REFILL_SECONDS,
        )
        self._paused_until = 0.0
        self._pause_error: type[TooManyActions | DailyQuotaExceeded] = TooManyActions
        self._lock = anyio.Lock()

    @asynccontextmanager
    async def request(self) -> AsyncGenerator[None]:
        """One paced request, or the pausing error at once, unsent, while a pause lasts."""
        async with self._lock:
            now = self._now()

            if now < self._paused_until:
                raise not_sent(self._pause_error())

            wait = max(self._interval_wait(now), self._bucket.wait(now))

            if wait > 0:
                await self._sleep(wait)

            self._last_started = self._now()

            self._bucket.take(self._last_started)

            try:
                yield
            except (TooManyActions, DailyQuotaExceeded) as error:
                pause = self._cooldown if isinstance(error, TooManyActions) else QUOTA_PAUSE_SECONDS
                self._paused_until = self._now() + pause
                self._pause_error = type(error)

                raise

    def _interval_wait(self, now: float) -> float:
        if self._last_started is None:
            return 0.0

        return self._last_started + self._interval - now
