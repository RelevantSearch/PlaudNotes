---
version: 3
---

# Plan 1 — Infrastructure (rs_infra)

**Date:** 2026-04-30
**Status:** Draft
**Parent:** [index.md](./index.md) · [Design](./2026-04-30-plaud-mcp-team-design.md)

## Worktree

- Worktree: `~/git/RS/rs_infra_plaud-mcp-team` (already created off `origin/main`)
- Branch: `plaud-mcp-team`
- Cleanup: `git worktree remove ../rs_infra_plaud-mcp-team` after final PR merge

## Doc tracker

`/track-docs` at execution start. At each phase boundary, respond to phase-boundary findings via SendMessage. At PR creation per phase: send "Ready for PR" and wait for the doc-update commit per CLAUDE.md item 7.

## Scope

Add a sibling module `workspace-integrations-plaud-mcp/` and a new Terragrunt env at `environments/non-production/workspace-integrations/plaud-mcp/` that share the existing `rs-workspace-integrations` project with drive-mcp. No changes to the existing `workspace-integrations-mcp` module.

The structure is deliberately a near-clone of drive-mcp's module, with three deltas:
1. Reads the project via `data "google_project"` instead of creating it
2. Creates a named Firestore database (`plaud-mcp`) instead of `(default)`
3. WIF binding targets `RelevantSearch/PlaudNotes` repo with default branch `main` (vs drive-mcp's `master`)

No IAP, no Cloud Armor, no separate admin host — the OAuth flow at the application layer handles all auth. Same shape as drive-mcp.

## Out of scope (deferred to v2 if ever)

- Production-tier env (only nonprod in v1)
- Custom 4xx pages
- Per-tool rate limits

## Phases

Each phase ships as its own PR per `feedback_pr_review_every_phase`. **No bundling.**

---

### Phase 0 — Pre-flight verification (no code)

Per `feedback_preflight_new_gcp_projects` and `feedback_verify_with_citations`. Empirical verification before writing IaC.

**Status:** Tasks 1-9 below were partially completed by parallel research streams launched 2026-04-30. Findings are in `spike/04-gcp-and-upstream-verification.md`. This plan inlines verified facts; Stefan runs the gcloud commands listed in the spike doc (the agent was permission-blocked) before Phase 1 PR opens.

**Tasks:**

1. **Project state on `rs-workspace-integrations`:**
   - `gcloud projects describe rs-workspace-integrations --format=json`
   - `gcloud services list --enabled --project=rs-workspace-integrations`
   - Required APIs (drive-mcp likely enabled most): `run.googleapis.com`, `firestore.googleapis.com`, `cloudkms.googleapis.com`, `artifactregistry.googleapis.com`, `compute.googleapis.com`, `dns.googleapis.com`, `secretmanager.googleapis.com`, `iamcredentials.googleapis.com`, `cloudresourcemanager.googleapis.com`, `serviceusage.googleapis.com`. **Note:** no `iap.googleapis.com` requirement (this design has no IAP).
   - Anything missing → manually `gcloud services enable` (mirror `project_api_enables_to_import`); follow up with `google_project_service` resources + `terragrunt import`.
2. **Firestore named-database availability:**
   - `gcloud firestore databases list --project=rs-workspace-integrations`
   - Expect `(default)` from drive-mcp; new `plaud-mcp` should be creatable.
3. **DNS zone:**
   - `gcloud dns managed-zones describe relevant-search-main --project=<dns-project>`
   - Verify zone hosts `relevantsearch.com` SOA + NS.
4. **rs-infra CI service account perms** — empirical, no assumptions per `feedback_preflight_new_gcp_projects`:
   - `gcloud projects get-iam-policy rs-workspace-integrations --filter='bindings.members:<ci-sa-email>'`
   - Capture exact role list. Anything missing for KMS keyring create, Cloud Run create, Firestore database create, IAM binding, Secret Manager version create — list explicitly. Add missing bindings in a pre-flight PR before any module work.
5. ✅ **drive-mcp WIF binding pattern — pinned in spike findings.** From `~/git/RS/main/rs_infra/modules/workspace-integrations-mcp/iam.tf:94-98`:
   ```hcl
   resource "google_service_account_iam_member" "deployer_wif_binding" {
     service_account_id = google_service_account.plaud_mcp_deployer.name
     role               = "roles/iam.workloadIdentityUser"
     member             = "principalSet://iam.googleapis.com/projects/${var.wif_project_number}/locations/global/workloadIdentityPools/${var.wif_pool_id}/attribute.repository/${var.github_org}/${var.github_repo}"
   }
   ```
   Pool's `attribute_condition` is `assertion.repository_owner == 'RelevantSearch'` (`~/git/RS/main/rs_infra/modules/wif-github-actions/main.tf:135-146`). Forking under `RelevantSearch/PlaudNotes` (NOT `jameshenning/PlaudNotes`) is required — a `jameshenning/`-owned fork would be rejected. Stefan confirms with `gcloud iam workload-identity-pools providers describe github-provider --location=global --workload-identity-pool=github-pool --project=rs-infra --format='value(attributeCondition)'` before Phase 1 PR.
6. **Plaud TOS — awareness only, no legal-review gate.** Per `feedback_credential_proxy_no_legal`, the user-supplied-credential-proxy pattern is standard SaaS-integration practice. Three TOS clauses (reverse-engineering, credential confidentiality, account-sharing) noted in `spike/03-plaud-api-tos.md` as awareness items but do NOT block rollout. Plaud OAuth API access (waitlist submitted) is the long-term migration target.
7. ✅ **Plaud `/user/me` verified** in `src/plaud_notes_mcp/plaud_client.py:~247`. US base `https://api.plaud.ai`, EU base `https://api-euc1.plaud.ai`. Stefan should still run an empirical curl with own `tokenstr`: `curl -i -H "Authorization: Bearer eyJ..." https://api.plaud.ai/user/me` — should return 200 + JSON profile.
8. ✅ **FastMCP package situation** — PlaudNotes upstream pins `mcp>=1.0.0` (in-tree `mcp.server.fastmcp.FastMCP`, NO standalone `fastmcp` package). Plan 2 Phase 1 migrates to standalone `fastmcp>=3.2.4` package, which is what implements `OAuthProxy` / `GoogleProvider`. The spike at [01-fastmcp-oauth.md](./spike/01-fastmcp-oauth.md) verified the AS pattern empirically against `fastmcp==3.2.4`. Pin that exact version in Plan 2 Phase 1.
9. ✅ **LB log_config — drop the redaction goal.** Per `spike/04-gcp-and-upstream-verification.md`: URL paths cannot be redacted via `optional_mode`/`optional_fields` (these only toggle TLS metadata + ORCA; URL is part of the required `httpRequest` field). drive-mcp's `lb.tf:28-31` uses `enable=true; sample_rate=1.0` with no redaction. Match that pattern. This is moot for the OAuth design — Bearer tokens are in the `Authorization` header (not logged), and authorization codes appear in the `redirect_uri` (claude.ai's domain, not ours).
10. **NEW — Plaud OAuth waitlist** (Stefan): submit application at [waitlist URL](https://support.plaud.ai/hc/en-us/articles/56061278749209-FAQs-for-Plaud-OAuth-API). Long-term migration target; doesn't block v1.
11. **NEW — claude.ai bug awareness** (Platform): bookmark `anthropics/claude-code#46140`, `claude-ai-mcp#155`, `#217`, `#215`, `#227`. Subscribe for status updates. If fixed before our deploy, we get a smoother rollout.

**Acceptance:**

Phase 0 findings appended as comment block to this plan. No code lands without all 9 verifications captured.

**PR review gate:** none (no code). Findings reviewed inline with Stefan before Phase 1 PR opens.

---

### Phase 1 — Module skeleton + variables

**Files:**

- `modules/workspace-integrations-plaud-mcp/`
  - `versions.tf`, `variables.tf`, `outputs.tf`, `main.tf` (project data only), `README.md`, `MODULE.md` (terraform-docs auto-generated)

**Variables** (mirror drive-mcp's `variables.tf` style — every variable has `description` + `type`):

```hcl
variable "project_id"           { type = string }   # rs-workspace-integrations (shared)
variable "folder_id"            { type = string }
variable "ci_service_account"   { type = string }
variable "org_id"               { type = string }
variable "region"               { type = string default = "us-central1" }
variable "service_name"         { type = string default = "plaud-mcp" }
variable "custom_domain"        { type = string }   # plaud-mcp.relevantsearch.com
variable "dev_domain"           { type = string default = "" }   # plaud-mcp-dev.relevantsearch.com (optional; empty = skip)
variable "dns_zone_name"        { type = string }
variable "dns_zone_project"     { type = string }
variable "kms_project_id"       { type = string }
variable "kms_keyring_name"     { type = string default = "plaud-mcp" }
variable "firestore_database_id" { type = string default = "plaud-mcp" }
variable "sops_secrets_file"    { type = string }
variable "image"                { type = string default = "us-docker.pkg.dev/cloudrun/container/hello" }
variable "min_instances"        { type = number default = 1 }
variable "max_instances"        { type = number default = 10 }
variable "github_org"           { type = string default = "RelevantSearch" }
variable "github_repo"          { type = string default = "PlaudNotes" }
variable "wif_project_number"   { type = string }
variable "wif_pool_id"          { type = string default = "github-pool" }
```

**Acceptance:** `tofu init` and `tofu validate` succeed in the env (when Phase 7 brings env config online); module README + MODULE.md render in GitHub.

**PR review gate:** Phase 1 ships as its own PR.

---

### Phase 2 — KMS keyring + keys + IAM

**File:** `modules/workspace-integrations-plaud-mcp/kms.tf` — clone drive-mcp's `kms.tf` shape.

Four keys:
- `cloud_run` — Cloud Run CMEK
- `firestore` — Firestore CMEK
- `secrets` — Secret Manager CMEK
- `token` — app-level envelope encryption of Plaud tokens

For each key: `google_kms_crypto_key` `for_each` over the four names; IAM bindings as in drive-mcp:

- Cloud Run service identity → `cryptoKeyEncrypterDecrypter` on `cloud_run`
- Artifact Registry service identity → same on `cloud_run`
- Firestore service identity → same on `firestore`
- Secret Manager service identity → same on `secrets`
- Runtime SA `plaud-mcp@...` (Phase 4) → same on `token` only
- CI SA `var.ci_service_account` → `roles/cloudkms.admin` on the keyring

**Acceptance:** `tofu plan` shows four keys + IAM with no spurious diff.

**PR review gate:** Phase 2 ships as its own PR.

---

### Phase 3 — Firestore database + Artifact Registry repo

**Files:**
- `modules/workspace-integrations-plaud-mcp/firestore.tf`
- `modules/workspace-integrations-plaud-mcp/artifact_registry.tf`

**Firestore:**

```hcl
resource "google_firestore_database" "plaud_mcp" {
  project                     = var.project_id
  name                        = var.firestore_database_id   # "plaud-mcp"
  location_id                 = var.region
  type                        = "FIRESTORE_NATIVE"
  cmek_config { kms_key_name = google_kms_crypto_key.key["firestore"].id }
  point_in_time_recovery_enablement = "POINT_IN_TIME_RECOVERY_ENABLED"
  depends_on = [google_kms_crypto_key_iam_member.firestore_sa]
}
```

**Artifact Registry:** mirror drive-mcp; repo id `plaud-mcp`, CMEK with `cloud_run` key, depends on KMS IAM.

**Acceptance:** `tofu plan` shows both resources, no diff after apply.

**PR review gate:** Phase 3 ships as its own PR.

---

### Phase 4 — Service accounts + WIF + Cloud Run service

**Files:**
- `modules/workspace-integrations-plaud-mcp/iam.tf`
- `modules/workspace-integrations-plaud-mcp/main.tf` (Cloud Run resource)

**Service accounts:**

- `plaud-mcp@<project>.iam.gserviceaccount.com` — runtime SA
  - `roles/datastore.user` on `var.project_id`
  - `roles/logging.logWriter` on `var.project_id`
  - `roles/cloudkms.cryptoKeyEncrypterDecrypter` on `token` key only
  - `roles/secretmanager.secretAccessor` on each plaud-mcp secret (Phase 6)
- `plaud-mcp-deployer@<project>.iam.gserviceaccount.com` — CI deployer SA
  - `roles/run.developer` on `var.project_id`
  - `roles/iam.serviceAccountUser` on `plaud-mcp@<project>` (to set Cloud Run runtime SA)
  - `roles/artifactregistry.writer` on the `plaud-mcp` repo

**WIF principalSet binding** — copy verbatim from Phase 0 task 5 capture:

```hcl
# Pinned shape from drive-mcp PR #155 (Phase 0 task 5):
resource "google_service_account_iam_member" "wif_deployer" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/projects/${var.wif_project_number}/locations/global/workloadIdentityPools/${var.wif_pool_id}/attribute.repository/${var.github_org}/${var.github_repo}"
}
```

If Phase 0 finds drive-mcp uses a different shape (e.g., per-branch attribute condition), substitute that shape verbatim.

**Cloud Run** — clone drive-mcp `google_cloud_run_v2_service.drive_mcp` with these deltas:

- `name = "plaud-mcp"`, service_account = `plaud-mcp@...`
- Plain env vars (set in Phase 4):
  - `GCP_PROJECT_ID` = var.project_id
  - `FIRESTORE_DATABASE` = `plaud-mcp`
  - `KMS_TOKEN_KEY` = `token` key resource ID
  - `PUBLIC_URL` = `https://${var.custom_domain}`
- Secret-backed env vars are added in Phase 6 (`GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `JWT_SIGNING_KEY`, `SESSION_SECRET`). **The Phase 6 patch must declare the FULL env list** including these four plain env vars — Cloud Run env vars are REPLACE semantics in Terraform. See Phase 6 callout.
- `lifecycle.ignore_changes` mirrors drive-mcp: `client`, `client_version`, `template[0].containers[0].image`, `scaling`. **Do NOT add env vars to ignore_changes** — Phase 6 must update them.
- `allUsersIngress=True` tag binding — already on the project from drive-mcp; verified in Phase 0
- `roles/run.invoker` for `allUsers` — auth handled in-app by FastMCP's TokenVerifier

**Acceptance:** `tofu apply` provisions Cloud Run with placeholder hello image; `gcloud run services describe plaud-mcp --region=us-central1` returns the service.

**PR review gate:** Phase 4 ships as its own PR.

---

### Phase 5 — Load Balancer + DNS + managed cert (prod + dev SAN)

**Files:** `modules/workspace-integrations-plaud-mcp/lb.tf`, `dns.tf` — mirror drive-mcp's `lb.tf` exactly.

- `google_compute_region_network_endpoint_group "plaud_mcp"` (serverless NEG → Cloud Run)
- `google_compute_backend_service "plaud_mcp"` — backend NEG, both prod and dev hosts share this single backend
- `google_compute_url_map "plaud_mcp"` — default backend is `plaud_mcp` for both hosts (no host-based routing needed — same Cloud Run service responds on both FQDNs)
- `google_compute_managed_ssl_certificate` with `managed.domains = compact([var.custom_domain, var.dev_domain])` — both prod and dev FQDNs as SANs on a single certificate
- `google_compute_target_https_proxy` + `google_compute_global_forwarding_rule`
- `google_compute_global_address` for the LB IP
- `google_dns_record_set` A records for both `var.custom_domain` AND `var.dev_domain` (when non-empty), both pointing at the same LB IP. Use `for_each` over a compact list.

**LB log_config — match drive-mcp pattern exactly (NO redaction attempted):**

```hcl
log_config {
  enable      = true
  sample_rate = 1.0
}
```

URL paths cannot be redacted via `optional_mode`/`optional_fields` per `spike/04-gcp-and-upstream-verification.md`. This is moot for the OAuth design — Bearer tokens go in the `Authorization` header (not logged), authorization codes appear only in claude.ai's `redirect_uri` (claude.ai's domain, not ours). Cited at `~/git/RS/main/rs_infra/modules/workspace-integrations-mcp/lb.tf:28-31`.

**Acceptance:**

- `curl https://plaud-mcp.relevantsearch.com` returns 200 from placeholder hello
- `curl https://plaud-mcp-dev.relevantsearch.com` returns 200 from same placeholder
- Cert provisions to ACTIVE within 30 min of DNS propagation (both SANs ACTIVE)
- LB access logs visible in Cloud Logging

**PR review gate:** Phase 5 ships as its own PR.

---

### Phase 6 — Secrets (Secret Manager + SOPS) + Cloud Run env wiring

**Pre-step (manual, before any IaC in this phase):**

- Create Google OAuth client for the Plaud-MCP IdP role (SEPARATE from drive-mcp's — different redirect URIs):
  - Cloud Console → APIs & Services → OAuth consent screen → ensure Internal type for Workspace `relevantsearch.com` (already configured for drive-mcp; verify scopes `openid email` requested)
  - Create OAuth client ID (Web app)
  - Authorized redirect URIs (FOUR total — both prod and dev × both `/oauth/google/callback` and `/admin/auth/callback`):
    - `https://plaud-mcp.relevantsearch.com/oauth/google/callback`
    - `https://plaud-mcp.relevantsearch.com/admin/auth/callback`
    - `https://plaud-mcp-dev.relevantsearch.com/oauth/google/callback`
    - `https://plaud-mcp-dev.relevantsearch.com/admin/auth/callback`
  - Capture Client ID + Client Secret → SOPS-encrypted into `secrets.enc.yaml`
- Generate two random secrets:
  - `jwt_signing_key`: `python -c "import secrets; print(secrets.token_urlsafe(32))"` → SOPS (FastMCP signs issued JWTs with this)
  - `session_secret`: `python -c "import secrets; print(secrets.token_urlsafe(32))"` → SOPS (signs `/admin` session cookie + CSRF double-submit tokens)

**Files:**

- `modules/workspace-integrations-plaud-mcp/secrets.tf`
- `environments/non-production/workspace-integrations/plaud-mcp/secrets.enc.yaml` (SOPS-encrypted, never committed in plaintext)
- Patch to `modules/workspace-integrations-plaud-mcp/main.tf` adding secret_key_ref env vars to Cloud Run

**Resources:**

- `data "sops_file" "secrets"` reading the SOPS file
- `google_secret_manager_secret` for each (CMEK with `secrets` key): `plaud-mcp-google-oauth-client-id`, `plaud-mcp-google-oauth-client-secret`, `plaud-mcp-jwt-signing-key`, `plaud-mcp-session-secret`
- `google_secret_manager_secret_version` per secret, value from SOPS
- `google_secret_manager_secret_iam_member` granting `roles/secretmanager.secretAccessor` to the runtime SA on each
- Cloud Run env var update via `value_source.secret_key_ref` (mirror drive-mcp `main.tf:60-87`):
  - `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `JWT_SIGNING_KEY`, `SESSION_SECRET`

**⚠️ Cloud Run env vars are REPLACE semantics in Terraform** (verified in `spike/04-gcp-and-upstream-verification.md`). The Phase 6 patch to `main.tf` must declare the **full** env list — both Phase 4's plain env vars (`GCP_PROJECT_ID`, `FIRESTORE_DATABASE`, `KMS_TOKEN_KEY`, `PUBLIC_URL`) AND the new secret-backed ones. If we only declare the new ones, the Phase 4 ones get removed on apply, and the container fails to start. Plan 1 Phase 6 PR must show a diff that adds env vars without removing existing ones (verify by reading the post-Phase-4 Cloud Run definition before drafting Phase 6).

**Acceptance:**

- All four secrets exist with `gcloud secrets versions list ...`
- Cloud Run revision has the four secret-backed env vars (verify with `gcloud run revisions describe`)
- Runtime SA can read each secret (`gcloud secrets versions access latest --secret=plaud-mcp-jwt-signing-key --impersonate-service-account=plaud-mcp@...`)

**PR review gate:** Phase 6 ships as its own PR.

---

### Phase 7 — Terragrunt env config + first apply

**Files:**

- `environments/non-production/workspace-integrations/plaud-mcp/terragrunt.hcl`

`terragrunt.hcl` mirrors drive-mcp's env config:

```hcl
include "root" { path = find_in_parent_folders() }
terraform {
  source = "../../../../modules/workspace-integrations-plaud-mcp"
}
inputs = {
  project_id         = "rs-workspace-integrations"
  folder_id          = local.nonprod_folder_id
  ci_service_account = local.ci_sa
  org_id             = local.org_id
  custom_domain      = "plaud-mcp.relevantsearch.com"
  dev_domain         = "plaud-mcp-dev.relevantsearch.com"
  dns_zone_name      = "relevant-search-main"
  dns_zone_project   = local.dns_project
  kms_project_id     = "kms-proj-a9dncstlc3zg"
  wif_project_number = local.rs_infra_project_number
  sops_secrets_file  = "${get_terragrunt_dir()}/secrets.enc.yaml"
}
```

**First apply** (CLAUDE.md item 6 documented bootstrap exception):

```
terragrunt init
terragrunt plan -out=plan.tfplan
terragrunt apply plan.tfplan
```

**Acceptance:**

All resources from Phases 2-6 created. End-to-end smoke checks pass:
- `curl https://plaud-mcp.relevantsearch.com/health` returns whatever the placeholder serves (real `/health` arrives in Plan 2)
- `curl https://plaud-mcp.relevantsearch.com/.well-known/oauth-authorization-server` returns 404 from the placeholder (will return JSON once Plan 2 deploys)

**PR review gate:** Phase 7 is the final PR for Plan 1.

---

## Critical review

Before each substantive PR, invoke the unforgiving Principal Architect review subagent (CLAUDE.md item 4) on the diff. Address all CRITICAL and MAJOR before pushing.

## PR strategy

- **One PR per phase.** No bundling.
- Each PR: Summary + Test Plan, conventional commits, atomic
- Post-push: monitor `gh pr checks --watch`, reply inline to all comments per `feedback_reply_inline_pr_comments` and `feedback_check_pr_comments_proactively`
- Never merge / approve / close — Stefan does

## Doc tracker

`/track-docs` at execution start. At each phase boundary respond via SendMessage. Pre-PR, send "Ready for PR" and wait for doc-update commit.

## Changelog

### v3 — 2026-04-30
Folds in spike findings. Phase 0 tasks 5/7/8/9 marked verified with citations. Phase 5 drops URL log-redaction goal (provider-impossible per spike), matches drive-mcp's bare `enable=true; sample_rate=1.0` pattern. Phase 5 adds `plaud-mcp-dev.relevantsearch.com` as managed-cert SAN for claude.ai bug iteration. Phase 6 secret names corrected: `jwt-signing-key` (was `mcp-signing-key`), `session-secret` (was `flow-cookie-secret`). Phase 6 adds explicit Cloud Run REPLACE-semantics callout (declare full env list, not just additions). Adds Bari TOS engagement and Plaud OAuth waitlist tasks to Phase 0.

### v2 — 2026-04-30
Rewritten for OAuth wrapper design. Drops Phase 6 (IAP attachment), Phase 7 (Cloud Armor) from v1.

### v1 — 2026-04-30
Initial draft (capability-URL design — superseded).
