"""fatsecret-mcp: an MCP server for a Household's FatSecret Food Diaries, and the Operator's commands.

Without a command it serves over stdio, as Claude Code starts it.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence

import anyio

from fatsecret_mcp.cli.serve import serve
from fatsecret_mcp.config.loader import USERS_VAR, HouseholdConfigError, load_consumer_key
from fatsecret_mcp.fatsecret.errors import FatSecretError


def main(argv: Sequence[str] | None = None) -> None:
    command_line = sys.argv[1:] if argv is None else list(argv)
    arguments = _parser().parse_args(command_line or ["serve"])
    environ = os.environ

    try:
        if arguments.command == "add-user":
            anyio.run(_add_user, environ)
        elif arguments.command == "check":
            if not anyio.run(_check, environ):
                sys.exit(1)
        else:
            anyio.run(_serve, environ, arguments)
    except (HouseholdConfigError, FatSecretError) as error:
        sys.exit(f"fatsecret-mcp: {error}")
    except (EOFError, KeyboardInterrupt):
        sys.exit("fatsecret-mcp: cancelled")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fatsecret-mcp", description=__doc__)
    commands = parser.add_subparsers(dest="command", metavar="command")

    commands.add_parser("add-user", help="onboard a User: FatSecret sign-in with a PIN, settings, password")
    commands.add_parser("check", help="check the configuration and every User's FatSecret connection")

    serve_command = commands.add_parser("serve", help="run the server (the default, over stdio)")

    serve_command.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    serve_command.add_argument("--host", default="127.0.0.1")
    serve_command.add_argument("--port", type=int, default=8000)

    return parser


async def _add_user(environ: Mapping[str, str]) -> None:
    import httpx2

    from fatsecret_mcp.cli.add_user import add_user
    from fatsecret_mcp.cli.console import Terminal
    from fatsecret_mcp.fatsecret.client import FatSecretApi

    consumer = load_consumer_key(environ)

    async with httpx2.AsyncClient(timeout=20) as http:
        await add_user(Terminal(), FatSecretApi(consumer, http), existing_users=environ.get(USERS_VAR))


async def _check(environ: Mapping[str, str]) -> bool:
    import httpx2

    from fatsecret_mcp.cli.check import check
    from fatsecret_mcp.cli.console import Terminal
    from fatsecret_mcp.fatsecret.client import FatSecretApi

    consumer = load_consumer_key(environ)

    async with httpx2.AsyncClient(timeout=20) as http:
        return await check(Terminal(), environ, FatSecretApi(consumer, http))


async def _serve(environ: Mapping[str, str], arguments: argparse.Namespace) -> None:
    await serve(
        environ,
        transport=arguments.transport,
        host=arguments.host,
        port=arguments.port,
    )
