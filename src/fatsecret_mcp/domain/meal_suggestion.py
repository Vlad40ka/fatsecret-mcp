"""Meal suggestions from the User's local time; midnight keeps the current Diary Day."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, time
from zoneinfo import ZoneInfo

from fatsecret_mcp.domain.models import Meal


def suggest_meal(now: datetime, timezone: ZoneInfo, meal_starts: Mapping[Meal, time]) -> Meal:
    local_time = now.astimezone(timezone).time()
    starts = sorted(meal_starts.items(), key=lambda item: item[1])
    suggested = starts[-1][0]

    for meal, start in starts:
        if local_time >= start:
            suggested = meal

    return suggested


def ask_about_previous_day(now: datetime, timezone: ZoneInfo) -> bool:
    return now.astimezone(timezone).hour < 4
