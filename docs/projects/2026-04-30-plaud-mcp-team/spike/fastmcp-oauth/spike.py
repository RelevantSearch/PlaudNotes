"""FastMCP 3.2.4 OAuth-as-AS-with-Google-as-IdP spike.

Goal: empirically verify that FastMCP supports the pattern:
    - claude.ai is the OAuth client.
    - Our MCP server is the Authorization Server (issues opaque code + JWT).
    - Google is JUST the IdP (we redirect the user's browser there for sign-in).

Two implementations are tested:
    Path A: ``OAuthProxy`` (high-level; same primitive ``GoogleProvider`` is built on)
    Path B: Subclass ``OAuthProvider`` directly and override ``authorize()``
            to return a Google URL, then register a custom callback route.

Run with: PYTHONPATH=/tmp/spike_pkgs python3 spike.py
"""

from __future__ import annotations

import sys

# ----------------------------------------------------------------------
# Pin sys.path FIRST so the install at /tmp/spike_pkgs is found before any
# third-party imports below resolve.
# ----------------------------------------------------------------------
sys.path.insert(0, "/tmp/spike_pkgs")

import asyncio
import base64
import hashlib
import json
import secrets
import time
from urllib.parse import parse_qs, urlparse

import httpx
import uvicorn
from fastmcp import FastMCP
from fastmcp.server.auth import OAuthProxy, TokenVerifier
from fastmcp.server.auth.auth import AccessToken

# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8421
BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"
GOOGLE_AUTHZ = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
JWT_KEY = "spike-jwt-signing-key-do-not-use-in-prod-please"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _pkce_pair() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


# ----------------------------------------------------------------------
# Build the FastMCP server with OAuthProxy pointed at Google.
# ----------------------------------------------------------------------
def build_app():
    """Build a FastMCP server where:
       - We are the AS (claude.ai talks to us at /authorize, /token, /register)
       - Google is the upstream IdP (we redirect users to Google's /o/oauth2/v2/auth)
       - Our /token returns a JWT we issued, signed by JWT_KEY.

    Note: in the spike we set ``token_verifier`` to a JWTVerifier that validates
    the JWTs we mint ourselves. In the real plaud server we'd validate Google's
    access token via GoogleTokenVerifier (calls oauth2.googleapis.com/tokeninfo).
    For the spike we're not making real Google calls, so we self-verify our JWT.
    """
    # OAuthProxy.token_verifier is used to validate the UPSTREAM (Google) access
    # token, not our minted JWT (the proxy verifies its own JWT internally with
    # jwt_signing_key). For the spike, we use a fake verifier that accepts any
    # token. In real plaud-mcp-team, this would be GoogleTokenVerifier.
    class FakeUpstreamVerifier(TokenVerifier):
        async def verify_token(self, token: str) -> AccessToken | None:
            return AccessToken(
                token=token,
                client_id="fake-google-client-id.apps.googleusercontent.com",
                scopes=["openid"],
                expires_at=int(time.time()) + 3600,
            )

    token_verifier = FakeUpstreamVerifier(required_scopes=["openid"])

    # Build the proxy. ``require_authorization_consent="external"`` skips the
    # FastMCP consent UI (we lean on Google for consent), matching how a real
    # plaud-team server would behave.
    proxy = OAuthProxy(
        upstream_authorization_endpoint=GOOGLE_AUTHZ,
        upstream_token_endpoint=GOOGLE_TOKEN,
        upstream_client_id="fake-google-client-id.apps.googleusercontent.com",
        upstream_client_secret="fake-google-client-secret",
        token_verifier=token_verifier,
        base_url=BASE_URL,
        redirect_path="/oauth/google/callback",
        require_authorization_consent="external",
        jwt_signing_key=JWT_KEY,
        # Disable forward_pkce so we can drive the test deterministically
        # (otherwise the proxy generates its own PKCE and stores the verifier).
        forward_pkce=True,
        allowed_client_redirect_uris=None,  # allow any (claude.ai uses its own)
    )

    mcp = FastMCP("plaud-mcp-spike", auth=proxy)

    @mcp.tool
    def whoami() -> str:
        return "spike"

    return mcp, proxy


