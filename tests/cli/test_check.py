"""`check`: the configuration, then one harmless FatSecret call per User, reported per User."""

from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

from fatsecret_mcp.cli.check import check
from fatsecret_mcp.config.models import Household
from fatsecret_mcp.fatsecret.errors import DailyQuotaExceeded, FatSecretError, IpAddressRejected
from tests.auth.test_http import BASE_URL, PASSWORD_HASH
from tests.cli.conftest import ENVIRON, ScriptedConsole
from tests.conftest import HOUSEHOLD_USERS, FakeClock
from tests.fatsecret.fake import FakeFatSecret

pytestmark = pytest.mark.anyio

# Over stdio a Household of several Users names the one the server acts as.
STDIO = ENVIRON | {"FATSECRET_MCP_USER": "jakub"}
SECRETS = [
    "consumer-key",
    "consumer-secret",
    "test-signing-key-0123456789abcdef",
    "jakub-token",
    "jakub-secret",
    "ola-token",
    "ola-secret",
    PASSWORD_HASH,
]


def line_for(console: ScriptedConsole, user_id: str) -> str:
    lines = [line for line in console.transcript if line.startswith(f"{user_id}: ")]

    assert len(lines) == 1
    return lines[0]


def with_password_hash(user: Mapping[str, object]) -> dict[str, object]:
    return dict(user) | {"connector_password_hash": PASSWORD_HASH}


async def test_a_working_household_reports_every_user_connected(
    fatsecret: FakeFatSecret, clock: FakeClock
) -> None:
    console = ScriptedConsole([])

    assert await check(console, STDIO, fatsecret, clock)

    assert "2 Users" in console.text
    assert line_for(console, "jakub") == "jakub: connected to FatSecret"
    assert line_for(console, "ola") == "ola: connected to FatSecret"
    assert fatsecret.requests == 2


async def test_a_revoked_token_is_reported_for_that_user_only(
    fatsecret: FakeFatSecret, household: Household, clock: FakeClock
) -> None:
    console = ScriptedConsole([])

    fatsecret.revoke(household.user("ola").fatsecret)

    assert not await check(console, STDIO, fatsecret, clock)

    assert line_for(console, "jakub") == "jakub: connected to FatSecret"
    assert "revoked" in line_for(console, "ola")


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (DailyQuotaExceeded(), "daily FatSecret request quota"),
        (IpAddressRejected(), "IP address"),
    ],
)
async def test_quota_and_ip_restrictions_have_their_own_messages(
    fatsecret: FakeFatSecret, clock: FakeClock, error: FatSecretError, message: str
) -> None:
    console = ScriptedConsole([])

    fatsecret.fail_with(error)

    assert not await check(console, STDIO, fatsecret, clock)

    assert message in line_for(console, "jakub")
    assert message in line_for(console, "ola")


async def test_an_invalid_configuration_is_reported_without_calling_fatsecret(
    fatsecret: FakeFatSecret, clock: FakeClock
) -> None:
    console = ScriptedConsole([])
    environ = STDIO | {"FATSECRET_MCP_USERS": '[{"id": "jakub"}]'}

    assert not await check(console, environ, fatsecret, clock)

    assert "FATSECRET_MCP_USERS[0].display_name" in console.text
    assert fatsecret.requests == 0


async def test_with_a_public_base_url_every_user_needs_a_connector_password(
    fatsecret: FakeFatSecret, clock: FakeClock
) -> None:
    console = ScriptedConsole([])
    environ = ENVIRON | {"FATSECRET_MCP_BASE_URL": BASE_URL}

    assert not await check(console, environ, fatsecret, clock)

    assert "connector_password_hash is required over HTTP" in console.text
    assert fatsecret.requests == 0


async def test_a_household_ready_for_http_says_so_and_prints_no_secrets(
    fatsecret: FakeFatSecret, clock: FakeClock
) -> None:
    console = ScriptedConsole([])
    users = [with_password_hash(user) for user in HOUSEHOLD_USERS]
    environ = ENVIRON | {
        "FATSECRET_MCP_BASE_URL": BASE_URL,
        "FATSECRET_MCP_USERS": json.dumps(users),
    }

    assert await check(console, environ, fatsecret, clock)

    assert BASE_URL in console.text
    for secret in SECRETS:
        assert secret not in console.text


async def test_over_stdio_a_household_of_several_needs_the_acting_user(
    fatsecret: FakeFatSecret, clock: FakeClock
) -> None:
    console = ScriptedConsole([])

    assert not await check(console, ENVIRON, fatsecret, clock)

    assert "FATSECRET_MCP_USER must name the User to act as" in console.text
    assert fatsecret.requests == 0
