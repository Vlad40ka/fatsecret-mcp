"""The connector's authorization-code flow, with Claude's published client metadata."""

from __future__ import annotations

import hmac
import re
import secrets
from html import escape
from urllib.parse import urlencode, urlsplit

import anyio
import httpx2
from mcp.server.auth.handlers.authorize import AuthorizationRequest
from mcp.server.auth.handlers.token import AuthorizationCodeRequest, RefreshTokenRequest
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import ValidationError
from starlette.datastructures import FormData
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from fatsecret_mcp.auth.models import (
    AccessClaims,
    AuthorizationDetails,
    CodeClaims,
    LoginRequest,
    RefreshClaims,
    SessionClaims,
)
from fatsecret_mcp.auth.passwords import DUMMY_PASSWORD_HASH, verify_password
from fatsecret_mcp.auth.throttling import SignInThrottle
from fatsecret_mcp.auth.tokens import InvalidCredential, open_token, pkce_challenge, seal_token
from fatsecret_mcp.config.models import Household, User
from fatsecret_mcp.domain.diary_day import Clock

_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")
_VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}")
_COOKIE = "fatsecret_connector_login"
_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_ACCESS_LIFETIME = 3600
# Every refresh issues a new refresh token with a fresh lifetime, so a connector that is used
# at least once a month stays signed in. Earlier refresh tokens stay valid until they expire.
_REFRESH_LIFETIME = 30 * 24 * 3600
_INVALID_SIGN_IN = "Invalid user name or password."
_THROTTLED_SIGN_IN = "Too many sign-in attempts. Try again in a few minutes."


