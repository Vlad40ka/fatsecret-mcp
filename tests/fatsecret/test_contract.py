"""One contract for every FatSecret gateway: the Fake and the real adapter on recorded responses.

The real adapter's "given" state is what was recorded from FatSecret; the Fake is set up to match it.
That keeps the Fake honest: anything the tools rely on has to hold for real FatSecret too.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date
from typing import Protocol
from urllib.parse import parse_qs, urlsplit

import pytest

from fatsecret_mcp.domain.models import CatalogFood, DraftEntry, Food, FoodEntry, Meal, Nutrition, Serving
from fatsecret_mcp.fatsecret.client import FatSecretApi
from fatsecret_mcp.fatsecret.errors import DailyQuotaExceeded, InvalidAccessToken, NotFound, PinRejected
from fatsecret_mcp.fatsecret.gateway import FatSecretGateway, RequestToken
from tests.fatsecret.fake import FakeFatSecret, food_entry
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

CATALOG = [
    CatalogFood(
        id="1641",
        name="Chicken Breast",
        brand=None,
    ),
    CatalogFood(
        id="4881229",
        name="Skinless Chicken Breast",
        brand=None,
    ),
    CatalogFood(
        id="5673332",
        name="Chicken Breast",
        brand="Kirkland Signature",
    ),
    CatalogFood(
        id="742402",
        name="Boneless Skinless Chicken Breast",
        brand="Publix",
    ),
    CatalogFood(
        id="1644",
        name="Roasted Broiled or Baked Chicken Breast",
        brand=None,
    ),
    CatalogFood(
        id="48833",
        name="Boneless Skinless Chicken Breasts",
        brand="Tyson Foods",
    ),
    CatalogFood(
        id="1646",
        name="Roasted Broiled or Baked Chicken Breast (Skin Not Eaten)",
        brand=None,
    ),
    CatalogFood(
        id="49396",
        name="Grilled Chicken Breast Strips",
        brand="Perdue",
    ),
    CatalogFood(
        id="4718517",
        name="Boneless Skinless Chicken Breast",
        brand="Wal-Mart",
    ),
    CatalogFood(
        id="6697691",
        name="Boneless Skinless Chicken Breast",
        brand="Sanderson Farms",
    ),
]

BRANDED_FOOD = Food(
    id="5673332",
    name="Chicken Breast",
    brand="Kirkland Signature",
    servings=[
        Serving(
            id="5509656",
            description="4 oz",
            metric_amount=112,
            metric_unit="g",
            number_of_units=1,
            nutrition=Nutrition(
                kcal=110,
                protein=20,
                fat=1.5,
                carbohydrate=0,
            ),
        ),
        Serving(
            id="0",
            description="100 g",
            metric_amount=100,
            metric_unit="g",
            number_of_units=100,
            nutrition=Nutrition(
                kcal=98,
                protein=17.86,
                fat=1.34,
                carbohydrate=0,
            ),
        ),
    ],
)


class World(Protocol):
    @property
    def gateway(self) -> FatSecretGateway: ...

    def given_recorded_diary_day(self, day: date) -> None: ...
    def given_recorded_month(self, scenario: str) -> None: ...
    def given_revoked_access_token(self) -> None: ...
    def given_catalog(self) -> None: ...
    def given_quota_exceeded(self) -> None: ...
    def given_a_recorded_food_entry_create(self) -> None: ...
    def given_a_user_signing_in_with_a_pin(self) -> None: ...
    def given_a_pin_the_user_never_saw(self) -> None: ...
    def pin_shown_after_approving(self, request_token: RequestToken) -> str: ...


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

    def given_catalog(self) -> None:
        self.fake.set_search_results("chicken breast", CATALOG)
        self.fake.add_food(BRANDED_FOOD)

    def given_quota_exceeded(self) -> None:
        self.fake.fail_with(DailyQuotaExceeded())

    def given_a_recorded_food_entry_create(self) -> None:
        # FatSecret knows the Skyr from the User's Food Diary, though its catalog can't return it.
        self.given_recorded_diary_day(ONE_ENTRY_DAY)

    def given_recorded_month(self, scenario: str) -> None:
        if scenario != "empty":
            self.given_recorded_diary_day(THREE_MEALS_DAY)
            self.given_recorded_diary_day(ONE_ENTRY_DAY)

        if scenario == "single":
            self.fake.delete_food_entries(USER, THREE_MEALS_DAY)

        self.fake.add_food_entry(
            USER, food_entry("Outside the month", Meal.LUNCH, date(2015, 2, 1), kcal=500)
        )

    def given_a_user_signing_in_with_a_pin(self) -> None:
        pass

    def given_a_pin_the_user_never_saw(self) -> None:
        pass

    def pin_shown_after_approving(self, request_token: RequestToken) -> str:
        return self.fake.approve_at(self.fake.authorize_url(request_token), USER)


class RecordedWorld:
    def __init__(self, recorded: RecordedFatSecret, gateway: FatSecretApi) -> None:
        self.recorded = recorded
        self.gateway: FatSecretGateway = gateway

    def given_recorded_diary_day(self, day: date) -> None:
        self.recorded.serve(RECORDED_FIXTURES[day])

    def given_revoked_access_token(self) -> None:
        self.recorded.answer_everything_with("error_invalid_access_token.json")

    def given_catalog(self) -> None:
        for name in (
            "foods_search.json",
            "foods_search_single.json",
            "foods_search_empty.json",
            "food_get_brand.json",
            "error_food_not_found.json",
        ):
            self.recorded.serve(name)

    def given_quota_exceeded(self) -> None:
        self.recorded.answer_everything_with("error_daily_quota.json")

    def given_a_recorded_food_entry_create(self) -> None:
        self.recorded.serve("food_entry_create.json")

    def given_recorded_month(self, scenario: str) -> None:
        suffix = "" if scenario == "full" else f"_{scenario}"

        self.recorded.serve(f"food_entries_get_month{suffix}.json")

    def given_a_user_signing_in_with_a_pin(self) -> None:
        self.recorded.serve("oauth_request_token.json")
        self.recorded.serve("oauth_access_token.json")
        self.recorded.serve("food_entries_get_empty.json")

    def given_a_pin_the_user_never_saw(self) -> None:
        self.recorded.serve("oauth_request_token.json")
        self.recorded.serve("oauth_access_token_wrong_pin.json")

    def pin_shown_after_approving(self, request_token: RequestToken) -> str:
        return "4417092"


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


@pytest.mark.parametrize("scenario", ["full", "single", "empty"])
async def test_month_totals_include_only_the_requested_month(world: World, scenario: str) -> None:
    world.given_recorded_month(scenario)

    totals = await world.gateway.get_month_totals(USER, date(2015, 1, 20))

    if scenario == "empty":
        assert totals == {}
        return

    assert set(totals) == {THREE_MEALS_DAY, ONE_ENTRY_DAY}
    assert totals[ONE_ENTRY_DAY] == Nutrition(
        kcal=74,
        protein=6.4,
        fat=1.3,
        carbohydrate=9.2,
    )

    first_day = totals[THREE_MEALS_DAY]

    if scenario == "single":
        assert first_day == Nutrition()
    else:
        assert first_day.kcal == 704
        assert first_day.protein == pytest.approx(97.01)
        assert first_day.fat == pytest.approx(24.6)
        assert first_day.carbohydrate == pytest.approx(18.4)


async def test_month_totals_reject_a_revoked_access_token(world: World) -> None:
    world.given_revoked_access_token()

    with pytest.raises(InvalidAccessToken):
        await world.gateway.get_month_totals(USER, REVOKED_TOKEN_DAY)


@pytest.mark.parametrize("limit", [1, 10])
async def test_catalog_search_returns_ordered_foods_and_optional_brands(world: World, limit: int) -> None:
    world.given_catalog()

    foods = await world.gateway.search_catalog("chicken breast", max_results=limit)

    assert foods == CATALOG[:limit]


async def test_catalog_search_can_find_nothing(world: World) -> None:
    world.given_catalog()

    assert await world.gateway.search_catalog("qqzxvjkw") == []


async def test_get_food_returns_serving_units_metrics_and_nutrition(world: World) -> None:
    world.given_catalog()

    assert await world.gateway.get_food("5673332") == BRANDED_FOOD


async def test_get_food_reports_an_unknown_food(world: World) -> None:
    world.given_catalog()

    with pytest.raises(NotFound):
        await world.gateway.get_food("999999999999")


@pytest.mark.parametrize("operation", ["search", "get"])
async def test_catalog_operations_report_quota_exhaustion(world: World, operation: str) -> None:
    world.given_quota_exceeded()

    call = world.gateway.search_catalog if operation == "search" else world.gateway.get_food
    argument = "chicken breast" if operation == "search" else "5673332"

    with pytest.raises(DailyQuotaExceeded):
        await call(argument)


SKYR_TWO_UNITS = DraftEntry(
    food_id="51467582",
    serving_id="43596665",
    number_of_units=2,
    entry_name="Skyr Waniliowy",
)


async def test_creates_a_food_entry_for_a_food_outside_the_us_catalog(world: World) -> None:
    world.given_a_recorded_food_entry_create()

    entry_id = await world.gateway.create_food_entry(USER, SKYR_TWO_UNITS, Meal.LUNCH, THREE_MEALS_DAY)

    assert isinstance(entry_id, str)
    assert entry_id


async def test_creating_a_food_entry_rejects_a_revoked_access_token(world: World) -> None:
    world.given_revoked_access_token()

    with pytest.raises(InvalidAccessToken):
        await world.gateway.create_food_entry(USER, SKYR_TWO_UNITS, Meal.LUNCH, THREE_MEALS_DAY)


async def test_the_pin_flow_gives_an_access_token_to_the_users_own_food_diary(world: World) -> None:
    world.given_a_user_signing_in_with_a_pin()

    request_token = await world.gateway.request_token()
    authorize_url = world.gateway.authorize_url(request_token)
    pin = world.pin_shown_after_approving(request_token)
    access_token = await world.gateway.exchange_pin(request_token, pin)

    link = urlsplit(authorize_url)

    assert link._replace(query="").geturl() == "https://authentication.fatsecret.com/oauth/authorize"
    assert parse_qs(link.query) == {"oauth_token": [request_token.token]}
    assert await world.gateway.list_food_entries(access_token, EMPTY_DAY) == []


async def test_a_pin_the_user_never_saw_is_rejected(world: World) -> None:
    world.given_a_pin_the_user_never_saw()

    request_token = await world.gateway.request_token()

    with pytest.raises(PinRejected):
        await world.gateway.exchange_pin(request_token, "0000000")
