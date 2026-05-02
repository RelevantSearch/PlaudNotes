---
version: 3
---

# Plan 2 — App (PlaudNotes fork + OAuth AS)

**Date:** 2026-04-30
**Status:** Draft
**Parent:** [index.md](./index.md) · [Design](./2026-04-30-plaud-mcp-team-design.md)

## Worktree

Plan 2 work happens in the fork repo, not in `rs_infra`. After Phase 1 below creates the fork:

- Worktree: `~/git/RS/PlaudNotes_plaud-mcp-team` off `origin/main`
- Branch: `plaud-mcp-team`
- Cleanup: `git worktree remove ../PlaudNotes_plaud-mcp-team` after final PR merge

Plan docs are mirrored from this rs_infra location into the fork at `docs/projects/2026-04-30-plaud-mcp-team/` once the fork exists (mirrors drive-mcp setup).

## Doc tracker

`/track-docs` at execution start in the fork worktree.

## Prerequisites

- Plan 1 fully merged and applied. Specifically:
  - Cloud Run service `plaud-mcp` exists serving placeholder hello image
  - Firestore database `plaud-mcp` exists (CMEK)
  - KMS keys `plaud-mcp/{cloud_run,firestore,secrets,token}` exist with correct IAM
  - LB serving `plaud-mcp.relevantsearch.com`
  - Secret Manager has `plaud-mcp-google-oauth-client-id`, `plaud-mcp-google-oauth-client-secret`, `plaud-mcp-jwt-signing-key`, `plaud-mcp-session-secret`
  - WIF binding for `RelevantSearch/PlaudNotes` repo
- Plan 1 Phase 0 verifications complete and captured:
  - Plaud TOS verdict (open question 1)
  - Plaud `/me` endpoint URL (open question 2)
  - FastMCP version pinned in upstream PlaudNotes (open question 3)
- Google OAuth consent screen confirmed Internal-type for `relevantsearch.com`

## Scope

Fork `jameshenning/PlaudNotes` to **`RelevantSearch/PlaudNotes`** (forking under `RelevantSearch` is required — existing WIF pool condition rejects other owners). Add a `team` deployment mode controlled by `PLAUD_DEPLOYMENT_MODE=team` that exposes the MCP server as an OAuth 2.1 authorization server using FastMCP's `GoogleProvider`, plus a separate `/admin` web flow for Plaud-token registration. When the env is unset, existing single-tenant local-stdio behavior is preserved.

**Package migration**: PlaudNotes upstream uses in-tree `mcp.server.fastmcp.FastMCP` (`mcp>=1.0.0`). We migrate to standalone **`fastmcp>=3.2.4`** which ships `OAuthProxy` and pre-configured `GoogleProvider`. Plan 2 Phase 1 handles this. (Note: there is no `fastmcp` 4.x; 3.2.4 is current GA per the Stream A spike.)

**Plaud-token capture is decoupled from OAuth**: vanilla `GoogleProvider` runs the claude.ai OAuth flow with no customization. First MCP tool call when no Plaud token is registered returns a structured "no_plaud_token" MCP error pointing the user at `/admin`. The `/admin` endpoint has its own light-touch Google sign-in (separate redirect URI, same Google client) → server-side session cookie → Plaud-token paste form → validate via Plaud `/user/me` → KMS-encrypt + persist.

**Code budget:** ~300-400 LOC including tests. Most of the OAuth surface is FastMCP-provided (~10 lines of config). Existing 12 MCP tools require zero behavior changes (only `_get_client()` rewiring).

## Out of scope (deferred)

- Per-user audit log of which Plaud recordings were accessed
- Bulk admin endpoint to revoke / list users (manual Firestore queries for v1)
- Upstream PR back to `jameshenning/PlaudNotes`

## TDD discipline

Per CLAUDE.md item 3: every code change is preceded by a failing test in the same commit. Phase order: Red → Green → Refactor.

## Phases

Each phase ships as its own PR per `feedback_pr_review_every_phase`. **No bundling.**

---

### Phase 1 — Fork + package migration

**Tasks:**

