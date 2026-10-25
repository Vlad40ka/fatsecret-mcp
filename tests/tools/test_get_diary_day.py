"""get_diary_day: a User asks what they ate on a Diary Day and how much of their Daily Targets is left."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from mcp import Client

from fatsecret_mcp.config.models import Household
from fatsecret_mcp.domain.models import Meal
from fatsecret_mcp.fatsecret.errors import (
    CredentialsRejected,
    DailyQuotaExceeded,
    FatSecretError,
    InvalidAccessToken,
    IpAddressRejected,
    NotFound,
    UpstreamFailure,
)
from tests.conftest import FakeClock
from tests.fatsecret.fake import FakeFatSecret, food_entry

pytestmark = pytest.mark.anyio

TODAY = date(2026, 10, 14)
YESTERDAY = date(2026, 10, 13)

Connect = Callable[[str], Client]


async def get_diary_day(client: Client, **arguments: Any) -> dict[str, Any]:
    result = await client.call_tool("get_diary_day", arguments)

    assert not result.is_error, result.content
    assert result.structured_content is not None
    return result.structured_content


def entry_names(day: dict[str, Any]) -> list[str]:
    """The names of every Food Entry in a Diary Day, Meal by Meal."""
    names: list[str] = []

    for meal in day["meals"]:
        names += [entry["name"] for entry in meal["food_entries"]]

    return names


async def test_food_entries_are_grouped_by_meal_in_the_order_of_the_day(
    connect: Connect, fatsecret: FakeFatSecret, household: Household
) -> None:
    jakub = household.user("jakub").fatsecret
    chicken = food_entry(
        "Kurczak", Meal.LUNCH, TODAY, kcal=293, protein=44.32, fat=11.58, carbohydrate=0, amount="150 g"
    )

    fatsecret.add_food_entry(
        jakub, food_entry("Zupa pomidorowa", Meal.LUNCH, TODAY, kcal=180, amount="300 g")
    )
    fatsecret.add_food_entry(jakub, food_entry("Baton proteinowy", Meal.SNACKS, TODAY, kcal=200))
    fatsecret.add_food_entry(jakub, food_entry("Owsianka", Meal.BREAKFAST, TODAY, kcal=350))
    fatsecret.add_food_entry(jakub, chicken)

    async with connect("jakub") as client:
        day = await get_diary_day(client)

    assert day["diary_day"] == "2026-10-14"
    assert [meal["meal"] for meal in day["meals"]] == ["breakfast", "lunch", "snacks"]
    lunch = day["meals"][1]["food_entries"]

    assert [entry["name"] for entry in lunch] == ["Zupa pomidorowa", "Kurczak"]
    assert lunch[1] == {
        "food_entry_id": chicken.id,
        "name": "Kurczak",
        "amount": "150 g",
        "kcal": 293,
        "protein": 44.3,
        "fat": 11.6,
        "carbohydrate": 0,
    }


async def test_shows_totals_daily_targets_and_what_remains(
    connect: Connect, fatsecret: FakeFatSecret, household: Household
) -> None:
    jakub = household.user("jakub").fatsecret

    fatsecret.add_food_entry(
        jakub,
        food_entry("Owsianka", Meal.BREAKFAST, TODAY, kcal=350, protein=12.5, fat=8, carbohydrate=55.33),
    )
    fatsecret.add_food_entry(
        jakub, food_entry("Schabowy", Meal.DINNER, TODAY, kcal=2300, protein=95, fat=40.1, carbohydrate=180)
    )

    async with connect("jakub") as client:
        day = await get_diary_day(client)

    assert day["totals"] == {"kcal": 2650, "protein": 107.5, "fat": 48.1, "carbohydrate": 235.3}
    assert day["daily_targets"] == {"kcal": 2500, "protein": 160, "fat": 80, "carbohydrate": 280}
    assert day["remaining"] == {"kcal": -150, "protein": 52.5, "fat": 31.9, "carbohydrate": 44.7}


async def test_an_empty_diary_day_has_all_of_the_daily_targets_left(connect: Connect) -> None:
    async with connect("jakub") as client:
        day = await get_diary_day(client)

    assert day["meals"] == []
    assert day["totals"] == {"kcal": 0, "protein": 0, "fat": 0, "carbohydrate": 0}
    assert day["remaining"] == day["daily_targets"]


async def test_shows_an_explicit_past_diary_day(
    connect: Connect, fatsecret: FakeFatSecret, household: Household
) -> None:
    jakub = household.user("jakub").fatsecret

    fatsecret.add_food_entry(jakub, food_entry("Pizza", Meal.DINNER, date(2026, 10, 10), kcal=900))
    fatsecret.add_food_entry(jakub, food_entry("Owsianka", Meal.BREAKFAST, TODAY, kcal=350))

    async with connect("jakub") as client:
        day = await get_diary_day(client, diary_day="2026-10-10")

    assert day["diary_day"] == "2026-10-10"
    assert entry_names(day) == ["Pizza"]


async def test_history_is_loaded_only_when_needed_and_an_older_day_is_reused(
    connect: Connect, fatsecret: FakeFatSecret, household: Household
) -> None:
    user = household.user("jakub").fatsecret
    older_day = date(2026, 9, 30)

    fatsecret.add_food_entry(user, food_entry("Pizza", Meal.DINNER, older_day, kcal=900))

    async with connect("jakub") as client:
        await client.list_tools()

        assert fatsecret.requests == 0

        await get_diary_day(client)

        assert fatsecret.requests == 1

        first = await get_diary_day(client, diary_day=older_day.isoformat())
        second = await get_diary_day(client, diary_day=older_day.isoformat())

    assert entry_names(first) == entry_names(second) == ["Pizza"]
    assert fatsecret.requests == 2


async def test_a_past_day_stays_cached_until_an_explicit_refresh(
    connect: Connect, fatsecret: FakeFatSecret, household: Household, clock: FakeClock
) -> None:
    user = household.user("jakub").fatsecret

    fatsecret.add_food_entry(user, food_entry("Pizza", Meal.DINNER, YESTERDAY, kcal=900))

    async with connect("jakub") as client:
        first = await get_diary_day(client, diary_day=YESTERDAY.isoformat())
        clock.now += timedelta(hours=6)

        fatsecret.delete_food_entries(user, YESTERDAY)
        fatsecret.add_food_entry(user, food_entry("Skyr", Meal.SNACKS, YESTERDAY, kcal=100))
        cached = await get_diary_day(client, diary_day=YESTERDAY.isoformat())
        refreshed = await get_diary_day(client, diary_day=YESTERDAY.isoformat(), refresh=True)

    assert entry_names(first) == entry_names(cached) == ["Pizza"]
    assert entry_names(refreshed) == ["Skyr"]
    assert fatsecret.requests == 2


async def test_today_is_read_fresh_after_every_change(
    connect: Connect, fatsecret: FakeFatSecret, household: Household
) -> None:
    user = household.user("jakub").fatsecret

    async with connect("jakub") as client:
        empty = await get_diary_day(client)

        fatsecret.add_food_entry(user, food_entry("Skyr", Meal.SNACKS, TODAY, kcal=100))
        added = await get_diary_day(client)

        fatsecret.delete_food_entries(user, TODAY)
        deleted = await get_diary_day(client)

    assert entry_names(empty) == entry_names(deleted) == []
    assert entry_names(added) == ["Skyr"]
    assert fatsecret.requests == 3


async def test_a_new_server_session_does_not_reuse_the_previous_sessions_cached_day(
    connect: Connect, fatsecret: FakeFatSecret, household: Household
) -> None:
    user = household.user("jakub").fatsecret

    fatsecret.add_food_entry(user, food_entry("Pizza", Meal.DINNER, YESTERDAY, kcal=900))

    async with connect("jakub") as client:
        first = await get_diary_day(client, diary_day=YESTERDAY.isoformat())

    fatsecret.delete_food_entries(user, YESTERDAY)
    fatsecret.add_food_entry(user, food_entry("Skyr", Meal.SNACKS, YESTERDAY, kcal=100))

    async with connect("jakub") as client:
        second = await get_diary_day(client, diary_day=YESTERDAY.isoformat())

    assert entry_names(first) == ["Pizza"]
    assert entry_names(second) == ["Skyr"]
    assert fatsecret.requests == 2


@pytest.mark.parametrize(
    ("server_clock_utc", "users_diary_day"),
    [
        (datetime(2026, 10, 13, 21, 59, 59, tzinfo=UTC), YESTERDAY),  # 23:59:59 in Warsaw
        (datetime(2026, 10, 13, 22, 0, 0, tzinfo=UTC), TODAY),  # 00:00 in Warsaw, still the 13th in UTC
        (datetime(2026, 10, 13, 23, 30, 0, tzinfo=UTC), TODAY),
    ],
)
async def test_today_is_the_calendar_day_in_the_users_timezone_not_the_servers(
    connect: Connect,
    fatsecret: FakeFatSecret,
    household: Household,
    clock: FakeClock,
    server_clock_utc: datetime,
    users_diary_day: date,
) -> None:
    jakub = household.user("jakub").fatsecret

    fatsecret.add_food_entry(jakub, food_entry("Kolacja", Meal.DINNER, YESTERDAY, kcal=600))
    fatsecret.add_food_entry(jakub, food_entry("Kebab", Meal.SNACKS, TODAY, kcal=700))
    clock.now = server_clock_utc

    async with connect("jakub") as client:
        day = await get_diary_day(client)

    assert day["diary_day"] == users_diary_day.isoformat()


async def test_each_user_sees_only_their_own_food_diary_and_daily_targets(
    connect: Connect, fatsecret: FakeFatSecret, household: Household
) -> None:
    fatsecret.add_food_entry(
        household.user("jakub").fatsecret, food_entry("Schabowy", Meal.DINNER, TODAY, kcal=900)
    )
    fatsecret.add_food_entry(
        household.user("ola").fatsecret, food_entry("Sałatka", Meal.LUNCH, TODAY, kcal=400)
    )

    async with connect("ola") as client:
        day = await get_diary_day(client)

    assert entry_names(day) == ["Sałatka"]
    assert day["daily_targets"]["kcal"] == 1900


@pytest.mark.parametrize(
    "error",
    [
        InvalidAccessToken(),
        DailyQuotaExceeded(),
        IpAddressRejected(),
        NotFound(),
        CredentialsRejected(8),
        UpstreamFailure("HTTP 503"),
    ],
    ids=lambda error: type(error).__name__,
)
async def test_a_fatsecret_error_reaches_the_caller_as_a_clear_message(
    connect: Connect, fatsecret: FakeFatSecret, error: FatSecretError
) -> None:
    fatsecret.fail_with(error)

    async with connect("jakub") as client:
        result = await client.call_tool("get_diary_day", {})

    assert result.is_error
    [message] = [block.model_dump()["text"] for block in result.content]

    assert message.endswith(str(error))


async def test_get_diary_day_is_read_only_and_the_server_carries_fatsecret_attribution(
    connect: Connect,
) -> None:
    async with connect("jakub") as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        instructions = client.instructions

    assert tools["get_diary_day"].annotations is not None
    assert tools["get_diary_day"].annotations.read_only_hint is True
    assert instructions is not None
    assert "Powered by fatsecret Platform API" in instructions
