"""get_diary_day: one Diary Day, with totals against the User's Daily Targets."""

from __future__ import annotations

from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, ToolAnnotations

from fatsecret_mcp.config.models import User
from fatsecret_mcp.domain.day_summary import summarize_day
from fatsecret_mcp.domain.diary_day import Clock, today
from fatsecret_mcp.fatsecret.errors import FatSecretError
from fatsecret_mcp.fatsecret.gateway import FatSecretGateway
from fatsecret_mcp.tools.schemas import DiaryDay, DiaryDayOrToday, as_tool_result


def register(server: MCPServer, user: User, fatsecret: FatSecretGateway, clock: Clock) -> None:
    @server.tool(annotations=ToolAnnotations(title="Get Diary Day", read_only_hint=True))
    async def get_diary_day(diary_day: DiaryDayOrToday = None) -> Annotated[CallToolResult, DiaryDay]:
        """Read the User's Food Diary for one Diary Day.

        Returns the Food Entries grouped by Meal (name, amount, kcal and Macros), the day's totals, the
        User's Daily Targets and what remains of them. Use it for questions like "what have I eaten
        today?" or "how much is left?".
        """
        day = diary_day or today(clock(), user.timezone)

        try:
            entries = await fatsecret.list_food_entries(user.fatsecret, day)
        except FatSecretError as error:
            raise ToolError(str(error)) from error

        summary = summarize_day(day, entries, user.daily_targets.as_nutrition())

        return as_tool_result(DiaryDay.of(summary))