1. Stefan forks `jameshenning/PlaudNotes` → **`RelevantSearch/PlaudNotes`** via GitHub UI (forking under RelevantSearch is required — see scope note)
2. Confirm default branch is `main`; branch protection on `main` (require PR + status checks)
3. Clone fork: `git clone git@github.com:RelevantSearch/PlaudNotes.git ~/git/RS/PlaudNotes`
4. Create worktree: `git -C ~/git/RS/PlaudNotes worktree add ../PlaudNotes_plaud-mcp-team -b plaud-mcp-team origin/main`
5. Mirror plan docs from rs_infra into fork: `cp -r ~/git/RS/rs_infra_plaud-mcp-team/docs/projects/2026-04-30-plaud-mcp-team ~/git/RS/PlaudNotes_plaud-mcp-team/docs/projects/`
6. Add `CLAUDE.md` to fork:
   - Default branch is `main`
   - Tests: `pytest`; lint: `ruff` + `black`
   - Python 3.12 pinned
   - Pointer to monorepo CLAUDE.md universal rules
7. **Package migration — in-tree `mcp.server.fastmcp` → standalone `fastmcp>=3.2.4`:**
   - Edit `pyproject.toml`: add `fastmcp>=3.2.4,<4` to dependencies; keep `mcp>=1.0.0` (still used for non-FastMCP types)
   - Replace import `from mcp.server.fastmcp import FastMCP` → `from fastmcp import FastMCP` in `src/plaud_notes_mcp/server.py`
   - Verify `FastMCP` class API surface compatibility (the spike confirmed standalone `fastmcp` is API-compatible for our usage; verify the existing 12 tools still register correctly)
   - **Replace `APIKeyMiddleware`** (existing static-Bearer auth in upstream, incompatible with OAuth) with FastMCP's `GoogleProvider` (Phase 5)
   - Run upstream tests against new versions; if any break, resolve them in this PR
8. Pin Python 3.12 in `pyproject.toml` and `Dockerfile`

**Acceptance:** Repo cloned, worktree created, plan docs mirrored, `CLAUDE.md` committed, package migration committed, all upstream tests still pass against new versions.

**PR review gate:** Phase 1 ships as its own PR.

---

### Phase 2 — CI scaffolding (red before green)

**Test first:** Add a single failing test that imports the (yet-to-be-written) team-mode entrypoint:

```python
# tests/test_smoke.py
def test_team_mode_module_importable():
    from plaud_notes_mcp import team   # noqa: F401
```

This fails until Phase 5 lands. CI must run it and fail correctly.

**Files:**

- `.github/workflows/ci.yml`:
  - `on: push` and `on: pull_request`
  - jobs: `lint` (ruff + black) and `test` (pytest)
  - Python 3.12 pinned
  - Test job starts the Firestore emulator as a background service before pytest:
    ```yaml
    - run: gcloud emulators firestore start --host-port=127.0.0.1:8087 &
    - run: |
        until nc -z 127.0.0.1 8087; do sleep 0.5; done
    - run: pytest
      env:
        FIRESTORE_EMULATOR_HOST: 127.0.0.1:8087
    ```
  - KMS calls mocked at `google.cloud.kms.AsyncKeyManagementServiceClient` level (no KMS emulator exists)
- `.github/workflows/deploy.yml` (skeleton, gated `if: github.ref == 'refs/heads/main'` — note `main`, NOT `master`):
  - WIF auth via `google-github-actions/auth@v2` impersonating `plaud-mcp-deployer@rs-workspace-integrations.iam.gserviceaccount.com`
  - Build + push image to `us-central1-docker.pkg.dev/rs-workspace-integrations/plaud-mcp/server:${{ github.sha }}`
  - `gcloud run deploy plaud-mcp --no-traffic --tag=sha-${{ github.sha }}`
  - Smoke `curl https://plaud-mcp.relevantsearch.com/health`
  - `gcloud run services update-traffic plaud-mcp --to-tags=sha-${{ github.sha }}=100`
- Add `ruff`, `black`, `pytest`, `pytest-asyncio`, `httpx` (test client) to dev deps

**Acceptance:** CI runs on PR, lint passes, smoke test fails as expected (red phase). Deploy workflow skeleton present, gated to `main` only.

