"""Load one User's Food Diary only when needed, sharing past days across all callers.

Past entries stay in memory for at most twelve hours from the upstream read. Today is always fresh.
Explicit refresh and write invalidation discard earlier reads, including those still in flight.
Uncached period summaries use month totals rather than fetching every day's entries.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import anyio

from fatsecret_mcp.domain.day_summary import total_nutrition
from fatsecret_mcp.domain.diary_day import Clock, today
from fatsecret_mcp.domain.models import FoodEntry, Nutrition, RecentFood
from fatsecret_mcp.domain.recent_foods import recent_diary_days, recent_foods
from fatsecret_mcp.fatsecret.errors import FatSecretError, InvalidAccessToken
from fatsecret_mcp.fatsecret.gateway import AccessToken, FatSecretGateway

PAST_DAY_CACHE_TTL = timedelta(hours=12)


class UserDiary:
    def __init__(
        self,
        *,
        fatsecret: FatSecretGateway,
        user: AccessToken,
        timezone: ZoneInfo,
        clock: Clock,
        recent_food_days: int,
    ) -> None:
        self._fatsecret = fatsecret
        self._user = user
        self._timezone = timezone
        self._clock = clock
        self._recent_food_days = recent_food_days
        self._past_days: dict[date, tuple[datetime, tuple[FoodEntry, ...]]] = {}
        self._lock = anyio.Lock()
        self._cache_changed = anyio.Event()

    @asynccontextmanager
    async def cache_lifespan(self) -> AsyncGenerator[None]:
        """Release expired entries while idle and clear all cached entries when the server stops."""
        async with anyio.create_task_group() as group:
            group.start_soon(self._expire_cached_days)

            try:
                yield
            finally:
                group.cancel_scope.cancel()
                self._past_days.clear()

    async def entries(self, day: date, *, refresh: bool = False) -> list[FoodEntry]:
        """Read one day, reusing a past day's entries unless an explicit refresh is requested."""
        async with self._lock:
            self._drop_expired_days()
            cached = self._cached_entries(day)

            if cached is not None and not refresh:
                return cached

            return await self._read_day(day)

    async def recent_foods(self, *, refresh: bool = False) -> list[RecentFood]:
        """Recent Foods from this User's history, sharing the scan across all callers."""
        async with self._lock:
            days = self._history_days()

            self._drop_expired_days()

            if refresh:
                self._discard_days(days)

            entries = await self._recent_entries(days)

        return recent_foods(entries)

    async def period_totals(self, days: list[date], *, refresh: bool = False) -> dict[date, Nutrition]:
        """Use cached entries where available and month totals for the remaining Diary Days."""
        async with self._lock:
            self._drop_expired_days()

            if refresh:
                self._discard_days(days)

            totals: dict[date, Nutrition] = {}
            missing_days: list[date] = []

            for day in days:
                entries = self._cached_entries(day)

                if entries is None:
                    missing_days.append(day)
                elif entries:
                    totals[day] = total_nutrition(entries)

            totals.update(await self._missing_totals(missing_days))

            return totals

    async def invalidate(self, day: date) -> None:
        """Discard a day's history after a successful write, including any earlier in-flight read."""
        async with self._lock:
            self._past_days.pop(day, None)

    def _history_days(self) -> list[date]:
        current_day = today(self._clock(), self._timezone)

        return recent_diary_days(current_day, self._recent_food_days)

    def _drop_expired_days(self) -> None:
        now = self._clock()
        oldest = now - PAST_DAY_CACHE_TTL
        current_day = today(now, self._timezone)

        for day, (fetched_at, _) in list(self._past_days.items()):
            if fetched_at <= oldest or fetched_at > now or day >= current_day:
                del self._past_days[day]

    async def _expire_cached_days(self) -> None:
        while True:
            self._cache_changed = anyio.Event()
            changed = self._cache_changed

            # Synchronous eviction must not wait behind a lock held during upstream I/O.
            self._drop_expired_days()
            delay = self._seconds_until_expiry()

            if delay is None:
                await changed.wait()
            else:
                with anyio.move_on_after(delay):
                    await changed.wait()

    def _seconds_until_expiry(self) -> float | None:
        if not self._past_days:
            return None

        fetched_times = [fetched_at for fetched_at, _ in self._past_days.values()]
        earliest_expiry = min(fetched_times) + PAST_DAY_CACHE_TTL
        remaining = (earliest_expiry - self._clock()).total_seconds()

        return max(0, remaining)

    def _discard_days(self, days: list[date]) -> None:
        for day in days:
            self._past_days.pop(day, None)

    def _cached_entries(self, day: date) -> list[FoodEntry] | None:
        cached = self._past_days.get(day)

        if cached is None:
            return None

        _, entries = cached

        return list(entries)

    async def _read_day(self, day: date) -> list[FoodEntry]:
        self._past_days.pop(day, None)

        try:
            entries = await self._fatsecret.list_food_entries(self._user, day)
        except InvalidAccessToken:
            self._past_days.clear()

            raise

        fetched_at = self._clock()

        if day < today(fetched_at, self._timezone):
            self._past_days[day] = (fetched_at, tuple(entries))
            self._cache_changed.set()

        return entries

    async def _recent_entries(self, days: list[date]) -> list[FoodEntry]:
        entries_by_day: dict[date, list[FoodEntry]] = {}

        for day in days:
            cached_entries = self._cached_entries(day)

            if cached_entries is not None:
                entries_by_day[day] = cached_entries

        missing_days = [day for day in days if day not in entries_by_day]
        # Collect gateway errors so the task group doesn't turn them into an ExceptionGroup.
        failures: list[FatSecretError] = []

        async def read_day(day: date) -> None:
            try:
                entries_by_day[day] = await self._read_day(day)
            except FatSecretError as error:
                failures.append(error)
                group.cancel_scope.cancel()

        async with anyio.create_task_group() as group:
            for day in missing_days:
                group.start_soon(read_day, day)

        if failures:
            raise failures[0]

        entries: list[FoodEntry] = []

        for day in days:
            entries += entries_by_day[day]

        return entries

    async def _missing_totals(self, days: list[date]) -> dict[date, Nutrition]:
        month_starts = [day.replace(day=1) for day in days]
        months = dict.fromkeys(month_starts)
        month_totals: dict[date, Nutrition] = {}

        try:
            for month in months:
                month_totals.update(await self._fatsecret.get_month_totals(self._user, month))
        except InvalidAccessToken:
            self._past_days.clear()

            raise

        totals: dict[date, Nutrition] = {}

        for day in days:
            nutrition = month_totals.get(day)

            if nutrition is None:
                continue
            if nutrition != Nutrition():
                totals[day] = nutrition
                continue

            entries = await self._read_day(day)

            if entries:
                totals[day] = total_nutrition(entries)

        return totals
