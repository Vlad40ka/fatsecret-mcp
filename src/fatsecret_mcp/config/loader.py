"""Reading the Household configuration from environment variables, once, at startup.

- `FATSECRET_CONSUMER_KEY`, `FATSECRET_CONSUMER_SECRET`: the server's OAuth 1.0 consumer key and secret.
- `FATSECRET_MCP_USERS`: a JSON list of Users, for example
  `[{"id": "ola", "display_name": "Ola", "fatsecret_access_token": "…", "fatsecret_access_secret": "…",
  "daily_targets": {"kcal": 2000, "protein": 120, "fat": 70, "carbohydrate": 220},
  "timezone": "Europe/Warsaw"}]`.
- `FATSECRET_MCP_USER`: over stdio, the id of the User the server acts as. Optional when there's only one.

Errors name the bad variable or field but never print a value, since most values here are secrets.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping

from pydantic import SecretStr, TypeAdapter, ValidationError
from pydantic_core import ErrorDetails

from fatsecret_mcp.config.models import Household, User

CONSUMER_KEY_VAR = "FATSECRET_CONSUMER_KEY"
CONSUMER_SECRET_VAR = "FATSECRET_CONSUMER_SECRET"
USERS_VAR = "FATSECRET_MCP_USERS"
ACTING_USER_VAR = "FATSECRET_MCP_USER"

_USERS = TypeAdapter(list[User])


class HouseholdConfigError(Exception):
    """The Household configuration is missing or invalid. The message names the problem, never a value."""


def load_household(environ: Mapping[str, str]) -> Household:
    consumer_key = _required(environ, CONSUMER_KEY_VAR)
    consumer_secret = _required(environ, CONSUMER_SECRET_VAR)

    try:
        users = _USERS.validate_json(_required(environ, USERS_VAR))
    except ValidationError as error:
        problems = "; ".join(_describe(details) for details in error.errors(include_input=False))

        raise HouseholdConfigError(f"Invalid Household configuration: {problems}") from None

    if not users:
        raise HouseholdConfigError(f"{USERS_VAR} must list at least one User")

    repeated = _repeated_ids(users)

    if repeated:
        raise HouseholdConfigError(f"{USERS_VAR} repeats a User id: {', '.join(repeated)}")

    return Household(
        consumer_key=SecretStr(consumer_key),
        consumer_secret=SecretStr(consumer_secret),
        users=tuple(users),
    )


def acting_user(household: Household, environ: Mapping[str, str]) -> User:
    """The User the server acts as over stdio, where there's no sign-in to tell Users apart."""
    user_id = environ.get(ACTING_USER_VAR, "").strip()

    if not user_id:
        if len(household.users) == 1:
            return household.users[0]
        raise HouseholdConfigError(f"{ACTING_USER_VAR} must name the User to act as: {_ids(household)}")

    try:
        return household.user(user_id)
    except KeyError:
        raise HouseholdConfigError(
            f"{ACTING_USER_VAR} names no User in {USERS_VAR}; the ids are: {_ids(household)}"
        ) from None


def _required(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()

    if not value:
        raise HouseholdConfigError(f"{name} is not set")

    return value


def _repeated_ids(users: list[User]) -> list[str]:
    counts = Counter(user.id for user in users)

    return sorted(user_id for user_id, count in counts.items() if count > 1)


def _describe(details: ErrorDetails) -> str:
    path = "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in details["loc"])

    return f"{USERS_VAR}{path}: {details['msg']}"


def _ids(household: Household) -> str:
    return ", ".join(user.id for user in household.users)
