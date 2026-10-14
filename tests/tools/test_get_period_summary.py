"""Period summaries through MCP: daily totals, empty days, range limits and User isolation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import pytest
from mcp import Client

from fatsecret_mcp.config.models import Household
from fatsecret_mcp.domain.models import Meal
from fatsecret_mcp.fatsecret.errors import DailyQuotaExceeded, InvalidAccessToken
from tests.conftest import FakeClock
from tests.fatsecret.fake import FakeFatSecret, food_entry

pytestmark = pytest.mark.anyio

Connect = Callable[[str], Client]
DAY = date(2026, 10, 14)
ZERO = {"kcal": 0, "protein": 0, "fat": 0, "carbohydrate": 0}
TARGETS = {"kcal": 2500, "protein": 160, "fat": 80, "carbohydrate": 280}


async def get_period(client: Client, from_day: str, to_day: str) -> dict[str, Any]:
    result = await client.call_tool("get_period_summary", {"from_day": from_day, "to_day": to_day})

    assert not result.is_error, result.content
    assert result.structured_content is not None
    return result.structured_content


async def test_a_cached_history_answers_a_period_question_without_more_requests(
    connect: Connect, fatsecret: FakeFatSecret, household: Household
) -> None:
    user = household.user("jakub").fatsecret
    yesterday = DAY - timedelta(days=1)
    water_day = DAY - timedelta(days=2)
    deleted_day = DAY - timedelta(days=3)

    fatsecret.add_food_entry(user, food_entry("Skyr", Meal.SNACKS, yesterday, kcal=200))
    fatsecret.add_food_entry(user, food_entry("Water", Meal.SNACKS, water_day, kcal=0))
    fatsecret.add_food_entry(user, food_entry("Deleted", Meal.SNACKS, deleted_day, kcal=300))
    fatsecret.delete_food_entries(user, deleted_day)
    fatsecret.limit_requests_to(15)

    async with connect("jakub") as client:
        search = await client.call_tool("search_foods", {"description": "skyr"})
        period = await get_period(client, "2026-10-01", yesterday.isoformat())

    assert not search.is_error
    assert [day["has_food_entries"] for day in period["days"]] == [False] * 11 + [True, True]
    assert [day["totals"]["kcal"] for day in period["days"]] == [0] * 12 + [200]
    assert fatsecret.requests == 15


async def test_a_mixed_period_reuses_cached_days_and_fetches_only_the_missing_month(
    connect: Connect, fatsecret: FakeFatSecret, household: Household
) -> None:
    user = household.user("jakub").fatsecret

    for day, kcal in ((date(2026, 9, 29), 100), (date(2026, 9, 30), 200), (date(2026, 10, 1), 300)):
        fatsecret.add_food_entry(user, food_entry("Skyr", Meal.SNACKS, day, kcal=kcal))

    fatsecret.limit_requests_to(3)

    async with connect("jakub") as client:
        for day in ("2026-09-29", "2026-09-30"):
            result = await client.call_tool("get_diary_day", {"diary_day": day})

            assert not result.is_error

        fatsecret.delete_food_entries(user, date(2026, 9, 30))
        period = await get_period(client, "2026-09-29", "2026-10-01")

    assert [day["totals"]["kcal"] for day in period["days"]] == [100, 200, 300]
    assert fatsecret.requests == 3


async def test_a_cached_past_day_and_today_use_history_and_fresh_totals_together(
    connect: Connect, fatsecret: FakeFatSecret, household: Household
) -> None:
    user = household.user("jakub").fatsecret
    yesterday = DAY - timedelta(days=1)

    fatsecret.add_food_entry(user, food_entry("Skyr", Meal.SNACKS, yesterday, kcal=100))

    async with connect("jakub") as client:
        await client.call_tool("get_diary_day", {"diary_day": yesterday.isoformat()})
        first = await get_period(client, yesterday.isoformat(), DAY.isoformat())

        fatsecret.add_food_entry(user, food_entry("Lunch", Meal.LUNCH, DAY, kcal=500))
        second = await get_period(client, yesterday.isoformat(), DAY.isoformat())

    assert [day["totals"]["kcal"] for day in first["days"]] == [100, 0]
    assert [day["totals"]["kcal"] for day in second["days"]] == [100, 500]
    assert fatsecret.requests == 3


async def test_refreshing_a_period_discards_its_stale_entries_for_subsequent_day_questions(
    connect: Connect, fatsecret: FakeFatSecret, household: Household
) -> None:
    user = household.user("jakub").fatsecret
    yesterday = DAY - timedelta(days=1)

    fatsecret.add_food_entry(user, food_entry("Pizza", Meal.DINNER, yesterday, kcal=900))

    async with connect("jakub") as client:
        await client.call_tool("get_diary_day", {"diary_day": yesterday.isoformat()})

        fatsecret.delete_food_entries(user, yesterday)
        fatsecret.add_food_entry(user, food_entry("Skyr", Meal.SNACKS, yesterday, kcal=100))
        cached = await get_period(client, yesterday.isoformat(), yesterday.isoformat())
        refreshed = await client.call_tool(
            "get_period_summary",
            {"from_day": yesterday.isoformat(), "to_day": yesterday.isoformat(), "refresh": True},
        )
        day = await client.call_tool("get_diary_day", {"diary_day": yesterday.isoformat()})

    assert cached["days"][0]["totals"]["kcal"] == 900
    assert not refreshed.is_error
    assert refreshed.structured_content is not None
    assert refreshed.structured_content["days"][0]["totals"]["kcal"] == 100
    assert not day.is_error
    assert day.structured_content is not None
    assert day.structured_content["totals"]["kcal"] == 100
    assert fatsecret.requests == 3


async def test_an_expired_period_uses_fresh_month_totals_even_after_an_intermediate_cached_report(
    connect: Connect, fatsecret: FakeFatSecret, household: Household, clock: FakeClock
) -> None:
    user = household.user("jakub").fatsecret
    yesterday = DAY - timedelta(days=1)

    fatsecret.add_food_entry(user, food_entry("Skyr Stary", Meal.SNACKS, yesterday, kcal=100))

    async with connect("jakub") as client:
        await client.call_tool("get_diary_day", {"diary_day": yesterday.isoformat()})
        clock.now += timedelta(hours=6)

        fatsecret.delete_food_entries(user, yesterday)
        fatsecret.add_food_entry(user, food_entry("Skyr Nowy", Meal.SNACKS, yesterday, kcal=200))
        cached = await get_period(client, yesterday.isoformat(), yesterday.isoformat())
        clock.now += timedelta(hours=6)
        expired = await get_period(client, yesterday.isoformat(), yesterday.isoformat())

    assert cached["days"][0]["totals"]["kcal"] == 100
    assert expired["days"][0]["totals"]["kcal"] == 200
    assert fatsecret.requests == 2


async def test_period_caches_belong_only_to_the_calling_user(
    connect: Connect, fatsecret: FakeFatSecret, household: Household
) -> None:
    yesterday = DAY - timedelta(days=1)

    fatsecret.add_food_entry(
        household.user("jakub").fatsecret, food_entry("Skyr", Meal.SNACKS, yesterday, kcal=100)
    )
    fatsecret.add_food_entry(
        household.user("ola").fatsecret, food_entry("Skyr", Meal.SNACKS, yesterday, kcal=200)
    )

    async with connect("jakub") as client:
        await client.call_tool("get_diary_day", {"diary_day": yesterday.isoformat()})
        jakubs = await get_period(client, yesterday.isoformat(), yesterday.isoformat())

    async with connect("ola") as client:
        olas = await get_period(client, yesterday.isoformat(), yesterday.isoformat())

    assert jakubs["days"][0]["totals"]["kcal"] == 100
    assert olas["days"][0]["totals"]["kcal"] == 200
    assert fatsecret.requests == 2


async def test_an_uncached_period_uses_month_totals_without_loading_the_whole_food_history(
    connect: Connect, fatsecret: FakeFatSecret, household: Household
) -> None:
    user = household.user("jakub").fatsecret

    for offset in range(14):
        day = DAY - timedelta(days=offset)

        fatsecret.add_food_entry(user, food_entry("Skyr", Meal.SNACKS, day, kcal=100))

    fatsecret.limit_requests_to(1)

    async with connect("jakub") as client:
        period = await get_period(client, "2026-10-01", DAY.isoformat())

    assert [day["totals"]["kcal"] for day in period["days"]] == [100] * 14
    assert fatsecret.requests == 1


async def test_daily_totals_and_targets_include_both_endpoints(
    connect: Connect, fatsecret: FakeFatSecret, household: Household
) -> None:
    user = household.user("jakub").fatsecret

    fatsecret.add_food_entry(
        user, food_entry("Breakfast", Meal.BREAKFAST, DAY, kcal=350, protein=12.54, fat=8, carbohydrate=55.33)
    )
    fatsecret.add_food_entry(
        user, food_entry("Dinner", Meal.DINNER, DAY, kcal=600, protein=40.14, fat=20, carbohydrate=65.33)
    )
    fatsecret.add_food_entry(
        user, food_entry("Lunch", Meal.LUNCH, DAY + timedelta(days=1), kcal=400, protein=20, fat=10)
    )
    fatsecret.add_food_entry(
        user, food_entry("Outside the period", Meal.LUNCH, DAY - timedelta(days=1), kcal=900)
    )

    async with connect("jakub") as client:
        period = await get_period(client, "2026-10-14", "2026-10-15")

    assert period == {
        "from_day": "2026-10-14",
        "to_day": "2026-10-15",
        "days": [
            {
                "diary_day": "2026-10-14",
                "has_food_entries": True,
                "totals": {"kcal": 950, "protein": 52.7, "fat": 28, "carbohydrate": 120.7},
                "daily_targets": TARGETS,
            },
            {
                "diary_day": "2026-10-15",
                "has_food_entries": True,
                "totals": {"kcal": 400, "protein": 20, "fat": 10, "carbohydrate": 0},
                "daily_targets": TARGETS,
            },
        ],
    }


async def test_absent_and_deleted_days_are_marked_without_confusing_zero_nutrition_entries(
    connect: Connect, fatsecret: FakeFatSecret, household: Household
) -> None:
    user = household.user("jakub").fatsecret

    fatsecret.add_food_entry(user, food_entry("Deleted", Meal.LUNCH, DAY, kcal=400))
    fatsecret.delete_food_entries(user, DAY)
    fatsecret.add_food_entry(user, food_entry("Water", Meal.LUNCH, DAY + timedelta(days=1), kcal=0))
    fatsecret.add_food_entry(user, food_entry("Small amount", Meal.LUNCH, DAY + timedelta(days=2), kcal=0.1))

    async with connect("jakub") as client:
        period = await get_period(client, "2026-10-13", "2026-10-16")

    assert [day["has_food_entries"] for day in period["days"]] == [False, False, True, True]
    assert [day["totals"] for day in period["days"]] == [ZERO, ZERO, ZERO, ZERO]
    assert all(day["daily_targets"] == TARGETS for day in period["days"])


@pytest.mark.parametrize(
    ("from_day", "to_day"),
    [
        (date(2026, 10, 31), date(2026, 11, 1)),
        (date(2026, 12, 31), date(2027, 1, 1)),
        (date(2028, 2, 29), date(2028, 3, 1)),
        (date(2026, 1, 31), date(2026, 3, 2)),
    ],
)
async def test_a_period_can_cross_month_and_year_boundaries(
    connect: Connect, fatsecret: FakeFatSecret, household: Household, from_day: date, to_day: date
) -> None:
    user = household.user("jakub").fatsecret
    middle = from_day + timedelta(days=1)

    fatsecret.add_food_entry(user, food_entry("First", Meal.LUNCH, from_day, kcal=100))
    fatsecret.add_food_entry(user, food_entry("Last", Meal.LUNCH, to_day, kcal=200))
    fatsecret.add_food_entry(user, food_entry("Middle", Meal.LUNCH, middle, kcal=50))

    async with connect("jakub") as client:
        period = await get_period(client, from_day.isoformat(), to_day.isoformat())

    days = period["days"]

    assert len(days) == (to_day - from_day).days + 1
    assert days[0]["diary_day"] == from_day.isoformat()
    assert days[-1]["diary_day"] == to_day.isoformat()
    assert days[0]["totals"]["kcal"] == 100
    assert days[-1]["totals"]["kcal"] == (250 if middle == to_day else 200)
    assert sum(day["totals"]["kcal"] for day in days) == 350


@pytest.mark.parametrize(
    ("from_day", "to_day", "length"),
    [("2026-10-14", "2026-10-14", 1), ("2026-10-01", "2026-10-31", 31), ("9999-12-31", "9999-12-31", 1)],
)
async def test_one_day_and_exactly_31_days_are_valid(
    connect: Connect, from_day: str, to_day: str, length: int
) -> None:
    async with connect("jakub") as client:
        period = await get_period(client, from_day, to_day)

    assert len(period["days"]) == length
    assert all(day["has_food_entries"] is False for day in period["days"])


@pytest.mark.parametrize(
    ("from_day", "to_day", "message"),
    [
        ("2026-10-01", "2026-11-01", "at most 31 Diary Days"),
        ("2026-10-15", "2026-10-14", "from_day must be on or before to_day"),
        ("not-a-date", "2026-10-14", "from_day"),
        ("2026-10-14", "2026-02-30", "to_day"),
    ],
)
async def test_invalid_ranges_return_clear_errors_before_accessing_fatsecret(
    connect: Connect, fatsecret: FakeFatSecret, from_day: str, to_day: str, message: str
) -> None:
    fatsecret.fail_with(DailyQuotaExceeded())

    async with connect("jakub") as client:
        result = await client.call_tool("get_period_summary", {"from_day": from_day, "to_day": to_day})

    assert result.is_error
    assert message in str(result.content)


async def test_each_user_gets_their_own_totals_and_targets(
    connect: Connect, fatsecret: FakeFatSecret, household: Household
) -> None:
    fatsecret.add_food_entry(
        household.user("jakub").fatsecret, food_entry("Lunch", Meal.LUNCH, DAY, kcal=900)
    )
    fatsecret.add_food_entry(household.user("ola").fatsecret, food_entry("Lunch", Meal.LUNCH, DAY, kcal=400))

    async with connect("ola") as client:
        period = await get_period(client, "2026-10-14", "2026-10-14")

    [day] = period["days"]

    assert day["totals"]["kcal"] == 400
    assert day["daily_targets"] == {"kcal": 1900, "protein": 110, "fat": 65, "carbohydrate": 210}


async def test_fatsecret_errors_reach_the_caller(connect: Connect, fatsecret: FakeFatSecret) -> None:
    fatsecret.fail_with(InvalidAccessToken())

    async with connect("jakub") as client:
        result = await client.call_tool(
            "get_period_summary", {"from_day": "2026-10-14", "to_day": "2026-10-14"}
        )

    assert result.is_error
    assert str(InvalidAccessToken()) in str(result.content)


async def test_get_period_summary_is_read_only(connect: Connect) -> None:
    async with connect("jakub") as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    annotations = tools["get_period_summary"].annotations

    assert annotations is not None
    assert annotations.read_only_hint is True
