"""In-memory limits on connector sign-in attempts, forgotten on restart."""

from __future__ import annotations

import math

ATTEMPTS_PER_NAME = 5
WINDOW_SECONDS = 900
# Names longer than any User id share a counter, so typed names can't grow memory.
_NAME_LENGTH = 128


class SignInThrottle:
    """Count sign-in attempts per typed name, whether or not the name is a User.

    An attempt counts from the moment it starts, so simultaneous guesses can't outrun the
    limit. Only a successful sign-in takes its attempts back.
    """

    def __init__(self) -> None:
        self.attempts: dict[str, list[float]] = {}

    def admit(self, name: str, now: float) -> int | None:
        """Count an attempt for a name, or return the seconds to wait before trying again."""
        self._forget_before(now - WINDOW_SECONDS)

        key = name[:_NAME_LENGTH]
        recent = self.attempts.get(key, [])

        if len(recent) >= ATTEMPTS_PER_NAME:
            return math.ceil(recent[0] + WINDOW_SECONDS - now)

        self.attempts[key] = [*recent, now]

        return None

    def succeeded(self, name: str) -> None:
        self.attempts.pop(name[:_NAME_LENGTH], None)

    def _forget_before(self, cutoff: float) -> None:
        for name in list(self.attempts):
            recent = [attempt for attempt in self.attempts[name] if attempt > cutoff]

            if recent:
                self.attempts[name] = recent
            else:
                del self.attempts[name]
