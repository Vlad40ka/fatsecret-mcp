"""`check`: finding configuration mistakes before Users do.

It validates the Household configuration as `serve` would at startup, then reads each User's
current Diary Day, one harmless FatSecret call per User, and reports whether their connection works.
"""

from __future__ import annotations

from collections.abc import Mapping

from fatsecret_mcp.cli.console import Console
from fatsecret_mcp.config.loader import (
    BASE_URL_VAR,
    HouseholdConfigError,
    acting_user,
    load_household,
    validate_http_household,
)
from fatsecret_mcp.config.models import User
from fatsecret_mcp.domain.diary_day import Clock, system_clock, today
from fatsecret_mcp.fatsecret.errors import FatSecretError
from fatsecret_mcp.fatsecret.gateway import FatSecretGateway


async def check(
    console: Console, environ: Mapping[str, str], fatsecret: FatSecretGateway, clock: Clock = system_clock
) -> bool:
    """Whether the configuration is valid and every User's FatSecret connection works."""
    try:
        household = load_household(environ)

        if household.public_base_url is not None:
            validate_http_household(household)
            transport = f"Ready for HTTP at {household.public_base_url}/mcp."
        else:
            transport = f"stdio only, as {acting_user(household, environ).id}: {BASE_URL_VAR} isn't set."
    except HouseholdConfigError as error:
        console.tell(f"Configuration: {error}")

        return False

    count = len(household.users)
    user_count = "1 User" if count == 1 else f"{count} Users"

    console.tell(f"Configuration: valid, {user_count}. {transport}")

    results = [await _check_user(console, fatsecret, user, clock) for user in household.users]

    return all(results)


async def _check_user(console: Console, fatsecret: FatSecretGateway, user: User, clock: Clock) -> bool:
    diary_day = today(clock(), user.timezone)

    try:
        await fatsecret.list_food_entries(user.fatsecret, diary_day)
    except FatSecretError as error:
        console.tell(f"{user.id}: {error}")

        return False

    console.tell(f"{user.id}: connected to FatSecret")

    return True
