"""search_foods: catalog matches with Servings that can be logged."""

from __future__ import annotations

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from fatsecret_mcp.fatsecret.errors import FatSecretError, NotFound
from fatsecret_mcp.fatsecret.gateway import FatSecretGateway
from fatsecret_mcp.tools.schemas import EnglishRendering, FoodDescription, FoodMatch, FoodSearch

CATALOG_LIMIT = 5


def register(server: MCPServer, fatsecret: FatSecretGateway) -> None:
    @server.tool(annotations=ToolAnnotations(title="Search Foods", read_only_hint=True))
    async def search_foods(
        description: FoodDescription, english_rendering: EnglishRendering = None
    ) -> FoodSearch:
        """Search FatSecret's catalog for Foods and their loggable Servings, with kcal and Macros.

        Pass the User's original description and, for non-English input, an English rendering.
        The catalog is searched with the English rendering, or the original when omitted.
        Nutrition and metric amounts describe the whole Serving; number_of_units is its FatSecret
        unit count. A not_found result is normal. For a missing branded product, the User can add it
        once in the FatSecret app. This tool does not write to the Food Diary.
        """
        if not description.strip():
            raise ToolError("description must not be blank.")

        query = (english_rendering if english_rendering is not None else description).strip()

        if not query:
            raise ToolError("english_rendering must not be blank.")

        matches: list[FoodMatch] = []

        try:
            candidates = await fatsecret.search_catalog(query, max_results=CATALOG_LIMIT)

            for candidate in candidates[:CATALOG_LIMIT]:
                try:
                    food = await fatsecret.get_food(candidate.id)
                except NotFound:
                    continue

                servings = [serving for serving in food.servings if serving.id != "0"]

                if servings:
                    matches.append(FoodMatch.of(food, servings))
        except FatSecretError as error:
            raise ToolError(str(error)) from error

        return FoodSearch(
            status="found" if matches else "not_found",
            matches=matches,
        )
