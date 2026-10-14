"""The shapes of the tools' inputs and results, as Claude sees them.

The MCP SDK publishes them as each tool's input and output schema. Diary totals are rounded for
reading; the Servings search_foods finds retain their precision. The `of` methods turn domain objects
into these shapes.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from fatsecret_mcp.domain.models import (
    DaySummary,
    Food,
    FoodEntry,
    Meal,
    Nutrition,
    PeriodDaySummary,
    RecentFood,
    Serving,
)

ATTRIBUTION = "Powered by fatsecret Platform API"

# --- Inputs ----------------------------------------------------------------

DiaryDayOrToday = Annotated[
    date | None,
    Field(description="The Diary Day, as YYYY-MM-DD. Omit for today in the User's timezone."),
]

PeriodStart = Annotated[date, Field(description="First Diary Day in the period, inclusive, as YYYY-MM-DD.")]
PeriodEnd = Annotated[date, Field(description="Last Diary Day in the period, inclusive, as YYYY-MM-DD.")]

FoodDescription = Annotated[str, Field(min_length=1, description="The description as the User said it.")]
EnglishRendering = Annotated[
    str | None,
    Field(min_length=1, description="English rendering for the catalog. Pass this for non-English input."),
]


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


class FoodServing(BaseModel):
    serving_id: str
    description: str
    metric_amount: float | None
    metric_unit: str | None
    number_of_units: float = Field(
        description="FatSecret units in this described Serving, e.g. 100 for '100 g'."
    )
    kcal: float
    protein: float
    fat: float
    carbohydrate: float

    @classmethod
    def of(cls, serving: Serving) -> FoodServing:
        return cls(
            serving_id=serving.id,
            description=serving.description,
            metric_amount=serving.metric_amount,
            metric_unit=serving.metric_unit,
            number_of_units=serving.number_of_units,
            kcal=serving.nutrition.kcal,
            protein=serving.nutrition.protein,
            fat=serving.nutrition.fat,
            carbohydrate=serving.nutrition.carbohydrate,
        )


class FoodMatch(BaseModel):
    source: Literal["recent_foods", "catalog"] = Field(
        description="recent_foods: logged by the User in recent weeks; catalog: FatSecret's catalog."
    )
    food_id: str
    name: str = Field(description="For a Recent Food, the name it was logged with.")
    brand: str | None
    servings: list[FoodServing] = Field(
        description=(
            "Loggable Servings, with kcal and Macros per described Serving. "
            "A Recent Food's are the ones the User logged it with, each as last logged."
        )
    )

    @classmethod
    def of_recent_food(cls, food: RecentFood) -> FoodMatch:
        food_servings = [FoodServing.of(serving) for serving in food.servings]

        return cls(
            source="recent_foods",
            food_id=food.food_id,
            name=food.name,
            brand=None,
            servings=food_servings,
        )

    @classmethod
    def of_catalog_food(cls, food: Food, servings: list[Serving]) -> FoodMatch:
        food_servings = [FoodServing.of(serving) for serving in servings]

        return cls(
            source="catalog",
            food_id=food.id,
            name=food.name,
            brand=food.brand,
            servings=food_servings,
        )


class FoodSearch(BaseModel):
    status: Literal["found", "not_found"]
    matches: list[FoodMatch]
    attribution: str = ATTRIBUTION


def _rounded(grams: float) -> float:
    # Adding 0.0 turns -0.0 into 0.0.
    return round(grams, 1) + 0.0