**PR review gate:** Phase 2 ships as its own PR (deliberately lands the failing test to prove CI works).

---

### Phase 3 — Firestore + KMS token store

**Tests first** (`tests/test_firestore_store.py`):

- `test_encrypt_then_decrypt_roundtrip` — Firestore emulator + KMS mock; store token, fetch, plaintext matches
- `test_unknown_sub_returns_none`
- `test_kms_failure_during_encrypt_raises`
- `test_save_replaces_ciphertext_for_existing_sub` — re-key flow: new ciphertext, other fields preserved (single-doc transaction)
- `test_oauth_client_save_and_lookup` — separate collection
- `test_authorization_code_ttl` — code expires after 5 min, lookup returns None
- `test_authorization_code_single_use` — first lookup deletes the code

Use `pytest-asyncio` + the official Firestore emulator wired via `FIRESTORE_EMULATOR_HOST`.

**Implementation** (`src/plaud_notes_mcp/firestore_store.py`):

- `class FirestoreStore`:
  - `__init__(self, project_id, database_id, kms_key_name)`
  - User token methods:
    - `async save_user_token(google_sub, email, plaud_token_plaintext, region) -> None`
    - `async get_user_token(google_sub) -> StoredToken | None`
    - `async delete_user_token(google_sub) -> None`
  - OAuth client methods:
    - `async save_oauth_client(client_id, client_secret_plaintext, redirect_uris) -> None`
    - `async get_oauth_client(client_id) -> StoredClient | None` (returns hashed secret for verification)
  - Authorization code methods:
    - `async save_authorization_code(code, client_id, google_sub, pkce_challenge, pkce_method, redirect_uri, ttl_seconds=300) -> None`
    - `async consume_authorization_code(code) -> StoredCode | None` (single-use; deletes on read; rejects expired)
- KMS via `google-cloud-kms` `AsyncKeyManagementServiceClient.encrypt`/`decrypt`

**Acceptance:** All tests pass against the emulator. Coverage > 90%.

**PR review gate:** Phase 3 ships as its own PR.

---

### Phase 4 — Token cache (decrypt LRU)

**Why this phase exists:** KMS decrypt is ~30-80ms p50, ~250ms p99. Quota: 60k decrypt ops/min/region. Decrypting on every MCP tool call is hot-path latency. In-process LRU cache slashes both.

**Tests first** (`tests/test_token_cache.py`):

- `test_hit_skips_kms_call`
- `test_miss_reads_firestore_and_decrypts`
- `test_ttl_expiration_forces_refetch`
- `test_lru_eviction_at_capacity`
- `test_invalidate_on_plaud_auth_error`
- `test_concurrent_misses_collapse_to_single_decrypt` (per-`sub` `asyncio.Lock`)

**Implementation** (`src/plaud_notes_mcp/token_cache.py`):

- `class TokenCache(store: FirestoreStore, ttl_seconds=60, max_size=50)`
  - Internal `OrderedDict[google_sub, (plaintext, region, expires_at)]`
  - `async get(google_sub) -> StoredToken | None`
  - `invalidate(google_sub) -> None`
  - Per-`sub` `asyncio.Lock` to collapse thundering-herd
  - Cache-hit-ratio metric: emit via Cloud Run structured logs (use `extra={"cache_event": "hit|miss"}`); a Cloud Logging log-based metric (`google_logging_metric` in rs_infra Plan 1 follow-up) extracts the ratio. **Single mechanism committed.**

**Note:** The `google_logging_metric` resource itself isn't strictly needed in Plan 1 v2 (we can add it post-launch when alerts fire); flagged here as a follow-up, not a blocker.

**Acceptance:** All cache tests pass. Manual benchmark: warm cache p99 < 5ms; cold cache p99 < 250ms.

**PR review gate:** Phase 4 ships as its own PR.

---

### Phase 5 — Configure FastMCP GoogleProvider + Firestore-backed client storage

**Why this phase is small:** the spike ([01-fastmcp-oauth.md](./spike/01-fastmcp-oauth.md)) confirmed `GoogleProvider` is `OAuthProxy` pre-configured for Google. Initialization is ~10 lines of config. The OAuth surface is entirely FastMCP-provided.

