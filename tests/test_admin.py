"""Tests for the /admin Plaud-token registration endpoint.

Auth on /admin uses a separate, light-touch Google sign-in flow (NOT the
GoogleProvider OAuth dance — that's reserved for claude.ai). Same Google
OAuth client; different redirect URI (/admin/auth/callback).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from plaud_notes_mcp.admin import build_admin_routes
from plaud_notes_mcp.admin_session import (
    decode_session_cookie,
    issue_csrf_pair,
    sign_state,
    verify_csrf_pair,
)
from plaud_notes_mcp.firestore_store import FirestoreStore
from plaud_notes_mcp.token_cache import TokenCache
from tests.conftest import FakeFirestoreClient

GOOGLE_CLIENT_ID = "test.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "test-client-secret"  # noqa: S105
SESSION_SECRET = "x" * 32
PUBLIC_URL = "https://plaud-mcp-test.relevantsearch.com"
KMS_KEY = "projects/p/locations/us-central1/keyRings/kr/cryptoKeys/k"


@pytest.fixture
def fake_kms():
    client = AsyncMock()

    async def encrypt(request, **_):
        return type("R", (), {"ciphertext": b"CT:" + request["plaintext"]})()

    async def decrypt(request, **_):
        return type("R", (), {"plaintext": request["ciphertext"][3:]})()

    client.encrypt = encrypt
    client.decrypt = decrypt
    return client


@pytest.fixture
def store(fake_kms):
    return FirestoreStore(
        project_id="p",
        database_id="(default)",
        kms_key_name=KMS_KEY,
        firestore_client=FakeFirestoreClient(),
        kms_client=fake_kms,
    )


@pytest.fixture
def cache(store):
    return TokenCache(store, ttl_seconds=60, max_size=10)


@pytest.fixture
def http_mocks():
    """Mock the upstream HTTP calls (Google /token and Plaud /user/me)."""
    return {
        "google_token": MagicMock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "google-access",
                    "id_token": "google-id-token-stub",
                    "expires_in": 3600,
                    "scope": "openid email",
                    "token_type": "Bearer",
                },
            )
        ),
        "google_userinfo": MagicMock(
            return_value=httpx.Response(
                200,
                json={"sub": "google-sub-12345", "email": "stefan@relevantsearch.com"},
            )
        ),
        "plaud_me_success": MagicMock(
            return_value=httpx.Response(200, json={"id": 42, "email": "stefan@relevantsearch.com"})
        ),
        "plaud_me_unauthorized": MagicMock(
            return_value=httpx.Response(401, json={"error": "invalid_token"})
        ),
    }


def _build_app(store, cache, http_mocks, *, plaud_outcome="success"):
    plaud_response = http_mocks["plaud_me_success" if plaud_outcome == "success" else "plaud_me_unauthorized"]

    def transport_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "oauth2.googleapis.com/token" in url:
            return http_mocks["google_token"]()
        if "googleapis.com/oauth2/v3/userinfo" in url or "openidconnect.googleapis.com/v1/userinfo" in url:
            return http_mocks["google_userinfo"]()
        if "/user/me" in url and "plaud.ai" in url:
            return plaud_response()
        return httpx.Response(404, json={"error": "unmocked URL", "url": url})

    transport = httpx.MockTransport(transport_handler)
    http_client = httpx.AsyncClient(transport=transport)

    routes, _owned = build_admin_routes(
        store=store,
        cache=cache,
        google_client_id=GOOGLE_CLIENT_ID,
        google_client_secret=GOOGLE_CLIENT_SECRET,
        session_secret=SESSION_SECRET,
        public_url=PUBLIC_URL,
        http_client=http_client,  # caller-owned; owned_client is None
    )
    app = Starlette(routes=routes)
    return app


def test_get_admin_no_session_redirects_to_login(store, cache, http_mocks):
    app = _build_app(store, cache, http_mocks)
    client = TestClient(app, follow_redirects=False, base_url="https://testserver")
    resp = client.get("/admin")
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/admin/auth/login")


def test_admin_auth_login_redirects_to_google_oauth(store, cache, http_mocks):
    app = _build_app(store, cache, http_mocks)
    client = TestClient(app, follow_redirects=False, base_url="https://testserver")
    resp = client.get("/admin/auth/login")
    assert resp.status_code == 302
    loc = resp.headers["location"]
    assert loc.startswith("https://accounts.google.com/o/oauth2/v2/auth")
    assert "client_id=" + GOOGLE_CLIENT_ID in loc
    assert "redirect_uri=" in loc
    assert "scope=openid+email" in loc or "scope=openid%20email" in loc
    assert "state=" in loc


def test_admin_auth_callback_exchanges_code_and_sets_session(store, cache, http_mocks):
    app = _build_app(store, cache, http_mocks)
    client = TestClient(app, follow_redirects=False, base_url="https://testserver")

    state = sign_state(SESSION_SECRET, return_to="/admin")
    resp = client.get(f"/admin/auth/callback?code=g-code&state={state}")
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin"
    set_cookie = resp.headers.get("set-cookie", "")
    assert "samesite=lax" in set_cookie.lower()
    assert "httponly" in set_cookie.lower()
    assert "secure" in set_cookie.lower()
    # Cookie value carries signed payload — sub + email.
    cookie_jar = client.cookies
    raw = cookie_jar.get("plaud_admin_session")
    payload = decode_session_cookie(SESSION_SECRET, raw)
    assert payload["google_sub"] == "google-sub-12345"
    assert payload["email"] == "stefan@relevantsearch.com"


def test_admin_auth_callback_rejects_tampered_state(store, cache, http_mocks):
    app = _build_app(store, cache, http_mocks)
    client = TestClient(app, follow_redirects=False, base_url="https://testserver")
    resp = client.get("/admin/auth/callback?code=g-code&state=tampered")
    assert resp.status_code == 400


def test_get_admin_with_valid_session_renders_form(store, cache, http_mocks):
    app = _build_app(store, cache, http_mocks)
    client = TestClient(app, follow_redirects=False, base_url="https://testserver")
    state = sign_state(SESSION_SECRET, return_to="/admin")
    client.get(f"/admin/auth/callback?code=g-code&state={state}")
    resp = client.get("/admin")
    assert resp.status_code == 200
    body = resp.text
    assert "Plaud token" in body
    assert "stefan@relevantsearch.com" in body  # identity confirmation
    assert 'name="csrf_token"' in body  # CSRF hidden input
    assert 'name="plaud_token"' in body
    assert 'name="region"' in body


def test_post_save_rejects_invalid_token_regex(store, cache, http_mocks):
    app = _build_app(store, cache, http_mocks)
    client = TestClient(app, follow_redirects=False, base_url="https://testserver")
    state = sign_state(SESSION_SECRET, return_to="/admin")
    client.get(f"/admin/auth/callback?code=g-code&state={state}")

    # Get the form to extract a valid CSRF token.
    form = client.get("/admin").text
    csrf = _extract_input_value(form, "csrf_token")
    resp = client.post(
        "/admin/save",
        data={"plaud_token": "garbage-not-jwt", "region": "us", "csrf_token": csrf},
    )
    assert resp.status_code == 400
    # Nothing persisted.
    assert http_mocks["plaud_me_success"].call_count == 0


def test_post_save_rejects_when_plaud_returns_401(store, cache, http_mocks):
    app = _build_app(store, cache, http_mocks, plaud_outcome="unauthorized")
    client = TestClient(app, follow_redirects=False, base_url="https://testserver")
    state = sign_state(SESSION_SECRET, return_to="/admin")
    client.get(f"/admin/auth/callback?code=g-code&state={state}")
    form = client.get("/admin").text
    csrf = _extract_input_value(form, "csrf_token")

    resp = client.post(
        "/admin/save",
        data={
            "plaud_token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.SflKxw",
            "region": "us",
            "csrf_token": csrf,
        },
    )
    assert resp.status_code == 401
    assert http_mocks["plaud_me_unauthorized"].call_count == 1


def test_post_save_persists_on_success(store, cache, http_mocks):
    app = _build_app(store, cache, http_mocks)
    client = TestClient(app, follow_redirects=False, base_url="https://testserver")
    state = sign_state(SESSION_SECRET, return_to="/admin")
    client.get(f"/admin/auth/callback?code=g-code&state={state}")
    form = client.get("/admin").text
    csrf = _extract_input_value(form, "csrf_token")

    resp = client.post(
        "/admin/save",
        data={
            "plaud_token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.SflKxw",
            "region": "eu",
            "csrf_token": csrf,
        },
    )
    assert resp.status_code == 200
    assert "Token saved" in resp.text


def test_post_save_rejects_missing_csrf(store, cache, http_mocks):
    app = _build_app(store, cache, http_mocks)
    client = TestClient(app, follow_redirects=False, base_url="https://testserver")
    state = sign_state(SESSION_SECRET, return_to="/admin")
    client.get(f"/admin/auth/callback?code=g-code&state={state}")
    resp = client.post(
        "/admin/save",
        data={
            "plaud_token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.SflKxw",
            "region": "us",
            # csrf_token missing
        },
    )
    assert resp.status_code == 403


def test_post_save_rejects_no_session(store, cache, http_mocks):
    app = _build_app(store, cache, http_mocks)
    client = TestClient(app, follow_redirects=False, base_url="https://testserver")
    resp = client.post(
        "/admin/save",
        data={
            "plaud_token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.SflKxw",
            "region": "us",
            "csrf_token": "anything",
        },
    )
    assert resp.status_code in (302, 401)


def test_csrf_pair_round_trip():
    sub = "google-sub-x"
    cookie_val, form_val = issue_csrf_pair(SESSION_SECRET, sub)
    assert verify_csrf_pair(SESSION_SECRET, sub, cookie_val, form_val) is True
    # Tampered form value rejected.
    assert verify_csrf_pair(SESSION_SECRET, sub, cookie_val, form_val + "x") is False
    # Different sub rejected.
    assert verify_csrf_pair(SESSION_SECRET, "google-sub-other", cookie_val, form_val) is False


def _extract_input_value(html: str, name: str) -> str:
    """Parse a hidden <input name="..." value="..."> from the form HTML."""
    needle = f'name="{name}"'
    idx = html.index(needle)
    val_idx = html.index('value="', idx) + len('value="')
    end = html.index('"', val_idx)
    return html[val_idx:end]
