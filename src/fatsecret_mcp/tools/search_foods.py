"""search_foods: catalog matches for a description, with Servings that can be logged."""

from __future__ import annotations

from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, ToolAnnotations

from fatsecret_mcp.fatsecret.errors import FatSecretError, NotFound
from fatsecret_mcp.fatsecret.gateway import FatSecretGateway
from fatsecret_mcp.tools.schemas import (
    EnglishRendering,
    FoodDescription,
    FoodMatch,
    FoodSearch,
    as_tool_result,
)

CATALOG_LIMIT = 5


def register(server: MCPServer, fatsecret: FatSecretGateway) -> None:
    @server.tool(annotations=ToolAnnotations(title="Search Foods", read_only_hint=True))
    async def search_foods(
        description: FoodDescription, english_rendering: EnglishRendering = None
    ) -> Annotated[CallToolResult, FoodSearch]:
        """Find catalog Foods with loggable Servings, kcal and Macros.

        Search one food at a time. Pass the User's original description and, for non-English input,
        an English rendering: the catalog is searched with the English rendering, or the original
        when omitted. Nutrition and metric amounts describe the whole Serving; number_of_units is its
        FatSecret unit count. A not_found result is normal. For a missing branded product, the User
        can add it once in the FatSecret app. This tool does not write to the Food Diary.
        """
        if not description.strip():
            raise ToolError("description must not be blank.")

        query = (english_rendering if english_rendering is not None else description).strip()

        if not query:
            raise ToolError("english_rendering must not be blank.")

        try:
            matches = await _catalog_matches(fatsecret, query)
        except FatSecretError as error:
            raise ToolError(str(error)) from error

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