**Tests first** (`tests/test_oauth_integration.py`):

- `test_well_known_oauth_authorization_server_metadata` — endpoint returns valid AS metadata; `code_challenge_methods_supported: ["S256"]` (mandatory per claude.ai contract per [02-claude-ai-mcp-behavior.md](./spike/02-claude-ai-mcp-behavior.md))
- `test_well_known_oauth_protected_resource_metadata` — RFC 9728 fields present
- `test_firestore_keyvalue_roundtrip` — our `AsyncKeyValue` adapter for FastMCP: get/set/delete; TTL respected on auth codes
- `test_oauth_dance_end_to_end_with_stubbed_google` — using `httpx.AsyncClient` and a stubbed Google token endpoint, drive the full flow: discover → register → /authorize → simulated Google redirect → /oauth/google/callback → /token. Assert JWT issued with `sub = google_sub`.
- `test_jwt_verifier_extracts_sub_and_emits_www_authenticate_on_failure` — invalid/expired/wrong-signature JWT all return 401 + `WWW-Authenticate: Bearer error="invalid_token"` header (claude.ai bug-mitigation contract)

**Implementation:**

- `src/plaud_notes_mcp/firestore_keyvalue.py` — implements FastMCP's `AsyncKeyValue` interface backed by Firestore. Used as `client_storage` for `GoogleProvider` (oauth_clients + oauth_authorization_codes collections, FastMCP-managed schema).
- `src/plaud_notes_mcp/team.py` — top-level entrypoint when `PLAUD_DEPLOYMENT_MODE=team`:
  ```python
  from fastmcp import FastMCP
  from fastmcp.server.auth.providers.google import GoogleProvider
  
  auth = GoogleProvider(
      client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"],
      client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
      base_url=os.environ["PUBLIC_URL"],
      redirect_path="/oauth/google/callback",
      jwt_signing_key=os.environ["JWT_SIGNING_KEY"],
      client_storage=FirestoreKeyValue(...),
      # Internal-type Google consent does its own gating; skip our consent screen
      require_authorization_consent="external",
  )
  mcp = FastMCP("plaud-mcp-team", auth=auth)
  ```
- Verify the spike script's exact configuration reproduces; pin `fastmcp==3.2.4` (or whatever the spike pinned).

**Acceptance:** All tests pass. The `team.py` configuration matches the spike script and the spike's reproducer test runs green.

**PR review gate:** Phase 5 ships as its own PR.

---

### Phase 6 — Just-in-time `/admin` endpoint (Plaud-token registration)

**Tests first** (`tests/test_admin.py`):

