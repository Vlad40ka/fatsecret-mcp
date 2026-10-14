"""The shapes of the tools' inputs and results, as Claude sees them.

The MCP SDK publishes them as each tool's input and output schema. In the results, numbers are rounded
for reading, and the `of` methods turn domain objects into these shapes.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from pydantic import BaseModel, Field

from fatsecret_mcp.domain.models import DaySummary, FoodEntry, Meal, Nutrition, PeriodDaySummary

# --- Inputs ----------------------------------------------------------------

DiaryDayOrToday = Annotated[
    date | None,
    Field(description="The Diary Day, as YYYY-MM-DD. Omit for today in the User's timezone."),
]

PeriodStart = Annotated[date, Field(description="First Diary Day in the period, inclusive, as YYYY-MM-DD.")]
PeriodEnd = Annotated[date, Field(description="Last Diary Day in the period, inclusive, as YYYY-MM-DD.")]


# --- Results ---------------------------------------------------------------


class Amounts(BaseModel):
    """kcal and Macros (grams)."""

    kcal: int
    protein: float
    fat: float
    carbohydrate: float

    @classmethod
    def of(cls, nutrition: Nutrition) -> Amounts:
        return cls(
            kcal=round(nutrition.kcal),
            protein=_rounded(nutrition.protein),
            fat=_rounded(nutrition.fat),
            carbohydrate=_rounded(nutrition.carbohydrate),
        )


class DiaryEntry(BaseModel):
    name: str
    amount: str = Field(description='The amount in its Serving, e.g. "150 g" or "2 servings".')
    kcal: int
    protein: float
    fat: float
    carbohydrate: float

    @classmethod
    def of(cls, entry: FoodEntry) -> DiaryEntry:
        amounts = Amounts.of(entry.nutrition)

        return cls(
            name=entry.name,
            amount=entry.amount,
            kcal=amounts.kcal,
            protein=amounts.protein,
            fat=amounts.fat,
            carbohydrate=amounts.carbohydrate,
        )


class MealEntries(BaseModel):
    meal: Meal
    food_entries: list[DiaryEntry]

    @classmethod
    def of(cls, meal: Meal, entries: list[FoodEntry]) -> MealEntries:
        food_entries = [DiaryEntry.of(entry) for entry in entries]

        return cls(meal=meal, food_entries=food_entries)


class DiaryDay(BaseModel):
    diary_day: date
    meals: list[MealEntries] = Field(description="Meals that have Food Entries, in the order of the day.")
    totals: Amounts
    daily_targets: Amounts
    remaining: Amounts = Field(description="Daily Targets minus totals; negative once a target is exceeded.")

    @classmethod
    def of(cls, summary: DaySummary) -> DiaryDay:
        meals = [MealEntries.of(meal, entries) for meal, entries in summary.meals.items()]

        return cls(
            diary_day=summary.diary_day,
            meals=meals,
            totals=Amounts.of(summary.totals),
            daily_targets=Amounts.of(summary.daily_targets),
            remaining=Amounts.of(summary.remaining),
        )


class PeriodDay(BaseModel):
    diary_day: date
    has_food_entries: bool = Field(description="False when the Diary Day has no Food Entries.")
    totals: Amounts
    daily_targets: Amounts

    @classmethod
    def of(cls, summary: PeriodDaySummary) -> PeriodDay:
        return cls(
            diary_day=summary.diary_day,
            has_food_entries=summary.has_food_entries,
            totals=Amounts.of(summary.totals),
            daily_targets=Amounts.of(summary.daily_targets),
        )


class PeriodSummary(BaseModel):
    from_day: date
    to_day: date
    days: list[PeriodDay] = Field(description="Every Diary Day in the period, in chronological order.")


def _rounded(grams: float) -> float:
    # Adding 0.0 turns -0.0 into 0.0.
    return round(grams, 1) + 0.0
