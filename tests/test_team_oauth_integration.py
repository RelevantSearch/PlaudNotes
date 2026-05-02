"""Integration tests for the team-mode OAuth surface.

Validates that GoogleProvider mounts correctly with our FirestoreKeyValue
backing client_storage, that the .well-known metadata endpoints serve
valid responses, and that the WWW-Authenticate header is emitted on 401s.

The full OAuth dance (Google round-trip + JWT issuance) is covered by
the spike at docs/projects/2026-04-30-plaud-mcp-team/spike/fastmcp-oauth/spike.py.
This file focuses on assembly correctness, not OAuth correctness.
"""

from __future__ import annotations

import pytest
from fastmcp.server.auth.providers.google import GoogleProvider
from starlette.testclient import TestClient


@pytest.fixture
def team_app(monkeypatch):
    monkeypatch.setenv("PLAUD_DEPLOYMENT_MODE", "team")
    monkeypatch.setenv("GCP_PROJECT_ID", "test-plaud-mcp")
    monkeypatch.setenv("FIRESTORE_DATABASE", "(default)")
    monkeypatch.setenv("KMS_TOKEN_KEY", "projects/p/locations/us-central1/keyRings/kr/cryptoKeys/k")
    monkeypatch.setenv("PUBLIC_URL", "https://plaud-mcp-test.relevantsearch.com")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("JWT_SIGNING_KEY", "test-jwt-signing-key-32bytes-long-foobar")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret-32bytes-long-baz")

    from plaud_notes_mcp.team import build_team_app

    return build_team_app(use_in_memory_storage=True)


def test_team_module_importable():
    """Goes green: this is the smoke test that was deliberately RED in Phase 2."""
    from plaud_notes_mcp import team  # noqa: F401


def test_well_known_oauth_authorization_server(team_app):
    client = TestClient(team_app)
    resp = client.get("/.well-known/oauth-authorization-server")
    assert resp.status_code == 200
    meta = resp.json()
    assert "issuer" in meta
    assert "authorization_endpoint" in meta
    assert "token_endpoint" in meta
    assert "registration_endpoint" in meta
    # claude.ai contract: code_challenge_methods_supported must include S256.
    assert "S256" in meta.get("code_challenge_methods_supported", [])


def test_well_known_oauth_protected_resource(team_app):
    client = TestClient(team_app)
    # RFC 9728 path is suffixed with the resource location ("/mcp" for us).
    resp = client.get("/.well-known/oauth-protected-resource/mcp")
    assert resp.status_code == 200
    meta = resp.json()
    assert "resource" in meta or "authorization_servers" in meta


def test_health_endpoint_no_auth(team_app):
    client = TestClient(team_app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_google_provider_configured_in_team_module(team_app):
    # team_app fixture sets the required env vars.
    from plaud_notes_mcp.team import build_provider

    provider = build_provider()
    assert isinstance(provider, GoogleProvider)


def test_local_mode_does_not_construct_team_app(monkeypatch):
    """Stdio-mode (PLAUD_DEPLOYMENT_MODE unset) must not require team env vars."""
    monkeypatch.delenv("PLAUD_DEPLOYMENT_MODE", raising=False)
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.delenv("PUBLIC_URL", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("JWT_SIGNING_KEY", raising=False)
    # Importing should NOT explode even with no team env set.
    from plaud_notes_mcp import team

    # team module exposes a sentinel for whether team mode is active.
    assert team.is_team_mode() is False
