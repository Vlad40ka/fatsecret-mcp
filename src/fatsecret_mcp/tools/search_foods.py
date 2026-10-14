"""search_foods: the User's Recent Foods first, then catalog matches, all with Servings that can be logged."""

from __future__ import annotations

from datetime import date

import anyio
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from fatsecret_mcp.config.models import User
from fatsecret_mcp.domain.diary_day import Clock, today
from fatsecret_mcp.domain.models import FoodEntry
from fatsecret_mcp.domain.recent_foods import matching_recent_foods, recent_diary_days, recent_foods
from fatsecret_mcp.fatsecret.errors import FatSecretError, NotFound
from fatsecret_mcp.fatsecret.gateway import AccessToken, FatSecretGateway
from fatsecret_mcp.tools.schemas import EnglishRendering, FoodDescription, FoodMatch, FoodSearch

RECENT_FOODS_LIMIT = 5
CATALOG_LIMIT = 5


def register(
    server: MCPServer, user: User, fatsecret: FatSecretGateway, clock: Clock, recent_food_days: int
) -> None:
    @server.tool(annotations=ToolAnnotations(title="Search Foods", read_only_hint=True))
    async def search_foods(
        description: FoodDescription, english_rendering: EnglishRendering = None
    ) -> FoodSearch:
        """Find Foods with loggable Servings, kcal and Macros: the User's Recent Foods, then the catalog.

        Pass the User's original description and, for non-English input, an English rendering.
        Recent Foods are Foods the User logged in recent weeks, often local or branded products the
        catalog lacks. They're matched on both texts, ignoring case and diacritics, and always come
        first. The catalog is searched with the English rendering, or the original when omitted.
        Each match names its source. A Recent Food keeps the name it was logged with and offers only
        the Servings the User logged it with, each as last logged. Nutrition and metric amounts
        describe the whole Serving; number_of_units is its FatSecret unit count. A not_found result is
        normal. For a missing branded product, the User can add it once in the FatSecret app. This
        tool does not write to the Food Diary.
        """
        if not description.strip():
            raise ToolError("description must not be blank.")

        query = (english_rendering if english_rendering is not None else description).strip()

        if not query:
            raise ToolError("english_rendering must not be blank.")

        descriptions = [description] if english_rendering is None else [description, english_rendering]
        days = recent_diary_days(today(clock(), user.timezone), recent_food_days)

        try:
            entries = await _list_food_entries(fatsecret, user.fatsecret, days)
            catalog_matches = await _catalog_matches(fatsecret, query)
        except FatSecretError as error:
            raise ToolError(str(error)) from error

        matching = matching_recent_foods(recent_foods(entries), descriptions)
        recent_matches = [FoodMatch.of_recent_food(food) for food in matching[:RECENT_FOODS_LIMIT]]
        matches = recent_matches + catalog_matches

        return FoodSearch(
            status="found" if matches else "not_found",
            matches=matches,
        )


async def _list_food_entries(
    fatsecret: FatSecretGateway, user: AccessToken, days: list[date]
) -> list[FoodEntry]:
    """The User's Food Entries on each of `days`, fetched concurrently, the oldest day first."""
    entries_by_day: dict[date, list[FoodEntry]] = {}
    # A task group would wrap a failure in an ExceptionGroup, so failures are collected and re-raised.
    failures: list[FatSecretError] = []

    async def list_day(day: date) -> None:
        try:
            entries_by_day[day] = await fatsecret.list_food_entries(user, day)
        except FatSecretError as error:
            failures.append(error)

    async with anyio.create_task_group() as group:
        for day in days:
            group.start_soon(list_day, day)

    if failures:
        raise failures[0]

    entries: list[FoodEntry] = []

    for day in days:
        entries += entries_by_day[day]

    return entries


async def _catalog_matches(fatsecret: FatSecretGateway, query: str) -> list[FoodMatch]:
    """The top catalog Foods for `query` that have Servings to log, in the catalog's order."""
    candidates = await fatsecret.search_catalog(query, max_results=CATALOG_LIMIT)
    matches: list[FoodMatch] = []

    for candidate in candidates[:CATALOG_LIMIT]:
        try:
            food = await fatsecret.get_food(candidate.id)
        except NotFound:
            continue

        servings = [serving for serving in food.servings if serving.id != "0"]

        if servings:
            matches.append(FoodMatch.of_catalog_food(food, servings))

    return matches
