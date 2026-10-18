"""Confirming Drafts through MCP: one Food Entry per item, only in the calling User's Food Diary."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import date, timedelta
from typing import Any

import anyio
import pytest
from mcp import Client

from fatsecret_mcp.config.models import Household
from fatsecret_mcp.domain.models import Food, Meal, Nutrition, Serving
from fatsecret_mcp.fatsecret.errors import InvalidAccessToken, UpstreamFailure
from tests.conftest import FakeClock
from tests.fatsecret.fake import FakeFatSecret
from tests.tools.test_draft_entries import decode_base64url

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


RICE_150_G = {"food_id": "rice", "serving_id": "rice-100g", "number_of_units": 150}
RICE_50_G = {"food_id": "rice", "serving_id": "rice-100g", "number_of_units": 50}


async def draft(
    client: Client,
    items: list[dict[str, Any]],
    *,
    meal: str | None = None,
    diary_day: str | None = None,
) -> str:
    arguments = {
        "items": items,
        "meal": meal,
        "diary_day": diary_day,
    }
    result = await client.call_tool("draft_entries", arguments)

    assert not result.is_error, result.content
    assert result.structured_content is not None

    return result.structured_content["draft_id"]


async def confirm(client: Client, draft_id: str) -> dict[str, Any]:
    result = await client.call_tool("confirm_draft", {"draft_id": draft_id})

    assert not result.is_error, result.content
    assert result.structured_content is not None

    return result.structured_content


async def diary_day(client: Client, day: date = TODAY) -> list[dict[str, Any]]:
    result = await client.call_tool("get_diary_day", {"diary_day": day.isoformat()})

    assert result.structured_content is not None

    return result.structured_content["meals"]


async def test_confirming_writes_each_item_to_the_draft_meal_and_day_and_returns_entry_ids(
    connect: Connect, fatsecret: FakeFatSecret, household: Household, rice: Food
) -> None:
    async with connect("jakub") as client:
        draft_id = await draft(client, [RICE_150_G, RICE_50_G], meal="dinner")
        confirmed = await confirm(client, draft_id)

    entries = await fatsecret.list_food_entries(household.user("jakub").fatsecret, TODAY)
    entry_ids = [entry.id for entry in entries]
    written = [(entry.name, entry.number_of_units, entry.meal) for entry in entries]

    assert confirmed["status"] == "confirmed"
    assert confirmed["meal"] == "dinner"
    assert confirmed["diary_day"] == "2026-10-14"
    assert confirmed["entries"] == [
        {"name": "Cooked Rice", "number_of_units": 150, "food_entry_id": entry_ids[0]},
        {"name": "Cooked Rice", "number_of_units": 50, "food_entry_id": entry_ids[1]},
    ]
    assert confirmed["failure"] is None
    assert written == [("Cooked Rice", 150, Meal.DINNER), ("Cooked Rice", 50, Meal.DINNER)]


async def test_confirming_writes_only_to_the_calling_users_food_diary(
    connect: Connect, household: Household, rice: Food
) -> None:
    async with connect("ola") as client:
        olas_before = await diary_day(client)

    async with connect("jakub") as client:
        await confirm(client, await draft(client, [RICE_150_G]))

        jakubs_after = await diary_day(client)

    async with connect("ola") as client:
        olas_after = await diary_day(client)

    assert olas_after == olas_before == []
    assert jakubs_after[0]["food_entries"][0]["name"] == "Cooked Rice"
    assert jakubs_after[0]["food_entries"][0]["kcal"] == 195


async def test_a_confirmed_past_day_is_read_again_instead_of_from_memory(
    connect: Connect, rice: Food
) -> None:
    async with connect("jakub") as client:
        before = await diary_day(client, YESTERDAY)

        await confirm(client, await draft(client, [RICE_150_G], diary_day=YESTERDAY.isoformat()))

        after = await diary_day(client, YESTERDAY)

    assert before == []
    assert after[0]["food_entries"][0]["name"] == "Cooked Rice"


async def test_confirming_the_same_draft_twice_creates_no_duplicates(
    connect: Connect, fatsecret: FakeFatSecret, household: Household, rice: Food
) -> None:
    async with connect("jakub") as client:
        draft_id = await draft(client, [RICE_150_G])
        first = await confirm(client, draft_id)
        second = await confirm(client, draft_id)

    entries = await fatsecret.list_food_entries(household.user("jakub").fatsecret, TODAY)

    assert len(entries) == 1
    assert first["status"] == "confirmed"
    assert second["status"] == "already_confirmed"
    assert second["entries"] == first["entries"]


async def test_two_confirms_of_the_same_draft_at_once_write_it_only_once(
    connect: Connect, fatsecret: FakeFatSecret, household: Household, rice: Food
) -> None:
    results: list[dict[str, Any]] = []

    async with connect("jakub") as client:
        draft_id = await draft(client, [RICE_150_G])

        async def confirm_and_keep() -> None:
            results.append(await confirm(client, draft_id))

        async with anyio.create_task_group() as group:
            group.start_soon(confirm_and_keep)
            group.start_soon(confirm_and_keep)

    entries = await fatsecret.list_food_entries(household.user("jakub").fatsecret, TODAY)
    statuses = sorted(result["status"] for result in results)

    assert len(entries) == 1
    assert statuses == ["already_confirmed", "confirmed"]


async def test_a_timeout_before_any_answer_asks_to_check_the_diary_before_confirming_again(
    connect: Connect, fatsecret: FakeFatSecret, rice: Food
) -> None:
    async with connect("jakub") as client:
        draft_id = await draft(client, [RICE_150_G])

        fatsecret.fail_with(UpstreamFailure("the request timed out"))

        result = await client.call_tool("confirm_draft", {"draft_id": draft_id})

    assert result.is_error
    assert "get_diary_day" in str(result.content)
    assert "Nothing was written" not in str(result.content)


async def test_a_draft_survives_a_server_restart_between_draft_and_confirm(
    connect: Connect, fatsecret: FakeFatSecret, household: Household, rice: Food
) -> None:
    async with connect("jakub") as client:
        draft_id = await draft(client, [RICE_150_G])

    async with connect("jakub") as restarted:
        confirmed = await confirm(restarted, draft_id)

    entries = await fatsecret.list_food_entries(household.user("jakub").fatsecret, TODAY)

    assert confirmed["status"] == "confirmed"
    assert [entry.name for entry in entries] == ["Cooked Rice"]


def tampered(draft_id: str) -> str:
    encoded, signature = draft_id.split(".")
    payload = json.loads(decode_base64url(encoded))
    payload["items"][0]["number_of_units"] = 1500
    serialized = json.dumps(payload, separators=(",", ":")).encode()
    forged = base64.urlsafe_b64encode(serialized).decode().rstrip("=")

    return f"{forged}.{signature}"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("expired", "expired"),
        ("tampered", "isn't a valid Draft"),
        ("garbled", "isn't a valid Draft"),
        ("another_user", "another User"),
    ],
)
async def test_an_expired_tampered_or_another_users_draft_is_rejected_and_nothing_is_written(
    connect: Connect,
    fatsecret: FakeFatSecret,
    household: Household,
    clock: FakeClock,
    rice: Food,
    change: str,
    message: str,
) -> None:
    drafting_user = "ola" if change == "another_user" else "jakub"

    async with connect(drafting_user) as client:
        draft_id = await draft(client, [RICE_150_G])

    if change == "expired":
        clock.now += timedelta(minutes=30)
    elif change == "tampered":
        draft_id = tampered(draft_id)
    elif change == "garbled":
        draft_id = "not-a-draft"

    async with connect("jakub") as client:
        result = await client.call_tool("confirm_draft", {"draft_id": draft_id})

    for user_id in ("jakub", "ola"):
        entries = await fatsecret.list_food_entries(household.user(user_id).fatsecret, TODAY)

        assert entries == []

    assert result.is_error
    assert message in str(result.content)


async def test_a_write_failing_part_way_through_says_which_items_were_written(
    connect: Connect, fatsecret: FakeFatSecret, household: Household, rice: Food
) -> None:
    async with connect("jakub") as client:
        draft_id = await draft(client, [RICE_150_G, RICE_50_G])

        fatsecret.limit_requests_to(fatsecret.requests + 1)

        partial = await confirm(client, draft_id)

        fatsecret.limit_requests_to(None)

        retried = await confirm(client, draft_id)

    entries = await fatsecret.list_food_entries(household.user("jakub").fatsecret, TODAY)

    assert partial["status"] == "partially_written"
    assert partial["entries"][0]["food_entry_id"] == entries[0].id
    assert partial["entries"][1]["food_entry_id"] is None
    assert "error 12" in partial["failure"]
    assert retried["status"] == "already_confirmed"
    assert retried["entries"] == partial["entries"]
    assert len(entries) == 1


async def test_a_draft_that_wrote_nothing_can_be_confirmed_again(
    connect: Connect, fatsecret: FakeFatSecret, household: Household, rice: Food
) -> None:
    async with connect("jakub") as client:
        draft_id = await draft(client, [RICE_150_G])

        fatsecret.revoke(household.user("jakub").fatsecret)

        failed = await client.call_tool("confirm_draft", {"draft_id": draft_id})

        fatsecret.add_account(household.user("jakub").fatsecret)

        confirmed = await confirm(client, draft_id)

    assert failed.is_error
    assert str(InvalidAccessToken()) in str(failed.content)
    assert "Nothing was written" in str(failed.content)
    assert confirmed["status"] == "confirmed"


async def test_the_confirm_tool_is_destructive_idempotent_and_asks_for_explicit_approval(
    connect: Connect,
) -> None:
    async with connect("jakub") as client:
        listed = (await client.list_tools()).tools

    tool = next(tool for tool in listed if tool.name == "confirm_draft")

    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is False
    assert tool.annotations.destructive_hint is True
    assert tool.annotations.idempotent_hint is True
    assert tool.output_schema is not None
    assert "explicitly approved" in (tool.description or "")


async def test_an_overlong_draft_id_is_refused_before_any_fatsecret_request(
    connect: Connect, fatsecret: FakeFatSecret
) -> None:
    async with connect("jakub") as client:
        result = await client.call_tool("confirm_draft", {"draft_id": "x" * 32769})

    assert result.is_error
    assert "at most" in str(result.content)
    assert fatsecret.requests == 0


async def test_a_draft_of_twenty_long_named_foods_can_be_confirmed(
    connect: Connect, fatsecret: FakeFatSecret, rice: Food
) -> None:
    long_named = replace(rice, name="Ryż gotowany z warzywami i przyprawami, " + "ż" * 200)

    fatsecret.add_food(long_named)

    async with connect("jakub") as client:
        draft_id = await draft(client, [RICE_50_G] * 20)
        confirmed = await confirm(client, draft_id)

    assert confirmed["status"] == "confirmed"
    assert len(confirmed["entries"]) == 20
