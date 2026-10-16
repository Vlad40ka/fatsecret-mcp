"""Run locally over stdio, or serve the Household's connector over HTTP."""

from __future__ import annotations

import argparse
import os
import sys
from typing import TYPE_CHECKING

import anyio

from fatsecret_mcp.config.loader import (
    HouseholdConfigError,
    acting_user,
    load_household,
    validate_http_household,
)

if TYPE_CHECKING:
    from fatsecret_mcp.config.models import Household, User


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    arguments = parser.parse_args()

    try:
        household = load_household(os.environ)

        if arguments.transport == "http":
            validate_http_household(household)
        else:
            user = acting_user(household, os.environ)

            anyio.run(_serve_stdio, household, user)

            return
    except HouseholdConfigError as error:
        sys.exit(f"fatsecret-mcp: {error}")

    anyio.run(_serve_http, household, arguments.host, arguments.port)


async def _serve_stdio(household: Household, user: User) -> None:
    import httpx2

    from fatsecret_mcp.fatsecret.client import FatSecretApi
    from fatsecret_mcp.server import build_server

    async with httpx2.AsyncClient(timeout=20) as http:
        fatsecret = FatSecretApi(household.consumer, http)
        server = build_server(
            user=user,
            fatsecret=fatsecret,
            signing_key=household.signing_key,
        )

        await server.run_stdio_async()


async def _serve_http(household: Household, host: str, port: int) -> None:
    import httpx2
    import uvicorn

    from fatsecret_mcp.fatsecret.client import FatSecretApi
    from fatsecret_mcp.http import build_http_app

    async with httpx2.AsyncClient(timeout=20) as http:
        fatsecret = FatSecretApi(
            consumer=household.consumer,
            http=http,
        )
        app = build_http_app(household, fatsecret, metadata_http=http)
        config = uvicorn.Config(
            app=app,
            host=host,
            port=port,
            access_log=False,
            log_level="warning",
        )
        server = uvicorn.Server(config)

        await server.serve()


if __name__ == "__main__":
    main()
