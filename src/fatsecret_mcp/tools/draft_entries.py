"""draft_entries: show proposed Food Entries before the User confirms anything."""

from __future__ import annotations

from datetime import UTC, timedelta
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, ToolAnnotations
from pydantic import SecretStr

from fatsecret_mcp.config.models import User
from fatsecret_mcp.domain.day_summary import total_nutrition
from fatsecret_mcp.domain.diary_day import Clock
from fatsecret_mcp.domain.draft_token import seal_draft
from fatsecret_mcp.domain.drafts import InvalidDraftItem, draft_item
from fatsecret_mcp.domain.meal_suggestion import ask_about_previous_day, suggest_meal
from fatsecret_mcp.domain.models import Draft, DraftItem, Meal, Nutrition, Serving
from fatsecret_mcp.domain.stand_ins import check_label, stand_in_item
from fatsecret_mcp.fatsecret.catalog import loggable_foods
from fatsecret_mcp.fatsecret.diary import UserDiary
from fatsecret_mcp.fatsecret.errors import FatSecretError, NotFound
from fatsecret_mcp.fatsecret.gateway import FatSecretGateway
from fatsecret_mcp.tools.schemas import (
    Amounts,
    DayAfter,
    DiaryDayOrToday,
    DraftItemInput,
    DraftItems,
    FoodDraft,
    FoodDraftInput,
    NutritionLabelDraftInput,
    ProposedEntry,
    as_tool_result,
)


def register(
    server: MCPServer,
    user: User,
    fatsecret: FatSecretGateway,
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

        Include assumptions about estimated amounts. Always ask the User whether the suggested Meal
        is correct. If previous_day_suggested is true, ask whether to use Snacks on the
        previous Diary Day instead. A correction needs a new Draft with the explicit Meal or Diary Day.
        Nothing is written by this tool; a Draft expires after thirty minutes.
        """
        now = clock()
        local = now.astimezone(user.timezone)
        day = diary_day or local.date()
        selected_meal = meal or suggest_meal(now, user.timezone, user.meal_hours.as_starts())
        previous_day_suggested = (
            meal is None and diary_day is None and ask_about_previous_day(now, user.timezone)
        )
        prepared: list[DraftItem] = []

        try:
            for item in items:
                prepared.append(await _prepared_item(item, fatsecret))

            entries = await diary.entries(day)
        except (FatSecretError, InvalidDraftItem) as error:
            raise ToolError(str(error)) from error

        nutrition = [item.nutrition for item in prepared]
        totals = sum(nutrition, Nutrition())
        consumed = total_nutrition(entries) + totals
        targets = user.daily_targets.as_nutrition()
        draft_entries_to_seal = [item.entry for item in prepared]
        sealed_items = tuple(draft_entries_to_seal)
        sealed = Draft(
            user_id=user.id,
            items=sealed_items,
            meal=selected_meal,
            diary_day=day,
            expires_at=now.astimezone(UTC) + timedelta(minutes=30),
        )
        proposed_entries = [ProposedEntry.of(item) for item in prepared]
        result = FoodDraft(
            draft_id=seal_draft(sealed, signing_key.get_secret_value()),
            expires_at=sealed.expires_at,
            meal=selected_meal,
            meal_suggested=meal is None,
            diary_day=day,
            previous_day_suggested=previous_day_suggested,
            items=proposed_entries,
            totals=Amounts.of(totals),
            day_after=DayAfter(
                consumed=Amounts.of(consumed),
                daily_targets=Amounts.of(targets),
                remaining=Amounts.of(targets - consumed),
            ),
        )

        return as_tool_result(result)


async def _prepared_item(item: DraftItemInput, fatsecret: FatSecretGateway) -> DraftItem:
    if isinstance(item, NutritionLabelDraftInput):
        return await _stand_in(item, fatsecret)

    name, serving = await _resolve_serving(item, fatsecret)

    return draft_item(item.food_id, name, serving, item.number_of_units, item.assumption)


async def _stand_in(item: NutritionLabelDraftInput, fatsecret: FatSecretGateway) -> DraftItem:
    label = item.nutrition_label.as_label()

    # An implausible label is rejected before it costs any catalog requests.
    check_label(label)

    foods = await loggable_foods(fatsecret, item.search_phrase)

    return stand_in_item(
        product_name=item.product_name,
        label=label,
        grams=item.grams,
        foods=foods,
        assumption=item.assumption,
    )


async def _resolve_serving(item: FoodDraftInput, fatsecret: FatSecretGateway) -> tuple[str, Serving]:
    if item.serving_id == "0":
        raise ToolError("Serving 0 is derived and cannot be logged. Choose a Serving from search_foods.")

    try:
        catalog_food = await fatsecret.get_food(item.food_id)
    except NotFound:
        raise ToolError("Unknown Food: use the ids of a match from search_foods.") from None

    servings = [serving for serving in catalog_food.servings if serving.id == item.serving_id]

    if not servings:
        raise ToolError("Serving does not belong to this Food.")

    return catalog_food.name, servings[0]
