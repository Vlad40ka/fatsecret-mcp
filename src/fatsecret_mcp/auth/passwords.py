"""Connector password hashes: salted stdlib scrypt, with one bounded cost."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from threading import BoundedSemaphore

_PREFIX = "scrypt$32768$8$1$"
_FORMAT = re.compile(r"scrypt\$32768\$8\$1\$[0-9a-f]{32}\$[0-9a-f]{128}")
DUMMY_PASSWORD_HASH = _PREFIX + "00" * 16 + "$" + "00" * 64

# Lowercase letters and digits without look-alikes (0/o, 1/l/i), so a password is easy to type on a phone.
_PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
_PASSWORD_GROUPS = 5
_PASSWORD_GROUP_LENGTH = 4
_PASSWORD_WORKERS = 2


class PasswordChecks:
    """Keep a password worker occupied until scrypt finishes, even if its request is cancelled."""

    def __init__(self) -> None:
        self._workers = BoundedSemaphore(_PASSWORD_WORKERS)

    def verify(self, password: str, stored: str) -> bool | None:
        """Check in a worker thread, or return None immediately when both workers are busy."""
        if not self._workers.acquire(blocking=False):
            return None

        try:
            return verify_password(password, stored)
        finally:
            self._workers.release()


def generate_password() -> str:
    """A random connector password: five groups of four characters, about 99 bits."""
    groups = [_random_group() for _ in range(_PASSWORD_GROUPS)]

    return "-".join(groups)


def _random_group() -> str:
    characters = [secrets.choice(_PASSWORD_ALPHABET) for _ in range(_PASSWORD_GROUP_LENGTH)]

    return "".join(characters)


def valid_password_hash(value: str) -> bool:
    return _FORMAT.fullmatch(value) is not None


def hash_password(password: str) -> str:
    if not password or len(password.encode()) > 1024:
        raise ValueError("Connector passwords must contain between 1 and 1024 bytes")

    salt = secrets.token_bytes(16)
    digest = _derive(password, salt)

    return _PREFIX + salt.hex() + "$" + digest.hex()


def verify_password(password: str, stored: str) -> bool:
    if not valid_password_hash(stored) or len(password.encode()) > 1024:
        return False

    _, _, _, _, salt, expected = stored.split("$")
    digest = _derive(password, bytes.fromhex(salt))

    return hmac.compare_digest(digest, bytes.fromhex(expected))


def _derive(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=32768,
        r=8,
        p=1,
        maxmem=128 * 1024 * 1024,
        dklen=64,
    )
