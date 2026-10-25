"""draft_entries: show proposed Food Entries before the User confirms anything."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, ToolAnnotations
from pydantic import SecretStr

from fatsecret_mcp.config.models import User
from fatsecret_mcp.domain.diary_day import Clock, today
from fatsecret_mcp.domain.draft_token import seal_draft
from fatsecret_mcp.domain.drafts import (
    InvalidDraftItem,
    UnlikelyDiaryDay,
    check_diary_day,
    draft_expiry,
    draft_item,
    draft_totals,
)
from fatsecret_mcp.domain.meal_suggestion import ask_about_previous_day, suggest_meal
from fatsecret_mcp.domain.models import Draft, DraftItem, Food, FoodEntry, Meal, MetricAmount, Serving
from fatsecret_mcp.domain.repeated_entries import (
    check_source_day,
    repeated_item,
    serving_metric_amount,
    source_entry,
    written_metric_amount,
)
from fatsecret_mcp.domain.stand_ins import check_label, stand_in_item
from fatsecret_mcp.fatsecret.catalog import loggable_foods
from fatsecret_mcp.fatsecret.diary import UserDiary
from fatsecret_mcp.fatsecret.errors import FatSecretError, NotFound
from fatsecret_mcp.fatsecret.food_cache import FoodCache
from fatsecret_mcp.fatsecret.gateway import FatSecretGateway
from fatsecret_mcp.tools.schemas import (
    MAX_DRAFT_ID_LENGTH,
    Amounts,
    DayAfter,
    DiaryDayOrToday,
    DiaryEntryDraftInput,
    DraftItemInput,
    DraftItems,
    FoodDraft,
    FoodDraftInput,
    NutritionLabelDraftInput,
    ProposedEntry,
    as_tool_result,
)

# The entries of each Diary Day that a Draft's Diary Entry items repeat from.
SourceDays = dict[date, list[FoodEntry]]


def register(
    server: MCPServer,
    user: User,
    fatsecret: FatSecretGateway,
    food_cache: FoodCache,
    diary: UserDiary,
    clock: Clock,
    signing_key: SecretStr,
) -> None:
    annotations = ToolAnnotations(
        title="Draft Entries",
        read_only_hint=True,
    )

    @server.tool(annotations=annotations)
    async def draft_entries(
        items: DraftItems, meal: Meal | None = None, diary_day: DiaryDayOrToday = None
    ) -> Annotated[CallToolResult, FoodDraft]:
        """Propose Food Entries without writing to the Food Diary. Show the Draft to the User.

        A Food item uses Food and Serving ids from search_foods. number_of_units is the FatSecret unit
        count, not necessarily grams: scale it from the Serving's number_of_units.

        A Nutrition Label item logs a branded product missing from the catalog as a Stand-in: the
        closest catalog Food for search_phrase, in the amount whose kcal match the label, under the
        product's name. Ask for a photo of the Nutrition Label unless the User already gave its values.
        For a product logged before as a Stand-in,
        reuse its label from this conversation or your memory, and ask again otherwise.
        Estimate the label, with estimated=true, only when the User has no packaging. Show the
        label values, the logged values and the difference. If the User dislikes the Stand-in,
        draft again with a different search_phrase.

        A Diary Entry item logs a Food Entry from the Food Diary again, under its own name, with its
        Food, Serving and nutrition, so no catalog search is needed. For "the same as yesterday's
        breakfast", read that Diary Day with get_diary_day, list its Food Entries with their amounts
        to the User and ask whether everything was the same, or what to leave out or eat more or less
        of. Then draft the chosen entries by diary_day and food_entry_id, with metric_amount for a
        changed amount in grams or ml.

        A Draft holds at most 20 items: split a larger meal into several Drafts.

        Include assumptions about estimated amounts. Always ask the User whether the suggested Meal
        is correct. If previous_day_suggested is true, ask whether to use Snacks on the
        previous Diary Day instead. A correction needs a new Draft with the explicit Meal or Diary Day.
        Nothing is written by this tool; a Draft expires after thirty minutes.
        """
        now = clock()
        user_today = today(now, user.timezone)
        day = diary_day or user_today
        selected_meal = meal or suggest_meal(now, user.timezone, user.meal_hours.as_starts())
        previous_day_suggested = (
            meal is None and diary_day is None and ask_about_previous_day(now, user.timezone)
        )
        prepared: list[DraftItem] = []

        try:
            check_diary_day(day, user_today)

            source_days = await _source_days(items, diary, user_today)

            for item in items:
                prepared.append(await _prepared_item(item, fatsecret, food_cache, source_days))

            entries = source_days[day] if day in source_days else await diary.entries(day)
        except (FatSecretError, InvalidDraftItem, UnlikelyDiaryDay) as error:
            raise ToolError(str(error)) from error

        totals = draft_totals(entries, prepared, user.daily_targets.as_nutrition())
        draft_entries_to_seal = [item.entry for item in prepared]
        sealed_items = tuple(draft_entries_to_seal)
        sealed = Draft(
            user_id=user.id,
            items=sealed_items,
            meal=selected_meal,
            diary_day=day,
            expires_at=draft_expiry(now),
        )
        draft_id = seal_draft(sealed, signing_key.get_secret_value())

        # confirm_draft would refuse a longer id, so the User couldn't confirm this Draft.
        if len(draft_id) > MAX_DRAFT_ID_LENGTH:
            raise ToolError("These Food names are too long for one Draft. Split it into several Drafts.")

        proposed_entries = [ProposedEntry.of(item) for item in prepared]
        result = FoodDraft(
            draft_id=draft_id,
            expires_at=sealed.expires_at,
            meal=selected_meal,
            meal_suggested=meal is None,
            diary_day=day,
            previous_day_suggested=previous_day_suggested,
            items=proposed_entries,
            totals=Amounts.of(totals.added),
            day_after=DayAfter.of(totals),
        )

        return as_tool_result(result)


async def _source_days(items: DraftItems, diary: UserDiary, user_today: date) -> SourceDays:
    """The entries of each Diary Day that Diary Entry items repeat from, each day read once.

    Every source day is checked before the first read, so a refused one costs no request.
    """
    repeated_days = [item.diary_day for item in items if isinstance(item, DiaryEntryDraftInput)]
    days = list(dict.fromkeys(repeated_days))
    entries: SourceDays = {}

    for day in days:
        check_source_day(day, user_today)

    for day in days:
        entries[day] = await diary.entries(day)

    return entries


async def _prepared_item(
    item: DraftItemInput,
    fatsecret: FatSecretGateway,
    food_cache: FoodCache,
    source_days: SourceDays,
) -> DraftItem:
    if isinstance(item, NutritionLabelDraftInput):
        return await _stand_in(item, fatsecret, food_cache)

    if isinstance(item, DiaryEntryDraftInput):
        return await _repeated(item, fatsecret, food_cache, source_days)

    name, serving = await _resolve_serving(item, fatsecret, food_cache)

    return draft_item(item.food_id, name, serving, item.number_of_units, item.assumption)


async def _repeated(
    item: DiaryEntryDraftInput,
    fatsecret: FatSecretGateway,
    food_cache: FoodCache,
    source_days: SourceDays,
) -> DraftItem:
    source = source_entry(source_days[item.diary_day], item.food_entry_id, item.diary_day)
    source_amount = written_metric_amount(source)

    # Only an amount not written in grams or ml costs a lookup of its Serving's weight.
    if source_amount is None:
        source_amount = await _looked_up_metric_amount(source, fatsecret, food_cache)

    return repeated_item(
        source=source,
        source_amount=source_amount,
        eaten_amount=item.metric_amount,
        assumption=item.assumption,
    )


async def _looked_up_metric_amount(
    source: FoodEntry, fatsecret: FatSecretGateway, food_cache: FoodCache
) -> MetricAmount | None:
    """The source's amount from its catalog Serving's weight; None when the catalog can't tell."""
    try:
        catalog_food = await food_cache.get_food(source.food_id, fatsecret)
    except NotFound:
        return None

    serving = _serving_of(catalog_food, source.serving_id)

    if serving is None:
        return None

    return serving_metric_amount(source, serving)


