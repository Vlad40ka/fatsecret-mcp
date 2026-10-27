"""GET /authorize: check Claude's authorization request, then show the sign-in page."""

from __future__ import annotations

import re
import secrets

from mcp.server.auth.handlers.authorize import AuthorizationRequest
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from fatsecret_mcp.auth.client_metadata import CLAUDE_CALLBACKS, ClaudeMetadata
from fatsecret_mcp.auth.login_page import login_page
from fatsecret_mcp.auth.models import AuthorizationDetails, ConnectorIdentity, FetchLimited, LoginRequest
from fatsecret_mcp.auth.responses import oauth_error, temporarily_unavailable
from fatsecret_mcp.auth.tokens import seal_token
from fatsecret_mcp.domain.diary_day import Clock

LOGIN_COOKIE = "fatsecret_connector_login"
LOGIN_LIFETIME = 600
_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")
_SCOPE = "diary"


async def start_authorization(
    request: Request, *, identity: ConnectorIdentity, claude_metadata: ClaudeMetadata, clock: Clock
) -> Response:
    try:
        params = AuthorizationRequest.model_validate(request.query_params)
    except ValidationError:
        return oauth_error("invalid_request")

    refusal = _refused_pkce_or_resource(params, request, identity)

    if refusal is not None:
        return oauth_error(refusal)

    client = await claude_metadata.lookup(params.client_id)
    redirect_uri = request.query_params.get("redirect_uri", "")

    if isinstance(client, FetchLimited):
        return temporarily_unavailable(client.retry_after)

    if client is None or not _is_allowed_callback(client, redirect_uri):
        return oauth_error("invalid_request")

    scopes = _requested_scopes(params)

    if any(scope != _SCOPE for scope in scopes):
        return oauth_error("invalid_scope")

    details = AuthorizationDetails(
        client_id=params.client_id,
        redirect_uri=redirect_uri,
        code_challenge=params.code_challenge,
        resource=identity.resource,
        scopes=scopes,
        state=params.state,
    )

    return _sign_in_form(details, identity, clock().timestamp())


def _refused_pkce_or_resource(
    params: AuthorizationRequest, request: Request, identity: ConnectorIdentity
) -> str | None:
    """The OAuth error for a request meant for another resource or without an S256 challenge."""
    if params.resource != identity.resource:
        return "invalid_target"
    if request.query_params.get("code_challenge_method") != "S256":
        return "invalid_request"
    if _CHALLENGE.fullmatch(params.code_challenge) is None:
        return "invalid_request"

    return None


def _is_allowed_callback(client: OAuthClientInformationFull, redirect_uri: str) -> bool:
    """One of Claude's own callbacks, also listed in the client's metadata."""
    registered = [str(uri) for uri in client.redirect_uris or []]

    return redirect_uri in registered


def _requested_scopes(params: AuthorizationRequest) -> tuple[str, ...]:
    if not params.scope:
        return (_SCOPE,)

    return tuple(params.scope.split())


def _sign_in_form(details: AuthorizationDetails, identity: ConnectorIdentity, now: float) -> HTMLResponse:
    """The sign-in page, carrying the sealed request and its CSRF value in a browser cookie."""
    csrf = secrets.token_urlsafe(32)
    login = LoginRequest(
        authorization=details,
        csrf=csrf,
        expires_at=now + LOGIN_LIFETIME,
    )
    sealed = seal_token(login, identity.signing_key)
    response = login_page(sealed, details.redirect_uri)

    response.set_cookie(
        LOGIN_COOKIE,
        csrf,
        max_age=LOGIN_LIFETIME,
        secure=identity.base_url.startswith("https://"),
        httponly=True,
        samesite="lax",
        path="/authorize",
    )

    return response