- Auth flow:
  - `test_get_admin_no_session_redirects_to_login` — GET `/admin` without session cookie → 302 to `/admin/auth/login`
  - `test_admin_auth_login_redirects_to_google_oauth` — 302 to `accounts.google.com/o/oauth2/v2/auth` with `openid email` scopes, our `redirect_uri = {PUBLIC_URL}/admin/auth/callback`, signed `state`
  - `test_admin_auth_callback_exchanges_code_sets_session` — stubbed Google `/token` returns sub+email; handler sets HttpOnly+SameSite=Lax+Secure session cookie signed with `SESSION_SECRET`; redirects to `/admin`
  - `test_admin_auth_callback_rejects_tampered_state` — flipped state byte → 400, no session set
  - `test_admin_session_cookie_samesite_lax_not_strict` — explicit verify (architect-review v2 lesson: Strict cookies are not sent on Google's cross-site redirect back)
- Form rendering:
  - `test_get_admin_with_valid_session_renders_form` — form has CSRF hidden field, identity-confirmation heading "Saving Plaud token for {email}"
  - `test_get_admin_with_existing_token_pre_fills_region_and_masks_token` — re-key UX
- POST /admin/save:
  - `test_post_save_validates_token_regex` — `^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$`; garbage → form re-rendered, no network call
  - `test_post_save_validates_token_against_plaud_user_me` — mocks Plaud `/user/me` to 401 → form re-renders with error, nothing persisted
  - `test_post_save_validates_token_against_plaud_user_me_success` — Plaud 200 → KMS-encrypt + persist
  - `test_post_save_csrf_double_submit_required` — POST without matching cookie+form pair → rejected
  - `test_post_save_does_not_log_plaud_token`
  - `test_re_key_replaces_ciphertext_preserves_created_at` — second submit updates ciphertext, `created_at` unchanged, `last_rekeyed_at` updated

**Implementation** (`src/plaud_notes_mcp/admin.py`):

- `GET /admin`:
  - Verify session cookie. None or invalid → 302 to `/admin/auth/login`
  - Render minimal HTML form (server-rendered Jinja, no JS framework beyond a few inline lines for clipboard). System fonts, minimal CSS.
  - Form fields: `plaud_token` (password input), `region` (radio us/eu defaults to "us"), CSRF hidden field.
  - Identity-confirmation heading: "Saving Plaud token for `{email}`" with a "Different account?" link that signs out and restarts `/admin/auth/login`.
- `GET /admin/auth/login`:
  - Initiate Google OAuth: 302 to Google with `scope=openid email`, our `redirect_uri = {PUBLIC_URL}/admin/auth/callback`, signed `state` (HMAC with `SESSION_SECRET`).
  - **Note on Google client reuse:** same Google OAuth client as the MCP OAuth flow (one client, four redirect URIs registered in Cloud Console — see Plan 1 Phase 6 pre-step).
- `GET /admin/auth/callback`:
  - Verify `state` HMAC. Exchange code for ID token + access token at Google's `/token`. Extract `sub` + `email`.
  - Set session cookie: HttpOnly, SameSite=Lax (NOT Strict — Google's cross-site redirect would lose Strict cookies), Secure, signed with `SESSION_SECRET`, 1h TTL.
  - 302 to `/admin`.
- `POST /admin/save`:
  - Verify session + CSRF (signed double-submit: HMAC token in HttpOnly cookie + matching hidden form field)
  - Regex-check token → reject early on garbage
  - Call Plaud `/user/me` with `httpx.AsyncClient(timeout=httpx.Timeout(connect=5, read=10, write=5, pool=5))`. Region base URL from form: `https://api.plaud.ai` or `https://api-euc1.plaud.ai`.
  - On Plaud 401: re-render form with error
  - On Plaud 200: KMS-encrypt token, `FirestoreStore.save_user_token(google_sub, email, token, region)` (single-doc set with merge to preserve `created_at`)
  - Render success page: "Token saved. Return to claude.ai and retry."
- **Audit success page HTML** for any external resource fetches — no CDN fonts, no analytics, no remote images.

**Acceptance:** All admin tests pass. Manual smoke (Phase 9): visit `/admin` cold → Google OAuth → form → paste token → save → success page.

**PR review gate:** Phase 6 ships as its own PR.

---

### Phase 7 — Per-request `PlaudClient` + tool integration

**Tests first** (`tests/test_per_request_client.py`):

- `test_jwt_middleware_extracts_sub_to_contextvar` — request with valid JWT → ContextVar populated with `PlaudClient` whose token == decrypted user token
- `test_jwt_middleware_rejects_no_token_with_401_and_www_authenticate` — `Authorization` header missing → 401 with `WWW-Authenticate: Bearer error="invalid_token"` (always emit, claude.ai bug-mitigation contract)
- `test_jwt_middleware_rejects_expired_jwt`
- `test_jwt_middleware_no_plaud_token_returns_structured_mcp_error` — JWT valid but `user_tokens/{sub}` absent → return MCP error body `{"error": "no_plaud_token", "registration_url": "{PUBLIC_URL}/admin", "message": "Register your Plaud token at the URL above, then retry."}` (NOT a 401 on the HTTP layer — claude.ai stays connected; the user just sees the error in the chat). Plus `WWW-Authenticate` header for defense in depth.
- `test_concurrent_requests_isolated` — two concurrent requests with different JWTs see different `PlaudClient` instances
- `test_plaud_auth_error_invalidates_cache_and_returns_no_plaud_token_error` — upstream Plaud returns 401 → cache invalidated for that `sub`, server returns same "no_plaud_token" structured error
- `test_existing_tool_call_works_unchanged_with_contextvar` — call `list_recordings` against mocked Plaud, populated ContextVar; assert correct response (regression check on the 12 existing tools)

**Implementation:**

- `src/plaud_notes_mcp/auth_middleware.py`:
  - ASGI middleware mounted before `/mcp` routes (NOT on `/admin` — that has its own session-cookie auth)
  - Extract Bearer JWT, verify via FastMCP's `TokenVerifier` (configured with our `JWT_SIGNING_KEY`). On failure → 401 + `WWW-Authenticate` header.
  - Look up Plaud token via `TokenCache.get(sub)` (cache → Firestore → KMS). On miss → return structured MCP error `{"error": "no_plaud_token", "registration_url": "{PUBLIC_URL}/admin", ...}`. Status 200 at HTTP layer (the MCP protocol surfaces the error to the user) plus `WWW-Authenticate` header for defense in depth.
  - On hit: construct `PlaudClient(token, region)` with `httpx.Timeout(connect=5, read=20, write=10, pool=5)`. Set ContextVar `_plaud_client`.
- `src/plaud_notes_mcp/server.py` patch:
  - Replace module-level `_client: PlaudClient | None = None` with `ContextVar[PlaudClient | None]("plaud_client", default=None)`
  - `_get_client()` reads ContextVar; raises `UnauthorizedError` if unset (shouldn't happen with middleware in place — defense in depth for direct tool invocation)
  - All 12 existing tools wrap `client = _get_client()` in try/except for `PlaudAuthError`: on hit, invalidate cache for current `sub`, return structured "no_plaud_token" MCP error
- `/health` endpoint (no auth) returning `{"status": "ok"}`

**Acceptance:**

- All middleware + existing-tool regression tests pass
- Local stdio mode unchanged when `PLAUD_DEPLOYMENT_MODE` unset (regression test)
- Coverage > 90%

**PR review gate:** Phase 7 ships as its own PR.

---

### Phase 8 — App composition + Dockerfile + first deploy

**Tests first** (`tests/test_app_composition.py`):

- `test_team_mode_mounts_oauth_routes_and_auth_middleware`
- `test_local_mode_unchanged` — `PLAUD_DEPLOYMENT_MODE` unset → original stdio behavior, no Firestore client constructed
- `test_health_endpoint_no_auth`
- `test_well_known_oauth_authorization_server_returns_valid_metadata` — integration check across Phases 5-7

**Implementation:**

- `src/plaud_notes_mcp/team.py` (extends Phase 5's entrypoint):
  - Detect mode at startup; if `team`:
    - Instantiate `FirestoreStore`
    - Instantiate `TokenCache(store, ttl_seconds=60, max_size=50)`
    - Configure `GoogleProvider` (Phase 5) with `FirestoreKeyValue` client storage
    - Mount auth middleware on `/mcp` (Phase 7)
    - Mount `/admin` routes (Phase 6)
  - If unset / `local`: existing single-tenant behavior
- Update `Dockerfile`:
  - Add `PLAUD_DEPLOYMENT_MODE=team` as default ENV
  - Confirm non-root user, `EXPOSE 8000`
- `.github/workflows/deploy.yml` filled in from Phase 2 skeleton

**First deploy:**

Merge a small PR to `main`. CI builds, deploys to Cloud Run. Smoke checks:

```
curl https://plaud-mcp.relevantsearch.com/health
# {"status":"ok"}

curl https://plaud-mcp.relevantsearch.com/.well-known/oauth-authorization-server
# returns valid AS metadata JSON
```

**Acceptance:**

- Cloud Run revision deployed with real container (not placeholder)
- `/health` returns 200
- `/.well-known/oauth-authorization-server` returns valid JSON
- Cold-start latency < 3s

**PR review gate:** Phase 8 ships as its own PR.

---

### Phase 9 — End-to-end staged rollout (dev → prod)

Test against `plaud-mcp-dev.relevantsearch.com` first (claude.ai connector cache "burn-in" mitigation per [02-claude-ai-mcp-behavior.md](./spike/02-claude-ai-mcp-behavior.md)).

**Stage 1 — Claude Code CLI on dev** (immune to claude.ai bug):
```
claude mcp add --transport http plaud-mcp-dev https://plaud-mcp-dev.relevantsearch.com/mcp
```
Run any Plaud tool. First call returns `no_plaud_token`. Visit `/admin`. Paste tokenstr. Retry. Real data returned.

**Stage 2 — MCP Inspector on dev:**
```
npx @modelcontextprotocol/inspector
```
Connect to `https://plaud-mcp-dev.relevantsearch.com/mcp`. Verify spec-compliant behavior, list of tools.

**Stage 3 — claude.ai web on dev** (subject to `#46140`):
- Add connector pointing at `plaud-mcp-dev.relevantsearch.com`
- Run OAuth dance, hit JIT registration error, register at `/admin`, retry
- If `#46140` bites (Bearer never sent): document symptoms with a reproducer; decide whether to ship anyway with CLI fallback or wait for Anthropic fix
- Document the user-facing flow with screenshots

**Stage 4 — Negative tests:**
- Sign in with non-Workspace Google account → Google consent screen blocks (Internal-type)
- Revoke Plaud token at `web.plaud.ai` → next claude.ai tool call returns `no_plaud_token` → re-key via `/admin` → calls succeed; connector unchanged

**Stage 5 — Promote to production** (`plaud-mcp.relevantsearch.com`):
- Repeat Stages 1-4 on prod domain
- Onboard 2-3 early testers (Bari TOS verdict must be in by this point)
- After 48h with no issues: org-wide enablement

**Acceptance:** All stages green; UX papercuts captured for follow-up tickets.

**PR review gate:** Phase 9 has no PR (manual acceptance testing).

---

## Critical review

Before each substantive PR, invoke the unforgiving Principal Architect review subagent (CLAUDE.md item 4) on the diff. Address all CRITICAL and MAJOR before pushing.

## PR strategy

- **One PR per phase.** No bundling. Phases: 1, 2, 3, 4, 5, 6, 7, 8. Phase 9 has no PR (staged rollout).
- Each PR: Summary + Test Plan, conventional commits, atomic
- Post-push: monitor `gh pr checks --watch`, reply inline to all comments per `feedback_reply_inline_pr_comments` and `feedback_check_pr_comments_proactively`
- Never merge / approve / close — Stefan does

## Doc tracker

`/track-docs` at execution start in fork worktree. Phase boundaries → respond via SendMessage. Pre-PR → wait for doc-update commit.

## Rollback plan

If Phase 8 deploy is bad:

- `gcloud run services update-traffic plaud-mcp --to-revisions=<previous-rev>=100 --region=us-central1`
- Revert offending commit on `main`; CI redeploys

If Firestore data corruption:

- Firestore Point-in-time recovery enabled by default for 7 days; restore via `gcloud firestore databases restore`

If signing-key rotation goes wrong:

- All claude.ai connectors will fail JWT verification and re-trigger OAuth → users sign in again
- No data loss, only a brief re-auth wave

## Changelog

### v3 — 2026-04-30
Folds in spike findings ([01-fastmcp-oauth.md](./spike/01-fastmcp-oauth.md)) + research streams. Phase 5 collapses to "configure GoogleProvider + FirestoreKeyValue adapter" (~10 LOC of config replaces ~200 LOC of bespoke OAuth code). Phase 6 replaces mid-OAuth-flow Plaud-paste with separate just-in-time `/admin` endpoint (decoupled from OAuth dance, has its own Google sign-in). Phase 1 adds explicit package migration: in-tree `mcp.server.fastmcp` → standalone `fastmcp>=3.2.4`. Phase 7 returns structured "no_plaud_token" MCP error (not 401) when token missing — JWT stays valid, connector stays connected. Phase 9 reorganized into staged rollout (CLI → MCP Inspector → claude.ai dev → claude.ai prod) with explicit dev subdomain mitigation for `#46140`. Always emit `WWW-Authenticate` header on 401s. Code budget revised: 600-800 → 300-400 LOC.

### v2 — 2026-04-30
Rewritten for OAuth wrapper design.

### v1 — 2026-04-30
Initial draft (capability-URL design — superseded).
