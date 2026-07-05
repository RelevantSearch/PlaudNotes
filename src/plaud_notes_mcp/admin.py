"""/admin endpoint — Plaud-token registration via just-in-time Google sign-in.

Decoupled from the claude.ai OAuth dance. Same Google OAuth client, but a
DIFFERENT redirect URI (/admin/auth/callback). Workspace Internal-type
consent gates @relevantsearch.com.

Flow:
  1. GET /admin           → 302 /admin/auth/login if no session
  2. GET /admin/auth/login → 302 to Google with signed state
  3. Google → GET /admin/auth/callback?code&state → exchange, set cookie, 302 /admin
  4. GET /admin           → render form (region, plaud_token, csrf_token)
  5. POST /admin/save     → validate regex, call Plaud /user/me, KMS-encrypt, persist
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlencode

import httpx
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from plaud_notes_mcp.admin_session import (
    decode_session_cookie,
    issue_csrf_pair,
    issue_session_cookie,
    sign_state,
    verify_csrf_pair,
    verify_state,
)
from plaud_notes_mcp.firestore_store import FirestoreStore
from plaud_notes_mcp.token_cache import TokenCache

logger = logging.getLogger(__name__)

SESSION_COOKIE = "plaud_admin_session"
CSRF_COOKIE = "plaud_admin_csrf"
TOKEN_REGEX = re.compile(r"^eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+$")
SESSION_TTL_SECONDS = 3600

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
PLAUD_BASE = {"us": "https://api.plaud.ai", "eu": "https://api-euc1.plaud.ai"}


def build_admin_routes(
    *,
    store: FirestoreStore,
    cache: TokenCache,
    google_client_id: str,
    google_client_secret: str,
    session_secret: str,
    public_url: str,
    http_client: httpx.AsyncClient | None = None,
) -> tuple[list[Route], httpx.AsyncClient | None]:
    """Build the /admin Starlette routes.

    Returns (routes, owned_client). When the caller injects http_client the
    returned owned_client is None (caller is responsible for lifecycle).
    When this function constructs the client itself, it returns it so the
    parent app can close() it on shutdown via lifespan.
    """

    redirect_uri = public_url.rstrip("/") + "/admin/auth/callback"
    owned_client: httpx.AsyncClient | None = None
    if http_client is None:
        owned_client = httpx.AsyncClient(timeout=httpx.Timeout(connect=5, read=10, write=5, pool=5))
        client = owned_client
    else:
        client = http_client

    async def admin_index(request: Request) -> Response:
        session = decode_session_cookie(session_secret, request.cookies.get(SESSION_COOKIE))
        if session is None:
            return RedirectResponse("/admin/auth/login", status_code=302)
        return _render_form(request, session, session_secret, store, cache)

    async def admin_login(request: Request) -> Response:
        state = sign_state(session_secret, return_to="/admin")
        params = {
            "client_id": google_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email",
            "state": state,
            "prompt": "select_account",
        }
        return RedirectResponse(f"{GOOGLE_AUTH_URL}?{urlencode(params)}", status_code=302)

    async def admin_callback(request: Request) -> Response:
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        if not code or not verify_state(session_secret, state):
            return Response("invalid state", status_code=400)
        # Exchange Google authcode for access token + id token.
        token_resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": google_client_id,
                "client_secret": google_client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if token_resp.status_code != 200:
            logger.warning("google token exchange failed", extra={"status": token_resp.status_code})
            return Response("google token exchange failed", status_code=400)
        try:
            token_payload = token_resp.json()
        except ValueError:
            logger.warning("google token endpoint returned non-JSON")
            return Response("google token endpoint returned non-JSON", status_code=502)
        access_token = token_payload.get("access_token")
        if not access_token:
            logger.warning("google token response missing access_token")
            return Response("google token response missing access_token", status_code=502)
        ui_resp = await client.get(
            GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
        )
        if ui_resp.status_code != 200:
            return Response("google userinfo failed", status_code=400)
        ui = ui_resp.json()
        google_sub = ui["sub"]
        email = ui.get("email", "")
        cookie_val = issue_session_cookie(
            session_secret, google_sub=google_sub, email=email, ttl_seconds=SESSION_TTL_SECONDS
        )
        resp = RedirectResponse("/admin", status_code=302)
        resp.set_cookie(
            SESSION_COOKIE,
            cookie_val,
            max_age=SESSION_TTL_SECONDS,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
        )
        return resp

    async def admin_save(request: Request) -> Response:
        session = decode_session_cookie(session_secret, request.cookies.get(SESSION_COOKIE))
        if session is None:
            return RedirectResponse("/admin/auth/login", status_code=302)
        form = await request.form()
        token = (form.get("plaud_token") or "").strip()
        region = (form.get("region") or "us").strip()
        csrf_form = form.get("csrf_token")
        csrf_cookie = request.cookies.get(CSRF_COOKIE)
        if not verify_csrf_pair(session_secret, session["google_sub"], csrf_cookie, csrf_form):
            return Response("csrf failed", status_code=403)
        if region not in PLAUD_BASE:
            return Response("invalid region", status_code=400)
        if not TOKEN_REGEX.match(token):
            return Response("invalid plaud token format", status_code=400)
        # Validate by calling Plaud /user/me.
        me_resp = await client.get(
            f"{PLAUD_BASE[region]}/user/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        if me_resp.status_code == 401:
            return Response("plaud rejected token", status_code=401)
        if me_resp.status_code != 200:
            logger.warning(
                "plaud /user/me unexpected status",
                extra={
                    "status": me_resp.status_code,
                    "google_sub_prefix": session["google_sub"][:8],
                },
            )
            return Response("plaud upstream error", status_code=502)
        # Persist (encrypts under KMS); invalidate cache so next /mcp request
        # fetches fresh state.
        await store.save_user_token(
            google_sub=session["google_sub"],
            email=session["email"],
            plaud_token=token,
            region=region,
        )
        cache.invalidate(session["google_sub"])
        return HTMLResponse(_render_success(session["email"]))

    routes = [
        Route("/admin", admin_index, methods=["GET"]),
        Route("/admin/auth/login", admin_login, methods=["GET"]),
        Route("/admin/auth/callback", admin_callback, methods=["GET"]),
        Route("/admin/save", admin_save, methods=["POST"]),
    ]
    return routes, owned_client


def _render_form(
    request: Request,
    session: dict[str, Any],
    session_secret: str,
    store: FirestoreStore,
    cache: TokenCache,
) -> HTMLResponse:
    cookie_val, form_val = issue_csrf_pair(session_secret, session["google_sub"])
    email = session["email"]
    body = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Plaud MCP — register token</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 540px; margin: 4em auto; padding: 0 1em; color: #1a1a1a; }}
  h1 {{ font-size: 1.25rem; }}
  label {{ display: block; margin: 1.5em 0 0.4em; font-weight: 600; }}
  input[type=password], input[type=text] {{ width: 100%; padding: 0.5em; font-family: monospace; font-size: 0.95em; }}
  fieldset {{ border: 1px solid #ccc; padding: 0.6em 1em 0.8em; }}
  button {{ margin-top: 1.6em; padding: 0.6em 1.2em; font-size: 1em; cursor: pointer; }}
  .meta {{ color: #555; font-size: 0.9em; margin-top: 0.4em; }}
</style>
</head>
<body>
<h1>Saving Plaud token for <strong>{_html_escape(email)}</strong></h1>
<p class="meta">Wrong account? <a href="/admin/auth/login">Sign in as a different user</a>.</p>
<form method="POST" action="/admin/save" autocomplete="off">
  <label for="plaud_token">Plaud token (from web.plaud.ai → DevTools → Application → Local Storage → tokenstr)</label>
  <input id="plaud_token" name="plaud_token" type="password" required spellcheck="false" autocomplete="off">
  <fieldset>
    <legend>Region</legend>
    <label><input type="radio" name="region" value="us" checked> US (api.plaud.ai)</label>
    <label><input type="radio" name="region" value="eu"> EU (api-euc1.plaud.ai)</label>
  </fieldset>
  <input type="hidden" name="csrf_token" value="{form_val}">
  <button type="submit">Save token</button>
</form>
</body>
</html>"""
    resp = HTMLResponse(body)
    resp.set_cookie(
        CSRF_COOKIE,
        cookie_val,
        httponly=True,
        secure=True,
        samesite="strict",  # POST is same-origin so Strict is OK here.
        path="/admin",
    )
    return resp


def _render_success(email: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Token saved</title>
<style>body {{ font-family: system-ui, sans-serif; max-width: 540px; margin: 4em auto; padding: 0 1em; }}</style>
</head>
<body>
<h1>Token saved</h1>
<p>Token registered for <strong>{_html_escape(email)}</strong>. Return to claude.ai and retry your tool call.</p>
</body>
</html>"""


def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )
