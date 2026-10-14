"""Recent Foods: the Foods a User logged in recent weeks, found by a description in any language.

They're built from the User's own Food Entries, not from the catalog: a Basic key can't fetch non-US
Foods, and the catalog doesn't know a Stand-in's name. So each Serving carries the amount, units, kcal
and Macros of the Food Entry it was last logged in. Per unit, that's the entry's values divided by its
number of units, just like a catalog Serving.

Matching ignores case and diacritics and compares words. Words match without their final vowels, so
Polish endings don't get in the way: "jajka" matches "Jajko", and "kurczaka" matches "Kurczak".
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta

from fatsecret_mcp.domain.models import FoodEntry, RecentFood, Serving

DERIVED_SERVING_ID = "0"
"""FatSecret's computed Serving, which can't be logged."""

METRIC_AMOUNT = re.compile(r"(?P<amount>\d+(?:\.\d+)?) (?P<unit>g|ml)")

WORD = re.compile(r"\w+")
VOWELS = "aeiouy"
SHORTEST_STEM = 3
SHORTEST_PREFIX = 4

FUNCTION_WORDS = frozenset(
    {
        # English
        "an",
        "and",
        "for",
        "in",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
        "without",
        # Polish
        "bez",
        "do",
        "lub",
        "na",
        "od",
        "oraz",
        "po",
        "we",
        "ze",
    }
)
"""Words that match nothing on their own. One-letter words, like Polish "z" or "w", are left out too."""

# "ł" has no decomposition in Unicode, so it doesn't lose its stroke like "ą" loses its tail.
UNDECOMPOSABLE_LETTERS = str.maketrans({"ł": "l"})


def recent_diary_days(today: date, days: int) -> list[date]:
    """The `days` Diary Days up to and including `today`, oldest first."""
    first_day = today - timedelta(days=days - 1)
    diary_days = [first_day + timedelta(days=offset) for offset in range(days)]

    return diary_days


def recent_foods(entries: list[FoodEntry]) -> list[RecentFood]:
    """The User's Food Entries, de-duplicated by Food, Serving and entry name."""
    chronological = sorted(entries, key=lambda entry: entry.diary_day)
    by_food_and_name: dict[tuple[str, str], list[FoodEntry]] = {}

    for entry in chronological:
        if entry.serving_id != DERIVED_SERVING_ID:
            by_food_and_name.setdefault((entry.food_id, entry.name), []).append(entry)

    foods = [_recent_food(food_entries) for food_entries in by_food_and_name.values()]

    return foods


def matching_recent_foods(foods: list[RecentFood], descriptions: list[str]) -> list[RecentFood]:
    """The Recent Foods whose name shares a word with one of the descriptions, the best match first.

    Ranked by match quality, then by how often and how recently the User logged them.
    """
    word_lists = [_words(description) for description in descriptions]
    matches = [food for food in foods if _match_quality(food.name, word_lists) > 0]
    ranked = sorted(matches, key=lambda food: _rank(food, word_lists))

    return ranked


def _recent_food(entries: list[FoodEntry]) -> RecentFood:
    """One Food under one entry name, from its Food Entries in chronological order."""
    last = entries[-1]

    return RecentFood(
        food_id=last.food_id,
        name=last.name,
        servings=_servings_as_last_logged(entries),
        times_logged=len(entries),
        last_logged=last.diary_day,
    )


def _servings_as_last_logged(entries: list[FoodEntry]) -> list[Serving]:
    """Each Serving the Food Entries were logged with, as last logged, the most recent first."""
    last_entries: dict[str, FoodEntry] = {}

    for entry in reversed(entries):
        last_entries.setdefault(entry.serving_id, entry)

    servings = [_serving_as_logged(entry) for entry in last_entries.values()]

    return servings


def _serving_as_logged(entry: FoodEntry) -> Serving:
    """A Serving described by the amount it was logged in, e.g. "150 g": 150 units of FatSecret's "100 g"."""
    metric = METRIC_AMOUNT.fullmatch(entry.amount)
    metric_amount = float(metric["amount"]) if metric else None
    metric_unit = metric["unit"] if metric else None

    return Serving(
        id=entry.serving_id,
        description=entry.amount,
        metric_amount=metric_amount,
        metric_unit=metric_unit,
        number_of_units=entry.number_of_units,
        nutrition=entry.nutrition,
    )


def _rank(food: RecentFood, word_lists: list[list[str]]) -> tuple[float, int, int]:
    """Sort key: the best match first, then the most often logged, then the most recently logged."""
    quality = _match_quality(food.name, word_lists)

    return (-quality, -food.times_logged, -food.last_logged.toordinal())


def _match_quality(name: str, word_lists: list[list[str]]) -> float:
    """The largest share of one description's words found in `name`, from 0 to 1."""
    name_words = _words(name)
    shares = [_share_found(words, name_words) for words in word_lists]

    return max(shares, default=0)


def _share_found(words: list[str], name_words: list[str]) -> float:
    if not words:
        return 0

    found = [word for word in words if _found_in(word, name_words)]

    return len(found) / len(words)


def _found_in(word: str, name_words: list[str]) -> bool:
    return any(_same_word(word, name_word) for name_word in name_words)


def _same_word(first: str, second: str) -> bool:
    """Whether two words match once their final vowels are dropped.

    They match when they're then equal ("jajka" and "jajko"), or when the shorter one, at least four
    letters long, begins the longer ("waniliowy" and "waniliowego"). So "pie" doesn't match "pierś".
    """
    shorter, longer = sorted([_stem(first), _stem(second)], key=len)

    if len(shorter) < SHORTEST_PREFIX:
        return shorter == longer

    return longer.startswith(shorter)


def _stem(word: str) -> str:
    """`word` without its final vowels, unless that would leave fewer than three letters."""
    stem = word.rstrip(VOWELS)

    return stem if len(stem) >= SHORTEST_STEM else word


def _words(text: str) -> list[str]:
    """The words in `text` that can match, lowercase and without diacritics."""
    plain = _without_diacritics(text.casefold())
    words = [word for word in WORD.findall(plain) if _can_match(word)]

    return words


def _without_diacritics(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.translate(UNDECOMPOSABLE_LETTERS))
    letters = [char for char in decomposed if not unicodedata.combining(char)]

    return "".join(letters)


def _can_match(word: str) -> bool:
    """Numbers, one-letter words and function words like "with" or "ze" match nothing."""
    return len(word) > 1 and not word.isdigit() and word not in FUNCTION_WORDS
