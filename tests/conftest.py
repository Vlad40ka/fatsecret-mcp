from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from mcp import Client

from fatsecret_mcp.config.loader import load_household
from fatsecret_mcp.config.models import Household
from fatsecret_mcp.server import build_server
from tests.fatsecret.fake import FakeFatSecret

HOUSEHOLD_USERS = [
    {
        "id": "jakub",
        "display_name": "Jakub",
        "fatsecret_access_token": "jakub-token",
        "fatsecret_access_secret": "jakub-secret",
        "daily_targets": {"kcal": 2500, "protein": 160, "fat": 80, "carbohydrate": 280},
    },
    {
        "id": "ola",
        "display_name": "Ola",
        "fatsecret_access_token": "ola-token",
        "fatsecret_access_secret": "ola-secret",
        "daily_targets": {"kcal": 1900, "protein": 110, "fat": 65, "carbohydrate": 210},
        "timezone": "Europe/Warsaw",
    },
]


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def household() -> Household:
    return load_household(
        {
            "FATSECRET_CONSUMER_KEY": "consumer-key",
            "FATSECRET_CONSUMER_SECRET": "consumer-secret",
            "FATSECRET_MCP_USERS": json.dumps(HOUSEHOLD_USERS),
        }
    )


@pytest.fixture
def fatsecret(household: Household) -> FakeFatSecret:
    fake = FakeFatSecret()

    for user in household.users:
        fake.add_account(user.fatsecret)
    return fake


@pytest.fixture
def clock() -> FakeClock:
    """The server's clock runs in UTC; noon on 2026-10-14 in Warsaw."""
    return FakeClock(datetime(2026, 10, 14, 10, 0, tzinfo=UTC))


@pytest.fixture
def connect(household: Household, fatsecret: FakeFatSecret, clock: FakeClock) -> Callable[[str], Client]:
    """An in-memory MCP client session with the real server, acting as one User of the Household."""

    def connect(user_id: str) -> Client:
        return Client(build_server(household.user(user_id), fatsecret, clock))

    return connect
