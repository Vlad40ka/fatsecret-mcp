"""Onboarding a User with `add-user`: the PIN flow, their settings, and a configuration the server accepts."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import date

import pytest
from mcp import Client

from fatsecret_mcp.cli.add_user import add_user
from fatsecret_mcp.config.loader import HouseholdConfigError, load_household
from fatsecret_mcp.config.models import Household
from fatsecret_mcp.domain.models import Food, Meal, Nutrition, Serving
from fatsecret_mcp.fatsecret.budget import daily_budget
from fatsecret_mcp.fatsecret.errors import PinRejected
from fatsecret_mcp.fatsecret.gateway import AccessToken
from fatsecret_mcp.server import build_server
from tests.auth.test_http import BASE_URL, CALLBACK, CLIENT_ID, diary, serve, sign_in
from tests.cli.conftest import ENVIRON, Answer, ScriptedConsole
from tests.conftest import HOUSEHOLD_USERS, FakeClock
from tests.fatsecret.fake import FakeFatSecret, food_entry

pytestmark = pytest.mark.anyio

OLA = AccessToken(
    token="ola-fatsecret-token",
    secret="ola-fatsecret-secret",
)
TODAY = date(
    year=2026,
    month=10,
    day=14,
)
HTTP_ENVIRON = ENVIRON | {"FATSECRET_MCP_BASE_URL": BASE_URL}
CLIENT_METADATA: dict[str, object] = {
    "client_id": CLIENT_ID,
    "client_name": "Claude",
    "redirect_uris": [CALLBACK],
    "token_endpoint_auth_method": "none",
    "grant_types": ["authorization_code"],
    "response_types": ["code"],
}
PASSWORD = re.compile(r"^\s+([a-z2-9]{4}(?:-[a-z2-9]{4}){4})$")
OLA_DETAILS = ["Ola", "", "1900", "110", "65", "210", "", "n"]


def onboarding(fatsecret: FakeFatSecret, details: Sequence[Answer] = OLA_DETAILS) -> ScriptedConsole:
    """Ola approves at the link the command shows and types the PIN; then the Operator gives `details`."""

    def pin() -> str:
        return fatsecret.approve_at(shown_authorize_url(console), OLA)

    console = ScriptedConsole([pin, *details])

    return console


def shown_authorize_url(console: ScriptedConsole) -> str:
    links = re.findall(r"https://authentication\.fatsecret\.com/oauth/authorize\?\S+", console.text)

    assert len(links) == 1
    return links[0]


def shown_password(console: ScriptedConsole) -> str:
    passwords = [match.group(1) for line in console.transcript if (match := PASSWORD.match(line))]

    assert len(passwords) == 1, "the password is shown exactly once"
    return passwords[0]


def shown_users_value(console: ScriptedConsole) -> str:
    values = [line for line in console.transcript if line.startswith("[")]

    assert len(values) == 1
    return values[0]


def household_from(console: ScriptedConsole) -> Household:
    return load_household(HTTP_ENVIRON | {"FATSECRET_MCP_USERS": shown_users_value(console)})


async def test_a_user_added_by_add_user_signs_in_and_reads_their_own_food_diary(clock: FakeClock) -> None:
    fatsecret = FakeFatSecret()
    console = onboarding(fatsecret)

    await add_user(console, fatsecret, existing_users=None)

    household = household_from(console)

    fatsecret.add_food_entry(OLA, food_entry("Owsianka", Meal.BREAKFAST, TODAY, kcal=350))

    async with serve(household, fatsecret, clock, CLIENT_METADATA) as http:
        tokens = await sign_in(http, "ola", shown_password(console))
        response = await diary(http, tokens["access_token"])

    result = response.json()["result"]

    assert result["isError"] is False
    assert result["structuredContent"]["totals"]["kcal"] == 350
    assert result["structuredContent"]["daily_targets"] == {
        "kcal": 1900,
        "protein": 110,
        "fat": 65,
        "carbohydrate": 210,
    }


async def test_a_user_added_by_add_user_can_draft_and_confirm_food_entries(clock: FakeClock) -> None:
    fatsecret = FakeFatSecret()
    console = onboarding(fatsecret)
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

    rice = Food(
        id="rice",
        name="Cooked Rice",
        brand=None,
        servings=[serving],
    )

    fatsecret.add_food(rice)
    await add_user(console, fatsecret, existing_users=None)

    household = household_from(console)
    server = build_server(
        user=household.user("ola"),
        fatsecret=fatsecret,
        clock=clock,
        signing_key=household.signing_key,
        daily_requests=daily_budget(len(household.users)),
    )
    items = [{"food_id": "rice", "serving_id": "rice-100g", "number_of_units": 150}]

    async with Client(server) as client:
        drafted = await client.call_tool("draft_entries", {"items": items, "meal": "lunch"})

        assert drafted.structured_content is not None
        confirmed = await client.call_tool(
            "confirm_draft", {"draft_id": drafted.structured_content["draft_id"]}
        )

    assert not confirmed.is_error, confirmed.content
    assert [entry.name for entry in await fatsecret.list_food_entries(OLA, TODAY)] == ["Cooked Rice"]


async def test_the_new_user_is_added_to_the_existing_household_which_stays_as_written() -> None:
    fatsecret = FakeFatSecret()
    console = onboarding(fatsecret, ["Kasia", "", "1700", "100", "60", "190", "", "n"])

    await add_user(console, fatsecret, existing_users=json.dumps(HOUSEHOLD_USERS))

    users = json.loads(shown_users_value(console))
    ids = [user["id"] for user in users]

    assert users[:2] == HOUSEHOLD_USERS
    assert ids == ["jakub", "ola", "kasia"]


async def test_an_id_already_in_the_household_is_refused_and_asked_again() -> None:
    fatsecret = FakeFatSecret()
    console = onboarding(fatsecret, ["Ola", "", "ola-k", "1900", "110", "65", "210", "", "n"])

    await add_user(console, fatsecret, existing_users=json.dumps(HOUSEHOLD_USERS))

    household = household_from(console)

    assert "already" in console.text
    assert household.user("ola").fatsecret.token == "ola-token"
    assert household.user("ola-k").fatsecret == OLA


async def test_an_invalid_existing_household_stops_before_anyone_signs_in() -> None:
    fatsecret = FakeFatSecret()
    console = onboarding(fatsecret)

    with pytest.raises(HouseholdConfigError, match="FATSECRET_MCP_USERS"):
        await add_user(console, fatsecret, existing_users='[{"id": "jakub"}]')

    assert fatsecret.requests == 0


async def test_the_default_id_is_the_display_name_in_plain_lowercase() -> None:
    fatsecret = FakeFatSecret()
    console = onboarding(fatsecret, ["Łucja Żak", "", "1900", "110", "65", "210", "", "n"])

    await add_user(console, fatsecret, existing_users=None)

    assert household_from(console).user("lucja-zak").display_name == "Łucja Żak"


async def test_invalid_answers_are_asked_again() -> None:
    fatsecret = FakeFatSecret()
    details = ["", "Ola", "Ola!", "ola", "dużo", "1900,5", "-5", "110", "65", "210", "Mars/Olympus", "", "n"]
    console = onboarding(fatsecret, details)

    await add_user(console, fatsecret, existing_users=None)

    user = household_from(console).user("ola")

    assert console.unanswered == 0
    assert user.daily_targets.kcal == 1900.5
    assert user.daily_targets.protein == 110
    assert str(user.timezone) == "Europe/Warsaw"


async def test_the_timezone_and_meal_hours_can_be_set() -> None:
    fatsecret = FakeFatSecret()
    meal_hours = ["y", "06:00", "06:00", "18:00", "22:00", "06:00", "12:00", "18:00", "22:00"]
    console = onboarding(fatsecret, ["Ola", "", "1900", "110", "65", "210", "Europe/London", *meal_hours])

    await add_user(console, fatsecret, existing_users=None)

    user = household_from(console).user("ola")

    assert "differ" in console.text
    assert str(user.timezone) == "Europe/London"
    assert (user.meal_hours.breakfast, user.meal_hours.lunch) == ("06:00", "12:00")
    assert (user.meal_hours.dinner, user.meal_hours.snacks) == ("18:00", "22:00")


async def test_only_the_configuration_value_and_the_one_time_password_reveal_secrets() -> None:
    fatsecret = FakeFatSecret()
    console = onboarding(fatsecret)

    await add_user(console, fatsecret, existing_users=None)

    users_value = shown_users_value(console)
    password = shown_password(console)
    other_lines = [line for line in console.transcript if line != users_value]

    for secret in (OLA.token, OLA.secret):
        assert secret in users_value
        assert secret not in "\n".join(other_lines)

    assert password not in users_value
    assert household_from(console).user("ola").connector_password_hash is not None


async def test_a_rejected_pin_ends_the_command_without_a_configuration() -> None:
    fatsecret = FakeFatSecret()
    console = ScriptedConsole(["1234567"])

    with pytest.raises(PinRejected):
        await add_user(console, fatsecret, existing_users=None)

    values = [line for line in console.transcript if line.startswith("[")]

    assert values == []
