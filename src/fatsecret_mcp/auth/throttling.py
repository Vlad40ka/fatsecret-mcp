"""In-memory limits on connector sign-in attempts, forgotten on restart."""

from __future__ import annotations

import math
from collections import OrderedDict, deque

ATTEMPTS_PER_NAME = 5
WINDOW_SECONDS = 900
ATTEMPTS_PER_MINUTE = 60
SUBMISSIONS_PER_FORM = 5
MAX_TRACKED_FORMS = 1024
MAX_TRACKED_NAMES = 1024
# Truncation bounds the size of each key; MAX_TRACKED_NAMES bounds their number.
_NAME_LENGTH = 128


class SignInThrottle:
    """Bound sign-in attempts across the server, per form and per typed name.

    All names share the same rules, whether or not they belong to a User. An attempt counts
    before password checking, so simultaneous guesses can't outrun the limits. A successful
    sign-in clears only that name's attempts; server and form counts stay in place.
    """

    def __init__(self) -> None:
        self.attempts: OrderedDict[str, list[float]] = OrderedDict()
        self.forms: OrderedDict[str, tuple[float, int]] = OrderedDict()
        self.total: deque[float] = deque()

    def admit_server(self, now: float) -> int | None:
        """At most sixty admitted submissions in any rolling minute; refusals don't extend it."""
        while self.total and self.total[0] <= now - 60:
            self.total.popleft()

        if len(self.total) >= ATTEMPTS_PER_MINUTE:
            return math.ceil(self.total[0] + 60 - now)

        self.total.append(now)

        return None

    def admit_form(self, csrf: str, expires_at: float, now: float) -> bool:
        """Count a submission without letting a form forget its earlier submissions."""
        if self.forms:
            oldest = next(iter(self.forms))
            expiry, _ = self.forms[oldest]

            if expiry <= now:
                self.forms.popitem(last=False)

        previous = self.forms.get(csrf)

        if previous is None:
            if len(self.forms) >= MAX_TRACKED_FORMS:
                return False

            self.forms[csrf] = (expires_at, 1)

            return True

        expiry, count = previous

        if count >= SUBMISSIONS_PER_FORM:
            return False

        self.forms[csrf] = (expiry, count + 1)

        return True

    def admit(self, name: str, now: float) -> int | None:
        """Count an attempt for a name, or return the seconds to wait before trying again."""
        self._forget_before(now - WINDOW_SECONDS)

        key = name[:_NAME_LENGTH]
        previous = self.attempts.get(key, [])
        recent = [attempt for attempt in previous if attempt > now - WINDOW_SECONDS]

        if key not in self.attempts and len(self.attempts) >= MAX_TRACKED_NAMES:
            oldest = next(iter(self.attempts.values()))
            earliest_expiry = oldest[-1] + WINDOW_SECONDS

            return math.ceil(earliest_expiry - now)

        if len(recent) >= ATTEMPTS_PER_NAME:
            return math.ceil(recent[0] + WINDOW_SECONDS - now)

        self.attempts[key] = [*recent, now]

        self.attempts.move_to_end(key)

        return None

    def succeeded(self, name: str) -> None:
        self.attempts.pop(name[:_NAME_LENGTH], None)

    def _forget_before(self, cutoff: float) -> None:
        # One expired name per attempt is enough to make room, without scanning the cache.
        if not self.attempts:
            return

        oldest = next(iter(self.attempts.values()))

        if oldest[-1] <= cutoff:
            self.attempts.popitem(last=False)
