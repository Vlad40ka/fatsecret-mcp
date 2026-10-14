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
NUTRITION = ["Skyr Waniliowy", "Chicken Breast", "18.4", "39.89", "11.58", "97.01", "24.6"]


@pytest.mark.parametrize(
    ("tool", "recorded"),
    [
        ("get_diary_day", "food_entries_get.json"),
        ("get_diary_day", "error_invalid_access_token.json"),
        ("get_period_summary", "food_entries_get_month.json"),
        ("get_period_summary", "error_invalid_access_token.json"),
    ],
)
async def test_a_diary_tool_call_logs_no_secrets_and_no_nutrition(
    tool: str, recorded: str, caplog: pytest.LogCaptureFixture
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
            arguments = (
                {"diary_day": "2015-01-12"}
                if tool == "get_diary_day"
                else {"from_day": "2015-01-12", "to_day": "2015-01-13"}
            )

            await client.call_tool(tool, arguments)

    assert caplog.records, "expected the call to log something, or this test proves nothing"
    for leak in SECRETS + NUTRITION:
        assert leak not in caplog.text
