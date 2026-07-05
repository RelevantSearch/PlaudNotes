---
version: 3
---

# Design: Team Plaud Notes MCP on Cloud Run (OAuth wrapper, v3)

**Date:** 2026-04-30
**Status:** Draft
**Parent:** [index.md](./index.md)

## Goal

Expose a team-wide Plaud Notes MCP server to the Relevant Search team via claude.ai's Custom Connectors using the MCP-spec OAuth 2.1 flow. The MCP server acts as its own OAuth Authorization Server (FastMCP's `GoogleProvider`); Google is used as the identity provider only, restricted to `@relevantsearch.com` via Internal-type consent. Per-user Plaud bearer tokens are captured via a separate **just-in-time `/admin` endpoint** that surfaces when a user's first MCP tool call returns "no Plaud token registered." Re-keying when the Plaud token expires reuses the `/admin` flow; the claude.ai connector configuration is unaffected.

## Non-goals

- External (non-Workspace) users
- Plaud OAuth / SSO (Plaud has no OAuth — manual `tokenstr` extraction is the only option)
- Production-tier environment in v1 (nonprod only)
- Cloud Armor / WAF in v1 (OAuth Bearer tokens are single-user-scoped — no per-slug brute-force surface)
- Geographic restrictions
- Ingestion of Plaud transcripts into Cerebro (separate project)

## Background

`jameshenning/PlaudNotes` (Python, MIT, default branch `main`) is a single-tenant MCP server: one `PLAUD_TOKEN` env var per process, cached as a module-level singleton (`server.py:_get_client`). It uses **in-tree** `mcp.server.fastmcp.FastMCP` (`mcp>=1.0.0` package). Twelve tools span listing, transcripts, summaries, search, tags, speakers, devices.

Plaud's `tokenstr` auth model is documented but officially-deprecated-eventually: Plaud has an **official OAuth 2.0 API in private beta** ([waitlist](https://support.plaud.ai/hc/en-us/articles/56061278749209-FAQs-for-Plaud-OAuth-API)) that is the long-term migration target. Until that's accessible, our `tokenstr` proxy is a strictly time-boxed bridge. Submit the waitlist application immediately so the migration clock starts.

claude.ai's Custom Connector UI requires either OAuth (Authorization Server discovery + dynamic client registration per the MCP spec) or no auth at all. Static Bearer / custom HTTP headers are not supported in the web/mobile UI as of April 2026 (`anthropics/claude-ai-mcp#112`). The Teams plan surfaces MCPs through claude.ai connectors, so claude.ai is the binding constraint.

For OAuth implementation, we use the **standalone `fastmcp` package (>= 3.2.4)**, NOT the in-tree `mcp.server.fastmcp` upstream PlaudNotes ships with. The standalone package is what implements `OAuthProxy` and pre-configured `GoogleProvider`. Plan 2 Phase 1 includes the package migration.

