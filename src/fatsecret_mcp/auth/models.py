"""Data carried in signed connector credentials."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class SignedValue(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    expires_at: float


class AuthorizationDetails(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    client_id: str
    redirect_uri: str
    code_challenge: str
    resource: str
    scopes: tuple[str, ...]
    state: str | None


class LoginRequest(SignedValue):
    kind: Literal["login"] = "login"
    authorization: AuthorizationDetails
    csrf: str


class CodeClaims(SignedValue):
    kind: Literal["code"] = "code"
    authorization: AuthorizationDetails
    user_id: str
    session_generation: int
    nonce: str


class SessionClaims(SignedValue):
    """A signed-in User's connector session, as carried by its access and refresh tokens."""

    issuer: str
    audience: str
    user_id: str
    session_generation: int
    client_id: str
    scopes: tuple[str, ...]
    nonce: str


class AccessClaims(SessionClaims):
    kind: Literal["access"] = "access"


class RefreshClaims(SessionClaims):
    kind: Literal["refresh"] = "refresh"
