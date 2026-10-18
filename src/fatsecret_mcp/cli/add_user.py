"""`add-user`: onboarding one User in about two minutes.

1. The User signs in to their own FatSecret account and allows access; FatSecret shows them a PIN.
2. The Operator types the User's name, Daily Targets, timezone and, optionally, Meal hours.
3. A connector password is generated and shown once; the configuration keeps only its scrypt hash.
4. The updated `FATSECRET_MCP_USERS` value is shown, ready to paste into the server's secrets.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Annotated, Any

from pydantic import BeforeValidator, Field, TypeAdapter, ValidationError

from fatsecret_mcp.auth.passwords import generate_password, hash_password
from fatsecret_mcp.cli.console import Console
from fatsecret_mcp.config.loader import USERS_VAR, meal_starts_differ
from fatsecret_mcp.config.models import DEFAULT_TIMEZONE, DisplayName, MealHours, MealStart, Timezone, UserId
from fatsecret_mcp.config.writer import existing_user_ids, users_value_with
from fatsecret_mcp.fatsecret.gateway import AccessToken, FatSecretGateway


def _decimal_point(value: object) -> object:
    """Accept "1900,5" as well as "1900.5"."""
    return value.replace(",", ".") if isinstance(value, str) else value


_DISPLAY_NAME = TypeAdapter(DisplayName)
_USER_ID = TypeAdapter(UserId)
_KCAL = TypeAdapter(Annotated[float, BeforeValidator(_decimal_point), Field(gt=0, allow_inf_nan=False)])
_GRAMS = TypeAdapter(Annotated[float, BeforeValidator(_decimal_point), Field(ge=0, allow_inf_nan=False)])
_TIMEZONE = TypeAdapter(Timezone)
_MEAL_START = TypeAdapter(MealStart)
_YES_OR_NO = TypeAdapter(bool)


async def add_user(console: Console, fatsecret: FatSecretGateway, *, existing_users: str | None) -> None:
    """Onboard a User, adding them to `existing_users` (a `FATSECRET_MCP_USERS` value, or None)."""
    taken = existing_user_ids(existing_users)

    if taken:
        console.tell(f"Adding a User to the Household in {USERS_VAR}: {', '.join(sorted(taken))}.")
    else:
        console.tell(f"{USERS_VAR} isn't set, so this starts a new Household.")

    access_token = await _sign_in(console, fatsecret)

    console.tell()
    console.tell("Step 2 of 3: the User's settings.")

    display_name = _ask(console, "Display name", _DISPLAY_NAME, hint="Type the User's name.")
    user_id = _ask_user_id(console, display_name, taken)
    daily_targets = _ask_daily_targets(console)
    timezone = _ask_timezone(console)
    meal_hours = _ask_meal_hours(console)

    password = generate_password()
    user: dict[str, Any] = {
        "id": user_id,
        "display_name": display_name,
        "fatsecret_access_token": access_token.token,
        "fatsecret_access_secret": access_token.secret,
        "daily_targets": daily_targets,
        "timezone": timezone,
        "connector_password_hash": hash_password(password),
    }

    if meal_hours is not None:
        user["meal_hours"] = meal_hours

    users_value = users_value_with(existing_users, user)

    _hand_over(console, display_name, user_id, password, users_value)


async def _sign_in(console: Console, fatsecret: FatSecretGateway) -> AccessToken:
    console.tell()
    console.tell("Step 1 of 3: the User signs in to FatSecret.")

    request_token = await fatsecret.request_token()

    console.tell(
        "Open this link on the User's phone or computer. They sign in with their own FatSecret account, "
        "the one in the FatSecret app (not the developer account), and allow access:"
    )
    console.tell(f"  {fatsecret.authorize_url(request_token)}")

    pin = console.ask("PIN shown by FatSecret: ")
    access_token = await fatsecret.exchange_pin(request_token, pin)

    console.tell("Signed in to FatSecret.")

    return access_token


def _ask[T](
    console: Console, question: str, adapter: TypeAdapter[T], *, hint: str, default: str | None = None
) -> T:
    """Ask until the answer is valid; an empty answer takes the default."""
    prompt = f"{question} [{default}]: " if default else f"{question}: "

    while True:
        answer = console.ask(prompt).strip() or default or ""

        try:
            return adapter.validate_python(answer)
        except ValidationError:
            console.tell(f"  {hint}")


def _ask_user_id(console: Console, display_name: str, taken: set[str]) -> str:
    suggested = _plain_id(display_name)

    while True:
        user_id = _ask(
            console,
            "User id, typed to sign in",
            _USER_ID,
            hint="Use lowercase letters, digits, - and _, starting with a letter or digit.",
            default=suggested or None,
        )

        if user_id not in taken:
            return user_id

        console.tell(f"  {user_id} is already in the Household. Choose another id.")


def _plain_id(display_name: str) -> str:
    """ "Łucja Żak" becomes "lucja-zak"."""
    decomposed = unicodedata.normalize("NFKD", display_name.lower().replace("ł", "l"))
    plain = decomposed.encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", plain)

    return "-".join(words)


def _ask_daily_targets(console: Console) -> dict[str, float]:
    console.tell("Daily Targets, as in the User's FatSecret app:")

    grams = "Type grams: zero or more, such as 120."
    kcal = _ask(console, "  kcal", _KCAL, hint="Type kcal: more than zero, such as 2000.")
    protein = _ask(console, "  Protein (g)", _GRAMS, hint=grams)
    fat = _ask(console, "  Fat (g)", _GRAMS, hint=grams)
    carbohydrate = _ask(console, "  Carbohydrate (g)", _GRAMS, hint=grams)

    return {
        "kcal": _json_number(kcal),
        "protein": _json_number(protein),
        "fat": _json_number(fat),
        "carbohydrate": _json_number(carbohydrate),
    }


def _json_number(value: float) -> float:
    """2000.0 is written as 2000."""
    return int(value) if value.is_integer() else value


def _ask_timezone(console: Console) -> str:
    timezone = _ask(
        console,
        "Timezone",
        _TIMEZONE,
        hint="Type an IANA timezone name, such as Europe/Warsaw.",
        default=DEFAULT_TIMEZONE.key,
    )

    return timezone.key


def _ask_meal_hours(console: Console) -> dict[str, str] | None:
    custom = _ask(console, "Custom Meal hours? (y/n)", _YES_OR_NO, hint="Answer y or n.", default="n")

    if not custom:
        return None

    defaults = MealHours()
    hint = "Type a time as HH:MM, such as 07:30."

    while True:
        console.tell("Local start of each Meal, as HH:MM. A Meal lasts until the next one starts:")

        meal_hours = MealHours(
            breakfast=_ask(console, "  Breakfast", _MEAL_START, hint=hint, default=defaults.breakfast),
            lunch=_ask(console, "  Lunch", _MEAL_START, hint=hint, default=defaults.lunch),
            dinner=_ask(console, "  Dinner", _MEAL_START, hint=hint, default=defaults.dinner),
            snacks=_ask(console, "  Snacks", _MEAL_START, hint=hint, default=defaults.snacks),
        )
        if meal_starts_differ(meal_hours):
            return meal_hours.model_dump()

        console.tell("  Meal start times must differ. Type them again.")


def _hand_over(console: Console, display_name: str, user_id: str, password: str, users_value: str) -> None:
    console.tell()
    console.tell("Step 3 of 3: hand over.")
    console.tell(
        f"Connector password for {display_name}, shown only this once. Give it to them now; "
        f"they sign in as {user_id}:"
    )
    console.tell(f"  {password}")
    console.tell()
    console.tell(f"Paste this whole value into the server's {USERS_VAR} secret, then redeploy:")
    console.tell(users_value)
