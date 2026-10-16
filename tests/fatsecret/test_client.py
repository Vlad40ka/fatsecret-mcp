"""The real FatSecret client on recorded responses: error mapping and response quirks."""

from __future__ import annotations

from datetime import date

import httpx2
import pytest

from fatsecret_mcp.domain.models import DraftEntry, Meal, Nutrition
from fatsecret_mcp.fatsecret.client import FatSecretApi
from fatsecret_mcp.fatsecret.errors import (
    CredentialsRejected,
    DailyQuotaExceeded,
    FatSecretError,
    InvalidAccessToken,
    IpAddressRejected,
    NotFound,
    PinRejected,
    TooManyActions,
    UpstreamFailure,
)
from fatsecret_mcp.fatsecret.gateway import RequestToken
from tests.fatsecret.recorded import CONSUMER, USER, RecordedFatSecret, fixture

pytestmark = pytest.mark.anyio

DAY = date(2026, 10, 13)


async def test_error_12_reports_temporary_throttling_without_exposing_the_upstream_message() -> None:
    response = httpx2.Response(
        200,
        json={"error": {"code": 12, "message": "User is performing too many actions: secret-token"}},
    )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _: response)) as http:
        with pytest.raises(TooManyActions) as raised:
            await FatSecretApi(CONSUMER, http).list_food_entries(USER, DAY)

    assert raised.value.code == 12
    assert "Wait a moment" in str(raised.value)
    assert "secret-token" not in str(raised.value)


@pytest.mark.parametrize(
    ("recorded_error", "domain_error"),
    [
        ("error_invalid_access_token.json", InvalidAccessToken),
        ("error_daily_quota.json", DailyQuotaExceeded),
        ("error_ip_restricted.json", IpAddressRejected),
        ("error_food_not_found.json", NotFound),
        ("error_invalid_consumer_key.json", CredentialsRejected),
        ("error_invalid_signature.json", CredentialsRejected),
    ],
)
@pytest.mark.parametrize("operation", ["food_entries", "month_totals", "search_catalog", "get_food"])
async def test_fatsecret_errors_map_to_domain_errors(
    recorded_error: str, domain_error: type[FatSecretError], operation: str
) -> None:
    recorded = RecordedFatSecret()

    recorded.answer_everything_with(recorded_error)

    async with recorded.client() as http:
        gateway = FatSecretApi(CONSUMER, http)
        calls = {
            "food_entries": lambda: gateway.list_food_entries(USER, DAY),
            "month_totals": lambda: gateway.get_month_totals(USER, DAY),
            "search_catalog": lambda: gateway.search_catalog("chicken breast"),
            "get_food": lambda: gateway.get_food("1641"),
        }

        with pytest.raises(domain_error) as raised:
            await calls[operation]()

    assert raised.value.code == fixture(recorded_error)["response"]["json"]["error"]["code"]


@pytest.mark.parametrize(
    "answer",
    [
        httpx2.Response(503, text="Service Unavailable"),
        httpx2.Response(200, text="<html>maintenance</html>"),
        httpx2.Response(200, json=["not", "an", "object"]),
        httpx2.Response(200, json={"error": {"code": 20, "message": "System temporarily unavailable"}}),
        httpx2.Response(200, json={"error": {"code": 6, "message": "Invalid/expired timestamp"}}),
        httpx2.Response(200, json={"error": {"message": "no code"}}),
        httpx2.Response(200, json={"food_entries": {"food_entry": [{"food_entry_id": "1"}]}}),
    ],
    ids=[
        "http-503",
        "not-json",
        "not-an-object",
        "unavailable-error",
        "clock-skew-error",
        "error-without-code",
        "missing-fields",
    ],
)
async def test_anything_else_from_fatsecret_is_an_upstream_failure(answer: httpx2.Response) -> None:
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _: answer)) as http:
        with pytest.raises(UpstreamFailure):
            await FatSecretApi(CONSUMER, http).list_food_entries(USER, DAY)


