"""Claude's published client metadata: its pinned identity and callback, and what it must allow."""

from __future__ import annotations

import httpx2
from mcp.shared.auth import OAuthClientInformationFull

# The identity and callback of Claude's web, desktop and mobile apps, pinned exactly,
# so no other document on claude.ai can choose where a User's code is sent.
CLAUDE_CLIENT_ID = "https://claude.ai/oauth/mcp-oauth-client-metadata"
CLAUDE_CALLBACKS = frozenset({"https://claude.ai/api/mcp/auth_callback"})
_MAX_METADATA_BYTES = 65536


async def fetch_client(http: httpx2.AsyncClient, client_id: str) -> OAuthClientInformationFull | None:
    """Claude's published client, or None for any other `client_id` or an unusable document."""
    if client_id != CLAUDE_CLIENT_ID:
        return None

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
    if client.client_id != client_id or client.token_endpoint_auth_method != "none" or client.client_secret:
        return False

    return "authorization_code" in client.grant_types and "code" in client.response_types
