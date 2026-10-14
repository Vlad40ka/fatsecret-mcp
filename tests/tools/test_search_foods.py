"""search_foods: a User describes a food and gets their Recent Foods first, then catalog matches.

Searches go through an in-memory MCP client against the real tool surface.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date, timedelta
from typing import Any

import pytest
from mcp import Client

from fatsecret_mcp.config.models import Household
from fatsecret_mcp.domain.models import Food, FoodEntry, Meal, Nutrition, Serving
from fatsecret_mcp.fatsecret.errors import DailyQuotaExceeded, InvalidAccessToken
from fatsecret_mcp.fatsecret.gateway import AccessToken
from fatsecret_mcp.server import build_server
from tests.conftest import FakeClock
from tests.fatsecret.fake import FakeFatSecret, food_entry

pytestmark = pytest.mark.anyio
Connect = Callable[[str], Client]
ATTRIBUTION = "Powered by fatsecret Platform API"
TODAY = date(2026, 10, 14)


def days_ago(days: int) -> date:
    return TODAY - timedelta(days=days)


def logged(name: str, diary_day: date, **details: Any) -> FoodEntry:
    """A Food Entry in a Food and Serving of `name`'s own, unless `details` name others."""
    details.setdefault("food_id", f"food-{name}")
    details.setdefault("serving_id", f"serving-{name}")
    details.setdefault("kcal", 100)

    return food_entry(name, Meal.SNACKS, diary_day, **details)


async def search(client: Client, description: str, english_rendering: str | None = None) -> dict[str, Any]:
    arguments = {"description": description}

    if english_rendering is not None:
        arguments["english_rendering"] = english_rendering

    result = await client.call_tool("search_foods", arguments)

    assert not result.is_error, result.content
    assert result.structured_content is not None
    return result.structured_content


def recent_food_names(found: dict[str, Any]) -> list[str]:
    recent_foods = [match for match in found["matches"] if match["source"] == "recent_foods"]
    names = [match["name"] for match in recent_foods]

    return names


@pytest.fixture
def jakub(household: Household) -> AccessToken:
    return household.user("jakub").fatsecret


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


# --- Recent Foods ----------------------------------------------------------


async def test_recent_foods_come_before_catalog_matches(
    connect: Connect, fatsecret: FakeFatSecret, jakub: AccessToken, chicken: Food
) -> None:
    fatsecret.add_food_entry(
        jakub,
        logged(
            "Chicken Breast",
            days_ago(1),
            food_id=chicken.id,
            serving_id="50321",
            amount="150 g",
            number_of_units=150,
            kcal=293,
            protein=44.32,
            fat=11.58,
        ),
    )

    async with connect("jakub") as client:
        found = await search(client, "pierś kurczaka", "chicken breast")

    assert [match["source"] for match in found["matches"]] == ["recent_foods", "catalog"]
    assert found["matches"][0] == {
        "source": "recent_foods",
        "food_id": chicken.id,
        "name": "Chicken Breast",
        "brand": None,
        "servings": [
            {
                "serving_id": "50321",
                "description": "150 g",
                "metric_amount": 150,
                "metric_unit": "g",
                "number_of_units": 150,
                "kcal": 293,
                "protein": 44.32,
                "fat": 11.58,
                "carbohydrate": 0,
            },
        ],
    }


@pytest.mark.parametrize(
    ("logged_as", "description"),
    [
        ("Płatki Owsiane Błyskawiczne", "płatki owsiane"),
        ("Płatki Owsiane Błyskawiczne", "platki owsiane"),
        ("Platki owsiane", "PŁATKI OWSIANE"),
        ("Żurek śląski", "zurek slaski"),
        ("Łosoś wędzony", "losos"),
    ],
)
async def test_a_polish_description_matches_a_recent_food_with_or_without_diacritics(
    connect: Connect, fatsecret: FakeFatSecret, jakub: AccessToken, logged_as: str, description: str
) -> None:
    fatsecret.add_food_entry(jakub, logged(logged_as, days_ago(2)))

    async with connect("jakub") as client:
        found = await search(client, description)

    assert found["status"] == "found"
    assert recent_food_names(found) == [logged_as]


