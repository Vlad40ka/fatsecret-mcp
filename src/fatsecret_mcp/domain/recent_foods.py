"""Recent Foods: the Foods a User logged in recent weeks, found by a description in any language.

They're built from the User's own Food Entries, not from the catalog: a Basic key can't fetch non-US
Foods, and the catalog doesn't know a Stand-in's name. So each Serving carries the amount, units, kcal
and Macros of the Food Entry it was last logged in. Per unit, that's the entry's values divided by its
number of units, just like a catalog Serving. Its grams come from any Food Entry of that Serving
logged in grams: if 0.7 servings were 70 g, then 1.7 servings are 170 g.

Matching ignores case and diacritics and compares words. Words match without their final vowels, so
Polish endings don't get in the way: "jajka" matches "Jajko", and "kurczaka" matches "Kurczak".
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta

from fatsecret_mcp.domain.models import FoodEntry, MetricAmount, RecentFood, Serving

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
    """The User's Food Entries, de-duplicated by Food, Serving and entry name.

    Names that differ only by a brand in front count as one: the FatSecret app sometimes logs
    "Crownfield Płatki Owsiane" and sometimes "Płatki Owsiane". A Stand-in's name has nothing in common
    with its Food's, so it stays a Recent Food of its own.
    """
    chronological = sorted(entries, key=lambda entry: entry.diary_day)
    loggable = [entry for entry in chronological if entry.serving_id != DERIVED_SERVING_ID]
    product_names = _product_names(loggable)
    metric_per_unit = _metric_per_unit(loggable)
    by_food_and_name: dict[tuple[str, str], list[FoodEntry]] = {}

    for entry in loggable:
        name = product_names[(entry.food_id, entry.name)]

        by_food_and_name.setdefault((entry.food_id, name), []).append(entry)

    foods = [
        _recent_food(name, food_entries, metric_per_unit)
        for (_, name), food_entries in by_food_and_name.items()
    ]

    return foods


def matching_recent_foods(foods: list[RecentFood], descriptions: list[str]) -> list[RecentFood]:
    """The Recent Foods that match the descriptions best, the most often and most recently logged first.

    The best match has the largest share of a description's words in its name. Weaker matches are left
    out, so "skyr waniliowy" finds "Skyr Waniliowy" rather than every skyr and every vanilla pudding.
    """
    word_lists = [_words(description) for description in descriptions]
    qualities = [_match_quality(food.name, word_lists) for food in foods]
    best = max(qualities, default=0)

    if best == 0:
        return []

    best_matches = [food for food, quality in zip(foods, qualities, strict=True) if quality == best]
    ranked = sorted(best_matches, key=_by_frequency_and_recency)

    return ranked


def _product_names(entries: list[FoodEntry]) -> dict[tuple[str, str], str]:
    """The name each Food and entry name is shown under: the Food's longest name that ends with it."""
    names_by_food: dict[str, list[str]] = {}

    # The most recent first, so that of two names differing only in case, the later one is shown.
    for entry in reversed(entries):
        names = names_by_food.setdefault(entry.food_id, [])

        if entry.name not in names:
            names.append(entry.name)

    product_names: dict[tuple[str, str], str] = {}

    for food_id, names in names_by_food.items():
        for name in names:
            product_names[(food_id, name)] = _longest_ending_with(name, names)

    return product_names


def _longest_ending_with(name: str, names: list[str]) -> str:
    """The longest of `names` that is `name`, ignoring case, or ends with it after a space."""
    ending = name.casefold()
    candidates = [other for other in names if _ends_with_words(other.casefold(), ending)]

    return max(candidates, key=len)


def _ends_with_words(name: str, ending: str) -> bool:
    return name == ending or name.endswith(f" {ending}")


def _metric_per_unit(entries: list[FoodEntry]) -> dict[tuple[str, str], MetricAmount]:
    """The grams or millilitres in one unit of each Serving, from its last Food Entry logged in them."""
    per_unit: dict[tuple[str, str], MetricAmount] = {}

    for entry in entries:
        metric = METRIC_AMOUNT.fullmatch(entry.amount)

        if metric and entry.number_of_units > 0:
            per_unit[(entry.food_id, entry.serving_id)] = MetricAmount(
                amount=float(metric["amount"]) / entry.number_of_units,
                unit=metric["unit"],
            )

    return per_unit


def _recent_food(
    name: str, entries: list[FoodEntry], metric_per_unit: dict[tuple[str, str], MetricAmount]
) -> RecentFood:
    """One Food under one name, from its Food Entries in chronological order."""
    last = entries[-1]

    return RecentFood(
        food_id=last.food_id,
        name=name,
        servings=_servings_as_last_logged(entries, metric_per_unit),
        times_logged=len(entries),
        last_logged=last.diary_day,
    )


def _servings_as_last_logged(
    entries: list[FoodEntry], metric_per_unit: dict[tuple[str, str], MetricAmount]
) -> list[Serving]:
    """Each Serving the Food Entries were logged with, as last logged, the most recent first."""
    last_entries: dict[str, FoodEntry] = {}

    for entry in reversed(entries):
        last_entries.setdefault(entry.serving_id, entry)

    servings = [_serving_as_logged(entry, metric_per_unit) for entry in last_entries.values()]

    return servings


def _serving_as_logged(entry: FoodEntry, metric_per_unit: dict[tuple[str, str], MetricAmount]) -> Serving:
    """A Serving described by the amount it was logged in, e.g. "150 g" or "1.7 servings"."""
    per_unit = metric_per_unit.get((entry.food_id, entry.serving_id))
    metric_amount = round(per_unit.amount * entry.number_of_units, 1) if per_unit else None
    metric_unit = per_unit.unit if per_unit else None

    return Serving(
        id=entry.serving_id,
        description=entry.amount,
        metric_amount=metric_amount,
        metric_unit=metric_unit,
        number_of_units=entry.number_of_units,
        nutrition=entry.nutrition,
    )


def _by_frequency_and_recency(food: RecentFood) -> tuple[int, int]:
    """Sort key: the most often logged first, then the most recently logged."""
    return (-food.times_logged, -food.last_logged.toordinal())


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
