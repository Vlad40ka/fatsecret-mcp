"""Foods fetched with their Servings, kept in memory and shared by every tool and every User.

Catalog data isn't per User, so a Food one User fetched saves the others a request. A cached Food
costs nobody's daily budget; fetching it costs the budget of the User whose call fetched it. A Food
leaves the cache an hour after it was fetched, well within FatSecret's 24-hour storage rule, even
while the server is idle. Reading it doesn't extend that.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import anyio

from fatsecret_mcp.domain.diary_day import Clock
from fatsecret_mcp.domain.models import Food
from fatsecret_mcp.fatsecret.gateway import FatSecretGateway

FOOD_CACHE_TTL = timedelta(hours=1)


class FoodCache:
    def __init__(self, *, clock: Clock) -> None:
        self._clock = clock
        self._foods: dict[str, tuple[datetime, Food]] = {}
        self._fetched = anyio.Event()

    @asynccontextmanager
    async def lifespan(self) -> AsyncGenerator[None]:
        """Release expired Foods while idle and forget every Food when the server stops."""
        async with anyio.create_task_group() as group:
            group.start_soon(self._expire_while_idle)

            try:
                yield
            finally:
                group.cancel_scope.cancel()
                self._foods.clear()

    async def get_food(self, food_id: str, fatsecret: FatSecretGateway) -> Food:
        """The cached Food, or the Food fetched through `fatsecret`, the calling User's gateway."""
        self._drop_expired()
        cached = self._foods.get(food_id)

        if cached is not None:
            _, food = cached

            return food

        food = await fatsecret.get_food(food_id)

        self._foods[food_id] = (self._clock(), food)
        self._fetched.set()

        return food

    def _drop_expired(self) -> None:
        now = self._clock()
        oldest = now - FOOD_CACHE_TTL

        for food_id, (fetched_at, _) in list(self._foods.items()):
            if fetched_at <= oldest or fetched_at > now:
                del self._foods[food_id]

    async def _expire_while_idle(self) -> None:
        """Sleep until the oldest Food expires or, while the cache is empty, until a Food is fetched."""
        while True:
            self._fetched = anyio.Event()
            fetched = self._fetched

            self._drop_expired()
            delay = self._seconds_until_expiry()

            if delay is None:
                await fetched.wait()
            else:
                await anyio.sleep(delay)

    def _seconds_until_expiry(self) -> float | None:
        if not self._foods:
            return None

        fetched_times = [fetched_at for fetched_at, _ in self._foods.values()]
        oldest_expiry = min(fetched_times) + FOOD_CACHE_TTL

        return (oldest_expiry - self._clock()).total_seconds()
