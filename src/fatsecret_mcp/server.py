"""The MCP server: its name, its instructions for Claude, and the tools it offers."""

from __future__ import annotations

from importlib.metadata import version

from mcp.server import MCPServer

from fatsecret_mcp.config.models import User
from fatsecret_mcp.domain.diary_day import Clock, system_clock
from fatsecret_mcp.fatsecret.gateway import FatSecretGateway
from fatsecret_mcp.tools import get_diary_day

ATTRIBUTION = "Powered by fatsecret Platform API"

DESCRIPTION = (
    "Unofficial MCP server for a FatSecret Food Diary, not affiliated with or endorsed by FatSecret. "
    f"{ATTRIBUTION}."
)

INSTRUCTIONS = f"""\
This server reads the User's own FatSecret Food Diary, the one they see in the FatSecret app.

- get_diary_day shows one Diary Day: Food Entries grouped by Meal, the day's totals, the User's Daily \
Targets and what remains. Omit diary_day for today: the server knows the User's timezone.
- Macros are protein, fat and carbohydrate, in grams.
- Report the numbers neutrally and in the User's language. This server gives no diet, nutrition or \
health advice.

{ATTRIBUTION}.
"""


def build_server(user: User, fatsecret: FatSecretGateway, clock: Clock = system_clock) -> MCPServer:
    """The MCP server acting as `user`, reading their Food Diary through `fatsecret`."""
    server = MCPServer(
        name="fatsecret-mcp",
        title="FatSecret",
        description=DESCRIPTION,
        instructions=INSTRUCTIONS,
        version=version("fatsecret-mcp"),
    )

    get_diary_day.register(server, user, fatsecret, clock)
    return server