@pytest.mark.parametrize(
    ("logged_as", "description"),
    [
        ("Jajko kurze", "dwa jajka"),
        ("Kurczak pieczony", "pół kurczaka"),
        ("Skyr Waniliowy", "kubek skyru waniliowego"),
        ("Masło extra", "chleb z masłem"),
    ],
)
async def test_polish_word_endings_dont_prevent_a_match(
    connect: Connect, fatsecret: FakeFatSecret, jakub: AccessToken, logged_as: str, description: str
) -> None:
    fatsecret.add_food_entry(jakub, logged(logged_as, days_ago(2)))

    async with connect("jakub") as client:
        found = await search(client, description)

    assert recent_food_names(found) == [logged_as]


async def test_the_english_rendering_is_matched_against_recent_foods_too(
    connect: Connect, fatsecret: FakeFatSecret, jakub: AccessToken
) -> None:
    fatsecret.add_food_entry(jakub, logged("Greek Yogurt", days_ago(3)))

    async with connect("jakub") as client:
        found = await search(client, "jogurt grecki", "greek yogurt")

    assert recent_food_names(found) == ["Greek Yogurt"]


@pytest.mark.parametrize(
    ("logged_as", "description", "english_rendering"),
    [
        ("Chicken Breast with Skin 2 pcs", "chleb z masłem, 2 kromki", "2 slices of bread with butter"),
        ("Pierś z kurczaka", "szarlotka", "apple pie"),
    ],
)
async def test_sharing_only_numbers_function_words_or_short_word_beginnings_is_no_match(
    connect: Connect,
    fatsecret: FakeFatSecret,
    jakub: AccessToken,
    logged_as: str,
    description: str,
    english_rendering: str,
) -> None:
    fatsecret.add_food_entry(jakub, logged(logged_as, days_ago(1)))

    async with connect("jakub") as client:
        found = await search(client, description, english_rendering)

    assert found["status"] == "not_found"


async def test_a_recent_food_the_catalog_cant_fetch_keeps_its_serving_kcal_and_macros(
    connect: Connect, fatsecret: FakeFatSecret, jakub: AccessToken
) -> None:
    # A Polish Food from the recorded Food Diary. The Fake's catalog doesn't have it, and the real
    # food.get answers "Invalid ID" for it, so its nutrition can only come from the Food Entry.
    fatsecret.add_food_entry(
        jakub,
        logged(
            "Skyr Waniliowy",
            days_ago(1),
            food_id="51467582",
            serving_id="43596665",
            amount="2 servings",
            number_of_units=2,
            kcal=148,
            protein=12.8,
            fat=2.6,
            carbohydrate=18.4,
        ),
    )

    async with connect("jakub") as client:
        found = await search(client, "skyr waniliowy", "vanilla skyr")

    assert found == {
        "status": "found",
        "attribution": ATTRIBUTION,
        "matches": [
            {
                "source": "recent_foods",
                "food_id": "51467582",
                "name": "Skyr Waniliowy",
                "brand": None,
                "servings": [
                    {
                        "serving_id": "43596665",
                        "description": "2 servings",
                        "metric_amount": None,
                        "metric_unit": None,
                        "number_of_units": 2,
                        "kcal": 148,
                        "protein": 12.8,
                        "fat": 2.6,
                        "carbohydrate": 18.4,
                    },
                ],
            },
        ],
    }


async def test_a_food_logged_again_is_one_recent_food_with_each_serving_as_last_logged(
    connect: Connect, fatsecret: FakeFatSecret, jakub: AccessToken
) -> None:
    diary = [
        (5, "43596665", "2 servings", 148),
        (3, "43596666", "150 g", 111),
        (2, "43596665", "1 serving", 74),
        # A derived Serving can't be logged, so it's never offered.
        (1, "0", "100 g", 74),
    ]

    for days_before, serving_id, amount, kcal in diary:
        entry = logged(
            "Skyr Waniliowy",
            days_ago(days_before),
            food_id="51467582",
            serving_id=serving_id,
            amount=amount,
            kcal=kcal,
        )

        fatsecret.add_food_entry(jakub, entry)

    async with connect("jakub") as client:
        found = await search(client, "skyr")

    [match] = found["matches"]
    servings = [
        (serving["serving_id"], serving["description"], serving["kcal"]) for serving in match["servings"]
    ]

    assert servings == [("43596665", "1 serving", 74), ("43596666", "150 g", 111)]


