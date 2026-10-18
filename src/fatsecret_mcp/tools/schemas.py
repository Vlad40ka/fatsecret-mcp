"""The shapes of the tools' inputs and results, as Claude sees them.

The MCP SDK publishes them as each tool's input and output schema. Diary totals are rounded for
reading; the Servings search_foods finds retain their precision. The `of` methods turn domain objects
into these shapes.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Annotated, Any, Literal

from mcp_types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag

from fatsecret_mcp.domain.models import (
    Confirmation,
    ConfirmedEntry,
    DaySummary,
    DraftItem,
    Food,
    FoodEntry,
    Meal,
    Nutrition,
    NutritionLabel,
    PeriodDaySummary,
    Serving,
    StandIn,
)

ATTRIBUTION = "Powered by fatsecret Platform API"

# --- Inputs ----------------------------------------------------------------

# Lists and text are bounded, so one tool call makes a bounded number of FatSecret requests.
MAX_DRAFT_ITEMS = 20
# A search description or its English rendering.
MAX_SEARCH_LENGTH = 200
# A product name or a Stand-in's search phrase.
MAX_NAME_LENGTH = 100
MAX_ASSUMPTION_LENGTH = 300
MAX_ID_LENGTH = 20
# Room for twenty items with long, non-ASCII catalog names, sealed and Base64-encoded.
MAX_DRAFT_ID_LENGTH = 32768

DiaryDayOrToday = Annotated[
    date | None,
    Field(description="The Diary Day, as YYYY-MM-DD. Omit for today in the User's timezone."),
]

PeriodStart = Annotated[date, Field(description="First Diary Day in the period, inclusive, as YYYY-MM-DD.")]
PeriodEnd = Annotated[date, Field(description="Last Diary Day in the period, inclusive, as YYYY-MM-DD.")]

FoodDescription = Annotated[
    str,
    Field(
        min_length=1,
        max_length=MAX_SEARCH_LENGTH,
        description="The description of one food as the User said it.",
    ),
]
EnglishRendering = Annotated[
    str | None,
    Field(
        min_length=1,
        max_length=MAX_SEARCH_LENGTH,
        description="English rendering for the catalog. Pass this for non-English input.",
    ),
]
CatalogId = Annotated[str, Field(min_length=1, max_length=MAX_ID_LENGTH)]
Assumption = Annotated[str | None, Field(max_length=MAX_ASSUMPTION_LENGTH)]


class FoodDraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    food_id: CatalogId
    serving_id: CatalogId
    number_of_units: float = Field(
        gt=0,
        le=10000,
        allow_inf_nan=False,
    )
    assumption: Assumption = None


class NutritionLabelValues(BaseModel):
    """kcal and Macros per 100 g of the product."""

    model_config = ConfigDict(extra="forbid")

    kcal: float = Field(gt=0, le=900, allow_inf_nan=False)
    protein: float = Field(ge=0, le=100, allow_inf_nan=False)
    fat: float = Field(ge=0, le=100, allow_inf_nan=False)
    carbohydrate: float = Field(ge=0, le=100, allow_inf_nan=False)
    estimated: bool = Field(description="True when estimated without the packaging, not read from it.")

    def as_label(self) -> NutritionLabel:
        per_100_g = Nutrition(
            kcal=self.kcal,
            protein=self.protein,
            fat=self.fat,
            carbohydrate=self.carbohydrate,
        )

        return NutritionLabel(per_100_g=per_100_g, estimated=self.estimated)


class NutritionLabelDraftInput(BaseModel):
    """A product missing from the catalog, logged as a Stand-in under its own name."""

    model_config = ConfigDict(extra="forbid")

    product_name: str = Field(
        min_length=1,
        max_length=MAX_NAME_LENGTH,
        description="The product's name, given to its Food Entry.",
    )
    nutrition_label: NutritionLabelValues
    grams: float = Field(
        gt=0,
        le=10000,
        allow_inf_nan=False,
        description="Grams of the product eaten.",
    )
    search_phrase: str = Field(
        min_length=1,
        max_length=MAX_NAME_LENGTH,
        description='English catalog search for a similar generic Food, e.g. "protein bar".',
    )
    assumption: Assumption = None


# Fields only a Nutrition Label item has, so one missing its label is still told what it lacks.
NUTRITION_LABEL_ITEM_FIELDS = {"product_name", "nutrition_label", "grams", "search_phrase"}


def _draft_item_kind(item: Any) -> str:
    """Tells the two kinds of item apart by the fields only a Nutrition Label item has."""
    if isinstance(item, NutritionLabelDraftInput):
        return "nutrition_label"

    if isinstance(item, dict):
        present = [field in item for field in NUTRITION_LABEL_ITEM_FIELDS]

        if any(present):
            return "nutrition_label"

    return "food"


DraftItemInput = Annotated[
    Annotated[FoodDraftInput, Tag("food")] | Annotated[NutritionLabelDraftInput, Tag("nutrition_label")],
    Discriminator(_draft_item_kind),
]

DraftItems = Annotated[
    list[DraftItemInput],
    Field(
        min_length=1,
        max_length=MAX_DRAFT_ITEMS,
        description=f"At most {MAX_DRAFT_ITEMS} items. Split a larger meal into several Drafts.",
    ),
]

DraftId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=MAX_DRAFT_ID_LENGTH,
        description="The draft_id returned by draft_entries.",
    ),
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
    food_id: str
    name: str
    brand: str | None
    servings: list[FoodServing] = Field(
        description="Loggable Servings, with kcal and Macros per described Serving."
    )

    @classmethod
    def of(cls, food: Food) -> FoodMatch:
        food_servings = [FoodServing.of(serving) for serving in food.servings]

        return cls(
            food_id=food.id,
            name=food.name,
            brand=food.brand,
            servings=food_servings,
        )


class FoodSearch(BaseModel):
    status: Literal["found", "not_found"]
    matches: list[FoodMatch]
    attribution: str = ATTRIBUTION


class StandInDetails(BaseModel):
    food_name: str = Field(description="The catalog Food logged in the product's place.")
    grams_eaten: float
    nutrition_label: Amounts = Field(description="The product's Nutrition Label for the grams eaten.")
    difference: Amounts = Field(description="The logged values minus the Nutrition Label's.")
    estimated: bool = Field(description="True when the Nutrition Label was estimated, not read from it.")

    @classmethod
    def of(cls, stand_in: StandIn, logged: Nutrition) -> StandInDetails:
        return cls(
            food_name=stand_in.food_name,
            grams_eaten=stand_in.grams_eaten,
            nutrition_label=Amounts.of(stand_in.label_for_grams_eaten),
            difference=Amounts.of(logged - stand_in.label_for_grams_eaten),
            estimated=stand_in.estimated,
        )


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
    stand_in: StandInDetails | None = Field(
        description="Set when the entry is a Stand-in for a product missing from the catalog."
    )

    @classmethod
    def of(cls, item: DraftItem) -> ProposedEntry:
        amounts = Amounts.of(item.nutrition)
        stand_in = _stand_in_details(item)

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
            stand_in=stand_in,
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


class ConfirmedFoodEntry(BaseModel):
    name: str
    number_of_units: float
    food_entry_id: str | None = Field(description="The created Food Entry; null when it wasn't written.")

    @classmethod
    def of(cls, confirmed: ConfirmedEntry) -> ConfirmedFoodEntry:
        return cls(
            name=confirmed.entry.entry_name,
            number_of_units=confirmed.entry.number_of_units,
            food_entry_id=confirmed.food_entry_id,
        )


class DraftConfirmation(BaseModel):
    status: Literal["confirmed", "partially_written", "already_confirmed"] = Field(
        description=(
            "partially_written: writing stopped at an item without a food_entry_id. "
            "already_confirmed: an earlier call wrote these entries; nothing new was written."
        )
    )
    meal: Meal
    diary_day: date
    entries: list[ConfirmedFoodEntry]
    failure: str | None = Field(description="Why writing stopped part-way through.")
    attribution: str = ATTRIBUTION

    @classmethod
    def of(cls, confirmation: Confirmation, *, repeated: bool) -> DraftConfirmation:
        entries = [ConfirmedFoodEntry.of(confirmed) for confirmed in confirmation.entries]
        status = _confirmation_status(confirmation, repeated=repeated)

        return cls(
            status=status,
            meal=confirmation.draft.meal,
            diary_day=confirmation.draft.diary_day,
            entries=entries,
            failure=confirmation.failure,
        )


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


def _stand_in_details(item: DraftItem) -> StandInDetails | None:
    if item.stand_in is None:
        return None

    return StandInDetails.of(item.stand_in, item.nutrition)


def _confirmation_status(
    confirmation: Confirmation, *, repeated: bool
) -> Literal["confirmed", "partially_written", "already_confirmed"]:
    if repeated:
        return "already_confirmed"
    if confirmation.failure is not None:
        return "partially_written"

    return "confirmed"


def _rounded(grams: float) -> float:
    # Adding 0.0 turns -0.0 into 0.0.
    return round(grams, 1) + 0.0