async def _stand_in(
    item: NutritionLabelDraftInput, fatsecret: FatSecretGateway, food_cache: FoodCache
) -> DraftItem:
    label = item.nutrition_label.as_label()

    # An implausible label is rejected before it costs any catalog requests.
    check_label(label)

    foods = await loggable_foods(fatsecret, food_cache, item.search_phrase)

    return stand_in_item(
        product_name=item.product_name,
        label=label,
        grams=item.grams,
        foods=foods,
        assumption=item.assumption,
    )


async def _resolve_serving(
    item: FoodDraftInput, fatsecret: FatSecretGateway, food_cache: FoodCache
) -> tuple[str, Serving]:
    if item.serving_id == "0":
        raise ToolError("Serving 0 is derived and cannot be logged. Choose a Serving from search_foods.")

    try:
        catalog_food = await food_cache.get_food(item.food_id, fatsecret)
    except NotFound:
        raise ToolError("Unknown Food: use the ids of a match from search_foods.") from None

    serving = _serving_of(catalog_food, item.serving_id)

    if serving is None:
        raise ToolError("Serving does not belong to this Food.")

    return catalog_food.name, serving


def _serving_of(food: Food, serving_id: str) -> Serving | None:
    servings = [serving for serving in food.servings if serving.id == serving_id]

    return servings[0] if servings else None
