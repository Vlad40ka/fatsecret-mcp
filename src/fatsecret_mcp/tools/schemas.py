"""The shapes of the tools' inputs and results, as Claude sees them.

The MCP SDK publishes them as each tool's input and output schema. Diary totals are rounded for
reading; the Servings search_foods finds retain their precision. The `of` methods turn domain objects
into these shapes.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Annotated, Literal

from mcp_types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict, Field

from fatsecret_mcp.domain.models import (
    DaySummary,
    DraftItem,
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


class FoodDraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    food_id: str = Field(min_length=1)
    serving_id: str = Field(min_length=1)
    number_of_units: float = Field(
        gt=0,
        le=10000,
        allow_inf_nan=False,
    )
    assumption: str | None = None
    entry_name: str | None = Field(
        default=None,
        min_length=1,
        description="Copy the chosen match's name from search_foods. Omitted names use Recent Foods first.",
    )


DraftItems = Annotated[list[FoodDraftInput], Field(min_length=1)]


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


class ProposedEntry(BaseModel):
    food_id: str
    serving_id: str
    name: str
    serving: str
    number_of_units: float
    amount: str
    kcal: int
    protein: float
    fat: float
    carbohydrate: float
    assumption: str | None

    @classmethod
    def of(cls, item: DraftItem) -> ProposedEntry:
        amounts = Amounts.of(item.nutrition)

        return cls(
            food_id=item.entry.food_id,
            serving_id=item.entry.serving_id,
            name=item.entry.entry_name,
            serving=item.serving,
            number_of_units=item.entry.number_of_units,
            amount=item.amount,
            kcal=amounts.kcal,
            protein=amounts.protein,
            fat=amounts.fat,
            carbohydrate=amounts.carbohydrate,
            assumption=item.assumption,
        )


class DayAfter(BaseModel):
    consumed: Amounts
    daily_targets: Amounts
    remaining: Amounts


class FoodDraft(BaseModel):
    draft_id: str
    expires_at: datetime
    meal: Meal
    meal_suggested: bool
    diary_day: date
    previous_day_suggested: bool
    items: list[ProposedEntry]
    totals: Amounts
    day_after: DayAfter
    attribution: str = ATTRIBUTION


def as_tool_result(result: BaseModel) -> CallToolResult:
    """`result` as the tool's answer: structured, plus the same JSON as compact text for the model.

    The SDK's own text copy is indented, which costs about 60% more tokens and carries nothing more.
    """
    structured = result.model_dump(mode="json")
    text = json.dumps(structured, ensure_ascii=False, separators=(",", ":"))

    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=structured,
    )


def _rounded(grams: float) -> float:
    # Adding 0.0 turns -0.0 into 0.0.
    return round(grams, 1) + 0.0
