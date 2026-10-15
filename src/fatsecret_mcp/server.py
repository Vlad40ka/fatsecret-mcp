"""The MCP server: its name, its instructions for Claude, and the tools it offers."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from importlib.metadata import version

from mcp.server import MCPServer
from pydantic import SecretStr

from fatsecret_mcp.config.models import DEFAULT_RECENT_FOOD_DAYS, User
from fatsecret_mcp.domain.diary_day import Clock, system_clock
from fatsecret_mcp.fatsecret.diary import UserDiary
from fatsecret_mcp.fatsecret.gateway import FatSecretGateway
from fatsecret_mcp.tools import (
    confirm_draft,
    draft_entries,
    get_diary_day,
    get_period_summary,
    search_foods,
)
from fatsecret_mcp.tools.schemas import ATTRIBUTION

DESCRIPTION = (
    "Unofficial MCP server for a FatSecret Food Diary, not affiliated with or endorsed by FatSecret. "
    f"{ATTRIBUTION}."
)

INSTRUCTIONS = f"""\
This server reads the User's own FatSecret Food Diary, the one they see in the FatSecret app.

- get_diary_day shows one Diary Day: Food Entries grouped by Meal, the day's totals, the User's Daily \
Targets and what remains. Omit diary_day for today: the server knows the User's timezone.
- get_period_summary shows daily totals and Daily Targets for an inclusive range of up to 31 Diary \
Days. Days with has_food_entries=false have no Food Entries. Compare periods with two calls.
- search_foods finds Foods with loggable Servings: the User's Recent Foods first, then the catalog. \
Pass an English rendering for non-English descriptions. A not_found result means there are no \
loggable matches.
- draft_entries proposes Food Entries, assumptions, a Meal and Diary Day, plus the day after adding \
against Daily Targets. Show the Draft and ask about a suggested Meal. When previous_day_suggested \
is true, ask about Snacks on the previous Diary Day. Corrections need a new Draft. This tool does \
not write to the Food Diary.
- confirm_draft writes a Draft's Food Entries to the Food Diary. Call it only after the User \
explicitly approved that Draft in their latest message.
- Macros are protein, fat and carbohydrate, in grams.
- Report the numbers neutrally and in the User's language. This server gives no diet, nutrition or \
health advice.

{ATTRIBUTION}.
"""


def build_server(
    user: User,
    fatsecret: FatSecretGateway,
    clock: Clock = system_clock,
    recent_food_days: int = DEFAULT_RECENT_FOOD_DAYS,
    *,
    signing_key: SecretStr,
) -> MCPServer:
    """The MCP server acting as `user`: it reads their diary and writes the Drafts they confirm."""
    diary = UserDiary(
        fatsecret=fatsecret,
        user=user.fatsecret,
        timezone=user.timezone,
        clock=clock,
        recent_food_days=recent_food_days,
    )

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncGenerator[None]:
        async with diary.cache_lifespan():
            yield

    server = MCPServer(
        name="fatsecret-mcp",
        title="FatSecret",
        description=DESCRIPTION,
        instructions=INSTRUCTIONS,
        version=version("fatsecret-mcp"),
        lifespan=lifespan,
    )

    get_diary_day.register(server, user, diary, clock)
    get_period_summary.register(server, user, diary)
    search_foods.register(server, fatsecret, diary)
    draft_entries.register(server, user, fatsecret, diary, clock, signing_key)
    confirm_draft.register(server, user, fatsecret, diary, clock, signing_key)

    return server
