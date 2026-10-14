"""Replays FatSecret responses recorded in tests/fixtures/fatsecret to the real adapter.

Each request must match a recorded one (same endpoint, same non-OAuth parameters) and carry a valid
OAuth 1.0 signature for the test credentials below, so the tests also check the adapter's wire format.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import httpx2

from fatsecret_mcp.fatsecret.gateway import AccessToken, ConsumerKey
from fatsecret_mcp.fatsecret.oauth import signature

FIXTURES = Path(__file__).parent.parent / "fixtures" / "fatsecret"

CONSUMER = ConsumerKey(key="test-consumer-key", secret="test-consumer-secret")
USER = AccessToken(token="test-access-token", secret="test-access-secret")


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


class RecordedFatSecret:
    def __init__(self) -> None:
        self._exchanges: list[dict[str, Any]] = []
        self._any_request: dict[str, Any] | None = None

    def serve(self, exchange: str | dict[str, Any]) -> None:
        """Answer the request recorded in `exchange` (a fixture name or a loaded fixture) as recorded."""
        self._exchanges.append(fixture(exchange) if isinstance(exchange, str) else exchange)

    def answer_everything_with(self, exchange: str) -> None:
        """Answer any request with this recorded response. FatSecret's errors look alike for every method."""
        self._any_request = fixture(exchange)

    def client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self._handle))

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        params = dict(request.url.params)

        _check_signature(request, params)
        if self._any_request is not None:
            return _response(self._any_request)
        plain = {key: value for key, value in params.items() if not key.startswith("oauth_")}

        for exchange in self._exchanges:
            recorded = exchange["request"]

            if (
                recorded["http_method"] == request.method
                and recorded["url"] == _without_query(request)
                and recorded["params"] == plain
            ):
                return _response(exchange)
        raise AssertionError(f"no recorded FatSecret exchange for {request.method} {plain}")


def _check_signature(request: httpx2.Request, params: dict[str, str]) -> None:
    assert params.get("oauth_consumer_key") == CONSUMER.key
    token_secret = ""

    if "oauth_token" in params:
        assert params["oauth_token"] == USER.token
        token_secret = USER.secret
    signed = {key: value for key, value in params.items() if key != "oauth_signature"}
    expected = signature(request.method, _without_query(request), signed, CONSUMER.secret, token_secret)

    assert params.get("oauth_signature") == expected, "the request's OAuth 1.0 signature doesn't verify"


def _without_query(request: httpx2.Request) -> str:
    return str(request.url.copy_with(query=None))


def _response(exchange: dict[str, Any]) -> httpx2.Response:
    recorded = cast(dict[str, Any], exchange["response"])
    headers = {"content-type": recorded["content_type"]}

    if "json" in recorded:
        return httpx2.Response(recorded["status"], headers=headers, content=json.dumps(recorded["json"]))
    return httpx2.Response(recorded["status"], headers=headers, text=recorded["text"])
