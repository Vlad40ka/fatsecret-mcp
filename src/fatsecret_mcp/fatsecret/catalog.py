"""Catalog Foods that can be logged: the top search matches, fetched with their Servings."""

from __future__ import annotations

from dataclasses import replace

from fatsecret_mcp.domain.models import Food
from fatsecret_mcp.fatsecret.errors import NotFound
from fatsecret_mcp.fatsecret.food_cache import FoodCache
from fatsecret_mcp.fatsecret.gateway import FatSecretGateway

CATALOG_LIMIT = 3


async def loggable_foods(fatsecret: FatSecretGateway, food_cache: FoodCache, query: str) -> list[Food]:
    """The top catalog Foods for `query`, in the catalog's order, without derived Servings (id 0).

    Each Food comes from `food_cache` when it was fetched recently. Foods the catalog can't fetch,
    or that have no other Servings, are left out.
    """
    candidates = await fatsecret.search_catalog(query, max_results=CATALOG_LIMIT)
    foods: list[Food] = []

    for candidate in candidates[:CATALOG_LIMIT]:
        try:
            food = await food_cache.get_food(candidate.id, fatsecret)
        except NotFound:
            continue

        servings = [serving for serving in food.servings if serving.id != "0"]

        if servings:
            foods.append(replace(food, servings=servings))

    return foods