async def test_a_stand_in_and_real_entries_of_its_food_are_separate_recent_foods(
    connect: Connect, fatsecret: FakeFatSecret, jakub: AccessToken
) -> None:
    real_entry = logged(
        "Chicken Breast",
        days_ago(2),
        food_id="1641",
        serving_id="50321",
        amount="150 g",
        kcal=293,
    )
    stand_in = logged(
        "Baton proteinowy DZIK",
        days_ago(1),
        food_id="1641",
        serving_id="50321",
        amount="90 g",
        kcal=176,
    )

    fatsecret.add_food_entry(jakub, real_entry)
    fatsecret.add_food_entry(jakub, stand_in)

    async with connect("jakub") as client:
        bar = await search(client, "baton DZIK")
        chicken = await search(client, "chicken breast")

    assert recent_food_names(bar) == ["Baton proteinowy DZIK"]
    assert [serving["kcal"] for serving in bar["matches"][0]["servings"]] == [176]
    assert recent_food_names(chicken) == ["Chicken Breast"]
    assert [serving["kcal"] for serving in chicken["matches"][0]["servings"]] == [293]


async def test_only_the_best_matching_recent_foods_are_offered(
    connect: Connect, fatsecret: FakeFatSecret, jakub: AccessToken
) -> None:
    for name in ("Skyr Waniliowy", "Pudding Proteinowy Waniliowy", "Bakoma Skyr Naturalny"):
        fatsecret.add_food_entry(jakub, logged(name, days_ago(1)))

    async with connect("jakub") as client:
        vanilla_skyr = await search(client, "skyr waniliowy", "vanilla skyr")
        any_vanilla = await search(client, "coś waniliowego")

    assert recent_food_names(vanilla_skyr) == ["Skyr Waniliowy"]
    assert recent_food_names(any_vanilla) == ["Skyr Waniliowy", "Pudding Proteinowy Waniliowy"]


async def test_equally_good_matches_are_ranked_by_frequency_then_recency(
    connect: Connect, fatsecret: FakeFatSecret, jakub: AccessToken
) -> None:
    diary = {
        "Skyr Naturalny": [10, 9, 8],
        "Skyr Truskawkowy": [4, 3, 2],
        "Skyr Jagodowy": [0],
    }

    for name, days in diary.items():
        for days_before in days:
            fatsecret.add_food_entry(jakub, logged(name, days_ago(days_before)))

    async with connect("jakub") as client:
        found = await search(client, "skyr")

    assert recent_food_names(found) == ["Skyr Truskawkowy", "Skyr Naturalny", "Skyr Jagodowy"]


async def test_a_name_with_the_brand_in_front_is_the_same_recent_food(
    connect: Connect, fatsecret: FakeFatSecret, jakub: AccessToken
) -> None:
    oats = {"food_id": "124208300", "serving_id": "97561866", "kcal": 257}

    fatsecret.add_food_entry(
        jakub,
        logged(
            "Crownfield Płatki Owsiane Błyskawiczne", days_ago(3), **oats, amount="70 g", number_of_units=0.7
        ),
    )
    fatsecret.add_food_entry(
        jakub,
        logged("Płatki Owsiane Błyskawiczne", days_ago(1), **oats, amount="0.7 serving", number_of_units=0.7),
    )

    async with connect("jakub") as client:
        found = await search(client, "płatki owsiane")

    [match] = found["matches"]

    assert match["name"] == "Crownfield Płatki Owsiane Błyskawiczne"
    assert [serving["description"] for serving in match["servings"]] == ["0.7 serving"]


