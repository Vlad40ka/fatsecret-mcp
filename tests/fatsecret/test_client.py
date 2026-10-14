"""The real FatSecret client on recorded responses: error mapping and response quirks."""

from __future__ import annotations

from datetime import date

import httpx2
import pytest

from fatsecret_mcp.fatsecret.client import FatSecretApi
from fatsecret_mcp.fatsecret.errors import (
    CredentialsRejected,
    DailyQuotaExceeded,
    FatSecretError,
    InvalidAccessToken,
    IpAddressRejected,
    NotFound,
    UpstreamFailure,
)
from tests.fatsecret.recorded import CONSUMER, USER, RecordedFatSecret, fixture

pytestmark = pytest.mark.anyio

DAY = date(2026, 10, 13)


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
async def test_fatsecret_errors_map_to_domain_errors(
    recorded_error: str, domain_error: type[FatSecretError]
) -> None:
    recorded = RecordedFatSecret()

    recorded.answer_everything_with(recorded_error)

    async with recorded.client() as http:
        with pytest.raises(domain_error) as raised:
            await FatSecretApi(CONSUMER, http).list_food_entries(USER, DAY)

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
