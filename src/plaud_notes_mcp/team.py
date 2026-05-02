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


def build_team_app(*, use_in_memory_storage: bool = False) -> Starlette:
    """Assemble the team-mode ASGI app: GoogleProvider routes + /health.

    `use_in_memory_storage=True` skips Firestore for OAuth state and is
    only useful in tests. Production keeps Firestore (default).
    """
    from plaud_notes_mcp.server import mcp

    client_storage = None if use_in_memory_storage else _build_firestore_kv()
    provider = build_provider(client_storage=client_storage)
    mcp.auth = provider

    # FastMCP exposes its OAuth + MCP routes via http_app(). Mount /health
    # at the root layer so probes don't traverse auth.
    inner = mcp.http_app(transport="streamable-http")
    app = Starlette(
        routes=[
            Route("/health", _health, methods=["GET"]),
        ],
        lifespan=inner.lifespan,
    )
    app.mount("/", inner)
    return app