class ConnectorAuth:
    def __init__(self, household: Household, http: httpx2.AsyncClient, clock: Clock) -> None:
        assert household.public_base_url is not None
        self.household = household
        self.http = http
        self.clock = clock
        self.base_url = household.public_base_url
        self.resource = self.base_url + "/mcp"
        self.issuer = self.base_url + "/"
        self.signing_key = household.signing_key.get_secret_value()
        self.used_codes: dict[str, float] = {}
        self.throttle = SignInThrottle()

    async def authorize(self, request: Request) -> Response:
        if request.method == "POST":
            return await self._sign_in(request)

        try:
            params = AuthorizationRequest.model_validate(request.query_params)
        except ValidationError:
            return _error("invalid_request")

        if params.resource != self.resource:
            return _error("invalid_target")
        if request.query_params.get("code_challenge_method") != "S256":
            return _error("invalid_request")
        if _CHALLENGE.fullmatch(params.code_challenge) is None:
            return _error("invalid_request")

        client = await self._client(params.client_id)
        redirect_uri = request.query_params.get("redirect_uri", "")

        if client is None:
            return _error("invalid_request")

        redirect_uris = [str(uri) for uri in client.redirect_uris or []]

        redirect = params.redirect_uri

        if (
            redirect_uri not in redirect_uris
            or redirect is None
            or redirect.scheme != "https"
            or redirect.username
            or redirect.password
            or redirect.fragment
        ):
            return _error("invalid_request")

        scopes = tuple(params.scope.split()) if params.scope else ("diary",)

        if any(scope != "diary" for scope in scopes):
            return _error("invalid_scope")

        details = AuthorizationDetails(
            client_id=params.client_id,
            redirect_uri=redirect_uri,
            code_challenge=params.code_challenge,
            resource=self.resource,
            scopes=scopes,
            state=params.state,
        )
        csrf = secrets.token_urlsafe(32)
        login = LoginRequest(
            authorization=details,
            csrf=csrf,
            expires_at=self.clock().timestamp() + 600,
        )
        sealed = seal_token(login, self.signing_key)
        response = _login_page(sealed, redirect_uri)

        response.set_cookie(
            _COOKIE,
            csrf,
            max_age=600,
            secure=self.base_url.startswith("https://"),
            httponly=True,
            samesite="lax",
            path="/authorize",
        )

        return response

    async def _sign_in(self, request: Request) -> Response:
        if request.headers.get("origin", self.base_url) != self.base_url:
            return _error("invalid_request")
        if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
            return _error("invalid_request")

        form = await request.form()
        sealed = str(form.get("request", ""))

        try:
            login = open_token(sealed, self.signing_key, LoginRequest, now=self.clock().timestamp())
        except InvalidCredential:
            return _error("invalid_request")

        cookie = request.cookies.get(_COOKIE, "")

        if not hmac.compare_digest(cookie.encode(), login.csrf.encode()):
            return _error("invalid_request")

        username = str(form.get("username", "")).strip().casefold()
        password = str(form.get("password", ""))
        retry_after = self.throttle.admit(username, self.clock().timestamp())

        if retry_after is not None:
            response = _login_page(
                sealed,
                login.authorization.redirect_uri,
                alert=_THROTTLED_SIGN_IN,
                status_code=429,
            )

            response.headers["Retry-After"] = str(retry_after)

            return response

        user = self._user(username)
        stored = user.connector_password_hash if user else None
        password_hash = stored.get_secret_value() if stored else DUMMY_PASSWORD_HASH
        verified = await anyio.to_thread.run_sync(verify_password, password, password_hash)

        if user is None or not verified:
            return _login_page(
                sealed,
                login.authorization.redirect_uri,
                alert=_INVALID_SIGN_IN,
                status_code=401,
            )

        self.throttle.succeeded(username)

        code = CodeClaims(
            authorization=login.authorization,
            user_id=user.id,
            session_generation=user.session_generation,
            expires_at=self.clock().timestamp() + 60,
            nonce=secrets.token_urlsafe(32),
        )
        query = {
            "code": seal_token(code, self.signing_key),
            "iss": self.issuer,
        }

        if login.authorization.state is not None:
            query["state"] = login.authorization.state

        separator = "&" if "?" in login.authorization.redirect_uri else "?"
        callback = login.authorization.redirect_uri + separator + urlencode(query)
        response = RedirectResponse(
            url=callback,
            status_code=302,
            headers=_NO_STORE,
        )

        response.delete_cookie(_COOKIE, path="/authorize")

        return response

    async def token(self, request: Request) -> Response:
        if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
            return _error("invalid_request")

        form = await request.form()
        grant_type = form.get("grant_type")

        if grant_type == "authorization_code":
            return self._exchange_code(form)
        if grant_type == "refresh_token":
            return self._refresh(form)

        return _error("unsupported_grant_type")

    def _exchange_code(self, form: FormData) -> Response:
        try:
            params = AuthorizationCodeRequest.model_validate(form)
        except ValidationError:
            return _error("invalid_request")

        if params.resource != self.resource:
            return _error("invalid_target")
        if _VERIFIER.fullmatch(params.code_verifier) is None:
            return _error("invalid_grant")

        now = self.clock().timestamp()

        try:
            code = open_token(params.code, self.signing_key, CodeClaims, now=now)
        except InvalidCredential:
            return _error("invalid_grant")

        details = code.authorization
        challenge = pkce_challenge(params.code_verifier)
        user = self._user(code.user_id)
        valid_user = user is not None and user.session_generation == code.session_generation

        if (
            not valid_user
            or code.nonce in self.used_codes
            or params.client_id != details.client_id
            or form.get("redirect_uri") != details.redirect_uri
            or details.resource != self.resource
            or not hmac.compare_digest(challenge, details.code_challenge)
        ):
            return _error("invalid_grant")

        self.used_codes = {nonce: expiry for nonce, expiry in self.used_codes.items() if expiry > now}
        self.used_codes[code.nonce] = code.expires_at

        return self._issue_tokens(
            user_id=code.user_id,
            session_generation=code.session_generation,
            client_id=details.client_id,
            scopes=details.scopes,
            now=now,
        )

    def _refresh(self, form: FormData) -> Response:
        try:
            params = RefreshTokenRequest.model_validate(form)
        except ValidationError:
            return _error("invalid_request")

        # The refresh token is already bound to this resource, so an omitted indicator is fine.
        if params.resource is not None and params.resource != self.resource:
            return _error("invalid_target")

        now = self.clock().timestamp()

        try:
            claims = open_token(params.refresh_token, self.signing_key, RefreshClaims, now=now)
        except InvalidCredential:
            return _error("invalid_grant")

        if not self._current_session(claims) or params.client_id != claims.client_id:
            return _error("invalid_grant")

        # "diary" is the only scope, so a refresh can't narrow it: both tokens keep the grant.
        requested = set(params.scope.split() if params.scope else [])
        granted = set(claims.scopes)

        if not requested <= granted:
            return _error("invalid_scope")

        return self._issue_tokens(
            user_id=claims.user_id,
            session_generation=claims.session_generation,
            client_id=claims.client_id,
            scopes=claims.scopes,
            now=now,
        )

    def _issue_tokens(
        self,
        *,
        user_id: str,
        session_generation: int,
        client_id: str,
        scopes: tuple[str, ...],
        now: float,
    ) -> Response:
        access = AccessClaims(
            issuer=self.issuer,
            audience=self.resource,
            user_id=user_id,
            session_generation=session_generation,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + _ACCESS_LIFETIME,
            nonce=secrets.token_urlsafe(32),
        )
        refresh = RefreshClaims(
            issuer=self.issuer,
            audience=self.resource,
            user_id=user_id,
            session_generation=session_generation,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + _REFRESH_LIFETIME,
            nonce=secrets.token_urlsafe(32),
        )
        result = OAuthToken(
            access_token=seal_token(access, self.signing_key),
            expires_in=_ACCESS_LIFETIME,
            scope=" ".join(scopes),
            refresh_token=seal_token(refresh, self.signing_key),
        )

        return JSONResponse(
            content=result.model_dump(exclude_none=True),
            headers=_NO_STORE,
        )

    def authenticate(self, token: str) -> AccessClaims | None:
        try:
            claims = open_token(token, self.signing_key, AccessClaims, now=self.clock().timestamp())
        except InvalidCredential:
            return None

        if not self._current_session(claims):
            return None

        return claims

    def _current_session(self, claims: SessionClaims) -> bool:
        """Whether this server issued the session and its User's configuration still allows it."""
        if claims.issuer != self.issuer or claims.audience != self.resource:
            return False

        user = self._user(claims.user_id)

        return user is not None and user.session_generation == claims.session_generation

    def _user(self, user_id: str) -> User | None:
        try:
            return self.household.user(user_id)
        except KeyError:
            return None

    async def _client(self, client_id: str) -> OAuthClientInformationFull | None:
        # This Household connector trusts Claude's published identity. Restrict the fetch
        # before making any request, including redirects, to avoid arbitrary outbound URLs.
        try:
            url = urlsplit(client_id)
        except ValueError:
            return None

        if url.scheme != "https" or url.netloc != "claude.ai" or not url.path or url.query or url.fragment:
            return None

        try:
            async with self.http.stream("GET", client_id, follow_redirects=False, timeout=5) as response:
                if response.status_code != 200:
                    return None

                body = bytearray()

                async for chunk in response.aiter_bytes():
                    body.extend(chunk)

                    if len(body) > 65536:
                        return None

            client = OAuthClientInformationFull.model_validate_json(bytes(body))
        except (httpx2.HTTPError, ValidationError, ValueError):
            return None

        if (
            client.client_id != client_id
            or client.token_endpoint_auth_method != "none"
            or client.client_secret
        ):
            return None
        if "authorization_code" not in client.grant_types or "code" not in client.response_types:
            return None

        return client


