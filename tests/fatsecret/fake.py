"""An in-memory FatSecret: one Food Diary per FatSecret account."""

from __future__ import annotations

from datetime import date
from itertools import count

from fatsecret_mcp.domain.models import FoodEntry, Meal, Nutrition
from fatsecret_mcp.fatsecret.errors import CredentialsRejected, FatSecretError, InvalidAccessToken
from fatsecret_mcp.fatsecret.gateway import AccessToken


class FakeFatSecret:
    def __init__(self) -> None:
        self._accounts: dict[str, AccessToken] = {}
        self._diaries: dict[str, list[FoodEntry]] = {}
        self._deleted_days: dict[str, set[date]] = {}
        self._failure: FatSecretError | None = None

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

    def fail_with(self, error: FatSecretError) -> None:
        """Every call from now on raises `error`, as if FatSecret had answered with it."""
        self._failure = error

    async def list_food_entries(self, user: AccessToken, day: date) -> list[FoodEntry]:
        self._authenticate(user)
        return [entry for entry in self._diaries[user.token] if entry.diary_day == day]

    async def get_month_totals(self, user: AccessToken, month: date) -> dict[date, Nutrition]:
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

    def _authenticate(self, user: AccessToken) -> None:
        if self._failure is not None:
            raise self._failure
        account = self._accounts.get(user.token)

        if account is None:
            raise InvalidAccessToken()
        if account.secret != user.secret:
            raise CredentialsRejected(8)


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
) -> FoodEntry:
    """A Food Entry with the details the tools don't look at filled in."""
    entry_id = next(_ids)

    return FoodEntry(
        id=str(entry_id),
        food_id=f"food-{entry_id}",
        serving_id=f"serving-{entry_id}",
        name=name,
        amount=amount,
        number_of_units=1,
        meal=meal,
        diary_day=diary_day,
        nutrition=Nutrition(kcal=kcal, protein=protein, fat=fat, carbohydrate=carbohydrate),
    )
