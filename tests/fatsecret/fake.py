"""An in-memory FatSecret: a shared catalog, one Food Diary per FatSecret account, and the PIN flow.

Every call is counted and, like a network call, gives way to other calls while it's in flight.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from itertools import count
from urllib.parse import parse_qs, quote, urlsplit

import anyio.lowlevel

from fatsecret_mcp.domain.models import CatalogFood, DraftEntry, Food, FoodEntry, Meal, Nutrition
from fatsecret_mcp.fatsecret.errors import (
    CredentialsRejected,
    FatSecretError,
    InvalidAccessToken,
    NotFound,
    PinRejected,
    from_code,
)
from fatsecret_mcp.fatsecret.gateway import AccessToken, RequestToken


class FakeFatSecret:
    def __init__(self) -> None:
        self._accounts: dict[str, AccessToken] = {}
        self._diaries: dict[str, list[FoodEntry]] = {}
        self._deleted_days: dict[str, set[date]] = {}
        self._failure: FatSecretError | None = None
        self._catalog: dict[str, Food] = {}
        self._search_results: dict[str, list[CatalogFood]] = {}
        self._approvals: dict[str, tuple[str, AccessToken]] = {}
        self._in_flight = 0
        self._request_limit: int | None = None
        self.requests = 0
        """How many calls FatSecret has answered."""
        self.most_in_flight = 0
        """The most calls that were in flight at the same time."""

    def add_food(self, food: Food) -> None:
        self._catalog[food.id] = food

    def remove_food(self, food_id: str) -> None:
        """The catalog no longer has this Food, neither to fetch nor among search results."""
        del self._catalog[food_id]

        for query, foods in self._search_results.items():
            remaining = [food for food in foods if food.id != food_id]
            self._search_results[query] = remaining

    def set_search_results(self, query: str, foods: Sequence[CatalogFood]) -> None:
        self._search_results[query] = list(foods)

    async def search_catalog(self, query: str, *, max_results: int | None = None) -> list[CatalogFood]:
        await self._request()

        if self._failure is not None:
            raise self._failure

        return self._search_results.get(query, [])[: max_results or 20]

    async def get_food(self, food_id: str) -> Food:
        await self._request()

        if self._failure is not None:
            raise self._failure

        if food_id not in self._catalog:
            raise NotFound()

        return self._catalog[food_id]

    def add_account(self, user: AccessToken) -> None:
        self._accounts[user.token] = user

        self._diaries.setdefault(user.token, [])
        self._deleted_days.setdefault(user.token, set())

    def revoke(self, user: AccessToken) -> None:
        del self._accounts[user.token]

    def add_food_entry(self, user: AccessToken, entry: FoodEntry) -> None:
        self._diaries[user.token].append(entry)

    def delete_food_entries(self, user: AccessToken, day: date) -> None:
        remaining = [entry for entry in self._diaries[user.token] if entry.diary_day != day]

        self._diaries[user.token] = remaining
        self._deleted_days[user.token].add(day)

    def fail_with(self, error: FatSecretError | None) -> None:
        """Every call from now on raises `error`, as if FatSecret had answered with it; None stops it."""
        self._failure = error

    def limit_requests_to(self, requests: int | None) -> None:
        """Answer with error 12 once this many requests have been made; None lifts the limit."""
        self._request_limit = requests

    async def request_token(self) -> RequestToken:
        await self._request()

        if self._failure is not None:
            raise self._failure

        number = next(_ids)

        return RequestToken(
            token=f"request-token-{number}",
            secret=f"request-secret-{number}",
        )

    def authorize_url(self, request_token: RequestToken) -> str:
        token = quote(request_token.token, safe="")

        return f"https://authentication.fatsecret.com/oauth/authorize?oauth_token={token}"

    def approve_at(self, authorize_url: str, account: AccessToken) -> str:
        """The User opens `authorize_url`, signs in to `account` and approves; returns the PIN shown."""
        request_token = parse_qs(urlsplit(authorize_url).query)["oauth_token"][0]
        pin = f"{next(_ids):07d}"

        self.add_account(account)
        self._approvals[request_token] = (pin, account)

        return pin

    async def exchange_pin(self, request_token: RequestToken, pin: str) -> AccessToken:
        await self._request()

        if self._failure is not None:
            raise self._failure

        approval = self._approvals.pop(request_token.token, None)

        if approval is None or approval[0] != pin.strip():
            raise PinRejected()

        return approval[1]

    async def list_food_entries(self, user: AccessToken, day: date) -> list[FoodEntry]:
        await self._request()
        self._authenticate(user)
        return [entry for entry in self._diaries[user.token] if entry.diary_day == day]

    async def get_month_totals(self, user: AccessToken, month: date) -> dict[date, Nutrition]:
        await self._request()
        self._authenticate(user)

        first_day = month.replace(day=1)
        totals: dict[date, Nutrition] = {}

        for day in self._deleted_days[user.token]:
            if day.replace(day=1) == first_day:
                totals[day] = Nutrition()

        for entry in self._diaries[user.token]:
            if entry.diary_day.replace(day=1) == first_day:
                totals[entry.diary_day] = totals.get(entry.diary_day, Nutrition()) + entry.nutrition

        return totals

    async def create_food_entry(self, user: AccessToken, entry: DraftEntry, meal: Meal, day: date) -> str:
        await self._request()
        self._authenticate(user)

        per_unit = self._nutrition_per_unit(user, entry.food_id, entry.serving_id)
        nutrition = _scaled(per_unit, entry.number_of_units)
        created = food_entry(
            name=entry.entry_name,
            meal=meal,
            diary_day=day,
            kcal=nutrition.kcal,
            protein=nutrition.protein,
            fat=nutrition.fat,
            carbohydrate=nutrition.carbohydrate,
            amount=f"{entry.number_of_units:g} units",
            food_id=entry.food_id,
            serving_id=entry.serving_id,
            number_of_units=entry.number_of_units,
        )

        self._diaries[user.token].append(created)

        return created.id

    def _nutrition_per_unit(self, user: AccessToken, food_id: str, serving_id: str) -> Nutrition:
        """Like FatSecret, the Fake knows a Food from its catalog or from the User's own Food Entries."""
        food = self._catalog.get(food_id)
        servings = food.servings if food is not None else []
        entries = self._diaries[user.token]

        for serving in servings:
            if serving.id == serving_id:
                return _scaled(serving.nutrition, 1 / serving.number_of_units)

        for logged in entries:
            if logged.food_id == food_id and logged.serving_id == serving_id:
                return _scaled(logged.nutrition, 1 / logged.number_of_units)

        raise NotFound()

    async def _request(self) -> None:
        self.requests += 1
        self._in_flight += 1
        self.most_in_flight = max(self.most_in_flight, self._in_flight)

        await anyio.lowlevel.checkpoint()

        self._in_flight -= 1

        if self._request_limit is not None and self.requests > self._request_limit:
            raise from_code(12)

    def _authenticate(self, user: AccessToken) -> None:
        if self._failure is not None:
            raise self._failure
        account = self._accounts.get(user.token)

        if account is None:
            raise InvalidAccessToken()
        if account.secret != user.secret:
            raise CredentialsRejected(8)


def _scaled(nutrition: Nutrition, factor: float) -> Nutrition:
    return Nutrition(
        kcal=nutrition.kcal * factor,
        protein=nutrition.protein * factor,
        fat=nutrition.fat * factor,
        carbohydrate=nutrition.carbohydrate * factor,
    )


_ids = count(1)


def food_entry(
    name: str,
    meal: Meal,
    diary_day: date,
    *,
    kcal: float,
    protein: float = 0,
    fat: float = 0,
    carbohydrate: float = 0,
    amount: str = "1 serving",
    food_id: str | None = None,
    serving_id: str | None = None,
    number_of_units: float = 1,
) -> FoodEntry:
    """A Food Entry with the details a test doesn't name filled in, in a Food and Serving of its own."""
    entry_id = next(_ids)

    return FoodEntry(
        id=str(entry_id),
        food_id=food_id or f"food-{entry_id}",
        serving_id=serving_id or f"serving-{entry_id}",
        name=name,
        amount=amount,
        number_of_units=number_of_units,
        meal=meal,
        diary_day=diary_day,
        nutrition=Nutrition(kcal=kcal, protein=protein, fat=fat, carbohydrate=carbohydrate),
    )
