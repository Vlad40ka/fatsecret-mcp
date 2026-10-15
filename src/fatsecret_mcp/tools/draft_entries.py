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
from fatsecret_mcp.domain.models import Draft, DraftItem, Meal, Nutrition, RecentFood, Serving
from fatsecret_mcp.fatsecret.diary import UserDiary
from fatsecret_mcp.fatsecret.errors import FatSecretError, NotFound
from fatsecret_mcp.fatsecret.gateway import FatSecretGateway
from fatsecret_mcp.tools.schemas import (
    Amounts,
    DayAfter,
    DiaryDayOrToday,
    DraftItems,
    FoodDraft,
    FoodDraftInput,
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

        Use Food and Serving ids from search_foods. Copy the chosen match's name into entry_name
        to preserve the selection, including a catalog Food whose ids are shared by a Recent
        Stand-in. Without entry_name, Recent Foods take priority; ambiguous ids require a name.
        number_of_units is the FatSecret unit count,
        not necessarily grams: scale it from the Serving's number_of_units. Include assumptions
        about estimated amounts. Always ask the User whether the suggested Meal is correct.
        If previous_day_suggested is true, ask whether to use Snacks on the previous Diary Day
        instead. A correction needs a new Draft with the explicit Meal or Diary Day.
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
            recent_foods = await diary.recent_foods()

            for item in items:
                name, serving = await _resolve_serving(item, recent_foods, fatsecret)

                prepared.append(
                    draft_item(item.food_id, name, serving, item.number_of_units, item.assumption)
                )

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


async def _resolve_serving(
    item: FoodDraftInput, recent_foods: list[RecentFood], fatsecret: FatSecretGateway
) -> tuple[str, Serving]:
    if item.serving_id == "0":
        raise ToolError("Serving 0 is derived and cannot be logged. Choose a Serving from search_foods.")

    matching = [food for food in recent_foods if food.food_id == item.food_id]

    if item.entry_name is not None:
        matching = [food for food in matching if food.name == item.entry_name]

    if matching:
        candidates: list[tuple[str, Serving]] = []

        for food in matching:
            for serving in food.servings:
                if serving.id == item.serving_id:
                    candidates.append((food.name, serving))

        if len(candidates) > 1:
            raise ToolError("These ids identify several Recent Foods. Copy entry_name from search_foods.")
        if not candidates:
            raise ToolError("Serving was never logged with this Recent Food.")

        return candidates[0]

    try:
        catalog_food = await fatsecret.get_food(item.food_id)
    except NotFound:
        raise ToolError("Unknown Food: neither a catalog Food nor among your Recent Foods.") from None

    if item.entry_name is not None and item.entry_name != catalog_food.name:
        raise ToolError("entry_name must match a Food name returned by search_foods.")

    servings = [serving for serving in catalog_food.servings if serving.id == item.serving_id]

    if not servings:
        raise ToolError("Serving does not belong to this Food.")

    return catalog_food.name, servings[0]
