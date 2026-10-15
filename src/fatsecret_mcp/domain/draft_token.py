"""Seal a self-contained Draft with the instance's persistent signing key."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import asdict

from fatsecret_mcp.domain.models import Draft


def seal_draft(draft: Draft, signing_key: str) -> str:
    items = [asdict(item) for item in draft.items]
    payload = {
        "user_id": draft.user_id,
        "items": items,
        "meal": draft.meal.value,
        "diary_day": draft.diary_day.isoformat(),
        "expires_at": draft.expires_at.isoformat(),
    }
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    encoded = _base64url(serialized.encode())
    signature = hmac.digest(signing_key.encode(), encoded.encode(), hashlib.sha256)

    return f"{encoded}.{_base64url(signature)}"


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")
