"""Composition tests for main() — verifies the team-mode branch builds the
team app and that local stdio mode is preserved.

Doesn't actually start uvicorn (would be flaky); patches uvicorn.run to
capture which app was passed.
"""

from __future__ import annotations

from unittest.mock import patch

from starlette.applications import Starlette


def test_main_team_mode_builds_team_app(monkeypatch):
    monkeypatch.setenv("PLAUD_TRANSPORT", "http")
    monkeypatch.setenv("PLAUD_DEPLOYMENT_MODE", "team")
    monkeypatch.setenv("GCP_PROJECT_ID", "test-plaud-mcp")
    monkeypatch.setenv("FIRESTORE_DATABASE", "(default)")
    monkeypatch.setenv("KMS_TOKEN_KEY", "projects/p/locations/us-central1/keyRings/kr/cryptoKeys/k")
    monkeypatch.setenv("PUBLIC_URL", "https://plaud-mcp-test.relevantsearch.com")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "test.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret")
    monkeypatch.setenv("JWT_SIGNING_KEY", "k" * 32)
    monkeypatch.setenv("SESSION_SECRET", "x" * 32)

    from plaud_notes_mcp import server

    captured = {}

    def fake_uvicorn_run(app, **kwargs):
        captured["app"] = app
        captured["kwargs"] = kwargs

    with patch("uvicorn.run", side_effect=fake_uvicorn_run):
        server.main()

    assert isinstance(captured.get("app"), Starlette)
    # Host defaults to 127.0.0.1; the Dockerfile sets PLAUD_MCP_HOST=0.0.0.0
    # for prod. Just verify the kwarg was forwarded.
    assert "host" in captured["kwargs"]
    assert "port" in captured["kwargs"]


def test_main_stdio_mode_unchanged(monkeypatch):
    monkeypatch.delenv("PLAUD_TRANSPORT", raising=False)
    monkeypatch.delenv("PLAUD_DEPLOYMENT_MODE", raising=False)
    monkeypatch.setenv("PLAUD_TOKEN", "eyJ.local.x")

    from plaud_notes_mcp import server

    captured = {}

    def fake_run(*args, **kwargs):
        captured.update({"args": args, "kwargs": kwargs})

    with patch.object(server.mcp, "run", side_effect=fake_run):
        server.main()
    assert captured["kwargs"].get("transport") == "stdio"


def test_main_http_legacy_api_key_path(monkeypatch):
    monkeypatch.setenv("PLAUD_TRANSPORT", "http")
    monkeypatch.delenv("PLAUD_DEPLOYMENT_MODE", raising=False)
    monkeypatch.setenv("PLAUD_MCP_API_KEY", "static-key")

    from plaud_notes_mcp import server

    captured = {}

    def fake_uvicorn_run(app, **kwargs):
        captured["app"] = app

    with patch("uvicorn.run", side_effect=fake_uvicorn_run):
        server.main()
    assert captured.get("app") is not None