@pytest.mark.parametrize(
    "transport_error", [httpx2.ConnectError("connection refused"), httpx2.ReadTimeout("timed out")]
)
async def test_an_unreachable_fatsecret_is_an_upstream_failure(transport_error: httpx2.HTTPError) -> None:
    def fail(_: httpx2.Request) -> httpx2.Response:
        raise transport_error

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(fail)) as http:
        with pytest.raises(UpstreamFailure):
            await FatSecretApi(CONSUMER, http).list_food_entries(USER, DAY)


REQUEST_TOKEN = RequestToken(
    token="REQUEST_TOKEN",
    secret="REQUEST_TOKEN_SECRET",
)


@pytest.mark.parametrize(
    "answer",
    [
        httpx2.Response(503, text="Service Unavailable"),
        httpx2.Response(401, text="Invalid consumer key"),
        httpx2.Response(200, text="oauth_callback_confirmed=true"),
        httpx2.Response(200, text="oauth_token=REQUEST_TOKEN"),
    ],
    ids=["http-503", "http-401", "no-token", "no-secret"],
)
async def test_an_unexpected_sign_in_answer_is_an_upstream_failure(answer: httpx2.Response) -> None:
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _: answer)) as http:
        api = FatSecretApi(CONSUMER, http)

        with pytest.raises(UpstreamFailure):
            await api.request_token()

        with pytest.raises(UpstreamFailure):
            await api.exchange_pin(REQUEST_TOKEN, "4417092")


async def test_a_rejected_pin_does_not_quote_the_request_token() -> None:
    recorded = RecordedFatSecret()

    recorded.serve("oauth_access_token_wrong_pin.json")

    async with recorded.client() as http:
        with pytest.raises(PinRejected) as raised:
            await FatSecretApi(CONSUMER, http).exchange_pin(REQUEST_TOKEN, "0000000")

    assert "REQUEST_TOKEN" not in str(raised.value)
    assert "Start the sign-in again" in str(raised.value)


async def test_a_single_food_entry_sent_as_a_bare_object_is_still_listed() -> None:
    # FatSecret documents this shape. Version 2 recorded a one-item list instead, so it's derived here.
    exchange = fixture("food_entries_get_single.json")
    food_entries = exchange["response"]["json"]["food_entries"]
    food_entries["food_entry"] = food_entries["food_entry"][0]
    recorded = RecordedFatSecret()

    recorded.serve(exchange)

    async with recorded.client() as http:
        entries = await FatSecretApi(CONSUMER, http).list_food_entries(USER, date(2015, 1, 13))

    assert [entry.name for entry in entries] == ["Skyr Waniliowy"]


async def test_a_snack_with_a_fractional_amount_is_created_as_fatsecret_expects() -> None:
    # The recorded request, as it's sent for Snacks ("other") and one and a half units.
    exchange = fixture("food_entry_create.json")
    exchange["request"]["params"]["meal"] = "other"
    exchange["request"]["params"]["number_of_units"] = "1.5"
    entry = DraftEntry(
        food_id="51467582",
        serving_id="43596665",
        number_of_units=1.5,
        entry_name="Skyr Waniliowy",
    )
    recorded = RecordedFatSecret()

    recorded.serve(exchange)

    async with recorded.client() as http:
        entry_id = await FatSecretApi(CONSUMER, http).create_food_entry(
            USER, entry, Meal.SNACKS, date(2015, 1, 12)
        )

    assert entry_id == "9000001"


@pytest.mark.parametrize(
    ("description", "amount"),
    [
        ("2 servings Skyr Waniliowy", "2 servings"),
        ("2 1/2 servings Skyr Waniliowy", "2.5 servings"),
        ("1/3 cup Skyr Waniliowy", "0.33 cup"),
        ("0.1 :custom:10 g Skyr Waniliowy", "10 g"),
        ("1/3 :custom:33 g Skyr Waniliowy", "33 g"),
        ("2 1/2 :custom:250s  g Skyr Waniliowy", "250 g"),
        ("1 7/8 :custom:187s  1/2 g Skyr Waniliowy", "187.5 g"),
        ("10 :custom:1000s  ml Skyr Waniliowy", "1000 ml"),
        ("4 :custom:4s  x 1 porcja (240 g) Skyr Waniliowy", "4 x 1 porcja (240 g)"),
    ],
)
async def test_the_amount_is_shown_as_the_fatsecret_app_shows_it(description: str, amount: str) -> None:
    # An amount typed in the app arrives as "<units> :custom:<amount>", with a stray "s" above one
    # serving. These shapes were seen in a real Food Diary; the recorded entry is reused to carry them.
    exchange = fixture("food_entries_get_single.json")
    exchange["response"]["json"]["food_entries"]["food_entry"][0]["food_entry_description"] = description

    recorded = RecordedFatSecret()
    recorded.serve(exchange)

    async with recorded.client() as http:
        [entry] = await FatSecretApi(CONSUMER, http).list_food_entries(USER, date(2015, 1, 13))

    assert entry.amount == amount


