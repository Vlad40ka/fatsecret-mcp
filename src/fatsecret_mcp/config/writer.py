"""Writing the `FATSECRET_MCP_USERS` value that the Operator pastes into the server's secrets.

The counterpart of `loader`: it adds one User and keeps every existing User exactly as written.
"""

from __future__ import annotations

import json
from typing import Any, cast

from fatsecret_mcp.config.loader import USERS_VAR, HouseholdConfigError, parse_users


def users_value_with(existing: str | None, user: dict[str, Any]) -> str:
    """`existing` (None for a new Household) with `user` added; never replaces a User with the same id."""
    if user["id"] in existing_user_ids(existing):
        raise HouseholdConfigError(f"{USERS_VAR} already has a User with the id {user['id']}")

    users = _existing_users(existing)

    users.append(user)

    value = json.dumps(users, ensure_ascii=False)

    # The value must load exactly as the server will load it.
    parse_users(value)

    return value


def existing_user_ids(existing: str | None) -> set[str]:
    """The ids already in `existing`, checked as the server checks them; none for a new Household."""
    value = _given(existing)

    if value is None:
        return set()

    ids = [user.id for user in parse_users(value)]

    return set(ids)


def _existing_users(existing: str | None) -> list[dict[str, Any]]:
    value = _given(existing)

    if value is None:
        return []

    return cast(list[dict[str, Any]], json.loads(value))


def _given(existing: str | None) -> str | None:
    """`existing`, or None when it's unset or blank, which means a new Household."""
    if existing is None or not existing.strip():
        return None

    return existing
