"""The Food cache: a Food fetched with its Servings is reused by every tool and every User for an hour."""

from __future__ import annotations

import gc
import weakref
from collections.abc import Callable
from datetime import timedelta

import pytest
from mcp import Client

from fatsecret_mcp.config.models import Household
from fatsecret_mcp.domain.models import Food, Nutrition, Serving
from fatsecret_mcp.fatsecret import food_cache as food_cache_module
from fatsecret_mcp.fatsecret.food_cache import FoodCache
from fatsecret_mcp.server import build_server
from tests.conftest import FakeClock
from tests.fatsecret.fake import FakeFatSecret
from tests.test_server import wait_until_collected

pytestmark = pytest.mark.anyio
Connect = Callable[[str], Client]
FAST_TTL = timedelta(milliseconds=25)


@pytest.fixture
def rice(fatsecret: FakeFatSecret) -> Food:
    serving = Serving(
        id="rice-100g",
        description="100 g",
        metric_amount=100,
        metric_unit="g",
        number_of_units=100,
        nutrition=Nutrition(
            kcal=130,
            protein=2.7,
            fat=0.3,
            carbohydrate=28.2,
        ),
    )
    food = Food(
        id="rice",
        name="Cooked Rice",
        brand=None,
        servings=[serving],
    )

    fatsecret.add_food(food)
    fatsecret.set_search_results("rice", [food])

    return food


async def test_a_draft_reuses_the_food_a_search_has_just_fetched(
    connect: Connect, fatsecret: FakeFatSecret, rice: Food
) -> None:
    item = {"food_id": rice.id, "serving_id": "rice-100g", "number_of_units": 150}

    async with connect("jakub") as client:
        searched = await client.call_tool("search_foods", {"description": "rice"})
        drafted = await client.call_tool("draft_entries", {"items": [item]})

    assert not searched.is_error
    assert not drafted.is_error
    # The search and its one Food, then only the Diary Day for the day after adding.
    assert fatsecret.requests == 2 + 1


async def test_repeating_a_search_costs_only_the_catalog_search(
    connect: Connect, fatsecret: FakeFatSecret, rice: Food
) -> None:
    async with connect("jakub") as client:
        first = await client.call_tool("search_foods", {"description": "rice"})
        again = await client.call_tool("search_foods", {"description": "rice"})

    assert first.structured_content == again.structured_content
    assert fatsecret.requests == 2 + 1


async def test_a_cached_food_costs_none_of_the_users_daily_budget(
    household: Household, fatsecret: FakeFatSecret, clock: FakeClock, food_cache: FoodCache, rice: Food
) -> None:
    item = {"food_id": rice.id, "serving_id": "rice-100g", "number_of_units": 150}
    server = build_server(
        user=household.user("jakub"),
        fatsecret=fatsecret,
        clock=clock,
        signing_key=household.signing_key,
        daily_requests=4,
        food_cache=food_cache,
    )

    async with Client(server) as client:
        searched = await client.call_tool("search_foods", {"description": "rice"})
        again = await client.call_tool("search_foods", {"description": "rice"})
        drafted = await client.call_tool("draft_entries", {"items": [item]})

    assert not searched.is_error
    assert not again.is_error
    assert not drafted.is_error, drafted.content
    assert fatsecret.requests == 4


async def test_a_cached_food_is_fetched_again_an_hour_after_it_was_fetched(
    connect: Connect, fatsecret: FakeFatSecret, clock: FakeClock, rice: Food
) -> None:
    async with connect("jakub") as client:
        await client.call_tool("search_foods", {"description": "rice"})

        clock.now += timedelta(minutes=59)
        await client.call_tool("search_foods", {"description": "rice"})

        assert fatsecret.requests == 2 + 1

        clock.now += timedelta(minutes=1)
        await client.call_tool("search_foods", {"description": "rice"})

    assert fatsecret.requests == 2 + 1 + 2


async def cache_food(client: Client, fatsecret: FakeFatSecret) -> weakref.ReferenceType[Food]:
    food = Food(
        id="oats",
        name="Oats",
        brand=None,
        servings=[
            Serving(
                id="oats-100g",
                description="100 g",
                metric_amount=100,
                metric_unit="g",
                number_of_units=100,
                nutrition=Nutrition(kcal=389),
            ),
        ],
    )
    reference = weakref.ref(food)

    fatsecret.add_food(food)
    fatsecret.set_search_results("oats", [food])
    result = await client.call_tool("search_foods", {"description": "oats"})

    assert not result.is_error

    # Leave the Food cache as the only owner of the fetched Food.
    fatsecret.remove_food(food.id)

    return reference


async def test_an_idle_server_releases_an_expired_food_without_another_request(
    connect: Connect,
    fatsecret: FakeFatSecret,
    food_cache: FoodCache,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(food_cache_module, "FOOD_CACHE_TTL", FAST_TTL)

    async with food_cache.lifespan(), connect("jakub") as client:
        reference = await cache_food(client, fatsecret)

        assert reference() is not None

        clock.now += FAST_TTL
        await wait_until_collected(reference)

    assert fatsecret.requests == 2


async def test_stopping_the_cache_releases_foods_that_have_not_expired(
    connect: Connect, fatsecret: FakeFatSecret, food_cache: FoodCache
) -> None:
    async with food_cache.lifespan(), connect("jakub") as client:
        reference = await cache_food(client, fatsecret)

        assert reference() is not None

    gc.collect()

    assert reference() is None
