"""Self-contained connector credentials, signed separately from Food Diary Drafts."""

from __future__ import annotations

import binascii
import hashlib

from pydantic import ValidationError

from fatsecret_mcp.auth.models import SignedValue
from fatsecret_mcp.signing import from_base64url, signature, signature_matches, to_base64url

# Signed apart from Draft ids, so one can never stand in for the other.
_CONTEXT = b"fatsecret-mcp:connector:"


class InvalidCredential(ValueError):
    """A credential is malformed, changed, expired or of another kind."""


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()

    return to_base64url(digest)


def seal_token(value: SignedValue, signing_key: str) -> str:
    encoded = to_base64url(value.model_dump_json().encode())
    signed = signature(_CONTEXT + encoded.encode(), signing_key)

    return encoded + "." + signed


def open_token[T: SignedValue](value: str, signing_key: str, model: type[T], *, now: float) -> T:
    if len(value) > 16384:
        raise InvalidCredential

    encoded, separator, signed = value.partition(".")

    if not separator or not signature_matches(signed, _CONTEXT + encoded.encode(), signing_key):
        raise InvalidCredential

    try:
        payload = from_base64url(encoded)
        claims = model.model_validate_json(payload)
    except (ValueError, binascii.Error, ValidationError):
        raise InvalidCredential from None

    if claims.expires_at <= now:
        raise InvalidCredential

    return claims