def _error(code: str) -> JSONResponse:
    return JSONResponse(
        content={"error": code},
        status_code=400,
        headers=_NO_STORE,
    )


def _login_page(
    sealed: str, redirect_uri: str, *, alert: str | None = None, status_code: int = 200
) -> HTMLResponse:
    message = f'<p role="alert">{alert}</p>' if alert else ""
    callback = urlsplit(redirect_uri)
    callback_source = f"{callback.scheme}://{callback.netloc}{callback.path}"
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Connect your FatSecret diary</title></head>
<body><main><h1>Connect your FatSecret diary</h1>
<p>Sign in to let Claude read your diary and save the drafts you confirm.</p>
{message}
<form method="post" action="/authorize">
<input type="hidden" name="request" value="{escape(sealed, quote=True)}">
<p><label>User name <input name="username" autocomplete="username" required></label></p>
<p><label>Connector password <input type="password" name="password" autocomplete="current-password" required>
</label></p><button type="submit">Connect to Claude</button>
</form></main></body></html>"""
    headers = {
        "Cache-Control": "no-store",
        "Referrer-Policy": "same-origin",
        "Content-Security-Policy": (
            f"default-src 'none'; form-action 'self' {callback_source}; "
            "frame-ancestors 'none'; base-uri 'none'"
        ),
        "X-Content-Type-Options": "nosniff",
    }

    return HTMLResponse(
        content=html,
        status_code=status_code,
        headers=headers,
    )
