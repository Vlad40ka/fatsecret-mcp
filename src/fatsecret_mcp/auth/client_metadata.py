"""Claude's published client metadata: where it may be fetched from and what it must allow."""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx2
from mcp.shared.auth import OAuthClientInformationFull

_MAX_METADATA_BYTES = 65536


async def fetch_client(http: httpx2.AsyncClient, client_id: str) -> OAuthClientInformationFull | None:
    """The client that `client_id` names, or None when it isn't a public Claude client."""
    if not _is_claude_url(client_id):
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


def _is_claude_url(client_id: str) -> bool:
    # This Household connector trusts Claude's published identity. Restrict the fetch
    # before making any request, including redirects, to avoid arbitrary outbound URLs.
    try:
        url = urlsplit(client_id)
    except ValueError:
        return False

    return (
        url.scheme == "https"
        and url.netloc == "claude.ai"
        and bool(url.path)
        and not url.query
        and not url.fragment
    )


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
