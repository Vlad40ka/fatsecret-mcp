"""Household configuration: loaded once from the environment, failing fast without printing secrets."""

from __future__ import annotations

import json
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from fatsecret_mcp.config.loader import HouseholdConfigError, acting_user, load_household

SECRETS = ["consumer-key-0123", "consumer-secret-4567", "ola-token-89ab", "ola-secret-cdef"]


def ola(**overrides: Any) -> dict[str, Any]:
    user: dict[str, Any] = {
        "id": "ola",
        "display_name": "Ola",
        "fatsecret_access_token": "ola-token-89ab",
        "fatsecret_access_secret": "ola-secret-cdef",
        "daily_targets": {"kcal": 1900, "protein": 110, "fat": 65, "carbohydrate": 210},
    }

    user.update(overrides)
    return user


def environ(*users: dict[str, Any], **variables: str) -> dict[str, str]:
    return {
        "FATSECRET_CONSUMER_KEY": "consumer-key-0123",
        "FATSECRET_CONSUMER_SECRET": "consumer-secret-4567",
        "FATSECRET_MCP_USERS": json.dumps(list(users)),
        **variables,
    }


def config_error(env: dict[str, str]) -> str:
    with pytest.raises(HouseholdConfigError) as raised:
        load_household(env)
    message = str(raised.value)

    for secret in SECRETS:
        assert secret not in message
    return message


def test_loads_the_consumer_key_and_each_user() -> None:
    household = load_household(environ(ola(), ola(id="jakub", display_name="Jakub", timezone="UTC")))

    assert household.consumer.key == "consumer-key-0123"
    assert household.consumer.secret == "consumer-secret-4567"
    assert [user.id for user in household.users] == ["ola", "jakub"]
    user = household.user("ola")

    assert user.display_name == "Ola"
    assert user.fatsecret.token == "ola-token-89ab"
    assert user.fatsecret.secret == "ola-secret-cdef"
    assert user.daily_targets.kcal == 1900
    assert user.daily_targets.carbohydrate == 210
    assert household.user("jakub").timezone == ZoneInfo("UTC")


def test_a_users_timezone_defaults_to_europe_warsaw() -> None:
    household = load_household(environ(ola()))

    assert household.user("ola").timezone == ZoneInfo("Europe/Warsaw")


def test_secrets_never_show_in_a_printed_household() -> None:
    household = load_household(environ(ola()))

    printed = f"{household!r} {household} {household.consumer!r} {household.user('ola').fatsecret!r}"

    for secret in SECRETS:
        assert secret not in printed


@pytest.mark.parametrize(
    "variable", ["FATSECRET_CONSUMER_KEY", "FATSECRET_CONSUMER_SECRET", "FATSECRET_MCP_USERS"]
)
def test_a_missing_variable_is_named(variable: str) -> None:
    env = environ(ola())

    del env[variable]

    assert variable in config_error(env)


@pytest.mark.parametrize(
    ("user", "bad_field"),
    [
        (
            ola(daily_targets={"kcal": 0, "protein": 110, "fat": 65, "carbohydrate": 210}),
            "[0].daily_targets.kcal",
        ),
        (
            ola(daily_targets={"kcal": 1900, "protein": -1, "fat": 65, "carbohydrate": 210}),
            "[0].daily_targets.protein",
        ),
        (ola(daily_targets={"kcal": 1900, "protein": 110, "fat": 65}), "[0].daily_targets.carbohydrate"),
        (ola(fatsecret_access_secret=""), "[0].fatsecret_access_secret"),
        (ola(id="Ola Nowak"), "[0].id"),
        (ola(display_name=" "), "[0].display_name"),
        (ola(timezone_="Europe/Warsaw"), "[0].timezone_"),
        # An access token pasted into the wrong field must not be echoed back.
        (ola(timezone="ola-token-89ab"), "[0].timezone"),
    ],
)
def test_an_invalid_user_field_is_named_without_its_value(user: dict[str, Any], bad_field: str) -> None:
    assert f"FATSECRET_MCP_USERS{bad_field}" in config_error(environ(user))


def test_a_user_without_an_access_token_is_rejected() -> None:
    user = ola()

    del user["fatsecret_access_token"]

    assert "FATSECRET_MCP_USERS[0].fatsecret_access_token" in config_error(environ(user))


def test_users_that_arent_valid_json_are_rejected_without_echoing_them() -> None:
    env = environ(ola())
    env["FATSECRET_MCP_USERS"] = '[{"id": "ola", "fatsecret_access_token": "ola-token-89ab",'

    assert "FATSECRET_MCP_USERS" in config_error(env)


def test_a_household_needs_at_least_one_user() -> None:
    assert "at least one User" in config_error(environ())


def test_user_ids_are_unique() -> None:
    assert "ola" in config_error(environ(ola(), ola(display_name="Ola again")))


def test_over_stdio_the_server_acts_as_the_user_named_in_the_environment() -> None:
    env = environ(ola(), ola(id="jakub"), FATSECRET_MCP_USER="jakub")

    assert acting_user(load_household(env), env).id == "jakub"


def test_over_stdio_a_household_of_one_needs_no_acting_user() -> None:
    env = environ(ola())

    assert acting_user(load_household(env), env).id == "ola"


@pytest.mark.parametrize("acting", [None, "kasia"])
def test_over_stdio_the_acting_user_must_be_one_of_several(acting: str | None) -> None:
    env = environ(ola(), ola(id="jakub"))

    if acting:
        env["FATSECRET_MCP_USER"] = acting

    with pytest.raises(HouseholdConfigError) as raised:
        acting_user(load_household(env), env)

    assert "FATSECRET_MCP_USER" in str(raised.value)
    assert "ola, jakub" in str(raised.value)
