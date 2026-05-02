---
version: 1
date: 2026-04-30
status: Complete
---

# Spike 01: FastMCP 3.x as OAuth AS with Google as IdP-only

## Verdict

**WORKS** — out-of-the-box, no fork or subclass required.

FastMCP 3.2.4 ships `OAuthProxy`, a primitive whose explicit purpose is exactly
the pattern this project needs: the FastMCP server is the AS that claude.ai
talks to, and an external IdP (Google) is used purely for user
identification. Confidence: **high — empirically verified end-to-end** with
the spike script below.

## Architecture FastMCP gives us for free

`OAuthProxy` is the right primitive. It composes two things:

1. The MCP SDK's `OAuthAuthorizationServerProvider` — meaning we ARE the AS.
   Routes `/.well-known/oauth-authorization-server`, `/register`, `/authorize`,
   `/token`, `/revoke` are wired up automatically via
   `mcp.server.auth.routes.create_auth_routes` (see `auth.py` line 748).
2. An override of `authorize()` (proxy.py line 757) that, instead of issuing
   a code locally, returns the upstream Google `/o/oauth2/v2/auth` URL — so
   the user's browser is bounced to Google for sign-in. The proxy stores a
   server-side transaction (PKCE, client-redirect-uri, claude.ai's state) keyed
   by an opaque `txn_id` it stuffs into the upstream `state` parameter.
3. A bonus route at `redirect_path` (default `/auth/callback`, configurable —
   we set `/oauth/google/callback`) that handles Google's callback, exchanges
   Google's code for a Google access token server-side, mints OUR opaque
   authorization code bound to claude.ai's PKCE challenge, and 302s the
   browser back to claude.ai's `redirect_uri`. claude.ai then exchanges that
   code at our `/token` for a FastMCP-issued JWT (HS256 by default, signed
   with `jwt_signing_key`).

The architect-review concern that "neither documented primitive is obviously
'we are the AS, Google is JUST an IdP'" was incorrect. `OAuthProxy` IS that
primitive. The MCP SDK's `OAuthAuthorizationServerProvider.authorize()`
docstring (`mcp/server/auth/provider.py` line 133) literally describes this
flow as the canonical pattern for MCP servers — including an ASCII diagram of
"Client -> MCP Server -> 3rd Party OAuth Server".

The two-subsystem split is real but not the wedge it appeared to be:
- `OAuthProvider` = base class for "we are the AS, we own user auth ourselves"
- `OAuthProxy` = subclass of `OAuthProvider` that delegates user auth to an
  external IdP via the OAuth code flow

We want `OAuthProxy`. Confidence: **high**, source-confirmed (proxy.py line
120: `class OAuthProxy(OAuthProvider, ConsentMixin)`).

## What the existing `GoogleProvider` is

`fastmcp.server.auth.providers.google.GoogleProvider` is `OAuthProxy` with
Google's authorize/token endpoints pre-filled. Source: `providers/google.py`
line 204: `class GoogleProvider(OAuthProxy)`. We could literally use it; the
only deviation is whether we want to validate Google access tokens by calling
`oauth2.googleapis.com/tokeninfo` (what `GoogleTokenVerifier` does — perfectly
fine for plaud) or do something custom. Confidence: **high**.

## Citations

| Claim | Source |
|---|---|
| FastMCP 3.2.4 is the latest release (2026-04-14) | [GitHub release](https://github.com/PrefectHQ/fastmcp/releases/tag/v3.2.4) |
| `OAuthProxy` is the third-party-IdP primitive | [`oauth_proxy/proxy.py:120`](https://github.com/PrefectHQ/fastmcp/blob/v3.2.4/src/fastmcp/server/auth/oauth_proxy/proxy.py#L120) |
| `OAuthProxy.authorize()` returns the upstream IdP URL | [`oauth_proxy/proxy.py:757`](https://github.com/PrefectHQ/fastmcp/blob/v3.2.4/src/fastmcp/server/auth/oauth_proxy/proxy.py#L757-L869) |
| `redirect_path` is the user-facing IdP-callback route | [`oauth_proxy/proxy.py:1882-1889`](https://github.com/PrefectHQ/fastmcp/blob/v3.2.4/src/fastmcp/server/auth/oauth_proxy/proxy.py#L1882-L1889) |
| `_handle_idp_callback` exchanges upstream code, mints our code | [`oauth_proxy/proxy.py:1905`](https://github.com/PrefectHQ/fastmcp/blob/v3.2.4/src/fastmcp/server/auth/oauth_proxy/proxy.py#L1905) |
| MCP SDK `authorize()` protocol explicitly describes this pattern | [`python-sdk/.../provider.py:133-174`](https://github.com/modelcontextprotocol/python-sdk/blob/main/src/mcp/server/auth/provider.py#L133-L174) |
| SDK `/authorize` handler uses `provider.authorize(...)` return value as 302 target | [`python-sdk/.../authorize.py:208-217`](https://github.com/modelcontextprotocol/python-sdk/blob/main/src/mcp/server/auth/handlers/authorize.py#L208-L217) |
| `OAuthProvider.get_routes` wires the standard AS routes | [`fastmcp/.../auth.py:748-754`](https://github.com/PrefectHQ/fastmcp/blob/v3.2.4/src/fastmcp/server/auth/auth.py#L748-L754) |
| `GoogleProvider` is just `OAuthProxy` pre-pointed at Google | [`fastmcp/.../providers/google.py:204`](https://github.com/PrefectHQ/fastmcp/blob/v3.2.4/src/fastmcp/server/auth/providers/google.py#L204) |
| `GoogleTokenVerifier` validates Google access tokens via `tokeninfo` | [`fastmcp/.../providers/google.py:92-201`](https://github.com/PrefectHQ/fastmcp/blob/v3.2.4/src/fastmcp/server/auth/providers/google.py#L92-L201) |

## Spike script

`spike/fastmcp-oauth/spike.py` (~270 LOC). Pinned dependencies: `fastmcp==3.2.4`,
`mcp==1.27.0`, `httpx==0.28.1`, `uvicorn==0.46.0`. Mocks Google's `/token`
endpoint locally so the spike runs offline.

```bash
python3 -m venv /tmp/spike_venv
pip3 install --target /tmp/spike_pkgs fastmcp httpx pytest pytest-asyncio
python3 spike/fastmcp-oauth/spike.py
```

The script:
1. Builds an `OAuthProxy` pointed at fake Google OAuth endpoints
2. Boots it under uvicorn at `http://127.0.0.1:8421`
3. Walks the full claude.ai dance with httpx
4. Asserts every step

## Run output (PASS)

```
=== TEST HARNESS ===
[1] discovery: 200
    issuer=http://127.0.0.1:8421/
    authz=http://127.0.0.1:8421/authorize
    token=http://127.0.0.1:8421/token
    register=http://127.0.0.1:8421/register
[2] register: 201
    client_id=8c7bcd7d-690d-4a29-ae87-437fbc628599
[3] authorize: 302
    -> https://accounts.google.com/o/oauth2/v2/auth?response_type=code&client_id=fake-google-client-id.apps.googleusercontent.c
    txn_id=QLTNaiaLrSRZsxHuBIhmEPW7E_puXDGDQZjxg4pokqA
[4] google callback: 302
    -> https://claude.ai/api/mcp/oauth/callback?code=gGhIonA9_fID7Xw0PfmW6STA9bJf_ZVGWTPvQz7I4oY&state=claude-state-l87sQQ8MQh4
    our opaque code=gGhIonA9_fID7Xw0PfmW6STA...
[5] token: 200
    access_token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ...
    token_type=Bearer
    JWT claims: iss=http://127.0.0.1:8421/ aud=http://127.0.0.1:8421/mcp

=== RESULT: PASS ===
```

Every required hop succeeded:
- claude.ai discovers our AS via `.well-known`
- claude.ai dynamically registers (RFC 7591)
- `/authorize` 302s to `accounts.google.com/o/oauth2/v2/auth` (Google as IdP)
- `/oauth/google/callback` (`redirect_path`) 302s back to claude.ai's
  `redirect_uri` with our opaque code, state preserved
- `/token` returns a JWT with `iss=our_base_url`, `aud=our_resource_url`

Confidence: **high — directly observed, three runs**.

## Caveats / things to know before building

1. **Google access tokens are opaque, not JWTs.** `GoogleTokenVerifier`
   validates them by calling `https://oauth2.googleapis.com/tokeninfo` per
   request. Plaud should expect ~50–150ms added latency on every authenticated
   tool call unless we cache. The proxy already caches the upstream token
   server-side via `_jti_mapping_store` (`proxy.py:1582-1593`), but
   `verify_token` is called fresh each request. Confidence: **medium** —
   inferred from `proxy.py:1606` calling `_token_validator.verify_token` on
   every JWT validation; not benchmarked.
2. **Default consent screen ON.** `OAuthProxy` shows its own consent
   interstitial unless you set `require_authorization_consent="external"`
   (which we did in the spike). For a "Google handles consent" model, set
   `"external"`. Confidence: **high** — `proxy.py:849-869`.
3. **`jwt_signing_key` is mandatory unless `upstream_client_secret` is
   provided.** When omitted, the proxy derives the JWT key from the upstream
   client secret via PBKDF2 (`proxy.py:423-446`). For plaud we should pass an
   explicit `jwt_signing_key` from Secret Manager and treat the upstream
   client secret as orthogonal. Confidence: **high** — source-read.
4. **Transaction store is in-memory by default.** Use `client_storage` with
   a real `AsyncKeyValue` backend (Redis, Firestore) for HA — otherwise a
   restart between `/authorize` and `/token` breaks every in-flight flow.
   Confidence: **high** — see `client_storage` parameter on `OAuthProxy`.
5. **`forward_pkce=True` (default) wraps claude.ai's PKCE in our own PKCE for
   the upstream Google call.** This is what we want; means our server is a
   proper PKCE client of Google. Confidence: **high** — `proxy.py:817-822`.
6. **`OAuthProxy` is huge (~93 KB / 2300+ LOC).** Reading the whole thing
   end-to-end is a real cost during onboarding for any engineer who needs to
   debug it. Plan for that.
7. **Spike does NOT exercise refresh_token rotation, revocation, or CIMD
   (Client ID Metadata Document).** Those code paths exist in the proxy and
   look correct on read, but were not empirically validated. Confidence on
   them working: **medium** — source-confirmed only.

## Implication for the project plan

The load-bearing assumption holds: `OAuthProxy` (or `GoogleProvider` if we
accept its defaults) is a one-call configuration of FastMCP and gives us
exactly the architecture in the design doc. No fork. No subclass override of
`/authorize`. No custom Starlette routes. ~10 lines of code:

```python
from fastmcp import FastMCP
from fastmcp.server.auth.providers.google import GoogleProvider

auth = GoogleProvider(
    client_id="...apps.googleusercontent.com",
    client_secret="GOCSPX-...",
    base_url="https://plaud-mcp.relevantsearch.com",
    redirect_path="/oauth/google/callback",
    require_authorization_consent="external",
    jwt_signing_key=os.environ["JWT_SIGNING_KEY"],  # from Secret Manager
    client_storage=FirestoreKeyValue(...),  # for HA
)
mcp = FastMCP("plaud-mcp-team", auth=auth)
```

Move on.

## Files

- Spike script: `spike/fastmcp-oauth/spike.py`
- This findings doc: `spike/01-fastmcp-oauth.md`

## Changelog

### v1 — 2026-04-30
Initial spike findings. Verdict: WORKS, end-to-end PASS.
