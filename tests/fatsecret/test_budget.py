"""Each User's daily FatSecret budget: one User spending theirs leaves the Household working."""

from __future__ import annotations

from datetime import timedelta

import pytest
from mcp import Client

from fatsecret_mcp.config.models import Household
from fatsecret_mcp.fatsecret.budget import FATSECRET_DAILY_QUOTA, daily_budget
from fatsecret_mcp.server import build_server
from tests.conftest import FakeClock
from tests.fatsecret.fake import FakeFatSecret

pytestmark = pytest.mark.anyio


def serve(household: Household, fatsecret: FakeFatSecret, clock: FakeClock, user_id: str) -> Client:
    server = build_server(
        user=household.user(user_id),
        fatsecret=fatsecret,
        clock=clock,
        signing_key=household.signing_key,
        daily_requests=3,
    )

    return Client(server)


async def test_a_user_who_spent_their_budget_is_refused_while_another_user_keeps_working(
    household: Household, fatsecret: FakeFatSecret, clock: FakeClock
) -> None:
    async with (
        serve(household, fatsecret, clock, "jakub") as jakub,
        serve(household, fatsecret, clock, "ola") as ola,
    ):
        assert not (await jakub.call_tool("get_diary_day", {})).is_error
        assert not (await jakub.call_tool("search_foods", {"description": "rice"})).is_error
        assert not (await jakub.call_tool("search_foods", {"description": "oats"})).is_error

        refused = await jakub.call_tool("search_foods", {"description": "skyr"})
        others = await ola.call_tool("search_foods", {"description": "skyr"})

    assert refused.is_error
    assert "daily FatSecret budget of 3 requests" in str(refused.content)
    assert "the next one at 12:00 on 15 October (Europe/Warsaw)" in str(refused.content)
    assert not others.is_error
    assert fatsecret.requests == 4


async def test_a_spent_request_frees_up_twenty_four_hours_after_it_was_made(
    household: Household, fatsecret: FakeFatSecret, clock: FakeClock
) -> None:
    async with serve(household, fatsecret, clock, "jakub") as jakub:
        for description in ("rice", "oats", "skyr"):
            assert not (await jakub.call_tool("search_foods", {"description": description})).is_error

        clock.now += timedelta(hours=23, minutes=59)

        assert (await jakub.call_tool("search_foods", {"description": "eggs"})).is_error

        clock.now += timedelta(minutes=1)

        assert not (await jakub.call_tool("search_foods", {"description": "eggs"})).is_error

    assert fatsecret.requests == 4


@pytest.mark.parametrize("users", [1, 2, 3, 5, 10])
def test_the_whole_household_fits_under_fatsecrets_daily_quota(users: int) -> None:
    assert daily_budget(users) * users < FATSECRET_DAILY_QUOTA


def test_two_users_get_over_two_thousand_requests_a_day_each() -> None:
    assert daily_budget(2) == 2250