# ----------------------------------------------------------------------
# Test harness — simulates claude.ai's OAuth dance.
# ----------------------------------------------------------------------
async def run_test_harness():
    """Walk the spec'd OAuth flow against the running server.

    1. Discover via /.well-known/oauth-authorization-server
    2. Register dynamic client (RFC 7591) at /register
    3. Hit /authorize  → assert 302 redirect to accounts.google.com
    4. Pretend Google calls back to /oauth/google/callback?code=...&state=txn_id
       (We monkey-skip the real Google token exchange by intercepting the proxy's
       upstream HTTP call — see _patch_upstream below.)
    5. Follow that redirect → claude.ai's redirect_uri with our opaque code
    6. POST /token to exchange that code → JWT
    7. Decode JWT, verify signature & claims.
    """
    print("\n=== TEST HARNESS ===")

    async with httpx.AsyncClient(follow_redirects=False, timeout=10.0) as http:
        # ---- 1. Discovery ----
        r = await http.get(f"{BASE_URL}/.well-known/oauth-authorization-server")
        print(f"[1] discovery: {r.status_code}")
        assert r.status_code == 200, r.text
        meta = r.json()
        print(f"    issuer={meta.get('issuer')}")
        print(f"    authz={meta.get('authorization_endpoint')}")
        print(f"    token={meta.get('token_endpoint')}")
        print(f"    register={meta.get('registration_endpoint')}")

        # ---- 2. Dynamic Client Registration ----
        r = await http.post(
            f"{BASE_URL}/register",
            json={
                "client_name": "fake-claude-ai",
                "redirect_uris": ["https://claude.ai/api/mcp/oauth/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",  # PKCE public client
                "scope": "openid",
            },
        )
        print(f"[2] register: {r.status_code}")
        if r.status_code not in (200, 201):
            print(f"    {r.text[:300]}")
            return False
        client = r.json()
        client_id = client["client_id"]
        print(f"    client_id={client_id}")

        # ---- 3. /authorize ----
        verifier, challenge = _pkce_pair()
        state = "claude-state-" + secrets.token_urlsafe(8)
        r = await http.get(
            f"{BASE_URL}/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": "https://claude.ai/api/mcp/oauth/callback",
                "scope": "openid",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )
        print(f"[3] authorize: {r.status_code}")
        if r.status_code not in (302, 303, 307):
            print(f"    body: {r.text[:400]}")
            return False
        upstream = r.headers["location"]
        print(f"    -> {upstream[:120]}")
        # Verify it points at Google
        if not upstream.startswith(GOOGLE_AUTHZ):
            print(f"    !! expected redirect to {GOOGLE_AUTHZ}, got {upstream[:80]}")
            return False
        # Pull the txn_id (proxy puts it in the upstream `state` param)
        upstream_qs = parse_qs(urlparse(upstream).query)
        txn_id = upstream_qs["state"][0]
        print(f"    txn_id={txn_id}")

        # ---- 4. Simulate Google redirecting back. ----
        # The proxy will then try to POST to GOOGLE_TOKEN to exchange the code.
        # We need to intercept that. Easiest path: monkey-patch the proxy's
        # internal httpx call. See _patch_upstream() below.
        fake_google_code = "fake-google-authcode-" + secrets.token_urlsafe(8)
        r = await http.get(
            f"{BASE_URL}/oauth/google/callback",
            params={"code": fake_google_code, "state": txn_id},
        )
        print(f"[4] google callback: {r.status_code}")
        if r.status_code not in (302, 303, 307):
            print(f"    body: {r.text[:400]}")
            return False
        client_redirect = r.headers["location"]
        print(f"    -> {client_redirect[:120]}")
        cb_qs = parse_qs(urlparse(client_redirect).query)
        if "code" not in cb_qs:
            print(f"    !! no code in callback redirect: {cb_qs}")
            return False
        our_code = cb_qs["code"][0]
        print(f"    our opaque code={our_code[:24]}...")
        assert cb_qs["state"][0] == state, "state must round-trip unchanged"

        # ---- 5. /token exchange ----
        r = await http.post(
            f"{BASE_URL}/token",
            data={
                "grant_type": "authorization_code",
                "code": our_code,
                "redirect_uri": "https://claude.ai/api/mcp/oauth/callback",
                "client_id": client_id,
                "code_verifier": verifier,
            },
        )
        print(f"[5] token: {r.status_code}")
        if r.status_code != 200:
            print(f"    body: {r.text[:600]}")
            return False
        tok = r.json()
        print(f"    access_token={tok['access_token'][:40]}...")
        print(f"    token_type={tok.get('token_type')}")

        # ---- 6. Decode JWT (no signature check, just structure) ----
        parts = tok["access_token"].split(".")
        if len(parts) != 3:
            print(f"    !! token is not a JWT: {tok['access_token'][:60]}")
            return False
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        print(f"    JWT claims: iss={payload.get('iss')} aud={payload.get('aud')}")

        return True


# ----------------------------------------------------------------------
# Mock the upstream Google token-exchange endpoint so the spike runs offline.
# ----------------------------------------------------------------------
def patch_upstream_token_call():
    """Intercept httpx POSTs to oauth2.googleapis.com/token and return a fake
    Google token response. The proxy will then mint its own JWT and complete
    the dance. This is the only thing that prevents this script from making a
    real outbound network call to Google.
    """
    import httpx as _httpx

    real_async_post = _httpx.AsyncClient.post

    async def fake_post(self, url, *args, **kwargs):  # noqa: ANN001
        if str(url).startswith(GOOGLE_TOKEN):
            payload = {
                "access_token": "fake-google-access-token-" + secrets.token_urlsafe(8),
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": "openid",
                "id_token": _make_fake_id_token(),
            }
            return _httpx.Response(
                200,
                content=json.dumps(payload).encode(),
                headers={"content-type": "application/json"},
                request=_httpx.Request("POST", url),
            )
        return await real_async_post(self, url, *args, **kwargs)

    _httpx.AsyncClient.post = fake_post


def _make_fake_id_token() -> str:
    """Construct a syntactically valid id_token (unsigned-style)."""
    header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    body = _b64url(
        json.dumps(
            {
                "iss": "https://accounts.google.com",
                "sub": "google-user-12345",
                "aud": "fake-google-client-id.apps.googleusercontent.com",
                "email": "spike@example.com",
                "email_verified": True,
                "exp": int(time.time()) + 3600,
                "iat": int(time.time()),
            }
        ).encode()
    )
    return f"{header}.{body}."


# ----------------------------------------------------------------------
# Driver: spin up uvicorn, run the harness, tear down.
# ----------------------------------------------------------------------
async def main() -> int:
    patch_upstream_token_call()
    mcp, proxy = build_app()
    app = mcp.http_app(path="/mcp")

    config = uvicorn.Config(
        app, host=SERVER_HOST, port=SERVER_PORT, log_level="warning"
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())

    # Wait for server to come up.
    for _ in range(50):
        await asyncio.sleep(0.1)
        if server.started:
            break
    else:
        print("!! server did not start")
        return 2

    try:
        ok = await run_test_harness()
    finally:
        server.should_exit = True
        await server_task

    print(f"\n=== RESULT: {'PASS' if ok else 'FAIL'} ===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
