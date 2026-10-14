"""What can go wrong when calling FatSecret.

Every error's message is written for the User and the Operator and holds no secrets.
`from_code` turns FatSecret's numeric error codes into these errors.
"""

from __future__ import annotations


class FatSecretError(Exception):
    """A FatSecret call failed."""

    code: int | None = None
    """FatSecret's numeric error code, when it sent one."""


class InvalidAccessToken(FatSecretError):
    code = 9

    def __init__(self) -> None:
        super().__init__(
            "FatSecret rejected this User's access token: it's invalid or was revoked. "
            "The Operator needs to sign the User in to FatSecret again."
        )


class DailyQuotaExceeded(FatSecretError):
    code = 11

    def __init__(self) -> None:
        super().__init__(
            "This server has used up its daily FatSecret request quota. "
            "Try again later; the quota resets daily."
        )


class IpAddressRejected(FatSecretError):
    code = 21

    def __init__(self) -> None:
        super().__init__(
            "FatSecret rejected the server's IP address (error 21). "
            "The Operator should check the IP restrictions in the FatSecret developer console."
        )


class TooManyActions(FatSecretError):
    code = 12

    def __init__(self) -> None:
        super().__init__(
            "FatSecret is temporarily limiting requests because too many actions were made "
            "in a short time (error 12). Wait a moment before trying again."
        )


class NotFound(FatSecretError):
    code = 106

    def __init__(self) -> None:
        super().__init__(
            "FatSecret has no such item, or this server's free API key can't see it "
            "(it only sees the US catalog)."
        )


class CredentialsRejected(FatSecretError):
    """FatSecret didn't accept the consumer key (error 5) or the request's signature (error 8)."""

    def __init__(self, code: int) -> None:
        self.code = code

        super().__init__(
            f"FatSecret rejected the server's credentials (error {code}). The Operator should check the "
            "FatSecret consumer key and secret, and this User's FatSecret access secret."
        )


class UpstreamFailure(FatSecretError):
    """FatSecret was unreachable, failed, or answered with something the server doesn't understand."""

    def __init__(self, reason: str, code: int | None = None) -> None:
        self.code = code

        super().__init__(f"FatSecret didn't answer properly ({reason}). Try again in a moment.")


def from_code(code: int) -> FatSecretError:
    """The error for one of FatSecret's documented error codes."""
    if code == 9:
        return InvalidAccessToken()
    if code == 11:
        return DailyQuotaExceeded()
    if code == 12:
        return TooManyActions()
    if code == 21:
        return IpAddressRejected()
    if code == 106:
        return NotFound()
    if code in (5, 8):
        return CredentialsRejected(code)
    return UpstreamFailure(f"error {code}", code)
