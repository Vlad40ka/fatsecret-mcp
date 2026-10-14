"""Catalog searches through an in-memory MCP client against the real tool surface."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest
from mcp import Client

from fatsecret_mcp.domain.models import Food, Nutrition, Serving
from fatsecret_mcp.fatsecret.errors import DailyQuotaExceeded
from tests.fatsecret.fake import FakeFatSecret

pytestmark = pytest.mark.anyio
Connect = Callable[[str], Client]
ATTRIBUTION = "Powered by fatsecret Platform API"


@pytest.fixture
def chicken(fatsecret: FakeFatSecret) -> Food:
    food = Food(
        id="1641",
        name="Chicken Breast",
        brand=None,
        servings=[
            Serving(
                id="50321",
                description="100 g",
                metric_amount=100,
                metric_unit="g",
                number_of_units=100,
                nutrition=Nutrition(
                    kcal=195,
                    protein=29.55,
                    fat=7.72,
                    carbohydrate=0,
                ),
            ),
        ],
    )

    fatsecret.add_food(food)
    fatsecret.set_search_results("chicken breast", [food])

    return food


@pytest.mark.parametrize(
    "arguments",
    [
        {"description": "pierś kurczaka", "english_rendering": "chicken breast"},
        {"description": "chicken breast"},
    ],
)
async def test_search_returns_loggable_servings_and_nutrition(
    connect: Connect, chicken: Food, arguments: dict[str, str]
) -> None:
    async with connect("jakub") as client:
        result = await client.call_tool("search_foods", arguments)

    assert not result.is_error
    assert result.structured_content == {
        "status": "found",
        "attribution": ATTRIBUTION,
        "matches": [
            {
                "source": "catalog",
                "food_id": chicken.id,
                "name": "Chicken Breast",
                "brand": None,
                "servings": [
                    {
                        "serving_id": "50321",
                        "description": "100 g",
                        "metric_amount": 100,
                        "metric_unit": "g",
                        "number_of_units": 100,
                        "kcal": 195,
                        "protein": 29.55,
                        "fat": 7.72,
                        "carbohydrate": 0,
                    },
                ],
            },
        ],
    }


async def test_no_matches_is_an_explicit_normal_result_with_attribution(connect: Connect) -> None:
    async with connect("jakub") as client:
        result = await client.call_tool("search_foods", {"description": "qqzxvjkw"})

    assert not result.is_error
    assert result.structured_content == {"status": "not_found", "matches": [], "attribution": ATTRIBUTION}


@pytest.mark.parametrize("only_derived", [False, True])
async def test_derived_servings_are_never_offered(
    connect: Connect, fatsecret: FakeFatSecret, chicken: Food, only_derived: bool
) -> None:
    derived = replace(chicken.servings[0], id="0")
    servings = [derived] if only_derived else [derived, *chicken.servings]
    food = replace(chicken, brand="Example", servings=servings)

    fatsecret.add_food(food)

    async with connect("jakub") as client:
        result = await client.call_tool("search_foods", {"description": "chicken breast"})

    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["attribution"] == ATTRIBUTION

    if only_derived:
        assert result.structured_content["status"] == "not_found"
        assert result.structured_content["matches"] == []
    else:
        [match] = result.structured_content["matches"]

        assert match["brand"] == "Example"
        assert [serving["serving_id"] for serving in match["servings"]] == ["50321"]


async def test_search_returns_only_the_top_five_foods(
    connect: Connect, fatsecret: FakeFatSecret, chicken: Food
) -> None:
    foods = [replace(chicken, id=str(index)) for index in range(8)]

    for food in foods:
        fatsecret.add_food(food)

    fatsecret.set_search_results("chicken breast", foods)

    async with connect("jakub") as client:
        result = await client.call_tool("search_foods", {"description": "chicken breast"})

    assert not result.is_error
    assert result.structured_content is not None
    assert [food["food_id"] for food in result.structured_content["matches"]] == ["0", "1", "2", "3", "4"]


async def test_a_food_no_longer_available_is_skipped(
    connect: Connect, fatsecret: FakeFatSecret, chicken: Food
) -> None:
    missing = replace(chicken, id="missing")

    fatsecret.set_search_results("chicken breast", [missing, chicken])

    async with connect("jakub") as client:
        result = await client.call_tool("search_foods", {"description": "chicken breast"})

    assert not result.is_error
    assert result.structured_content is not None
    assert [food["food_id"] for food in result.structured_content["matches"]] == [chicken.id]


@pytest.mark.parametrize(
    "arguments",
    [
        {"description": ""},
        {"description": "  "},
        {"description": "kurczak", "english_rendering": ""},
        {"description": "kurczak", "english_rendering": "  "},
    ],
)
async def test_blank_searches_are_rejected(
    connect: Connect, fatsecret: FakeFatSecret, arguments: dict[str, str]
) -> None:
    fatsecret.fail_with(DailyQuotaExceeded())

    async with connect("jakub") as client:
        result = await client.call_tool("search_foods", arguments)

    assert result.is_error
    assert str(DailyQuotaExceeded()) not in str(result.content)


async def test_upstream_errors_are_not_reported_as_no_matches(
    connect: Connect, fatsecret: FakeFatSecret
) -> None:
    fatsecret.fail_with(DailyQuotaExceeded())

    async with connect("jakub") as client:
        result = await client.call_tool("search_foods", {"description": "chicken breast"})

    assert result.is_error
    assert str(DailyQuotaExceeded()) in str(result.content)


async def test_search_is_read_only_and_requests_an_english_rendering(connect: Connect) -> None:
    async with connect("jakub") as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    tool = tools["search_foods"]

    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is True
    assert "non-English" in (tool.description or "")
    assert "English rendering" in (tool.description or "")
