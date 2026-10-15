"""`fatsecret-mcp`: run the server over stdio, acting as one User of the Household."""

from __future__ import annotations

import os
import sys

import anyio
import httpx2

from fatsecret_mcp.config.loader import HouseholdConfigError, acting_user, load_household
from fatsecret_mcp.config.models import Household, User
from fatsecret_mcp.fatsecret.client import FatSecretApi
from fatsecret_mcp.server import build_server


def main() -> None:
    try:
        household = load_household(os.environ)
        user = acting_user(household, os.environ)
    except HouseholdConfigError as error:
        sys.exit(f"fatsecret-mcp: {error}")

    anyio.run(_serve_stdio, household, user)


async def _serve_stdio(household: Household, user: User) -> None:
    async with httpx2.AsyncClient(timeout=20) as http:
        fatsecret = FatSecretApi(household.consumer, http)
        server = build_server(
            user=user,
            fatsecret=fatsecret,
            signing_key=household.signing_key,
        )

        await server.run_stdio_async()


if __name__ == "__main__":
    main()
