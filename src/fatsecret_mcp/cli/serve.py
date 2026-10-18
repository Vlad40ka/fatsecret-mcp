"""`serve`: run the server locally over stdio, or serve the Household's connector over HTTP.

Heavy modules are imported only when needed, so the HTTP server starts quickly after a cold start.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal

from fatsecret_mcp.config.loader import acting_user, load_household, validate_http_household

if TYPE_CHECKING:
    from fatsecret_mcp.config.models import Household, User

Transport = Literal["stdio", "http"]


async def serve(environ: Mapping[str, str], *, transport: Transport, host: str, port: int) -> None:
    household = load_household(environ)

    if transport == "http":
        validate_http_household(household)

        await _serve_http(household, host, port)
    else:
        await _serve_stdio(household, acting_user(household, environ))


async def _serve_stdio(household: Household, user: User) -> None:
    import httpx2

    from fatsecret_mcp.fatsecret.client import FatSecretApi
    from fatsecret_mcp.server import build_server

    async with httpx2.AsyncClient(timeout=20) as http:
        fatsecret = FatSecretApi(
            consumer=household.consumer,
            http=http,
        )
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
        app = build_http_app(household, fatsecret)
        config = uvicorn.Config(
            app=app,
            host=host,
            port=port,
            access_log=False,
            log_level="warning",
        )
        server = uvicorn.Server(config)

        await server.serve()
