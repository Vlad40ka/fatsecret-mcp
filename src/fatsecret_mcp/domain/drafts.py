"""Amounts and nutrition of proposed Food Entries."""

from __future__ import annotations

import math

from fatsecret_mcp.domain.models import DraftEntry, DraftItem, Nutrition, Serving

MAX_METRIC_AMOUNT = 10000
MAX_KCAL = 20000


class InvalidDraftItem(ValueError):
    """A proposed amount or its Serving cannot be used in a Draft."""


def draft_item(
    food_id: str, name: str, serving: Serving, number_of_units: float, assumption: str | None
) -> DraftItem:
    if not math.isfinite(serving.number_of_units) or serving.number_of_units <= 0:
        raise InvalidDraftItem("Serving has no valid positive unit count. Choose another Serving.")

    ratio = number_of_units / serving.number_of_units
    nutrition = Nutrition(
        kcal=serving.nutrition.kcal * ratio,
        protein=serving.nutrition.protein * ratio,
        fat=serving.nutrition.fat * ratio,
        carbohydrate=serving.nutrition.carbohydrate * ratio,
    )

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
