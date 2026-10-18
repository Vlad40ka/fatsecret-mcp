"""Signed values the server hands out: URL-safe base64 without padding, and HMAC-SHA256 signatures.

Connector credentials and Draft ids both use them, each signing its own message with the instance key.
"""

from __future__ import annotations

import base64
import hashlib
import hmac


def to_base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def from_base64url(value: str) -> bytes:
    """The bytes in `value`; raises `ValueError` (or `binascii.Error`) when it isn't base64url."""
    padding = "=" * (-len(value) % 4)

    return base64.urlsafe_b64decode(value + padding)


def signature(message: bytes, signing_key: str) -> str:
    digest = hmac.digest(signing_key.encode(), message, hashlib.sha256)

    return to_base64url(digest)


def signature_matches(signed: str, message: bytes, signing_key: str) -> bool:
    """Whether `signed` is the signature of `message`, compared in constant time."""
    expected = signature(message, signing_key)

    return hmac.compare_digest(signed.encode(), expected.encode())
