"""The remote connector: discovery, sign-in and the authenticated MCP endpoint."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from urllib.parse import urlsplit

import anyio
import httpx2
from anyio.abc import TaskGroup, TaskStatus
from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser, RequireAuthMiddleware
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.routes import create_protected_resource_routes
from mcp.server.transport_security import RequestBodyLimitMiddleware, TransportSecuritySettings
from mcp.shared.auth import OAuthMetadata
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.authentication import AuthCredentials
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from fatsecret_mcp.auth.provider import ConnectorAuth
from fatsecret_mcp.config.loader import validate_http_household
from fatsecret_mcp.config.models import Household, User
from fatsecret_mcp.domain.diary_day import Clock, system_clock
from fatsecret_mcp.fatsecret.gateway import FatSecretGateway

# HTTP client debug logs can contain the authorization query or a token response.
logging.getLogger("httpx2").setLevel(logging.WARNING)
logging.getLogger("httpcore2").setLevel(logging.WARNING)


def build_http_app(
    household: Household,
    fatsecret: FatSecretGateway,
    *,
    clock: Clock = system_clock,
    metadata_http: httpx2.AsyncClient | None = None,
) -> Starlette:
    validate_http_household(household)

    base_url = household.public_base_url

    assert base_url is not None
    metadata_client = metadata_http if metadata_http is not None else httpx2.AsyncClient(timeout=5)
    auth = ConnectorAuth(
        household=household,
        http=metadata_client,
        clock=clock,
    )
    mcp = _UserServers(
        household=household,
        fatsecret=fatsecret,
        clock=clock,
        auth=auth,
    )
    metadata = OAuthMetadata(
        issuer=AnyHttpUrl(base_url),
        authorization_endpoint=AnyHttpUrl(base_url + "/authorize"),
        token_endpoint=AnyHttpUrl(base_url + "/token"),
        response_types_supported=["code"],
        grant_types_supported=["authorization_code"],
        token_endpoint_auth_methods_supported=["none"],
        code_challenge_methods_supported=["S256"],
        client_id_metadata_document_supported=True,
        scopes_supported=["diary"],
        authorization_response_iss_parameter_supported=True,
    )
    routes = create_protected_resource_routes(
        resource_url=AnyHttpUrl(base_url + "/mcp"),
        authorization_servers=[AnyHttpUrl(base_url)],
        scopes_supported=["diary"],
    )

    authorization_metadata = Route(
        path="/.well-known/oauth-authorization-server",
        endpoint=MetadataHandler(metadata).handle,
    )
    health = Route(
        path="/health",
        endpoint=_health,
    )
    authorize = Route(
        path="/authorize",
        endpoint=auth.authorize,
        methods=["GET", "POST"],
    )
    token = Route(
        path="/token",
        endpoint=auth.token,
        methods=["POST"],
    )
    tools = Route(
        path="/mcp",
        endpoint=mcp,
    )

    routes.extend([authorization_metadata, health, authorize, token, tools])

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncGenerator[None]:
        async with AsyncExitStack() as clients:
            if metadata_http is None:
                await clients.enter_async_context(metadata_client)

            async with anyio.create_task_group() as tasks:
                mcp.tasks = tasks

                try:
                    yield
                finally:
                    tasks.cancel_scope.cancel()

    body_limit = Middleware(
        cls=RequestBodyLimitMiddleware,
        max_body_size=65536,
    )

    return Starlette(
        routes=routes,
        lifespan=lifespan,
        middleware=[body_limit],
    )


class _UserServers:
    """Start each User's MCP tools on first use, with lifetimes owned by the HTTP app."""

    def __init__(
        self, household: Household, fatsecret: FatSecretGateway, clock: Clock, auth: ConnectorAuth
    ) -> None:
        self.household = household
        self.fatsecret = fatsecret
        self.clock = clock
        self.auth = auth
        self.apps: dict[str, Starlette] = {}
        self.lock = anyio.Lock()
        self.tasks: TaskGroup | None = None
        self.protected = RequireAuthMiddleware(
            app=self._dispatch,
            required_scopes=["diary"],
            resource_metadata_url=AnyHttpUrl(auth.base_url + "/.well-known/oauth-protected-resource/mcp"),
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope)
        scheme, _, raw_token = request.headers.get("authorization", "").partition(" ")
        claims = self.auth.authenticate(raw_token) if scheme.lower() == "bearer" else None

        if claims is not None:
            token = AccessToken(
                token=raw_token,
                client_id=claims.client_id,
                subject=claims.user_id,
                scopes=list(claims.scopes),
                resource=claims.audience,
                claims={"iss": claims.issuer},
            )

            scope["user"] = AuthenticatedUser(token)
            scope["auth"] = AuthCredentials(list(claims.scopes))

        await self.protected(scope, receive, send)

    async def _dispatch(self, scope: Scope, receive: Receive, send: Send) -> None:
        principal = scope["user"]

        assert isinstance(principal, AuthenticatedUser)
        user_id = principal.access_token.subject

        assert user_id is not None

        async with self.lock:
            if user_id not in self.apps:
                assert self.tasks is not None
                user = self.household.user(user_id)
                app = await self.tasks.start(self._serve_user, user)

                self.apps[user_id] = app

        await self.apps[user_id](scope, receive, send)

    async def _serve_user(self, user: User, *, task_status: TaskStatus[Starlette]) -> None:
        # Discovery and token exchange do not need tools, diary caches or their imports.
        from fatsecret_mcp.server import build_server

        server = build_server(
            user=user,
            fatsecret=self.fatsecret,
            clock=self.clock,
            signing_key=self.household.signing_key,
        )
        security = TransportSecuritySettings(
            allowed_hosts=[urlsplit(self.auth.base_url).netloc],
            allowed_origins=[self.auth.base_url],
        )
        app = server.streamable_http_app(
            stateless_http=True,
            json_response=True,
            transport_security=security,
        )

        async with app.router.lifespan_context(app):
            task_status.started(app)
            await anyio.sleep_forever()


async def _health(request: Request) -> Response:
    return JSONResponse({"status": "ok"})
