"""Opt-in smoke test against the real FatSecret API. Never runs in CI.

It reads today's Diary Day for the acting User, through the whole server, and prints nothing from it.
Run it with the Household configuration in the environment, for example from a local `.env` file:

    FATSECRET_LIVE_SMOKE=1 uv run --env-file .env pytest tests/test_live_smoke.py
"""

from __future__ import annotations

import os

import httpx2
import pytest
from mcp import Client

from fatsecret_mcp.config.loader import acting_user, load_household
from fatsecret_mcp.fatsecret.budget import daily_budget
from fatsecret_mcp.fatsecret.client import FatSecretApi
from fatsecret_mcp.server import build_server

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        os.environ.get("FATSECRET_LIVE_SMOKE") != "1", reason="set FATSECRET_LIVE_SMOKE=1 to run"
    ),
]


async def test_reads_todays_diary_day_from_the_real_fatsecret() -> None:
    household = load_household(os.environ)
    user = acting_user(household, os.environ)

    async with (
        httpx2.AsyncClient(timeout=20) as http,
        Client(
            build_server(
                user=user,
                fatsecret=FatSecretApi(household.consumer, http),
                signing_key=household.signing_key,
                daily_requests=daily_budget(len(household.users)),
            )
        ) as client,
    ):
        result = await client.call_tool("get_diary_day", {})

    assert not result.is_error, result.content
    assert result.structured_content is not None
    assert set(result.structured_content) >= {"diary_day", "meals", "totals", "daily_targets", "remaining"}
