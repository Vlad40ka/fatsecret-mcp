"""Drafts through MCP: proposed entries and totals without writing to the Food Diary."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

import pytest
from mcp import Client

from fatsecret_mcp.config.models import Household, User
from fatsecret_mcp.domain.models import Food, Meal, Nutrition, Serving
from fatsecret_mcp.server import build_server
from tests.conftest import FakeClock
from tests.fatsecret.fake import FakeFatSecret, food_entry

pytestmark = pytest.mark.anyio
Connect = Callable[[str], Client]
TODAY = date(
    year=2026,
    month=10,
    day=14,
)
YESTERDAY = date(
    year=2026,
    month=10,
    day=13,
)


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

    return food


async def draft(
    client: Client,
    items: list[dict[str, Any]],
    *,
    meal: str | None = None,
    diary_day: str | None = None,
) -> dict[str, Any]:
    arguments = {
        "items": items,
        "meal": meal,
        "diary_day": diary_day,
    }
    result = await client.call_tool("draft_entries", arguments)

    assert not result.is_error, result.content
    assert result.structured_content is not None

    return result.structured_content


async def test_a_draft_shows_amount_assumption_totals_and_the_day_after_without_writing(
    connect: Connect, fatsecret: FakeFatSecret, household: Household, rice: Food
) -> None:
    eaten = food_entry(
        name="Breakfast",
        meal=Meal.BREAKFAST,
        diary_day=TODAY,
        kcal=2300,
        protein=150,
        fat=75,
        carbohydrate=250,
    )
    items = [
        {
            "food_id": rice.id,
            "serving_id": "rice-100g",
            "number_of_units": 150,
            "assumption": "A bowl ≈ 150 g",
        },
        {"food_id": rice.id, "serving_id": "rice-100g", "number_of_units": 50},
    ]

    fatsecret.add_food_entry(household.user("jakub").fatsecret, eaten)

    async with connect("jakub") as client:
        before = await client.call_tool("get_diary_day", {})
        proposed = await draft(client, items)
        after = await client.call_tool("get_diary_day", {})

    assert before.structured_content == after.structured_content
    assert proposed["meal"] == "lunch"
    assert proposed["meal_suggested"] is True
    assert proposed["diary_day"] == "2026-10-14"
    assert proposed["previous_day_suggested"] is False
    assert proposed["expires_at"] == "2026-10-14T10:30:00Z"
    assert proposed["items"][0] == {
        "food_id": "rice",
        "serving_id": "rice-100g",
        "name": "Cooked Rice",
        "serving": "100 g",
        "number_of_units": 150,
        "amount": "150 g",
        "kcal": 195,
        "protein": 4.1,
        "fat": 0.4,
        "carbohydrate": 42.3,
        "assumption": "A bowl ≈ 150 g",
    }
    assert proposed["items"][1]["assumption"] is None
    assert proposed["totals"] == {"kcal": 260, "protein": 5.4, "fat": 0.6, "carbohydrate": 56.4}
    assert proposed["day_after"] == {
        "consumed": {"kcal": 2560, "protein": 155.4, "fat": 75.6, "carbohydrate": 306.4},
        "daily_targets": {"kcal": 2500, "protein": 160, "fat": 80, "carbohydrate": 280},
        "remaining": {"kcal": -60, "protein": 4.6, "fat": 4.4, "carbohydrate": -26.4},
    }


@pytest.mark.parametrize(
    ("hour", "minute", "meal", "previous_day"),
    [
        (0, 0, "snacks", True),
        (3, 59, "snacks", True),
        (4, 0, "breakfast", False),
        (11, 29, "breakfast", False),
        (11, 30, "lunch", False),
        (15, 59, "lunch", False),
        (16, 0, "dinner", False),
        (20, 59, "dinner", False),
        (21, 0, "snacks", False),
        (23, 59, "snacks", False),
    ],
)
async def test_meal_boundaries_and_the_previous_day_question_use_the_users_local_time(
    connect: Connect,
    clock: FakeClock,
    household: Household,
    rice: Food,
    hour: int,
    minute: int,
    meal: str,
    previous_day: bool,
) -> None:
    local = datetime(
        year=2026,
        month=10,
        day=14,
        hour=hour,
        minute=minute,
        tzinfo=household.user("jakub").timezone,
    )
    clock.now = local.astimezone(UTC)
    items = [{"food_id": rice.id, "serving_id": "rice-100g", "number_of_units": 100}]

    async with connect("jakub") as client:
        proposed = await draft(client, items)

    assert proposed["meal"] == meal
    assert proposed["meal_suggested"] is True
    assert proposed["diary_day"] == "2026-10-14"
    assert proposed["previous_day_suggested"] is previous_day


@pytest.mark.parametrize(
    ("hour", "minute", "meal"),
    [
        (5, 59, "snacks"),
        (6, 0, "breakfast"),
        (12, 59, "breakfast"),
        (13, 0, "lunch"),
        (18, 59, "lunch"),
        (19, 0, "dinner"),
        (22, 59, "dinner"),
        (23, 0, "snacks"),
    ],
)
async def test_each_user_can_configure_their_meal_hours(
    household: Household,
    fatsecret: FakeFatSecret,
    clock: FakeClock,
    rice: Food,
    hour: int,
    minute: int,
    meal: str,
) -> None:
    settings = household.user("jakub").model_dump()
    settings["meal_hours"] = {"breakfast": "06:00", "lunch": "13:00", "dinner": "19:00", "snacks": "23:00"}
    user = User.model_validate(settings)
    local = datetime(
        year=2026,
        month=10,
        day=14,
        hour=hour,
        minute=minute,
        tzinfo=user.timezone,
    )
    clock.now = local.astimezone(UTC)
    server = build_server(
        user=user,
        fatsecret=fatsecret,
        clock=clock,
        signing_key=household.signing_key,
    )
    items = [{"food_id": rice.id, "serving_id": "rice-100g", "number_of_units": 100}]

    async with Client(server) as client:
        proposed = await draft(client, items)

    assert proposed["meal"] == meal


@pytest.mark.parametrize(
    ("explicit_meal", "explicit_day", "meal", "day", "suggested", "previous_day"),
    [
        ("dinner", "2026-10-13", "dinner", "2026-10-13", False, False),
        (None, "2026-10-13", "snacks", "2026-10-13", True, False),
        ("breakfast", None, "breakfast", "2026-10-14", False, False),
    ],
)
async def test_explicit_meal_and_diary_day_override_the_suggestions_and_use_that_days_totals(
    connect: Connect,
    fatsecret: FakeFatSecret,
    household: Household,
    clock: FakeClock,
    rice: Food,
    explicit_meal: str | None,
    explicit_day: str | None,
    meal: str,
    day: str,
    suggested: bool,
    previous_day: bool,
) -> None:
    # 01:00 on the 14th in Warsaw.
    clock.now = datetime(
        year=2026,
        month=10,
        day=13,
        hour=23,
        tzinfo=UTC,
    )
    yesterday = food_entry(
        name="Yesterday's dinner",
        meal=Meal.DINNER,
        diary_day=YESTERDAY,
        kcal=500,
    )
    items = [{"food_id": rice.id, "serving_id": "rice-100g", "number_of_units": 100}]

    fatsecret.add_food_entry(household.user("jakub").fatsecret, yesterday)

    async with connect("jakub") as client:
        proposed = await draft(client, items, meal=explicit_meal, diary_day=explicit_day)

    assert proposed["meal"] == meal
    assert proposed["diary_day"] == day
    assert proposed["meal_suggested"] is suggested
    assert proposed["previous_day_suggested"] is previous_day
    assert proposed["day_after"]["consumed"]["kcal"] == (630 if day == "2026-10-13" else 130)


async def test_a_non_us_recent_food_uses_its_logged_name_serving_and_nutrition(
    connect: Connect,
    fatsecret: FakeFatSecret,
    household: Household,
) -> None:
    skyr = food_entry(
        name="Skyr Waniliowy",
        meal=Meal.SNACKS,
        diary_day=YESTERDAY,
        food_id="skyr",
        serving_id="skyr-100g",
        number_of_units=2,
        amount="200 g",
        kcal=148,
        protein=12.8,
        fat=2.6,
        carbohydrate=18.4,
    )
    items = [{"food_id": "skyr", "serving_id": "skyr-100g", "number_of_units": 1.5}]

    fatsecret.add_food_entry(household.user("jakub").fatsecret, skyr)

    async with connect("jakub") as client:
        proposed = await draft(client, items)

    assert proposed["items"][0] == {
        "food_id": "skyr",
        "serving_id": "skyr-100g",
        "name": "Skyr Waniliowy",
        "serving": "200 g",
        "number_of_units": 1.5,
        "amount": "150 g",
        "kcal": 111,
        "protein": 9.6,
        "fat": 2,
        "carbohydrate": 13.8,
        "assumption": None,
    }


async def test_recent_foods_with_the_same_ids_require_the_name_selected_from_search(
    connect: Connect,
    fatsecret: FakeFatSecret,
    household: Household,
    rice: Food,
) -> None:
    for name, kcal in (("Cooked Rice", 130), ("Baton DZIK", 300)):
        entry = food_entry(
            name=name,
            meal=Meal.SNACKS,
            diary_day=YESTERDAY,
            food_id=rice.id,
            serving_id="rice-100g",
            number_of_units=100,
            amount="100 g",
            kcal=kcal,
        )

        fatsecret.add_food_entry(household.user("jakub").fatsecret, entry)

    item = {"food_id": rice.id, "serving_id": "rice-100g", "number_of_units": 50}

    async with connect("jakub") as client:
        ambiguous = await client.call_tool("draft_entries", {"items": [item]})
        item["entry_name"] = "Baton DZIK"
        bar = await draft(client, [item])
        item["entry_name"] = "Cooked Rice"
        actual_rice = await draft(client, [item])
        item["entry_name"] = "Arbitrary name"
        renamed = await client.call_tool("draft_entries", {"items": [item]})

    assert ambiguous.is_error
    assert "entry_name" in str(ambiguous.content)
    assert bar["items"][0]["name"] == "Baton DZIK"
    assert bar["totals"]["kcal"] == 150
    assert actual_rice["items"][0]["name"] == "Cooked Rice"
    assert actual_rice["totals"]["kcal"] == 65
    assert renamed.is_error
    assert "entry_name" in str(renamed.content)


@pytest.mark.parametrize(
    ("food_id", "serving_id", "units", "message"),
    [
        ("missing", "rice-100g", 100, "Unknown Food"),
        ("rice", "other-food-serving", 100, "Serving"),
        ("rice", "0", 100, "Serving"),
        ("rice", "rice-100g", 0, "greater than 0"),
        ("rice", "rice-100g", -1, "greater than 0"),
        ("rice", "rice-100g", 10001, "10000"),
        ("rice", "rice-100g", "NaN", "finite"),
        ("rice", "rice-100g", "Infinity", "finite"),
        ("rice", "scoop", 30, "implausibly large"),
        ("rice", "unknown-weight", 201, "implausibly large"),
    ],
)
async def test_unknown_food_wrong_serving_and_invalid_or_implausible_amounts_are_rejected(
    connect: Connect,
    fatsecret: FakeFatSecret,
    rice: Food,
    food_id: str,
    serving_id: str,
    units: float | str,
    message: str,
) -> None:
    derived = replace(rice.servings[0], id="0")
    scoop = replace(rice.servings[0], id="scoop", number_of_units=1, metric_amount=500)
    unknown_weight = replace(
        rice.servings[0],
        id="unknown-weight",
        number_of_units=1,
        metric_amount=None,
        metric_unit=None,
    )
    food = replace(rice, servings=[*rice.servings, derived, scoop, unknown_weight])
    items = [{"food_id": food_id, "serving_id": serving_id, "number_of_units": units}]

    fatsecret.add_food(food)

    async with connect("jakub") as client:
        result = await client.call_tool("draft_entries", {"items": items})

    assert result.is_error
    assert message in str(result.content)


def decode_base64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


async def test_the_signed_draft_contains_only_write_values_and_survives_a_new_server_session(
    connect: Connect,
    household: Household,
    rice: Food,
) -> None:
    items = [
        {
            "food_id": rice.id,
            "serving_id": "rice-100g",
            "number_of_units": 150,
            "assumption": "This assumption must not be in the sealed Draft",
        }
    ]

    async with connect("jakub") as client:
        first = await draft(client, items)

    async with connect("jakub") as restarted:
        second = await draft(restarted, items)

    encoded, signature = first["draft_id"].split(".")
    payload = json.loads(decode_base64url(encoded))
    key = household.signing_key.get_secret_value().encode()
    expected_signature = hmac.digest(key, encoded.encode(), hashlib.sha256)

    assert hmac.compare_digest(decode_base64url(signature), expected_signature)
    assert second["draft_id"] == first["draft_id"]
    assert payload == {
        "user_id": "jakub",
        "items": [
            {
                "food_id": "rice",
                "serving_id": "rice-100g",
                "number_of_units": 150,
                "entry_name": "Cooked Rice",
            }
        ],
        "meal": "lunch",
        "diary_day": "2026-10-14",
        "expires_at": "2026-10-14T10:30:00+00:00",
    }
    assert key.decode() not in json.dumps(first)


async def test_a_draft_is_bound_to_the_calling_user_and_their_daily_targets(
    connect: Connect,
    rice: Food,
) -> None:
    items = [{"food_id": rice.id, "serving_id": "rice-100g", "number_of_units": 100}]

    async with connect("jakub") as client:
        jakubs = await draft(client, items)

    async with connect("ola") as client:
        olas = await draft(client, items)

    encoded = olas["draft_id"].split(".")[0]
    payload = json.loads(decode_base64url(encoded))

    assert payload["user_id"] == "ola"
    assert olas["draft_id"] != jakubs["draft_id"]
    assert olas["day_after"]["daily_targets"]["kcal"] == 1900


async def test_the_draft_tool_is_read_only_has_a_result_schema_and_instructs_claude_to_ask(
    connect: Connect,
    rice: Food,
) -> None:
    items = [{"food_id": rice.id, "serving_id": "rice-100g", "number_of_units": 100}]

    async with connect("jakub") as client:
        listed = (await client.list_tools()).tools
        tool = next(tool for tool in listed if tool.name == "draft_entries")
        result = await client.call_tool("draft_entries", {"items": items})

    [block] = result.content
    text = block.model_dump()["text"]

    assert not result.is_error
    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is True
    assert tool.output_schema is not None
    assert "ask the User" in (tool.description or "")
    assert "previous Diary Day" in (tool.description or "")
    assert "entry_name" in (tool.description or "")
    assert json.loads(text) == result.structured_content
    assert "\n" not in text
    assert ": " not in text


@pytest.mark.parametrize(
    ("timezone", "day", "meal", "previous_day"),
    [
        ("Europe/Warsaw", "2026-10-15", "snacks", True),
        ("UTC", "2026-10-14", "snacks", False),
        ("America/Los_Angeles", "2026-10-14", "lunch", False),
        ("Asia/Tokyo", "2026-10-15", "breakfast", False),
    ],
)
async def test_the_same_utc_instant_uses_each_users_timezone_for_meal_and_diary_day(
    household: Household,
    fatsecret: FakeFatSecret,
    clock: FakeClock,
    rice: Food,
    timezone: str,
    day: str,
    meal: str,
    previous_day: bool,
) -> None:
    settings = household.user("jakub").model_dump()
    settings["timezone"] = timezone
    user = User.model_validate(settings)
    clock.now = datetime(
        year=2026,
        month=10,
        day=14,
        hour=22,
        tzinfo=UTC,
    )
    server = build_server(
        user=user,
        fatsecret=fatsecret,
        clock=clock,
        signing_key=household.signing_key,
    )
    items = [{"food_id": rice.id, "serving_id": "rice-100g", "number_of_units": 100}]

    async with Client(server) as client:
        proposed = await draft(client, items)

    assert proposed["diary_day"] == day
    assert proposed["meal"] == meal
    assert proposed["previous_day_suggested"] is previous_day
    assert proposed["expires_at"] == "2026-10-14T22:30:00Z"


async def test_a_recent_food_offers_only_logged_servings_even_when_its_catalog_food_has_more(
    connect: Connect,
    household: Household,
    fatsecret: FakeFatSecret,
    rice: Food,
) -> None:
    entry = food_entry(
        name=rice.name,
        meal=Meal.LUNCH,
        diary_day=YESTERDAY,
        food_id=rice.id,
        serving_id="rice-100g",
        kcal=130,
        number_of_units=100,
        amount="100 g",
    )
    other_serving = replace(rice.servings[0], id="not-logged")
    catalog = replace(rice, servings=[*rice.servings, other_serving])
    items = [{"food_id": rice.id, "serving_id": "not-logged", "number_of_units": 100}]

    fatsecret.add_food_entry(household.user("jakub").fatsecret, entry)
    fatsecret.add_food(catalog)

    async with connect("jakub") as client:
        result = await client.call_tool("draft_entries", {"items": items})

    assert result.is_error
    assert "never logged" in str(result.content)


async def test_another_users_recent_food_cannot_be_used_in_a_draft(
    connect: Connect,
    household: Household,
    fatsecret: FakeFatSecret,
) -> None:
    entry = food_entry(
        name="Ola's Skyr",
        meal=Meal.BREAKFAST,
        diary_day=YESTERDAY,
        food_id="olas-skyr",
        serving_id="skyr-serving",
        kcal=130,
    )
    items = [{"food_id": "olas-skyr", "serving_id": "skyr-serving", "number_of_units": 1}]

    fatsecret.add_food_entry(household.user("ola").fatsecret, entry)

    async with connect("jakub") as client:
        rejected = await client.call_tool("draft_entries", {"items": items})

    async with connect("ola") as client:
        accepted = await draft(client, items)

    assert rejected.is_error
    assert "Unknown Food" in str(rejected.content)
    assert accepted["items"][0]["amount"] == "1 x (1 serving)"
    assert accepted["items"][0]["name"] == "Ola's Skyr"


async def test_a_catalog_match_can_be_selected_when_one_recent_stand_in_uses_its_ids(
    connect: Connect,
    fatsecret: FakeFatSecret,
    household: Household,
    rice: Food,
) -> None:
    bar = food_entry(
        name="Baton DZIK",
        meal=Meal.SNACKS,
        diary_day=YESTERDAY,
        food_id=rice.id,
        serving_id="rice-100g",
        number_of_units=100,
        kcal=300,
        amount="100 g",
    )
    items = [
        {
            "food_id": rice.id,
            "serving_id": "rice-100g",
            "number_of_units": 50,
            "entry_name": rice.name,
        }
    ]

    fatsecret.add_food_entry(household.user("jakub").fatsecret, bar)
    fatsecret.set_search_results("rice", [rice])

    async with connect("jakub") as client:
        found = await client.call_tool("search_foods", {"description": "rice"})

        assert found.structured_content is not None
        assert found.structured_content["matches"][0]["name"] == rice.name

        proposed = await draft(client, items)
        items[0]["entry_name"] = "Baton DZIK"
        recent = await draft(client, items)

    assert proposed["items"][0]["name"] == rice.name
    assert proposed["totals"]["kcal"] == 65
    assert recent["items"][0]["name"] == "Baton DZIK"
    assert recent["totals"]["kcal"] == 150
