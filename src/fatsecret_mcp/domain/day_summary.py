"""One Diary Day at a glance: Food Entries by Meal, totals, Daily Targets and what remains."""

from __future__ import annotations

from datetime import date

from fatsecret_mcp.domain.models import DaySummary, FoodEntry, Meal, Nutrition


def summarize_day(diary_day: date, entries: list[FoodEntry], daily_targets: Nutrition) -> DaySummary:
    totals = sum((entry.nutrition for entry in entries), Nutrition())

    return DaySummary(
        diary_day=diary_day,
        meals=_by_meal(entries),
        totals=totals,
        daily_targets=daily_targets,
        remaining=daily_targets - totals,
    )


def _by_meal(entries: list[FoodEntry]) -> dict[Meal, list[FoodEntry]]:
    """Food Entries grouped by Meal, leaving out Meals without any, in the order of the day."""
    meals: dict[Meal, list[FoodEntry]] = {}

    for meal in Meal:
        meal_entries = [entry for entry in entries if entry.meal is meal]

        if meal_entries:
            meals[meal] = meal_entries

    return meals
