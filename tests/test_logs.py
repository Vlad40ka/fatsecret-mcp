"""Logs never contain tokens, secrets or nutrition data, even at DEBUG level."""

from __future__ import annotations

import json
import logging

import pytest
from mcp import Client

from fatsecret_mcp.config.loader import load_household
from fatsecret_mcp.fatsecret.client import FatSecretApi
from fatsecret_mcp.server import build_server
from tests.fatsecret.recorded import CONSUMER, USER, RecordedFatSecret

pytestmark = pytest.mark.anyio

SECRETS = [CONSUMER.key, CONSUMER.secret, USER.token, USER.secret]
NUTRITION = ["Skyr Waniliowy", "Chicken Breast", "18.4", "39.89", "11.58"]


@pytest.mark.parametrize("recorded", ["food_entries_get.json", "error_invalid_access_token.json"])
async def test_a_get_diary_day_call_logs_no_secrets_and_no_nutrition(
    recorded: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    household = load_household(
        {
            "FATSECRET_CONSUMER_KEY": CONSUMER.key,
            "FATSECRET_CONSUMER_SECRET": CONSUMER.secret,
            "FATSECRET_MCP_USERS": json.dumps(
                [
                    {
                        "id": "jakub",
                        "display_name": "Jakub",
                        "fatsecret_access_token": USER.token,
                        "fatsecret_access_secret": USER.secret,
                        "daily_targets": {"kcal": 2500, "protein": 160, "fat": 80, "carbohydrate": 280},
                    }
                ]
            ),
        }
    )
    fatsecret = RecordedFatSecret()

    fatsecret.answer_everything_with(recorded)

    async with fatsecret.client() as http:
        server = build_server(household.user("jakub"), FatSecretApi(household.consumer, http))

        async with Client(server) as client:
            await client.call_tool("get_diary_day", {"diary_day": "2015-01-12"})

    assert caplog.records, "expected the call to log something, or this test proves nothing"
    for leak in SECRETS + NUTRITION:
        assert leak not in caplog.text
