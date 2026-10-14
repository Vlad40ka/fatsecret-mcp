"""Shared Food Diary reads and invalidation through the UserDiary interface."""

from __future__ import annotations

from datetime import date, timedelta

import anyio
import pytest

from fatsecret_mcp.config.models import Household
from fatsecret_mcp.domain.models import FoodEntry, Meal
from fatsecret_mcp.fatsecret.diary import UserDiary
from fatsecret_mcp.fatsecret.errors import InvalidAccessToken, TooManyActions
from fatsecret_mcp.fatsecret.gateway import AccessToken
from tests.conftest import FakeClock
from tests.fatsecret.fake import FakeFatSecret, food_entry

pytestmark = pytest.mark.anyio
TODAY = date(2026, 10, 14)
YESTERDAY = TODAY - timedelta(days=1)


@pytest.fixture
def diary(fatsecret: FakeFatSecret, household: Household, clock: FakeClock) -> UserDiary:
    user = household.user("jakub")

    return UserDiary(
        fatsecret=fatsecret,
        user=user.fatsecret,
        timezone=user.timezone,
        clock=clock,
        recent_food_days=household.recent_food_days,
    )


async def test_invalidating_a_written_past_day_makes_its_new_foods_available_immediately(
    diary: UserDiary, fatsecret: FakeFatSecret, household: Household
) -> None:
    user = household.user("jakub").fatsecret

    fatsecret.add_food_entry(user, food_entry("Skyr Stary", Meal.SNACKS, YESTERDAY, kcal=100))
    before = await diary.recent_foods()

    fatsecret.delete_food_entries(user, YESTERDAY)
    fatsecret.add_food_entry(user, food_entry("Skyr Nowy", Meal.SNACKS, YESTERDAY, kcal=200))
    await diary.invalidate(YESTERDAY)
    after = await diary.recent_foods()

    assert [food.name for food in before] == ["Skyr Stary"]
    assert [food.name for food in after] == ["Skyr Nowy"]
    assert after[0].servings[0].nutrition.kcal == 200
    assert fatsecret.requests == 14 + 2


async def test_invalidation_discards_an_older_read_that_was_in_flight_when_the_day_was_written(
    diary: UserDiary, fatsecret: FakeFatSecret, household: Household, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = household.user("jakub").fatsecret
    reading = anyio.Event()
    release_read = anyio.Event()
    invalidated = anyio.Event()
    list_entries = fatsecret.list_food_entries

    fatsecret.add_food_entry(user, food_entry("Skyr Stary", Meal.SNACKS, YESTERDAY, kcal=100))

    async def pause_old_read(token: AccessToken, day: date) -> list[FoodEntry]:
        entries = await list_entries(token, day)

        if day == YESTERDAY:
            reading.set()
            await release_read.wait()

        return entries

    async def invalidate_written_day() -> None:
        await diary.invalidate(YESTERDAY)
        invalidated.set()

    monkeypatch.setattr(fatsecret, "list_food_entries", pause_old_read)

    with anyio.fail_after(2):
        async with anyio.create_task_group() as group:
            group.start_soon(diary.recent_foods)
            await reading.wait()

            fatsecret.delete_food_entries(user, YESTERDAY)
            fatsecret.add_food_entry(user, food_entry("Skyr Nowy", Meal.SNACKS, YESTERDAY, kcal=200))
            group.start_soon(invalidate_written_day)
            await anyio.lowlevel.checkpoint()

            assert not invalidated.is_set()

            release_read.set()

    after = await diary.recent_foods()

    assert invalidated.is_set()
    assert [food.name for food in after] == ["Skyr Nowy"]


async def test_changing_a_returned_day_list_does_not_change_the_cached_history(
    diary: UserDiary, fatsecret: FakeFatSecret, household: Household
) -> None:
    user = household.user("jakub").fatsecret

    fatsecret.add_food_entry(user, food_entry("Skyr", Meal.SNACKS, YESTERDAY, kcal=100))
    day = await diary.entries(YESTERDAY)

    day.clear()
    foods = await diary.recent_foods()

    assert [food.name for food in foods] == ["Skyr"]


async def test_an_older_day_stays_available_for_reports_without_entering_recent_foods(
    diary: UserDiary, fatsecret: FakeFatSecret, household: Household
) -> None:
    user = household.user("jakub").fatsecret
    older_day = TODAY - timedelta(days=14)

    fatsecret.add_food_entry(user, food_entry("Skyr Stary", Meal.SNACKS, older_day, kcal=100))
    first = await diary.entries(older_day)
    foods = await diary.recent_foods()
    second = await diary.entries(older_day)

    assert first == second
    assert [entry.name for entry in second] == ["Skyr Stary"]
    assert foods == []
    assert fatsecret.requests == 1 + 14


async def test_an_observed_revoked_token_discards_cached_past_days(
    diary: UserDiary, fatsecret: FakeFatSecret, household: Household
) -> None:
    user = household.user("jakub").fatsecret

    fatsecret.add_food_entry(user, food_entry("Skyr Stary", Meal.SNACKS, YESTERDAY, kcal=100))
    await diary.entries(YESTERDAY)

    fatsecret.revoke(user)

    with pytest.raises(InvalidAccessToken):
        await diary.entries(TODAY)

    fatsecret.add_account(user)
    fatsecret.delete_food_entries(user, YESTERDAY)
    fatsecret.add_food_entry(user, food_entry("Skyr Nowy", Meal.SNACKS, YESTERDAY, kcal=200))
    recovered = await diary.entries(YESTERDAY)

    assert [entry.name for entry in recovered] == ["Skyr Nowy"]
    assert fatsecret.requests == 3


async def test_a_failed_explicit_refresh_cannot_fall_back_to_the_discarded_day(
    diary: UserDiary, fatsecret: FakeFatSecret, household: Household
) -> None:
    user = household.user("jakub").fatsecret

    fatsecret.add_food_entry(user, food_entry("Skyr Stary", Meal.SNACKS, YESTERDAY, kcal=100))
    await diary.entries(YESTERDAY)

    fatsecret.limit_requests_to(1)

    with pytest.raises(TooManyActions):
        await diary.entries(YESTERDAY, refresh=True)

    fatsecret.limit_requests_to(100)
    fatsecret.delete_food_entries(user, YESTERDAY)
    fatsecret.add_food_entry(user, food_entry("Skyr Nowy", Meal.SNACKS, YESTERDAY, kcal=200))
    recovered = await diary.entries(YESTERDAY)

    assert [entry.name for entry in recovered] == ["Skyr Nowy"]
    assert fatsecret.requests == 3


async def test_moving_the_clock_backwards_does_not_extend_a_cached_days_lifetime(
    diary: UserDiary, fatsecret: FakeFatSecret, household: Household, clock: FakeClock
) -> None:
    user = household.user("jakub").fatsecret

    fatsecret.add_food_entry(user, food_entry("Skyr Stary", Meal.SNACKS, YESTERDAY, kcal=100))
    await diary.entries(YESTERDAY)

    clock.now -= timedelta(minutes=1)

    fatsecret.delete_food_entries(user, YESTERDAY)
    fatsecret.add_food_entry(user, food_entry("Skyr Nowy", Meal.SNACKS, YESTERDAY, kcal=200))
    refreshed = await diary.entries(YESTERDAY)

    assert [entry.name for entry in refreshed] == ["Skyr Nowy"]
    assert fatsecret.requests == 2
