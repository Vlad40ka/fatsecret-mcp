"""The real FatSecret gateway: signed HTTP calls to the Platform API.

Calls are method-based (`rest/server.api?method=…`) with the OAuth 1.0 parameters in the query string,
the wire format verified against a Basic-tier key. Errors come back as HTTP 200 with an error code.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import date
from typing import Any, cast

import httpx2

from fatsecret_mcp.domain.models import CatalogFood, Food, FoodEntry, Nutrition
from fatsecret_mcp.fatsecret import errors, parsing
from fatsecret_mcp.fatsecret.errors import FatSecretError, UpstreamFailure
from fatsecret_mcp.fatsecret.gateway import AccessToken, ConsumerKey
from fatsecret_mcp.fatsecret.oauth import signed_url

API_URL = "https://platform.fatsecret.com/rest/server.api"

logger = logging.getLogger(__name__)

# httpx2 logs every request URL at INFO, and these URLs carry the OAuth token and signature.
logging.getLogger("httpx2").setLevel(logging.WARNING)
logging.getLogger("httpcore2").setLevel(logging.WARNING)


class FatSecretApi:
    def __init__(self, consumer: ConsumerKey, http: httpx2.AsyncClient) -> None:
        self._consumer = consumer
        self._http = http

    async def search_catalog(self, query: str, *, max_results: int | None = None) -> list[CatalogFood]:
        params = {"search_expression": query}

        if max_results is not None:
            params["max_results"] = str(max_results)

        body = await self._call("foods.search", params, None)

        return parsing.catalog_foods(body)

    async def get_food(self, food_id: str) -> Food:
        body = await self._call("food.get.v5", {"food_id": food_id}, None)

        return parsing.food(body)

    async def list_food_entries(self, user: AccessToken, day: date) -> list[FoodEntry]:
        body = await self._call("food_entries.get.v2", {"date": str(parsing.day_number(day))}, user)

        return parsing.food_entries(body)

    async def get_month_totals(self, user: AccessToken, month: date) -> dict[date, Nutrition]:
        first_day = month.replace(day=1)
        body = await self._call(
            "food_entries.get_month.v2", {"date": str(parsing.day_number(first_day))}, user
        )

        return parsing.month_totals(body)

    async def _call(self, method: str, params: Mapping[str, str], user: AccessToken | None) -> dict[str, Any]:
        url = signed_url("GET", API_URL, {"method": method, "format": "json", **params}, self._consumer, user)

        try:
            body = _json_object(await self._get(url))

            if "error" in body:
                raise _error(body["error"])
        except FatSecretError as error:
            logger.warning("FatSecret %s failed: %s (code %s)", method, type(error).__name__, error.code)
            raise

        return body

    async def _get(self, url: str) -> httpx2.Response:
        try:
            return await self._http.get(url)
        except httpx2.TimeoutException:
            raise UpstreamFailure("the request timed out") from None
        except httpx2.HTTPError as error:
            # The exception's text can quote the signed URL, so only its type is kept.
            raise UpstreamFailure(type(error).__name__) from None


def _json_object(response: httpx2.Response) -> dict[str, Any]:
    if response.status_code != 200:
        raise UpstreamFailure(f"HTTP {response.status_code}")

    try:
        body = response.json()
    except ValueError:
        raise UpstreamFailure("the response isn't JSON") from None

    if not isinstance(body, dict):
        raise UpstreamFailure("unexpected response")

    return cast(dict[str, Any], body)


def _error(error: object) -> FatSecretError:
    # FatSecret's own message can quote OAuth values, so only the code is used.
    code = parsing.error_code(error)

    if code is None:
        return UpstreamFailure("an error without a code")

    return errors.from_code(code)
