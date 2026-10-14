"""Which Diary Day it is for a User, whatever the server's clock says."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

Clock = Callable[[], datetime]
"""Returns the current instant as an aware datetime. Injected so tests can control "now"."""


def system_clock() -> datetime:
    return datetime.now(UTC)


def today(now: datetime, timezone: ZoneInfo) -> date:
    """The User's current Diary Day: the calendar date in their own timezone."""
    return now.astimezone(timezone).date()
