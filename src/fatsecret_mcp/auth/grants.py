"""POST /token: exchange a one-use code, or a refresh token, for new tokens."""

from __future__ import annotations

import hmac
import re

from mcp.server.auth.handlers.token import AuthorizationCodeRequest, RefreshTokenRequest
from mcp.shared.auth import OAuthToken
from pydantic import ValidationError
from starlette.datastructures import FormData
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from fatsecret_mcp.auth.models import AuthorizationDetails, CodeClaims, ConnectorIdentity, RefreshClaims
from fatsecret_mcp.auth.responses import NO_STORE, is_form_post, oauth_error
from fatsecret_mcp.auth.sessions import is_current_generation, is_current_session, issue_tokens
from fatsecret_mcp.auth.tokens import InvalidCredential, open_token, pkce_challenge
from fatsecret_mcp.config.models import Household
from fatsecret_mcp.domain.diary_day import Clock

_VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}")


class SpentCodes:
    """Codes already exchanged, remembered until they would have expired anyway."""

    def __init__(self) -> None:
        self.expiries: dict[str, float] = {}

    def __contains__(self, code: CodeClaims) -> bool:
        return code.nonce in self.expiries

    def spend(self, code: CodeClaims, now: float) -> None:
        self.expiries = {nonce: expiry for nonce, expiry in self.expiries.items() if expiry > now}
        self.expiries[code.nonce] = code.expires_at


async def grant_tokens(
    request: Request,
    *,
    identity: ConnectorIdentity,
    household: Household,
    spent_codes: SpentCodes,
    clock: Clock,
) -> Response:
    if not is_form_post(request):
        return oauth_error("invalid_request")

    form = await request.form()
    grant_type = form.get("grant_type")

    if grant_type == "authorization_code":
        return _exchange_code(form, identity, household, spent_codes, clock().timestamp())
    if grant_type == "refresh_token":
        return _refresh(form, identity, household, clock().timestamp())

    return oauth_error("unsupported_grant_type")


def _exchange_code(
    form: FormData, identity: ConnectorIdentity, household: Household, spent_codes: SpentCodes, now: float
) -> Response:
    try:
        params = AuthorizationCodeRequest.model_validate(form)
    except ValidationError:
        return oauth_error("invalid_request")

    if params.resource != identity.resource:
        return oauth_error("invalid_target")
    if _VERIFIER.fullmatch(params.code_verifier) is None:
        return oauth_error("invalid_grant")

    try:
        code = open_token(params.code, identity.signing_key, CodeClaims, now=now)
    except InvalidCredential:
        return oauth_error("invalid_grant")

    valid_user = is_current_generation(household, code.user_id, code.session_generation)
    redirect_uri = form.get("redirect_uri")

    if (
        not valid_user
        or code in spent_codes
        or not _matches_authorization(code.authorization, params, redirect_uri, identity)
    ):
        return oauth_error("invalid_grant")

    spent_codes.spend(code, now)

    tokens = issue_tokens(
        identity,
        user_id=code.user_id,
        session_generation=code.session_generation,
        client_id=code.authorization.client_id,
        scopes=code.authorization.scopes,
        now=now,
    )

    return _token_response(tokens)


def _matches_authorization(
    details: AuthorizationDetails,
    params: AuthorizationCodeRequest,
    redirect_uri: object,
    identity: ConnectorIdentity,
) -> bool:
    """Whether the exchange comes from the same client, callback and PKCE verifier as the sign-in."""
    challenge = pkce_challenge(params.code_verifier)

    return (
        params.client_id == details.client_id
        and redirect_uri == details.redirect_uri
        and details.resource == identity.resource
        and hmac.compare_digest(challenge, details.code_challenge)
    )


def _refresh(form: FormData, identity: ConnectorIdentity, household: Household, now: float) -> Response:
    try:
        params = RefreshTokenRequest.model_validate(form)
    except ValidationError:
        return oauth_error("invalid_request")

    # The refresh token is already bound to this resource, so an omitted indicator is fine.
    if params.resource is not None and params.resource != identity.resource:
        return oauth_error("invalid_target")

    try:
        claims = open_token(params.refresh_token, identity.signing_key, RefreshClaims, now=now)
    except InvalidCredential:
        return oauth_error("invalid_grant")

    if not is_current_session(claims, identity, household) or params.client_id != claims.client_id:
        return oauth_error("invalid_grant")

    # "diary" is the only scope, so a refresh can't narrow it: both tokens keep the grant.
    requested = set(params.scope.split() if params.scope else [])
    granted = set(claims.scopes)

    if not requested <= granted:
        return oauth_error("invalid_scope")

    tokens = issue_tokens(
        identity,
        user_id=claims.user_id,
        session_generation=claims.session_generation,
        client_id=claims.client_id,
        scopes=claims.scopes,
        now=now,
    )

    return _token_response(tokens)


def _token_response(tokens: OAuthToken) -> JSONResponse:
    return JSONResponse(
        content=tokens.model_dump(exclude_none=True),
        headers=NO_STORE,
    )
