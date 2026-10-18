"""Proposed Food Entries: their amounts and nutrition, the Diary Day they're for, and their totals."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

from fatsecret_mcp.domain.day_summary import total_nutrition
from fatsecret_mcp.domain.models import DraftEntry, DraftItem, DraftTotals, FoodEntry, Nutrition, Serving

MAX_METRIC_AMOUNT = 10000
MAX_KCAL = 20000
DRAFT_LIFETIME = timedelta(minutes=30)
MAX_DAYS_BACK = 365
MAX_DAYS_AHEAD = 7


class InvalidDraftItem(ValueError):
    """A proposed amount or its Serving cannot be used in a Draft."""


class UnlikelyDiaryDay(ValueError):
    """A Diary Day too far from today to be meant."""


def check_diary_day(diary_day: date, today: date) -> None:
    """Allow Diary Days from a year back to a week ahead."""
    days_ahead = (diary_day - today).days

    if days_ahead < -MAX_DAYS_BACK or days_ahead > MAX_DAYS_AHEAD:
        raise UnlikelyDiaryDay(
            "A Draft's Diary Day must be at most a year back or a week ahead. Check the date with the User."
        )


def draft_totals(
    entries: Sequence[FoodEntry], items: Sequence[DraftItem], daily_targets: Nutrition
) -> DraftTotals:
    """The items added up, and the Diary Day's totals and what remains once they're added."""
    item_nutrition = [item.nutrition for item in items]
    added = sum(item_nutrition, Nutrition())
    consumed = total_nutrition(entries) + added

    return DraftTotals(
        added=added,
        consumed=consumed,
        daily_targets=daily_targets,
        remaining=daily_targets - consumed,
    )


def draft_expiry(now: datetime) -> datetime:
    return now.astimezone(UTC) + DRAFT_LIFETIME


def draft_item(
    food_id: str, name: str, serving: Serving, number_of_units: float, assumption: str | None
) -> DraftItem:
    if not math.isfinite(serving.number_of_units) or serving.number_of_units <= 0:
        raise InvalidDraftItem("Serving has no valid positive unit count. Choose another Serving.")

    ratio = number_of_units / serving.number_of_units
    nutrition = serving.nutrition.scaled(ratio)

    _check_amount(serving, ratio, nutrition)

    amount = _amount(serving, ratio)
    entry = DraftEntry(
        food_id=food_id,
        serving_id=serving.id,
        number_of_units=number_of_units,
        entry_name=name,
    )

    return DraftItem(
        entry=entry,
        serving=serving.description,
        amount=amount,
        nutrition=nutrition,
        assumption=assumption,
    )


def _check_amount(serving: Serving, ratio: float, nutrition: Nutrition) -> None:
    if not math.isfinite(nutrition.kcal) or nutrition.kcal > MAX_KCAL:
        raise InvalidDraftItem("Amount is implausibly large: at most 20000 kcal per item.")

    if serving.metric_amount is not None:
        amount = serving.metric_amount * ratio

        if not math.isfinite(amount) or amount > MAX_METRIC_AMOUNT:
            raise InvalidDraftItem("Amount is implausibly large: at most 10000 g or ml per item.")


def _amount(serving: Serving, ratio: float) -> str:
    if serving.metric_amount is not None and serving.metric_unit is not None:
        metric_amount = serving.metric_amount * ratio

        return f"{metric_amount:g} {serving.metric_unit}"

    return f"{ratio:g} x ({serving.description})"
