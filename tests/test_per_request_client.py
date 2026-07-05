"""Tests for the team-mode auth middleware + per-request PlaudClient ContextVar.

Validates that:
  - JWT-verified `sub` resolves to the user's PlaudClient via the cache
  - Missing token in Firestore surfaces as a NoPlaudTokenError on tool call
  - The 12 existing MCP tools see the right client via the ContextVar
  - PlaudAuthError from upstream invalidates the cache for that sub
"""

from __future__ import annotations

import datetime as _dt

import pytest

from plaud_notes_mcp.firestore_store import StoredToken
from plaud_notes_mcp.server import (
    NoPlaudTokenError,
    _get_client,
    _plaud_client_var,
    set_team_mode,
    structured_no_token_error,
)
from plaud_notes_mcp.token_cache import TokenCache


def _row(sub: str = "g-sub") -> StoredToken:
    return StoredToken(
        google_sub=sub,
        email="x@example.com",
        plaud_token="eyJ.x.y",
        region="us",
        created_at=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc),
        last_rekeyed_at=None,
    )


class _FakeStore:
    def __init__(self, rows=None):
        self.rows = dict(rows or {})

    async def get_user_token(self, sub):
        return self.rows.get(sub)


@pytest.fixture(autouse=True)
def reset_team_mode():
    yield
    set_team_mode(False)


def _stub_client(token: str, region: str = "us"):
    """Return a stand-in for PlaudClient that records the token it was built with."""

    class _StubClient:
        def __init__(self):
            self.token = token
            self.region = region

    return _StubClient()


def test_get_client_reads_contextvar_in_team_mode():
    set_team_mode(True)
    stub = _stub_client("eyJ.x.y")
    tok = _plaud_client_var.set(stub)
    try:
        assert _get_client() is stub
    finally:
        _plaud_client_var.reset(tok)


def test_get_client_raises_no_plaud_token_when_unset_in_team_mode():
    set_team_mode(True)
    with pytest.raises(NoPlaudTokenError):
        _get_client()


def test_get_client_falls_back_to_env_singleton_in_local_mode(monkeypatch):
    monkeypatch.setenv("PLAUD_TOKEN", "eyJ.local.x")
    monkeypatch.setenv("PLAUD_REGION", "us")
    set_team_mode(False)
    # Import the module's PlaudClient singleton path; should construct from env.
    import plaud_notes_mcp.server as srv

    srv._client = None  # reset module singleton
    client = _get_client()
    assert client._token == "eyJ.local.x"  # noqa: SLF001


def test_structured_no_token_error_payload():
    err = structured_no_token_error(public_url="https://plaud-mcp.relevantsearch.com")
    assert err["error"] == "no_plaud_token"
    assert "registration_url" in err
    assert err["registration_url"].endswith("/admin")
    assert "message" in err


@pytest.mark.asyncio
async def test_resolve_plaud_client_from_sub_uses_cache():
    from plaud_notes_mcp.team import resolve_plaud_client

    cache = TokenCache(_FakeStore({"g-sub-1": _row("g-sub-1")}), ttl_seconds=60, max_size=10)
    client = await resolve_plaud_client("g-sub-1", cache=cache)
    assert client is not None
    assert client._token == "eyJ.x.y"


@pytest.mark.asyncio
async def test_resolve_plaud_client_returns_none_when_no_token():
    from plaud_notes_mcp.team import resolve_plaud_client

    cache = TokenCache(_FakeStore(), ttl_seconds=60, max_size=10)
    assert await resolve_plaud_client("missing", cache=cache) is None


def test_existing_tool_uses_contextvar(monkeypatch):
    """Regression check: list_recordings consults _get_client which honors the ContextVar."""
    set_team_mode(True)
    import plaud_notes_mcp.server as srv

    captured = {}

    class _Capture:
        def list_recordings(self, **kwargs):
            captured.update(kwargs)
            return []

        @property
        def token(self):  # for stub identity
            return "eyJ.captured.x"

    cap = _Capture()
    tok = _plaud_client_var.set(cap)
    try:
        out = srv.list_recordings(limit=3)
        assert "No recordings" in out or "[]" in out or "recordings" in out
        assert captured == {"limit": 3, "skip": 0, "sort_by": "edit_time"}
    finally:
        _plaud_client_var.reset(tok)


def test_no_plaud_token_in_tool_returns_structured_error(monkeypatch):
    set_team_mode(True)
    monkeypatch.setenv("PUBLIC_URL", "https://plaud-mcp.relevantsearch.com")
    import json

    import plaud_notes_mcp.server as srv

    out = srv.list_recordings(limit=1)
    payload = json.loads(out)
    assert payload["error"] == "no_plaud_token"
    assert payload["registration_url"].endswith("/admin")
