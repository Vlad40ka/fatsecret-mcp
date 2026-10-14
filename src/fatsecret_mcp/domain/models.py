"""Food Diary and catalog concepts as data: Foods, Servings, Food Entries, and summaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class Meal(StrEnum):
    """The fixed slots a Diary Day is divided into, in the order they're shown."""

    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"
    SNACKS = "snacks"


@dataclass(frozen=True)
class Nutrition:
    """kcal plus Macros (protein, fat and carbohydrate, in grams)."""

    kcal: float = 0
    protein: float = 0
    fat: float = 0
    carbohydrate: float = 0

    def __add__(self, other: Nutrition) -> Nutrition:
        return Nutrition(
            kcal=self.kcal + other.kcal,
            protein=self.protein + other.protein,
            fat=self.fat + other.fat,
            carbohydrate=self.carbohydrate + other.carbohydrate,
        )

    def __sub__(self, other: Nutrition) -> Nutrition:
        return Nutrition(
            kcal=self.kcal - other.kcal,
            protein=self.protein - other.protein,
            fat=self.fat - other.fat,
            carbohydrate=self.carbohydrate - other.carbohydrate,
        )


@dataclass(frozen=True)
class FoodEntry:
    """One item recorded in a User's Food Diary."""

    id: str
    food_id: str
    serving_id: str
    name: str
    amount: str
    """The amount in its Serving, as FatSecret describes it: "150 g", "2 servings"."""
    number_of_units: float
    meal: Meal
    diary_day: date
    nutrition: Nutrition


@dataclass(frozen=True)
class CatalogFood:
    id: str
    name: str
    brand: str | None


@dataclass(frozen=True)
class Serving:
    id: str
    description: str
    metric_amount: float | None
    metric_unit: str | None
    number_of_units: float
    """FatSecret units represented by the description and nutrition (e.g. 100 for '100 g')."""
    nutrition: Nutrition


@dataclass(frozen=True)
class Food(CatalogFood):
    servings: list[Serving]


@dataclass(frozen=True)
class RecentFood:
    """A Food the User logged in recent weeks, under the name it was logged with.

    A Stand-in shares its Food with real Food Entries of that Food, but it's a Recent Food of its own.
    """

    food_id: str
    name: str
    servings: list[Serving]
    """Only the Servings the User logged it with, each as last logged: amount, units and nutrition."""
    times_logged: int
    last_logged: date


@dataclass(frozen=True)
class DaySummary:
    diary_day: date
    meals: dict[Meal, list[FoodEntry]]
    """Only the Meals that have Food Entries, in the order of the day."""
    totals: Nutrition
    daily_targets: Nutrition
    remaining: Nutrition
    """Daily Targets minus totals; negative once a target is exceeded."""


@dataclass(frozen=True)
class PeriodDaySummary:
    diary_day: date
    has_food_entries: bool
    totals: Nutrition
    daily_targets: Nutrition
