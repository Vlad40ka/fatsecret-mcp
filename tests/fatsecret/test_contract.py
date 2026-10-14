"""One contract for every FatSecret gateway: the Fake and the real adapter on recorded responses.

The real adapter's "given" state is what was recorded from FatSecret; the Fake is set up to match it.
That keeps the Fake honest: anything the tools rely on has to hold for real FatSecret too.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date
from typing import Protocol

import pytest

from fatsecret_mcp.domain.models import FoodEntry, Meal, Nutrition
from fatsecret_mcp.fatsecret.client import FatSecretApi
from fatsecret_mcp.fatsecret.errors import InvalidAccessToken
from fatsecret_mcp.fatsecret.gateway import FatSecretGateway
from tests.fatsecret.fake import FakeFatSecret
from tests.fatsecret.recorded import CONSUMER, USER, RecordedFatSecret

pytestmark = pytest.mark.anyio

# Test Food Entries logged on otherwise empty days in January 2015, then recorded.
THREE_MEALS_DAY = date(2015, 1, 12)
ONE_ENTRY_DAY = date(2015, 1, 13)
EMPTY_DAY = date(2015, 1, 14)
REVOKED_TOKEN_DAY = date(2026, 10, 13)

RECORDED_ENTRIES = {
    THREE_MEALS_DAY: [
        FoodEntry(
            id="9000001",
            food_id="51467582",
            serving_id="43596665",
            name="Skyr Waniliowy",
            amount="2 servings",
            number_of_units=2,
            meal=Meal.LUNCH,
            diary_day=THREE_MEALS_DAY,
            nutrition=Nutrition(kcal=148, protein=12.8, fat=2.6, carbohydrate=18.4),
        ),
        FoodEntry(
            id="9000002",
            food_id="1641",
            serving_id="4834",
            name="Chicken Breast",
            amount="1 cup cooked, diced",
            number_of_units=1,
            meal=Meal.DINNER,
            diary_day=THREE_MEALS_DAY,
            nutrition=Nutrition(kcal=263, protein=39.89, fat=10.42, carbohydrate=0),
        ),
        FoodEntry(
            id="9000003",
            food_id="1641",
            serving_id="50321",
            name="Chicken Breast",
            amount="150 g",
            number_of_units=150,
            meal=Meal.SNACKS,
            diary_day=THREE_MEALS_DAY,
            nutrition=Nutrition(kcal=293, protein=44.32, fat=11.58, carbohydrate=0),
        ),
    ],
    ONE_ENTRY_DAY: [
        FoodEntry(
            id="9000004",
            food_id="51467582",
            serving_id="43596665",
            name="Skyr Waniliowy",
            amount="1 serving",
            number_of_units=1,
            meal=Meal.BREAKFAST,
            diary_day=ONE_ENTRY_DAY,
            nutrition=Nutrition(kcal=74, protein=6.4, fat=1.3, carbohydrate=9.2),
        ),
    ],
    EMPTY_DAY: [],
}

RECORDED_FIXTURES = {
    THREE_MEALS_DAY: "food_entries_get.json",
    ONE_ENTRY_DAY: "food_entries_get_single.json",
    EMPTY_DAY: "food_entries_get_empty.json",
}


class World(Protocol):
    @property
    def gateway(self) -> FatSecretGateway: ...

    def given_recorded_diary_day(self, day: date) -> None: ...
    def given_revoked_access_token(self) -> None: ...


class FakeWorld:
    def __init__(self) -> None:
        self.fake = FakeFatSecret()

        self.fake.add_account(USER)
        self.gateway: FatSecretGateway = self.fake

    def given_recorded_diary_day(self, day: date) -> None:
        for entry in RECORDED_ENTRIES[day]:
            self.fake.add_food_entry(USER, entry)

    def given_revoked_access_token(self) -> None:
        self.fake.revoke(USER)


class RecordedWorld:
    def __init__(self, recorded: RecordedFatSecret, gateway: FatSecretApi) -> None:
        self.recorded = recorded
        self.gateway: FatSecretGateway = gateway

    def given_recorded_diary_day(self, day: date) -> None:
        self.recorded.serve(RECORDED_FIXTURES[day])

    def given_revoked_access_token(self) -> None:
        self.recorded.serve("error_invalid_access_token.json")


@pytest.fixture(params=["fake", "fatsecret_api"])
async def world(request: pytest.FixtureRequest) -> AsyncIterator[World]:
    if request.param == "fake":
        yield FakeWorld()
        return
    recorded = RecordedFatSecret()

    async with recorded.client() as http:
        yield RecordedWorld(recorded, FatSecretApi(CONSUMER, http))


@pytest.mark.parametrize("day", [THREE_MEALS_DAY, ONE_ENTRY_DAY, EMPTY_DAY])
async def test_lists_a_users_food_entries_for_a_diary_day(world: World, day: date) -> None:
    world.given_recorded_diary_day(day)

    entries = await world.gateway.list_food_entries(USER, day)

    assert entries == RECORDED_ENTRIES[day]


async def test_a_revoked_access_token_is_rejected(world: World) -> None:
    world.given_revoked_access_token()

    with pytest.raises(InvalidAccessToken):
        await world.gateway.list_food_entries(USER, REVOKED_TOKEN_DAY)
