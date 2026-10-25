"""Repeated Food Entries: a Food Entry from the Food Diary, proposed again in grams or millilitres.

The Food, Serving, name and nutrition come from the source entry, so a Food the catalog can't
return can still be logged. A changed amount scales its FatSecret units and nutrition alike.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from datetime import date

from fatsecret_mcp.domain.drafts import MAX_DAYS_BACK, InvalidDraftItem, UnlikelyDiaryDay, check_plausible
from fatsecret_mcp.domain.models import DraftEntry, DraftItem, FoodEntry, MetricAmount, RepeatedFrom, Serving

METRIC_UNITS = ("g", "ml")
WRITTEN_METRIC_AMOUNT = re.compile(rf"(?P<amount>\d+(?:\.\d+)?) ?(?P<unit>{'|'.join(METRIC_UNITS)})")
UNIT_DECIMALS = 3


def check_source_day(source_day: date, today: date) -> None:
    """Allow repeating from today back to a year ago."""
    days_back = (today - source_day).days

    if days_back < 0:
        raise UnlikelyDiaryDay("A Food Entry can only be repeated from today or an earlier Diary Day.")

    if days_back > MAX_DAYS_BACK:
        raise UnlikelyDiaryDay("A Food Entry can only be repeated from at most a year back.")


def source_entry(entries: Sequence[FoodEntry], food_entry_id: str, source_day: date) -> FoodEntry:
    """The Food Entry with this id among the source Diary Day's entries."""
    for entry in entries:
        if entry.id == food_entry_id:
            return entry

    raise InvalidDraftItem(
        f"Food Entry {food_entry_id} isn't on {source_day.isoformat()}. Read that Diary Day again with "
        "get_diary_day and refresh=true, and use a food_entry_id it shows."
    )


def written_metric_amount(entry: FoodEntry) -> MetricAmount | None:
    """The entry's amount when FatSecret wrote it in grams or ml, e.g. "150 g"."""
    match = WRITTEN_METRIC_AMOUNT.fullmatch(entry.amount)

    if match is None:
        return None

    amount = float(match["amount"])

    if amount <= 0:
        return None

    return MetricAmount(
        amount=amount,
        unit=match["unit"],
    )


def serving_metric_amount(entry: FoodEntry, serving: Serving) -> MetricAmount | None:
    """The entry's amount in grams or ml, from the weight of its Serving."""
    if serving.metric_amount is None or serving.metric_unit not in METRIC_UNITS:
        return None

    if not _positive(serving.metric_amount) or not _positive(serving.number_of_units):
        return None

    amount = serving.metric_amount * entry.number_of_units / serving.number_of_units

    return MetricAmount(
        amount=amount,
        unit=serving.metric_unit,
    )


def repeated_item(
    source: FoodEntry,
    source_amount: MetricAmount | None,
    eaten_amount: float | None,
    assumption: str | None,
) -> DraftItem:
    """The Draft item logging `source` again: `eaten_amount` grams or ml, or its own amount when None.

    `source_amount` is the source's amount in grams or ml; None when its weight is unknown.
    """
    if not _positive(source.number_of_units):
        raise InvalidDraftItem(f"{source.name} has no valid unit count in the Food Diary to repeat.")

    amount = _eaten_metric_amount(source, source_amount, eaten_amount)
    units = round(source.number_of_units * _ratio(amount, source_amount), UNIT_DECIMALS)

    if units <= 0:
        raise InvalidDraftItem("Amount is too small to log.")

    # FatSecret logs nutrition for the units it's given, so the rounded units scale it.
    nutrition = source.nutrition.scaled(units / source.number_of_units)
    grams_or_ml = amount.amount if amount is not None else None

    check_plausible(nutrition, grams_or_ml)

    entry = DraftEntry(
        food_id=source.food_id,
        serving_id=source.serving_id,
        number_of_units=units,
        entry_name=source.name,
    )
    repeated_from = RepeatedFrom(
        diary_day=source.diary_day,
        meal=source.meal,
    )

    return DraftItem(
        entry=entry,
        serving=_written(source, source_amount),
        amount=_written(source, amount),
        nutrition=nutrition,
        assumption=assumption,
        repeated_from=repeated_from,
    )


def _eaten_metric_amount(
    source: FoodEntry, source_amount: MetricAmount | None, eaten_amount: float | None
) -> MetricAmount | None:
    """The amount eaten this time in grams or ml; None when the source's weight is unknown."""
    if eaten_amount is None:
        return source_amount

    if source_amount is None:
        raise InvalidDraftItem(
            f"The weight of {source.name} ({source.amount}) is unknown, so it can only be repeated in "
            "its own amount. Omit metric_amount, or search the catalog for it in grams."
        )

    return MetricAmount(
        amount=eaten_amount,
        unit=source_amount.unit,
    )


def _ratio(amount: MetricAmount | None, source_amount: MetricAmount | None) -> float:
    if amount is None or source_amount is None:
        return 1

    return amount.amount / source_amount.amount


def _written(source: FoodEntry, amount: MetricAmount | None) -> str:
    """`amount` as "75 g", or the source's own amount when its weight is unknown."""
    if amount is None:
        return source.amount

    return f"{amount.amount:g} {amount.unit}"


def _positive(value: float) -> bool:
    return math.isfinite(value) and value > 0
