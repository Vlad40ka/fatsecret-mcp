"""search_foods: the User's Recent Foods first, then catalog matches, all with Servings that can be logged."""

from __future__ import annotations

from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, ToolAnnotations

from fatsecret_mcp.domain.recent_foods import matching_recent_foods
from fatsecret_mcp.fatsecret.diary import UserDiary
from fatsecret_mcp.fatsecret.errors import FatSecretError, NotFound
from fatsecret_mcp.fatsecret.gateway import FatSecretGateway
from fatsecret_mcp.tools.schemas import (
    EnglishRendering,
    FoodDescription,
    FoodMatch,
    FoodSearch,
    as_tool_result,
)

RECENT_FOODS_LIMIT = 5
CATALOG_LIMIT = 5


def register(server: MCPServer, fatsecret: FatSecretGateway, diary: UserDiary) -> None:
    @server.tool(annotations=ToolAnnotations(title="Search Foods", read_only_hint=True))
    async def search_foods(
        description: FoodDescription, english_rendering: EnglishRendering = None, refresh: bool = False
    ) -> Annotated[CallToolResult, FoodSearch]:
        """Find Foods with loggable Servings, kcal and Macros: the User's Recent Foods, then the catalog.

        Search one food at a time. Pass the User's original description and, for non-English input,
        an English rendering. Recent Foods are Foods the User logged in recent weeks, often local or
        branded products the catalog lacks. They're matched on both texts, ignoring case and
        diacritics; only the best matches are returned, and they always come first. The catalog is
        searched with the English rendering, or the original when omitted. Each match names its
        source. A Recent Food keeps the name it was logged with and offers only the Servings the User
        logged it with, each as last logged; its metric amount is known once the User has logged
        that Serving in grams or millilitres. Nutrition and metric amounts describe the whole
        Serving; number_of_units is its FatSecret unit count. A not_found result is normal. For a
        missing branded product, the User can add it once in the FatSecret app. This tool does not
        write to the Food Diary.
        History is loaded only when needed and past days are reused for up to twelve hours during
        this server session. Today is always fresh. Set refresh=true when the User requests fresh
        history or says they changed past entries in the FatSecret app.
        """
        if not description.strip():
            raise ToolError("description must not be blank.")

        query = (english_rendering if english_rendering is not None else description).strip()

        if not query:
            raise ToolError("english_rendering must not be blank.")

        descriptions = [description] if english_rendering is None else [description, english_rendering]

        try:
            foods = await diary.recent_foods(refresh=refresh)
            catalog_matches = await _catalog_matches(fatsecret, query)
        except FatSecretError as error:
            raise ToolError(str(error)) from error

        matching = matching_recent_foods(foods, descriptions)
        recent_matches = [FoodMatch.of_recent_food(food) for food in matching[:RECENT_FOODS_LIMIT]]
        matches = recent_matches + catalog_matches

        found = FoodSearch(
            status="found" if matches else "not_found",
            matches=matches,
        )

        return as_tool_result(found)


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
