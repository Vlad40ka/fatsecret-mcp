"""Claude's published client metadata: its pinned identity and callback, what it must allow,
and how rarely the server fetches it.
"""

from __future__ import annotations

import anyio
import httpx2
from mcp.shared.auth import OAuthClientInformationFull

from fatsecret_mcp.auth.models import FetchLimited
from fatsecret_mcp.domain.diary_day import Clock
from fatsecret_mcp.limits import RollingWindow

# The identity and callback of Claude's web, desktop and mobile apps, pinned exactly,
# so no other document on claude.ai can choose where a User's code is sent.
CLAUDE_CLIENT_ID = "https://claude.ai/oauth/mcp-oauth-client-metadata"
CLAUDE_CALLBACKS = frozenset({"https://claude.ai/api/mcp/auth_callback"})
CLIENT_LIFETIME = 3600
FETCHES_PER_MINUTE = 10
_MAX_METADATA_BYTES = 65536


class ClaudeMetadata:
    """Claude's client metadata, kept for an hour and fetched one request at a time.

    Only a usable document is kept, so a failed fetch is retried by the next sign-in. Fetches
    are limited server-wide, so a flood of sign-in pages can't make claude.ai limit this server.
    """

    def __init__(self, http: httpx2.AsyncClient, clock: Clock) -> None:
        self.http = http
        self.clock = clock
        self.cached: OAuthClientInformationFull | None = None
        self.expires_at = 0.0
        self.fetches = RollingWindow(
            limit=FETCHES_PER_MINUTE,
            seconds=60,
        )
        self.lock = anyio.Lock()

    async def lookup(self, client_id: str) -> OAuthClientInformationFull | FetchLimited | None:
        """Claude's client, a refusal while fetches are used up, or None for anything else."""
        if client_id not in (CLAUDE_CLIENT_ID, "https://chatgpt.com/oauth/client.json"):
            return None

        async with self.lock:
            # Read after waiting for the lock, which a slow fetch ahead may have held.
            now = self.clock().timestamp()

            if self.cached is not None and now < self.expires_at:
                return self.cached

            retry_after = self.fetches.retry_after(now)

            if retry_after is not None:
                return FetchLimited(retry_after=retry_after)

            self.cached = await _fetch(self.http, client_id)
            self.expires_at = now + CLIENT_LIFETIME

            return self.cached


async def _fetch(http: httpx2.AsyncClient, client_id: str) -> OAuthClientInformationFull | None:
    """The published client, or None for an unusable document."""
    body = await _download(http, client_id)

    if body is None:
        return None

    try:
        client = OAuthClientInformationFull.model_validate_json(body)
    except ValueError:
        return None

    if not _is_public_code_client(client, client_id):
        return None

    return client


async def _download(http: httpx2.AsyncClient, url: str) -> bytes | None:
    """The response body, or None on a failure, a redirect or more than the size limit."""
    body = bytearray()

    try:
        async with http.stream("GET", url, follow_redirects=False, timeout=5) as response:
            if response.status_code != 200:
                return None

            async for chunk in response.aiter_bytes():
                body.extend(chunk)

                if len(body) > _MAX_METADATA_BYTES:
                    return None
    except (httpx2.HTTPError, ValueError):
        return None

    return bytes(body)


def _is_public_code_client(client: OAuthClientInformationFull, client_id: str) -> bool:
    """A client without a secret that describes itself and uses the authorization-code flow."""
    if client.client_id != client_id or client.token_endpoint_auth_method not in ("none", "private_key_jwt") or client.client_secret:
        return False

    return "authorization_code" in client.grant_types and "code" in client.response_types
