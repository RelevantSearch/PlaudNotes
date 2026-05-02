"""Signed-cookie helpers for /admin: session payload + CSRF double-submit + state.

All values are HMAC-SHA256-signed with the session secret. No external
dep — stdlib only. URL-safe base64 to keep cookies / form fields ASCII.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _sign(secret: str, payload: bytes) -> bytes:
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()


def _wrap(secret: str, payload: dict[str, Any]) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = _sign(secret, body)
    return f"{_b64e(body)}.{_b64e(sig)}"


def _unwrap(secret: str, token: str) -> dict[str, Any] | None:
    try:
        body_b64, sig_b64 = token.split(".", 1)
        body = _b64d(body_b64)
        sig = _b64d(sig_b64)
    except (ValueError, base64.binascii.Error):  # type: ignore[attr-defined]
        return None
    expected = _sign(secret, body)
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


# ── Session ─────────────────────────────────────────────────────────────


def issue_session_cookie(
    secret: str,
    *,
    google_sub: str,
    email: str,
    ttl_seconds: int = 3600,
) -> str:
    return _wrap(
        secret,
        {
            "google_sub": google_sub,
            "email": email,
            "iat": int(time.time()),
            "exp": int(time.time()) + ttl_seconds,
        },
    )


def decode_session_cookie(secret: str, token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    payload = _unwrap(secret, token)
    if payload is None:
        return None
    if int(time.time()) >= int(payload.get("exp", 0)):
        return None
    return payload


# ── State (used on /admin/auth/login → /admin/auth/callback) ────────────


def sign_state(secret: str, *, return_to: str = "/admin", ttl_seconds: int = 600) -> str:
    return _wrap(
        secret,
        {
            "return_to": return_to,
            "nonce": _b64e(secrets.token_bytes(16)),
            "exp": int(time.time()) + ttl_seconds,
        },
    )


def verify_state(secret: str, token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    payload = _unwrap(secret, token)
    if payload is None:
        return None
    if int(time.time()) >= int(payload.get("exp", 0)):
        return None
    return payload


# ── CSRF double-submit ──────────────────────────────────────────────────


def issue_csrf_pair(secret: str, google_sub: str) -> tuple[str, str]:
    """Returns (cookie_value, form_value). Both are required on POST.

    Cookie is HttpOnly+Secure; form value is embedded as a hidden input.
    Both contain the same nonce; the HMAC binds the nonce to the user's
    Google sub so a token issued for user A cannot be used by user B.
    """
    nonce = secrets.token_urlsafe(16)
    sig = _b64e(_sign(secret, f"{google_sub}:{nonce}".encode("utf-8")))
    return f"{nonce}.{sig}", f"{nonce}.{sig}"


def verify_csrf_pair(
    secret: str, google_sub: str, cookie_value: str | None, form_value: str | None
) -> bool:
    if not cookie_value or not form_value:
        return False
    if not hmac.compare_digest(cookie_value, form_value):
        return False
    try:
        nonce, sig_b64 = cookie_value.split(".", 1)
        sig = _b64d(sig_b64)
    except (ValueError, base64.binascii.Error):  # type: ignore[attr-defined]
        return False
    expected = _sign(secret, f"{google_sub}:{nonce}".encode("utf-8"))
    return hmac.compare_digest(sig, expected)
