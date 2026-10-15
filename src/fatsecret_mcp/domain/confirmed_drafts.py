"""Remember confirmed Drafts until they expire, so a repeated confirm writes nothing twice.

The record lives in memory only: a restart forgets it, an accepted edge case for a single instance.
"""

from __future__ import annotations

from datetime import datetime

from fatsecret_mcp.domain.models import Confirmation


class ConfirmedDrafts:
    def __init__(self) -> None:
        self._confirmations: dict[str, Confirmation] = {}

    def get(self, draft_id: str, now: datetime) -> Confirmation | None:
        self._forget_expired(now)

        return self._confirmations.get(draft_id)

    def remember(self, draft_id: str, confirmation: Confirmation) -> None:
        self._confirmations[draft_id] = confirmation

    def _forget_expired(self, now: datetime) -> None:
        for draft_id, confirmation in list(self._confirmations.items()):
            if confirmation.draft.expires_at <= now:
                del self._confirmations[draft_id]