**Spike verified ([01-fastmcp-oauth.md](./spike/01-fastmcp-oauth.md))**: `OAuthProxy` is exactly the AS-with-external-IdP pattern we need. `GoogleProvider` is `OAuthProxy` pre-configured for Google — initialization is ~10 lines of config. The Python MCP SDK documents this pattern as the canonical case (see ASCII diagram at [`mcp/server/auth/provider.py:133-174`](https://github.com/modelcontextprotocol/python-sdk/blob/main/src/mcp/server/auth/provider.py#L133-L174)).

**claude.ai has an active OAuth-connector bug** ([anthropics/claude-code#46140](https://github.com/anthropics/claude-code/issues/46140), [claude-ai-mcp#155](https://github.com/anthropics/claude-ai-mcp/issues/155) and others, open as of 2026-04-29) where OAuth completes but Bearer token never arrives on subsequent MCP calls. Non-deterministic. Same servers work fine on Claude Code CLI, MCP Inspector, ChatGPT, and curl. We mitigate by (a) iterating against a dev subdomain `plaud-mcp-dev.relevantsearch.com` so we don't burn `plaud-mcp.relevantsearch.com`'s connector-cache state, (b) always emitting `WWW-Authenticate` on 401s (case-sensitive header bug `claude-ai-mcp#219` was fixed 2026-04-24, but we maintain the contract), (c) keeping Claude Code CLI as a documented fallback for users who hit the bug.

## Architecture

```
+-------------+     OAuth 2.1     +---------------------+   Google OAuth   +-----------+
|  claude.ai  | <---------------> |  MCP server          | <--------------> |  Google   |
|  connector  |  GoogleProvider   |  /mcp + /admin       |  (IdP only)      |  (Workspace
+-------------+                   |  (Cloud Run)         |                  |   SSO)    |
                                  +----------+-----------+                  +-----------+
                                             |
                                             v
                                   +---------------------+
                                   | Firestore           |
                                   |  oauth_clients/     |   (FastMCP-managed)
                                   |  user_tokens/       |   { plaud_token KMS-wrapped }
                                   +---------------------+
```

Two distinct user-facing flows on the same Cloud Run service:

- **`/mcp` + OAuth surface** (`/.well-known/...`, `/register`, `/authorize`, `/token`) — claude.ai's connector talks to this. `GoogleProvider` handles everything; we write zero OAuth code.
- **`/admin`** — separate web flow where the user registers (or re-keys) their Plaud `tokenstr`. Has its own light-touch Google sign-in (uses the same OAuth client as the MCP surface but a separate redirect URI; identifies the user via Google `sub`). Does NOT participate in claude.ai's OAuth dance.

### claude.ai connection flow (first time, per user)

1. User clicks "Add custom connector" in claude.ai, enters `https://plaud-mcp.relevantsearch.com/mcp`.
2. claude.ai fetches `/.well-known/oauth-authorization-server` from our server (FastMCP-provided).
3. claude.ai dynamically registers itself as an OAuth client via `POST /register` (RFC 7591, FastMCP-provided). FastMCP persists the client via our Firestore-backed `AsyncKeyValue` storage.
4. claude.ai initiates `/authorize` with PKCE S256.
5. `GoogleProvider` redirects the user's browser to Google's OAuth with `openid email` scopes. Workspace SSO restricts to `@relevantsearch.com`.
6. User signs in with Google. Google redirects to `/oauth/google/callback`.
7. `GoogleProvider` exchanges Google's code, extracts `sub` + `email`, mints **our own** auth code bound to `google_sub`, persists it to Firestore (5-min TTL), and 302s back to claude.ai's `redirect_uri` with our code.
8. claude.ai exchanges our code at `/token`. We issue a JWT (signing key from Secret Manager) with `sub = google_sub`, 1h access + 30d refresh.
9. claude.ai is now "connected." Connector configuration done.

**The user has NOT pasted a Plaud token yet.** That happens just-in-time below.

### Just-in-time Plaud token capture

10. claude.ai sends the first MCP tool call: `POST /mcp` with `Authorization: Bearer <our-jwt>`.
11. FastMCP middleware verifies the JWT, extracts `sub`. Our auth-aware middleware looks up `user_tokens/{google_sub}` in Firestore — **not found**.
12. Server returns a structured MCP error with body:
    ```
    {
      "error": "no_plaud_token",
      "registration_url": "https://plaud-mcp.relevantsearch.com/admin",
      "message": "Register your Plaud token at the URL above, then retry."
    }
    ```
    plus `WWW-Authenticate: Bearer error="invalid_token", error_description="..."` header (claude.ai bug mitigation per `claude-ai-mcp#219` even though that was fixed; defense in depth).
13. claude.ai surfaces this error to the user. User clicks the registration URL.
14. Browser GETs `/admin`. No session → `/admin` redirects to its own `/admin/auth/login`, which initiates Google OAuth (separate redirect URI: `/admin/auth/callback`). User is already signed into Google from step 6 — Google completes silently. We extract `sub` + `email` and set a server-side session cookie (HttpOnly + SameSite=Lax + Secure, 1h TTL, signed with `plaud-mcp-session-secret`).
15. `/admin` page renders the Plaud-token paste form. CSRF token via signed double-submit cookie.
16. User pastes Plaud `tokenstr`, selects region (us/eu), submits to `POST /admin/save`.
17. Server validates: regex check, then call Plaud's **`/user/me`** endpoint (verified in [03-plaud-api-tos.md](./spike/03-plaud-api-tos.md)) with the new token. On 401, form re-renders with an error and nothing is stored.
18. On Plaud 200: KMS-envelope-encrypt token, write `user_tokens/{google_sub}` in a single-doc transaction (or `set(merge=True)` semantics — either way atomic for one doc). Render a success page: "Token saved. Return to claude.ai and retry your request."
19. User retries in claude.ai (claude.ai may auto-retry; if not, user re-issues the original message). Tool call now succeeds.

### Tool-call flow (every subsequent request)

1. claude.ai sends `POST /mcp` with our JWT. FastMCP verifies signature + `exp`; extracts `sub`.
2. Auth-aware middleware reads `user_tokens/{sub}` (with in-process LRU cache: TTL 60s, max 50 entries), decrypts via KMS, instantiates `PlaudClient(token, region)` with explicit `httpx.Timeout(connect=5, read=20, write=10, pool=5)`.
3. ContextVar set; tool dispatches; result returned.
4. On `PlaudAuthError` from Plaud (token expired/revoked): invalidate cache for `sub`, return structured MCP error pointing at `/admin` for re-keying. Same JIT flow as steps 12-19 above.

### Re-key flow (Plaud token expires, ~every 10 months)

1. User attempts a tool call → `PlaudAuthError` → server returns "register at `/admin`" structured response.
2. User visits `/admin`. Session cookie still valid → form renders pre-loaded with their region (no Google sign-in needed if within session TTL).
3. User pastes fresh `tokenstr`, server validates via `/user/me`, replaces ciphertext for the same `google_sub`. claude.ai connector configuration is unchanged.

### Components

**App (fork `RelevantSearch/PlaudNotes` from `jameshenning/PlaudNotes`)**

Default branch is `main` (mirrors upstream). Forking under the `RelevantSearch` org is required — the existing WIF pool's `attribute_condition` is `assertion.repository_owner == 'RelevantSearch'` (verified at `~/git/RS/main/rs_infra/modules/wif-github-actions/main.tf:135-146`); a `jameshenning/`-owned fork would be rejected.

**Package migration:** PlaudNotes upstream uses in-tree `mcp.server.fastmcp.FastMCP` from `mcp>=1.0.0`. We migrate to the **standalone `fastmcp>=3.2.4` package** (the spike-verified primitive source). Plan 2 Phase 1 handles the swap. Existing 12 MCP tools have zero behavior changes — the `FastMCP` API surface is compatible.

**Routes:**

OAuth surface (entirely FastMCP-provided via `GoogleProvider`):
- `/.well-known/oauth-authorization-server` — RFC 8414 metadata
- `/.well-known/oauth-protected-resource` — RFC 9728 metadata
- `/register` — RFC 7591 dynamic client registration; clients persisted via our `FirestoreKeyValue` (a custom `AsyncKeyValue` backend for FastMCP's pluggable client storage)
- `/authorize` — `GoogleProvider` 302s to Google with `openid email` scopes
- `/oauth/google/callback` — `GoogleProvider` exchanges Google's code, mints our auth code, redirects back to claude.ai
- `/token` — `GoogleProvider` issues JWT access + refresh
- `/mcp` — Streamable HTTP MCP endpoint, gated by FastMCP's TokenVerifier

Custom surface (we write):
- `/admin` — GET, redirects to `/admin/auth/login` if no session, else renders Plaud-token paste form
- `/admin/auth/login` — initiates Google OAuth (separate redirect URI; same Google client)
- `/admin/auth/callback` — exchanges Google code, sets server-side session cookie, redirects to `/admin`
- `/admin/save` — POST, validates Plaud token via `/user/me`, KMS-encrypts, persists `user_tokens/{google_sub}`
- `/health` — unauthenticated liveness probe

The 12 existing MCP tools have their `_get_client()` rewired to read a per-request `ContextVar` populated by an auth-aware middleware that resolves Google `sub` → Plaud token (with cache) → `PlaudClient`. On missing token → middleware returns the structured "register at `/admin`" MCP error.

**Infrastructure (rs_infra new module `workspace-integrations-plaud-mcp`)**

Target project: `rs-workspace-integrations` (existing, owned by `modules/workspace-integrations-mcp` for drive-mcp). The new module **does not create the project** — it reads it via `data "google_project"` and adds plaud-specific resources. This is the only structural difference from drive-mcp's module.

- KMS keyring `plaud-mcp` in `kms-proj-a9dncstlc3zg`; keys: `cloud_run`, `firestore`, `secrets`, `token` (token = app-level envelope encryption of Plaud tokens).
- Artifact Registry repo `plaud-mcp` (CMEK).
- Firestore database (named, not `(default)`): name `plaud-mcp`, CMEK with `firestore` key. drive-mcp owns `(default)`.
- Cloud Run service `plaud-mcp` (min 1, max 10, 512MB, concurrency 20), CMEK-encrypted.
- Service account `plaud-mcp@rs-workspace-integrations.iam.gserviceaccount.com` with `datastore.user`, `cloudkms.cryptoKeyEncrypterDecrypter` (on `token` key only), `logging.logWriter`, `secretmanager.secretAccessor` (on plaud-mcp secrets only).
- Secret Manager (CMEK with `secrets` key, sourced via SOPS):
  - `plaud-mcp-google-oauth-client-id`, `plaud-mcp-google-oauth-client-secret` — Google IdP credentials (separate Cloud Console OAuth client from drive-mcp's; different redirect URIs)
  - `plaud-mcp-jwt-signing-key` — symmetric key FastMCP uses to sign issued JWTs (rotated every 90 days; rotation triggers full re-auth wave but no data loss)
  - `plaud-mcp-session-secret` — signs `/admin` server-side session cookies + CSRF double-submit tokens (rotated every 90 days)
- Serverless NEG → Cloud Run.
- External HTTPS LB + Google Compute managed SSL certificate covering BOTH:
  - `plaud-mcp.relevantsearch.com` — production
  - `plaud-mcp-dev.relevantsearch.com` — dev iteration host (mitigation for claude.ai connector cache "burn-in" per [02-claude-ai-mcp-behavior.md](./spike/02-claude-ai-mcp-behavior.md); both hosts serve the same Cloud Run service)
- DNS A records for both FQDNs in the existing `relevant-search-main` zone, both pointing at the same LB IP.
- WIF principalSet binding for `RelevantSearch/PlaudNotes` repo → `plaud-mcp-deployer` SA (existing pool condition `assertion.repository_owner == 'RelevantSearch'` is satisfied; verified at `~/git/RS/main/rs_infra/modules/wif-github-actions/main.tf:135-146`). **Default branch is `main`.**
- **No IAP** (the OAuth flow handles auth at the application layer; same as drive-mcp).
- **No Cloud Armor** (OAuth Bearer tokens are single-user-scoped — no per-slug brute-force defense needed).

### Data model (Firestore, database `plaud-mcp`)

`oauth_clients/{client_id}` and `oauth_authorization_codes/{code}` are managed by FastMCP via our `FirestoreKeyValue` (a custom `AsyncKeyValue` backend pluggable into FastMCP's client storage). Schema is FastMCP-defined; we don't write the storage logic, only the Firestore I/O glue. The `code` collection has 5-min TTL via the `expires_at` field; FastMCP handles cleanup on access.

`user_tokens/{google_sub}` — the only collection we own outright

```
{
  google_sub: string,                    // doc ID; immutable Google subject
  email: string,                         // for support / observability
  plaud_token_ciphertext: bytes,         // KMS envelope-encrypted Plaud bearer token
  kms_key_version: string,               // for rotation
  region: string,                        // "us" | "eu"
  created_at: timestamp,
  last_rekeyed_at: timestamp
}
```

No plaintext tokens are ever stored. Even a Firestore read bypass yields useless ciphertext without `cloudkms.cryptoKeyEncrypterDecrypter` on the `token` key, granted only to the runtime Cloud Run service account.

### Why no second IAP-gated admin host

In the v1 capability-URL design we needed IAP because the per-user URL was the credential and we needed Workspace SSO to gate token registration. With OAuth, Google sign-in inside the `/authorize` flow plays exactly that role — the consent screen is restricted to `@relevantsearch.com` Workspace via Internal-type consent. One Cloud Run service, one LB, one host. Drops half the v1 infra surface.

## Security & compliance

**Identity:**

- Google OAuth consent screen type = **Internal** → only `@relevantsearch.com` accounts can authenticate; no Google verification required.
- Google scopes: `openid` + `email` (we only need identity, not Drive/Mail). Narrower than drive-mcp.
- MCP-issued JWTs: HS256, 1-hour access TTL + 30-day refresh TTL, signed with `plaud-mcp-jwt-signing-key`, rotated every 90 days. PKCE (S256) required on `/authorize` (FastMCP enforces). Note: HS256 + secret-in-SecretManager means anyone with `secretAccessor` on the key can mint a JWT for any `google_sub`; documented future hardening to RS256 with KMS-backed asymmetric signing key.

**Token confidentiality:**

- Plaud tokens stored only as KMS-wrapped ciphertext.
- KMS decrypter role granted only to the runtime SA on the `token` key.
- All services use CMEK per `constraints/gcp.restrictNonCmekServices`.

**OAuth flow integrity:**

- PKCE S256 enforced on every `/authorize` (FastMCP enforces).
- `GoogleProvider` handles all `state` and authorization-code lifecycle internally. Authorization codes are single-use, short-TTL, deleted on first use (FastMCP-managed via `AsyncKeyValue` storage; we provide a Firestore-backed implementation).

**`/admin` flow integrity:**

- Server-side session cookie (HttpOnly + SameSite=Lax + Secure, 1h TTL) signed with `plaud-mcp-session-secret`. `SameSite=Lax` (not Strict) so cookies survive Google's cross-site redirect back to our `/admin/auth/callback` (per `feedback_verify_with_citations` lesson from architect review v2 — `Strict` cookies are NOT sent on cross-site top-level navigations).
- CSRF on the `POST /admin/save` form: signed double-submit (HMAC-derived token in HttpOnly cookie + hidden form field; both must match and HMAC-verify).
- **Identity-confirmation step** on the paste form (mitigation for shared-browser attack): the form heading reads "Saving Plaud token for `{email}`. [Different account?]" forcing the user to confirm whose Plaud token they're storing under whose Google identity. The "different account?" link signs them out and restarts `/admin/auth/login`.

**Plaud-token validation:**

- `POST /admin/save` calls Plaud's **`/user/me`** endpoint (verified in [03-plaud-api-tos.md](./spike/03-plaud-api-tos.md)) with the submitted token before persisting. On 401, form re-renders with an error and nothing is stored.
- Token regex: `^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$` (rejected at submit before the `/user/me` call).
- `httpx.AsyncClient(timeout=httpx.Timeout(connect=5, read=10, write=5, pool=5))` for the validation call.

**Logging hygiene:**

- App logs: `email_hash` (SHA-256 of lowercased email), `google_sub_prefix` (first 8 chars), `tool_name`, `latency_ms`, `error_code`. Never log Plaud token, MCP JWT, Google ID token, OAuth codes, session cookies, or any secret values.
- **LB access logs cannot redact URL paths** — verified in [04-gcp-and-upstream-verification.md](./spike/04-gcp-and-upstream-verification.md). `optional_mode`/`optional_fields` only toggle TLS metadata + ORCA, not `httpRequest`. This is moot for the OAuth design — Bearer tokens are sent in the `Authorization` header (not logged by default), and authorization codes appear only in the `redirect_uri` callback to claude.ai's domain (not ours). LB log_config matches drive-mcp's pattern: `enable=true; sample_rate=1.0`, no redaction attempted.

**Plaud TOS posture:**

Per [03-plaud-api-tos.md](./spike/03-plaud-api-tos.md), three TOS clauses (reverse-engineering, credential confidentiality, account-sharing) are noted as awareness items. The user-supplied-credential-proxy pattern this design uses is standard SaaS-integration practice (e.g., Calendly, Zapier, Bullhorn integrations) — each user supplies their own `tokenstr` to operate against their own Plaud account; KMS-encrypted at rest; never plaintext-stored; never shared across users. Per `feedback_credential_proxy_no_legal`, this is a CTO judgment call, not a counsel-review gate.

Plaud's official OAuth API (private beta, [waitlist](https://support.plaud.ai/hc/en-us/articles/56061278749209-FAQs-for-Plaud-OAuth-API)) is the long-term migration target. The `tokenstr` proxy is a time-boxed bridge until OAuth waitlist access is granted.

**claude.ai OAuth bug mitigations** (per [02-claude-ai-mcp-behavior.md](./spike/02-claude-ai-mcp-behavior.md)):

- **Dev subdomain** `plaud-mcp-dev.relevantsearch.com` for iteration so misconfig doesn't burn the prod domain's claude.ai connector cache state. Add as managed-cert SAN in Plan 1 Phase 5.
  - **Scope of the dev/prod separation: DNS-only, by design.** Both FQDNs serve the same Cloud Run service, share Firestore, share secrets, share the JWT signing key, share the Google OAuth client (with all four redirect URIs allow-listed). This is intentional — the dev subdomain exists solely to give claude.ai a different connector URL so a config bug burning the dev URL's cache state in claude.ai's connector store doesn't burn the prod URL too. There is no security boundary between the two FQDNs from our backend's perspective. A token registered against the dev URL works against the prod URL and vice versa — that's correct behavior, since they're the same logical environment for the same team.
  - We accept this trade explicitly: separating dev and prod into fully isolated environments (separate Cloud Run, separate Firestore, separate KMS keys, separate secrets, separate Google OAuth client) would double the infra surface for a 10-person team's internal tool. The risk we accept is "if dev backend has a config bug that affects auth or data, prod has the same bug." That's fine — dev isn't a staging environment, it's a DNS escape hatch.
  - If the team grows to a size where dev needs to be a real staging env (different signing keys, different data), that's a v2 design change. Not in scope here.
- **Always emit `WWW-Authenticate: Bearer ...` on 401s.** Defense in depth even though `claude-ai-mcp#219` (case-sensitive header bug) was fixed 2026-04-24.
- **Document Claude Code CLI as fallback** for users who hit `#46140` on web. The CLI doesn't have the bug. User-facing docs include "if claude.ai shows 'tool failed', try Claude Code CLI."
- **Test against MCP Inspector + Claude Code CLI first**, only graduate to claude.ai web after confirming the connector works in CLI. drive-mcp shipped this way and is debugged through the bug; we follow that runbook.

## Lessons applied from prior rs_infra incidents

| Incident | Lesson | Applied how |
|---|---|---|
| PR #82 / #85 — Cloud Run create failed | CMEK mandatory per org policy | Dedicated KMS keys per service before service resources (mirrors drive-mcp) |
| PR #89 — Firestore apply transient failure | IAM binding eventual consistency | `depends_on` from `google_firestore_database` to KMS IAM bindings |
| success-dna #44 — LB 403 on `/api/**` | Need serverless NEG, not Internet NEG | `google_compute_region_network_endpoint_group` with `cloud_run` target |
| PR #90 — Wildcard cert stuck in FAILED | DNS authorization CNAME not created | N/A — `google_compute_managed_ssl_certificate` validates via the LB A record |
| `google_iap_brand`/`client` deprecation (2026-03-19) | Can't auto-create OAuth clients | **Not relevant for this design** — Google OAuth client created manually in Console for the IdP role; no IAP in this design |
| `project_quota_project_bug` (in-flight) | Provider X-Goog-User-Project routing | Mirror drive-mcp's per-component Terragrunt state with provider config pinning billing project |
| ADR-007 | Terragrunt per-component state | New env at `environments/non-production/workspace-integrations/plaud-mcp/` |
| ADR-010 | SOPS for secrets | Google OAuth client + MCP signing key + flow-cookie secret in `secrets.enc.yaml` |
| `feedback_preflight_new_gcp_projects` | Verify perms / org policies / API enables before writing IaC | Pre-flight checklist in Plan 1 Phase 0 |
| `feedback_verify_with_citations` | Empirical verification, not inference | Phase 0 verifies FastMCP version on PlaudNotes upstream, Plaud `/me` endpoint, LB log_config field shape, drive-mcp WIF binding pattern |

## CI/CD

**App repo (`RelevantSearch/PlaudNotes`):**

- GitHub Actions, triggered on push to `main` and semver tags. **Default branch is `main`** (don't replay the drive-mcp `master` mistake).
- Steps: lint (ruff + black) → unit tests → integration tests (Firestore emulator + mocked Google OAuth + mocked Plaud) → docker build → push to Artifact Registry via WIF → `gcloud run deploy --no-traffic` → smoke `/health` → traffic shift to 100%.

**Infra repo (`rs_infra`):**

- Standard Terragrunt `plan`/`apply` per ADR-007.
- First apply manual to bootstrap KMS + Firestore (CLAUDE.md item 6 documented exception); subsequent changes via CI.

## Testing strategy

**App unit tests (TDD, written first):**

- OAuth surface (FastMCP-provided via `GoogleProvider`; we test the integration, not FastMCP itself):
  - `/.well-known/oauth-authorization-server` returns valid AS metadata with `code_challenge_methods_supported: ["S256"]` (mandatory per claude.ai's contract)
  - `FirestoreKeyValue` round-trip: get/set/delete, TTL on authorization codes, FastMCP can store and consume client registrations through it
  - JWT issued by `GoogleProvider` carries `sub = google_sub` after Google sign-in (integration test with stubbed Google OAuth)
  - JWT verification middleware extracts `sub`; rejects expired / wrong issuer / wrong signature; emits `WWW-Authenticate` header on 401
- `/admin` surface (our own code):
  - GET `/admin` with no session → 302 to `/admin/auth/login`
  - GET `/admin/auth/login` → 302 to Google OAuth with separate redirect URI
  - GET `/admin/auth/callback` exchanges code, sets session cookie (signed, HttpOnly, SameSite=Lax, Secure), 302 to `/admin`
  - GET `/admin` with valid session renders form pre-filled with masked existing token (if any) + identity-confirmation step
  - POST `/admin/save` validates token regex, calls Plaud `/user/me` (mocked); 401 → form re-renders, no persistence; 200 → KMS-encrypt + store + render success
  - CSRF: POST without matching double-submit cookie + form field rejected
  - Re-key: second submit for same `google_sub` updates ciphertext, preserves `created_at`
- Token storage layer:
  - KMS encrypt → store → fetch → decrypt round trip
  - In-process LRU cache: hit/miss, TTL expiry, eviction at capacity, invalidation on `PlaudAuthError`, thundering-herd collapse via per-`sub` lock
- Per-request client:
  - Auth-aware middleware on `/mcp`: valid JWT + Plaud token in Firestore → ContextVar populated → tool succeeds
  - Auth-aware middleware: valid JWT + NO Plaud token → returns structured "no_plaud_token" MCP error with `registration_url`, plus `WWW-Authenticate` header
  - httpx timeouts enforced (slow Plaud → fast failure, doesn't block other requests)
  - Concurrent requests with different `sub` are isolated
  - On `PlaudAuthError` from Plaud: cache invalidated for `sub`, structured "no_plaud_token" error returned
- Existing 12 MCP tool tests continue to pass (fixture updates to populate the ContextVar)

**Infra tests:**

- `tofu validate` and `tofu plan` clean in CI before merge
- Post-apply smoke: `curl https://plaud-mcp.relevantsearch.com/.well-known/oauth-authorization-server` returns valid AS metadata JSON
- KMS IAM verified: `gcloud kms keys get-iam-policy plaud-mcp/token` shows runtime SA only

**End-to-end (manual for v1):**

Test against `plaud-mcp-dev.relevantsearch.com` first; only graduate to prod domain after the dev flow is solid (claude.ai connector cache "burn-in" mitigation).

- **Stage 1 — Claude Code CLI (does NOT have the claude.ai OAuth bug):** Stefan adds the connector via `claude mcp add --transport http plaud-mcp-dev https://plaud-mcp-dev.relevantsearch.com/mcp`, runs the OAuth dance, hits the JIT registration error, visits `/admin`, pastes token, retries → real data returned. Validates the architecture independent of claude.ai web bugs.
- **Stage 2 — MCP Inspector:** same flow via `npx @modelcontextprotocol/inspector` to confirm spec-compliant behavior.
- **Stage 3 — claude.ai web (subject to `#46140`):** Stefan adds the connector in claude.ai pointing at `plaud-mcp-dev.relevantsearch.com`. Completes OAuth. If the bug bites (Bearer never sent), document the symptom, retry by removing/re-adding the connector. If reproducible, file an upstream issue with our reproducer.
- Stefan attempts to register with non-Workspace Google account → Google blocks at consent screen.
- Re-key test: Stefan revokes his Plaud token in `web.plaud.ai` → next tool call returns "no_plaud_token" structured error → Stefan visits `/admin` → pastes new token → calls succeed again. claude.ai connector unchanged.
- **Stage 4 — Promote to production domain:** once Stage 3 is solid on `plaud-mcp-dev`, repeat against `plaud-mcp.relevantsearch.com`. Remaining team onboards.

## Observability

- **Structured logs** (Cloud Logging): `email_hash`, `google_sub_prefix` (first 8 chars of SHA-256 for support correlation), `tool_name`, `latency_ms`, `error_code`. Never log JWTs, OAuth codes, Plaud tokens, or full Google subjects.
- **Alerts** (Cloud Monitoring → Slack):
  - 5xx rate > 1% over 5min
  - `PlaudAuthError` rate > 3/min cluster-wide (mass token expiration / Plaud outage signal)
  - "no_plaud_token" structured-error rate spike (onboarding stuck — JIT URL not visible to users?)
  - JWT verification failure rate > 1/min (likely key-rotation issue)
  - `/admin` Google-OAuth-callback failure rate > 1/min
  - Cloud Run cold-start ratio > 10%
  - Plaud `/user/me` call latency > p99 5s during onboarding (Plaud API degraded)
- **SLO:** 99.5% availability (internal tooling).

## Rollout plan

| Phase | Owner | Gate |
|---|---|---|
| 0. Phase 0 verifications complete (see Plan 1 Phase 0) + Plaud OAuth waitlist submitted | Stefan + Platform | All Phase 0 outputs captured; OAuth waitlist confirmation |
| 1. Infra (rs_infra PR series, one per phase) | Platform | `tofu plan` clean per phase, manual first apply for bootstrap, smoke checks pass |
| 2. Google OAuth consent screen + client (SEPARATE client from drive-mcp; new redirect URIs for `/oauth/google/callback` and `/admin/auth/callback`) | Workspace admin | Internal-type consent for `relevantsearch.com`; client ID/secret in SOPS |
| 3. App fork created in `RelevantSearch/PlaudNotes` (NOT under jameshenning) | Platform | Repo exists, default branch `main`, branch protection on, CLAUDE.md committed, FastMCP package migration committed |
| 4. App PRs (one per Plan 2 phase) | Platform | All tests green per phase |
| 5. CI deploy pipeline merged | Platform | First Cloud Run revision serving real container; `/health` 200; `/.well-known/oauth-authorization-server` returns valid metadata |
| 6. Stefan E2E via Claude Code CLI on `plaud-mcp-dev` | Stefan | OAuth + JIT flow completes via CLI; `list_recordings` returns real data |
| 7. Stefan E2E via MCP Inspector on `plaud-mcp-dev` | Stefan | Same flow, spec-compliant tool surface confirmed |
| 8. Stefan E2E via claude.ai web on `plaud-mcp-dev` | Stefan | OAuth + JIT flow completes; if `#46140` bites, document and decide whether to ship anyway with CLI fallback |
| 9. Promote to `plaud-mcp.relevantsearch.com`; 2-3 early testers | Stefan | No issues for 48h on prod domain |
| 10. Org-wide enablement | Stefan | Onboarding doc posted in Slack with screenshots + Claude Code CLI fallback instructions |

## Open questions (status post-research)

1. ✅ **Plaud TOS:** ambiguous per [03-plaud-api-tos.md](./spike/03-plaud-api-tos.md), but per `feedback_credential_proxy_no_legal` the user-supplied-credential-proxy pattern is standard SaaS-integration practice and not a legal-review gate. Plaud OAuth API access is the long-term migration target.
2. ✅ **Plaud `/user/me` endpoint:** verified in `src/plaud_notes_mcp/plaud_client.py:~247` — `GET /user/me` on the user's region base URL (US `https://api.plaud.ai`, EU `https://api-euc1.plaud.ai`). Returns 200 + profile, 401 on bad token.
3. ✅ **Package migration:** PlaudNotes upstream uses in-tree `mcp.server.fastmcp.FastMCP` (`mcp>=1.0.0`); we migrate to standalone `fastmcp>=3.2.4`. Plan 2 Phase 1 handles this.
4. ✅ **Region default:** US confirmed majority; `/admin` form offers radio us/eu, defaults to us. Auto-detection at runtime via Plaud's custom `-302` redirect (already in `plaud_client.py:~106-109`) is a fallback.
5. ✅ **Firestore named-database support:** GA, up to 100 per project, no documented feature gaps. Confirmed in [04-gcp-and-upstream-verification.md](./spike/04-gcp-and-upstream-verification.md).
6. **NEW — Plaud rate limits:** unofficial `tokenstr` API has no published numbers; `plaud_client.py` doesn't even handle 429 (only retries 5xx, 3 attempts). **Must probe empirically** — Stefan runs the curl checklist in [03-plaud-api-tos.md](./spike/03-plaud-api-tos.md) before onboarding the team.
7. **NEW — claude.ai OAuth bug rollout risk:** `#46140` is open and non-deterministic. Stages 1-2 of rollout (Claude Code CLI + MCP Inspector) validate architecture independent of the bug. Stage 3 (claude.ai web) may need retries. Mitigations baked into design.
8. **NEW — Plaud OAuth API waitlist:** submit immediately. Long-term migration target. No blocker for v1.

## Related

- ADR-003 — CMEK / Autokey encryption
- ADR-007 — Terragrunt state management
- ADR-008 — Workload Identity Federation
- ADR-010 — SOPS secrets management
- `docs/projects/2026-04-15-drive-mcp-team/` — sibling MCP, same project; reference architecture for OAuth wrapper, Cloud Run + LB + Firestore + KMS + WIF
- `modules/workspace-integrations-mcp/` — drive-mcp module; this design creates a sibling module that doesn't duplicate the project resource
- Memory: `reference_claude_ai_mcp_auth.md` (claude.ai auth limits — OAuth or no-auth only)

## Changelog

### v3 — 2026-04-30
Folds in findings from four parallel research streams ([01-fastmcp-oauth.md](./spike/01-fastmcp-oauth.md), [02-claude-ai-mcp-behavior.md](./spike/02-claude-ai-mcp-behavior.md), [03-plaud-api-tos.md](./spike/03-plaud-api-tos.md), [04-gcp-and-upstream-verification.md](./spike/04-gcp-and-upstream-verification.md)). Switches to vanilla `GoogleProvider` (drops mid-OAuth-flow Plaud-paste form). Replaces with separate just-in-time `/admin` endpoint. Adds dev subdomain mitigation for claude.ai OAuth bug. Adds Bari TOS review gate. Adds Plaud OAuth waitlist as long-term migration target. Drops "URL log redaction" goal (provider doesn't support it; moot for OAuth design). Pins package migration: in-tree `mcp.server.fastmcp` → standalone `fastmcp>=3.2.4`. Confirms `/user/me` endpoint URL.

### v2 — 2026-04-30
Switched from capability-URL to OAuth wrapper after architect review revealed LOC parity.

### v1 — 2026-04-30
Initial draft (capability-URL design).
