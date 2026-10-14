"""What the server needs from FatSecret, as one narrow interface.

`FatSecretApi` in `client.py` implements it against the real API; the tests use an in-memory fake.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from fatsecret_mcp.domain.models import FoodEntry


@dataclass(frozen=True)
class ConsumerKey:
    """The server's own OAuth 1.0 consumer key and secret from the FatSecret developer console."""

    key: str = field(repr=False)
    secret: str = field(repr=False)


@dataclass(frozen=True)
class AccessToken:
    """A User's OAuth 1.0 access token and secret, which act on their own FatSecret account."""

    token: str = field(repr=False)
    secret: str = field(repr=False)


class FatSecretGateway(Protocol):
    async def list_food_entries(self, user: AccessToken, day: date) -> list[FoodEntry]:
        """The User's Food Entries for one Diary Day; an empty list when there are none."""
        ...