async def test_a_single_month_day_sent_as_a_bare_object_is_still_listed() -> None:
    # A derived variant of the recorded list checks FatSecret's singleton-object response shape.
    exchange = fixture("food_entries_get_month.json")
    month = exchange["response"]["json"]["month"]
    month["day"] = month["day"][1]
    recorded = RecordedFatSecret()

    recorded.serve(exchange)

    async with recorded.client() as http:
        totals = await FatSecretApi(CONSUMER, http).get_month_totals(USER, date(2015, 1, 1))

    assert totals == {
        date(2015, 1, 13): Nutrition(
            kcal=74,
            protein=6.4,
            fat=1.3,
            carbohydrate=9.2,
        ),
    }


@pytest.mark.parametrize("bad_day", ["not-a-day", "9999999999999999999999"])
async def test_invalid_month_diary_days_become_upstream_failures(bad_day: str) -> None:
    exchange = fixture("food_entries_get_month.json")
    exchange["response"]["json"]["month"]["day"][0]["date_int"] = bad_day
    recorded = RecordedFatSecret()

    recorded.serve(exchange)

    async with recorded.client() as http:
        with pytest.raises(UpstreamFailure):
            await FatSecretApi(CONSUMER, http).get_month_totals(USER, date(2015, 1, 1))


async def test_generic_food_preserves_fractional_servings_and_gram_units() -> None:
    recorded = RecordedFatSecret()

    recorded.serve("food_get_generic.json")

    async with recorded.client() as http:
        food = await FatSecretApi(CONSUMER, http).get_food("1641")

    grams, half_breast = food.servings[:2]

    assert food.id == "1641"
    assert food.name == "Chicken Breast"
    assert food.brand is None
    assert len(food.servings) == 10
    assert grams.id == "50321"
    assert grams.description == "100 g"
    assert grams.number_of_units == 100
    assert grams.metric_amount == 100
    assert grams.metric_unit == "g"
    assert grams.nutrition == Nutrition(
        kcal=195,
        protein=29.55,
        fat=7.72,
        carbohydrate=0,
    )
    assert half_breast.number_of_units == 0.5
    assert half_breast.metric_amount == 84
    assert half_breast.nutrition.kcal == 164


async def test_single_serving_without_optional_metric_fields_is_supported() -> None:
    # Derived from the recorded response to cover a singleton and omitted optional fields.
    exchange = fixture("food_get_brand.json")
    servings = exchange["response"]["json"]["food"]["servings"]
    serving = servings["serving"][0]

    del serving["metric_serving_amount"]
    del serving["metric_serving_unit"]
    servings["serving"] = serving
    recorded = RecordedFatSecret()

    recorded.serve(exchange)

    async with recorded.client() as http:
        food = await FatSecretApi(CONSUMER, http).get_food("5673332")

    [parsed] = food.servings

    assert parsed.id == "5509656"
    assert parsed.description == "4 oz"
    assert parsed.metric_amount is None
    assert parsed.metric_unit is None
    assert parsed.nutrition.kcal == 110


@pytest.mark.parametrize("field", ["food_id", "food_name"])
async def test_food_missing_required_fields_is_an_upstream_failure(field: str) -> None:
    exchange = fixture("food_get_brand.json")

    del exchange["response"]["json"]["food"][field]
    recorded = RecordedFatSecret()

    recorded.serve(exchange)

    async with recorded.client() as http:
        with pytest.raises(UpstreamFailure):
            await FatSecretApi(CONSUMER, http).get_food("5673332")
