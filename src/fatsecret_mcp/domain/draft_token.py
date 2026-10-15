"""Seal a self-contained Draft with the instance's persistent signing key, and open it again."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import asdict
from datetime import date, datetime
from typing import Any

from fatsecret_mcp.domain.models import Draft, DraftEntry, Meal


class InvalidDraft(ValueError):
    """A Draft id that can't be confirmed: tampered with, expired, or another User's."""


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


def open_draft(draft_id: str, signing_key: str, *, user_id: str, now: datetime) -> Draft:
    """The Draft sealed in `draft_id`, once its signature, User and expiry check out."""
    encoded, _, signature = draft_id.partition(".")
    expected = hmac.digest(signing_key.encode(), encoded.encode(), hashlib.sha256)

    if not hmac.compare_digest(_base64url(expected).encode(), signature.encode()):
        raise InvalidDraft("This isn't a valid Draft id: it was changed or not issued by this server.")

    draft = _draft(json.loads(_from_base64url(encoded)))

    if draft.user_id != user_id:
        raise InvalidDraft("This Draft belongs to another User. Draft the Food Entries again.")
    if draft.expires_at <= now:
        raise InvalidDraft("This Draft has expired. Draft the Food Entries again.")

    return draft


def _draft(payload: dict[str, Any]) -> Draft:
    # The valid signature proves seal_draft wrote this payload, so its shape needs no checks.
    items = [_draft_entry(item) for item in payload["items"]]

    return Draft(
        user_id=payload["user_id"],
        items=tuple(items),
        meal=Meal(payload["meal"]),
        diary_day=date.fromisoformat(payload["diary_day"]),
        expires_at=datetime.fromisoformat(payload["expires_at"]),
    )


def _draft_entry(item: dict[str, Any]) -> DraftEntry:
    return DraftEntry(
        food_id=item["food_id"],
        serving_id=item["serving_id"],
        number_of_units=item["number_of_units"],
        entry_name=item["entry_name"],
    )


def _from_base64url(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)

    return base64.urlsafe_b64decode(value + padding)


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")
