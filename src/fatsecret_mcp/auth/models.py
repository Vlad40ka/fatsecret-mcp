"""The connector's identity and the data carried in its signed credentials."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from fatsecret_mcp.config.models import Household


class ConnectorIdentity(BaseModel):
    """Where this Household's connector lives, and the key that signs its credentials."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    base_url: str
    resource: str
    issuer: str
    signing_key: str = Field(repr=False)

    @classmethod
    def of(cls, household: Household) -> ConnectorIdentity:
        base_url = household.public_base_url

        assert base_url is not None

        return cls(
            base_url=base_url,
            resource=base_url + "/mcp",
            issuer=base_url + "/",
            signing_key=household.signing_key.get_secret_value(),
        )


class SignedValue(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    expires_at: float


class FetchLimited(BaseModel):
    """Claude's client metadata may not be fetched again for `retry_after` seconds."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    retry_after: int


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
