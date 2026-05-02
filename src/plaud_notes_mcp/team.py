"""Team-mode entrypoint.

Activated by PLAUD_DEPLOYMENT_MODE=team. Wires FastMCP's GoogleProvider
to our Firestore-backed AsyncKeyValue, and exposes the assembled ASGI
app for Cloud Run.

Imported even in single-tenant local mode for the package smoke test;
expensive GCP-client construction happens lazily inside build_team_app().
"""

from __future__ import annotations

import logging
import os

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from plaud_notes_mcp.firestore_keyvalue import FirestoreKeyValue
from plaud_notes_mcp.firestore_store import FirestoreStore
from plaud_notes_mcp.plaud_client import PlaudAuthError, PlaudClient
from plaud_notes_mcp.token_cache import TokenCache

logger = logging.getLogger(__name__)


def is_team_mode() -> bool:
    return os.environ.get("PLAUD_DEPLOYMENT_MODE", "").lower() == "team"


def _required(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(
            f"team-mode requires env var {name!r} (set in Plan 1 Phase 6 secrets)"
        )
    return val


def build_provider(client_storage=None):
    """Construct the FastMCP GoogleProvider with our config.

    Reads required env vars at call time so importing this module never
    forces team-mode setup. Pass client_storage=None to use FastMCP's
    default in-memory KV (fine for tests); production should pass a
    FirestoreKeyValue instance.
    """
    from fastmcp.server.auth.providers.google import GoogleProvider

    return GoogleProvider(
        client_id=_required("GOOGLE_OAUTH_CLIENT_ID"),
        client_secret=_required("GOOGLE_OAUTH_CLIENT_SECRET"),
        base_url=_required("PUBLIC_URL"),
        redirect_path="/oauth/google/callback",
        required_scopes=["openid", "email"],
        jwt_signing_key=_required("JWT_SIGNING_KEY"),
        # Workspace's Internal-type consent already gates @relevantsearch.com;
        # skip our consent screen.
        require_authorization_consent="external",
        client_storage=client_storage,
    )


def _build_firestore_kv() -> FirestoreKeyValue:
    return FirestoreKeyValue(
        project_id=_required("GCP_PROJECT_ID"),
        database_id=os.environ.get("FIRESTORE_DATABASE", "(default)"),
    )


def build_user_store() -> FirestoreStore:
    return FirestoreStore(
        project_id=_required("GCP_PROJECT_ID"),
        database_id=os.environ.get("FIRESTORE_DATABASE", "(default)"),
        kms_key_name=_required("KMS_TOKEN_KEY"),
    )


def build_token_cache(store: FirestoreStore | None = None) -> TokenCache:
    return TokenCache(
        store=store or build_user_store(),
        ttl_seconds=float(os.environ.get("TOKEN_CACHE_TTL_SECONDS", "60")),
        max_size=int(os.environ.get("TOKEN_CACHE_MAX_SIZE", "50")),
    )


async def _health(request):  # noqa: ANN001
    return JSONResponse({"status": "ok"})


async def resolve_plaud_client(google_sub: str, *, cache: TokenCache) -> PlaudClient | None:
    """Resolve google_sub → PlaudClient via the cache. None if no token registered.

    Uses upstream PlaudClient's DEFAULT_TIMEOUT (30s). Granular timeout
    control (connect/read/write/pool) is a follow-up that requires an
    upstream patch to PlaudClient.__init__.
    """
    row = await cache.get(google_sub)
    if row is None:
        return None
    return PlaudClient(token=row.plaud_token, region=row.region)


class TeamAuthMiddleware:
    """ASGI middleware that resolves the FastMCP-verified principal into a
    per-request PlaudClient, populated via _plaud_client_var. Runs after
    FastMCP's auth has verified the JWT.
    """

    def __init__(self, app, *, cache: TokenCache):
        self._app = app
        self._cache = cache

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        # FastMCP's auth populates request.scope["user"] for authorized
        # requests. If absent, downstream FastMCP returns 401 with
        # WWW-Authenticate; we don't need to do anything.
        from fastmcp.server.auth.providers.in_memory import AuthenticatedUser  # noqa: F401

        user = scope.get("user")
        token_var = None
        if user is not None and hasattr(user, "access_token"):
            access = user.access_token
            claims = getattr(access, "claims", None) or {}
            google_sub = claims.get("sub")
            if google_sub:
                row = await self._cache.get(google_sub)
                if row is not None:
                    from plaud_notes_mcp.server import _plaud_client_var

                    plaud_client = await resolve_plaud_client(google_sub, cache=self._cache)
                    token_var = _plaud_client_var.set(plaud_client)

        try:
            await self._app(scope, receive, send)
        finally:
            if token_var is not None:
                from plaud_notes_mcp.server import _plaud_client_var

                _plaud_client_var.reset(token_var)


def invalidate_on_plaud_auth_error(cache: TokenCache, google_sub: str) -> None:
    """Helper for cache invalidation when upstream Plaud rejects a token.

    Tools currently catch PlaudAuthError and return a string error; this
    helper lets a future enhancement (or admin endpoint) invalidate the
    cache so the next request forces a Firestore re-read.
    """
    cache.invalidate(google_sub)


def build_team_app(*, use_in_memory_storage: bool = False) -> Starlette:
    """Assemble the team-mode ASGI app: /health + /admin/* + GoogleProvider routes.

    `use_in_memory_storage=True` skips Firestore for OAuth state and the
    user-token store; only useful in tests. Production keeps Firestore.
    """
    from plaud_notes_mcp.admin import build_admin_routes
    from plaud_notes_mcp.server import mcp, set_team_mode

    set_team_mode(True)

    client_storage = None if use_in_memory_storage else _build_firestore_kv()
    provider = build_provider(client_storage=client_storage)
    mcp.auth = provider

    # User-token store + cache (used by /admin/save and the auth middleware
    # mounted in Phase 7).
    if use_in_memory_storage:
        store = None
        cache = None
        admin_routes: list = []
    else:
        store = build_user_store()
        cache = build_token_cache(store)
        admin_routes = build_admin_routes(
            store=store,
            cache=cache,
            google_client_id=_required("GOOGLE_OAUTH_CLIENT_ID"),
            google_client_secret=_required("GOOGLE_OAUTH_CLIENT_SECRET"),
            session_secret=_required("SESSION_SECRET"),
            public_url=_required("PUBLIC_URL"),
        )

    inner = mcp.http_app(transport="streamable-http")
    inner_lifespan = inner.lifespan  # capture before wrapping
    if cache is not None:
        # Wrap the FastMCP inner app in our auth middleware so the
        # _plaud_client_var ContextVar is populated for /mcp requests.
        inner = TeamAuthMiddleware(inner, cache=cache)
    app = Starlette(
        routes=[
            Route("/health", _health, methods=["GET"]),
            *admin_routes,
        ],
        lifespan=inner_lifespan,
    )
    app.mount("/", inner)
    app.state.user_store = store
    app.state.token_cache = cache
    return app
