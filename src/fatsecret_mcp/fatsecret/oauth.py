"""OAuth 1.0 request signing (HMAC-SHA1), the only signature method FatSecret supports."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections.abc import Mapping
from urllib.parse import quote

from fatsecret_mcp.fatsecret.gateway import AccessToken, ConsumerKey, RequestToken


def signed_url(
    http_method: str,
    url: str,
    params: Mapping[str, str],
    consumer: ConsumerKey,
    user: AccessToken | RequestToken | None,
) -> str:
    """`url` with `params` and the OAuth 1.0 parameters, signed, in its query string.

    `params` may carry extra OAuth parameters, such as the PIN flow's `oauth_callback`.
    """
    oauth = {
        "oauth_consumer_key": consumer.key,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_nonce": secrets.token_hex(16),
        "oauth_version": "1.0",
    }

    if user is not None:
        oauth["oauth_token"] = user.token

    token_secret = user.secret if user else ""

    signed = dict(params)
    signed.update(oauth)
    signed["oauth_signature"] = signature(http_method, url, signed, consumer.secret, token_secret)

    query = _query(signed)

    return f"{url}?{query}"


def signature(
    http_method: str, url: str, params: Mapping[str, str], consumer_secret: str, token_secret: str = ""
) -> str:
    """The OAuth 1.0 HMAC-SHA1 signature of a request (RFC 5849, section 3.4)."""
    normalized = _query(params, sort=True)
    base = "&".join((http_method.upper(), percent_encode(url), percent_encode(normalized)))

    key = f"{percent_encode(consumer_secret)}&{percent_encode(token_secret)}"
    digest = hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()

    return base64.b64encode(digest).decode()


def _query(params: Mapping[str, str], *, sort: bool = False) -> str:
    """`params` percent-encoded as `key=value` pairs joined by `&`, sorted when signing."""
    pairs = [(percent_encode(key), percent_encode(value)) for key, value in params.items()]
    ordered = sorted(pairs) if sort else pairs
    encoded = [f"{key}={value}" for key, value in ordered]

    return "&".join(encoded)


def percent_encode(value: str) -> str:
    """Percent-encoding as OAuth 1.0 requires: everything but unreserved characters (RFC 3986)."""
    return quote(value, safe="-._~")