async def test_a_serving_once_logged_in_grams_shows_its_grams(
    connect: Connect, fatsecret: FakeFatSecret, jakub: AccessToken
) -> None:
    skyr = {"food_id": "51467582", "serving_id": "43596665"}

    # Typed in the app as 200 g: FatSecret stores it as 2 servings.
    fatsecret.add_food_entry(
        jakub, logged("Skyr Waniliowy", days_ago(10), **skyr, amount="200 g", number_of_units=2, kcal=148)
    )
    fatsecret.add_food_entry(
        jakub,
        logged("Skyr Waniliowy", days_ago(1), **skyr, amount="1.7 servings", number_of_units=1.7, kcal=126),
    )

    async with connect("jakub") as client:
        found = await search(client, "skyr")

    [serving] = found["matches"][0]["servings"]

    assert serving["description"] == "1.7 servings"
    assert serving["metric_amount"] == 170
    assert serving["metric_unit"] == "g"


async def test_a_search_offers_the_five_best_recent_foods(
    connect: Connect, fatsecret: FakeFatSecret, jakub: AccessToken
) -> None:
    for days_before in range(7):
        fatsecret.add_food_entry(jakub, logged(f"Skyr nr {days_before}", days_ago(days_before)))

    async with connect("jakub") as client:
        found = await search(client, "skyr")

    assert recent_food_names(found) == [f"Skyr nr {days_before}" for days_before in range(5)]


async def test_recent_foods_come_only_from_the_calling_users_food_diary(
    connect: Connect, fatsecret: FakeFatSecret, household: Household, jakub: AccessToken
) -> None:
    fatsecret.add_food_entry(jakub, logged("Skyr Naturalny", days_ago(1)))
    fatsecret.add_food_entry(household.user("ola").fatsecret, logged("Skyr Waniliowy", days_ago(1)))

    async with connect("jakub") as client:
        jakubs = await search(client, "skyr")

    async with connect("ola") as client:
        olas = await search(client, "skyr")

    assert recent_food_names(jakubs) == ["Skyr Naturalny"]
    assert recent_food_names(olas) == ["Skyr Waniliowy"]


async def test_recent_foods_come_from_the_last_14_diary_days_including_today(
    connect: Connect, fatsecret: FakeFatSecret, jakub: AccessToken
) -> None:
    for days_before in (0, 13, 14):
        fatsecret.add_food_entry(
            jakub, logged(f"Skyr z {days_ago(days_before):%d.%m}", days_ago(days_before))
        )

    async with connect("jakub") as client:
        found = await search(client, "skyr")

    assert recent_food_names(found) == ["Skyr z 14.10", "Skyr z 01.10"]


async def test_the_number_of_diary_days_recent_foods_come_from_is_configurable(
    household: Household, fatsecret: FakeFatSecret, clock: FakeClock, jakub: AccessToken
) -> None:
    fatsecret.add_food_entry(jakub, logged("Skyr z 11.10", days_ago(3)))
    fatsecret.add_food_entry(jakub, logged("Skyr z 12.10", days_ago(2)))

    server = build_server(household.user("jakub"), fatsecret, clock, recent_food_days=3)

    async with Client(server) as client:
        found = await search(client, "skyr")

    assert recent_food_names(found) == ["Skyr z 12.10"]


async def test_a_search_reads_each_diary_day_once_and_all_at_once(
    connect: Connect, fatsecret: FakeFatSecret, chicken: Food
) -> None:
    foods = [replace(chicken, id=str(index)) for index in range(5)]

    for food in foods:
        fatsecret.add_food(food)

    fatsecret.set_search_results("chicken breast", foods)

    async with connect("jakub") as client:
        await search(client, "pierś kurczaka", "chicken breast")

    # 14 Diary Days, one catalog search and five Foods.
    assert fatsecret.requests == 14 + 1 + 5
    assert fatsecret.most_in_flight == 14


async def test_a_revoked_access_token_is_reported_rather_than_searching_the_catalog_alone(
    connect: Connect, fatsecret: FakeFatSecret, jakub: AccessToken, chicken: Food
) -> None:
    fatsecret.revoke(jakub)

    async with connect("jakub") as client:
        result = await client.call_tool("search_foods", {"description": "chicken breast"})

    assert result.is_error
    assert str(InvalidAccessToken()) in str(result.content)
