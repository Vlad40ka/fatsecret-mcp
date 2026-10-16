"""Bounded sign-in accounting through the throttle's public interface."""

from __future__ import annotations

from typing import ClassVar

from fatsecret_mcp.auth.throttling import SignInThrottle


class CountedTime(float):
    """Measure expiry comparisons on caller-supplied time, without wall-clock timing."""

    comparisons: ClassVar[int] = 0

    def __gt__(self, other: float) -> bool:
        CountedTime.comparisons += 1

        return super().__gt__(other)

    def __le__(self, other: float) -> bool:
        CountedTime.comparisons += 1

        return super().__le__(other)


def test_the_name_limit_does_not_forget_a_name_that_is_already_throttled() -> None:
    throttle = SignInThrottle()

    for _ in range(5):
        assert throttle.admit("jakub", 0) is None

    for attempt in range(1023):
        assert throttle.admit(f"guess-{attempt}", 0) is None

    assert throttle.admit("one-name-too-many", 0) == 900
    assert len(throttle.attempts) == 1024
    assert throttle.admit("jakub", 0) == 900
    assert throttle.admit("a-new-name", 900) is None


def test_forms_keep_their_submission_count_when_the_form_cache_is_full() -> None:
    throttle = SignInThrottle()

    for _ in range(5):
        assert throttle.admit_form("first-form", 600, 0)

    for form in range(1023):
        assert throttle.admit_form(f"form-{form}", 600, 0)

    for form in range(20_000):
        assert not throttle.admit_form(f"overflow-{form}", 600, 0)

    assert len(throttle.forms) == 1024
    assert not throttle.admit_form("first-form", 600, 0)
    assert throttle.admit_form("fresh-form", 1200, 600)


def test_tens_of_thousands_of_names_keep_memory_and_per_attempt_work_bounded() -> None:
    throttle = SignInThrottle()
    now = CountedTime(0)

    for attempt in range(1024):
        assert throttle.admit(f"guess-{attempt}", now) is None

    CountedTime.comparisons = 0

    assert throttle.admit("another-guess", now) == 900
    assert CountedTime.comparisons <= 10

    CountedTime.comparisons = 0

    assert throttle.admit("guess-0", now) is None
    assert CountedTime.comparisons <= 10

    for attempt in range(20_000):
        assert throttle.admit(f"new-guess-{attempt}", 0) == 900

    assert len(throttle.attempts) == 1024
