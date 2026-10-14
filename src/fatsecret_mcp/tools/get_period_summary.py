"""get_period_summary: daily totals against Daily Targets for up to 31 Diary Days."""

from __future__ import annotations

from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, ToolAnnotations

from fatsecret_mcp.config.models import User
from fatsecret_mcp.domain.period_summary import days_in_period, summarize_period
from fatsecret_mcp.fatsecret.diary import UserDiary
from fatsecret_mcp.fatsecret.errors import FatSecretError
from fatsecret_mcp.tools.schemas import PeriodDay, PeriodEnd, PeriodStart, PeriodSummary, as_tool_result


def register(server: MCPServer, user: User, diary: UserDiary) -> None:
    @server.tool(annotations=ToolAnnotations(title="Get Period Summary", read_only_hint=True))
    async def get_period_summary(
        from_day: PeriodStart, to_day: PeriodEnd, refresh: bool = False
    ) -> Annotated[CallToolResult, PeriodSummary]:
        """Read daily kcal and Macro totals alongside the User's configured Daily Targets.

        Includes both endpoints, at most 31 Diary Days, and marks days without Food Entries.
        To compare two periods, call once for each period. Returns numbers without evaluation.
        Past days reuse Food Entries already read during this server session, for up to twelve hours.
        Missing days use FatSecret's month totals; today is always fresh. Set refresh=true when the
        User requests fresh data or says they changed these days in the FatSecret app.
        """
        try:
            days = days_in_period(from_day, to_day)
        except ValueError as error:
            raise ToolError(str(error)) from error

        try:
            totals = await diary.period_totals(days, refresh=refresh)
        except FatSecretError as error:
            raise ToolError(str(error)) from error

        summaries = summarize_period(days, totals, user.daily_targets.as_nutrition())
        period_days = [PeriodDay.of(summary) for summary in summaries]

        summary = PeriodSummary(
            from_day=from_day,
            to_day=to_day,
            days=period_days,
        )

        return as_tool_result(summary)
