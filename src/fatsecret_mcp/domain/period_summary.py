"""A bounded period of Diary Days, including days without Food Entries."""

from __future__ import annotations

from datetime import date, timedelta

from fatsecret_mcp.domain.models import Nutrition, PeriodDaySummary


def days_in_period(from_day: date, to_day: date) -> list[date]:
    length = (to_day - from_day).days + 1

    if length < 1:
        raise ValueError("from_day must be on or before to_day.")
    if length > 31:
        raise ValueError("A period can contain at most 31 Diary Days, including both endpoints.")

    days = [from_day + timedelta(days=offset) for offset in range(length)]

    return days


def summarize_period(
    days: list[date], totals: dict[date, Nutrition], daily_targets: Nutrition
) -> list[PeriodDaySummary]:
    summaries = [
        PeriodDaySummary(
            diary_day=day,
            has_food_entries=day in totals,
            totals=totals.get(day, Nutrition()),
            daily_targets=daily_targets,
        )
        for day in days
    ]

    return summaries
