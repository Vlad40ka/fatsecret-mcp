"""Self-contained connector credentials, signed separately from Food Diary Drafts."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac

from pydantic import ValidationError

from fatsecret_mcp.auth.models import SignedValue


class InvalidCredential(ValueError):
    """A credential is malformed, changed, expired or of another kind."""


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()

    return _encode(digest)


def seal_token(value: SignedValue, signing_key: str) -> str:
    encoded = _encode(value.model_dump_json().encode())
    signature = _signature(encoded, signing_key)

    return encoded + "." + signature


def open_token[T: SignedValue](value: str, signing_key: str, model: type[T], *, now: float) -> T:
    if len(value) > 16384:
        raise InvalidCredential

    encoded, separator, signature = value.partition(".")
    expected = _signature(encoded, signing_key)

    if not separator or not hmac.compare_digest(signature.encode(), expected.encode()):
        raise InvalidCredential

    try:
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        claims = model.model_validate_json(payload)
    except (ValueError, binascii.Error, ValidationError):
        raise InvalidCredential from None

    if claims.expires_at <= now:
        raise InvalidCredential

    return claims


def _signature(encoded: str, signing_key: str) -> str:
    message = b"fatsecret-mcp:connector:" + encoded.encode()
    signature = hmac.digest(signing_key.encode(), message, hashlib.sha256)

    return _encode(signature)


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")
