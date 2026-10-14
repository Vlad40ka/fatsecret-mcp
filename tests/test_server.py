"""The server releases cached Food Entries without needing another tool call."""

from __future__ import annotations

import gc
import weakref
from collections.abc import Callable
from datetime import date, timedelta

import anyio
import pytest
from mcp import Client

from fatsecret_mcp.config.models import Household
from fatsecret_mcp.domain.models import FoodEntry, Meal
from fatsecret_mcp.fatsecret import diary as diary_module
from fatsecret_mcp.fatsecret.gateway import AccessToken
from fatsecret_mcp.server import build_server
from tests.conftest import FakeClock
from tests.fatsecret.fake import FakeFatSecret, food_entry

pytestmark = pytest.mark.anyio
DAY = date(2026, 10, 13)
FAST_TTL = timedelta(milliseconds=25)
Connect = Callable[[str], Client]


async def cache_day(
    client: Client,
    fatsecret: FakeFatSecret,
    user: AccessToken,
    *,
    refresh: bool = False,
) -> weakref.ReferenceType[FoodEntry]:
    entry = food_entry("Skyr", Meal.SNACKS, DAY, kcal=100)
    reference = weakref.ref(entry)

    fatsecret.add_food_entry(user, entry)
    result = await client.call_tool("get_diary_day", {"diary_day": DAY.isoformat(), "refresh": refresh})

    assert not result.is_error

    # Leave the server's cache as the only owner of the original Food Entry.
    fatsecret.delete_food_entries(user, DAY)

    return reference


async def wait_until_collected(reference: weakref.ReferenceType[FoodEntry]) -> None:
    with anyio.move_on_after(2):
        while reference() is not None:
            gc.collect()
            await anyio.sleep(0.001)

    assert reference() is None, "The server still retains an expired Food Entry."


async def test_an_idle_server_releases_expired_entries_without_another_request(
    connect: Connect,
    fatsecret: FakeFatSecret,
    household: Household,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(diary_module, "PAST_DAY_CACHE_TTL", FAST_TTL)

    async with connect("jakub") as client:
        reference = await cache_day(client, fatsecret, household.user("jakub").fatsecret)

        assert reference() is not None

        clock.now += timedelta(hours=25)
        await wait_until_collected(reference)

        assert fatsecret.requests == 1


async def test_stopping_the_server_releases_unexpired_entries_even_if_the_server_is_reachable(
    fatsecret: FakeFatSecret, household: Household, clock: FakeClock
) -> None:
    server = build_server(household.user("jakub"), fatsecret, clock)

    async with Client(server) as client:
        reference = await cache_day(client, fatsecret, household.user("jakub").fatsecret)

        assert reference() is not None

    gc.collect()

    assert reference() is None
    assert fatsecret.requests == 1


async def test_an_old_deadline_does_not_remove_a_refreshed_day(
    connect: Connect,
    fatsecret: FakeFatSecret,
    household: Household,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(diary_module, "PAST_DAY_CACHE_TTL", FAST_TTL)
    user = household.user("jakub").fatsecret

    async with connect("jakub") as client:
        old = await cache_day(client, fatsecret, user)
        clock.now += FAST_TTL
        fresh = await cache_day(client, fatsecret, user, refresh=True)

        await anyio.sleep(FAST_TTL.total_seconds() * 2)
        gc.collect()

        assert old() is None
        assert fresh() is not None

        clock.now += FAST_TTL
        await wait_until_collected(fresh)

        assert fatsecret.requests == 2


async def test_a_slow_read_does_not_prevent_expired_entries_from_being_released(
    connect: Connect,
    fatsecret: FakeFatSecret,
    household: Household,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(diary_module, "PAST_DAY_CACHE_TTL", FAST_TTL)
    reading = anyio.Event()
    release = anyio.Event()
    list_entries = fatsecret.list_food_entries

    async def blocked_read(user: AccessToken, day: date) -> list[FoodEntry]:
        reading.set()
        await release.wait()

        return await list_entries(user, day)

    async with connect("jakub") as client:
        reference = await cache_day(client, fatsecret, household.user("jakub").fatsecret)

        monkeypatch.setattr(fatsecret, "list_food_entries", blocked_read)

        async def read_today() -> None:
            result = await client.call_tool("get_diary_day", {})

            assert not result.is_error

        with anyio.fail_after(3):
            async with anyio.create_task_group() as group:
                group.start_soon(read_today)
                await reading.wait()

                clock.now += timedelta(hours=25)
                await wait_until_collected(reference)
                release.set()

        assert fatsecret.requests == 2
