---
version: 1
---

# Phase 0 GCP + Upstream Verification

**Date:** 2026-04-30
**Scope:** Empirical capture for plaud-mcp-team Phase 0. Per `feedback_verify_with_citations`, every claim is backed by a file:line citation, gh API output, or vendor doc URL. Confidence labelled per finding.

> **Note on gcloud commands.** Direct `gcloud` invocations against `rs-infra` and `rs-workspace-integrations` were blocked by sandbox permissions in this session, so Tasks 1 (provider attribute condition) and 5 (project state) are sourced from rs_infra IaC + Terragrunt inputs (the source of truth, since `manage_pool=true` is set there). Where IaC and runtime can drift (project labels, enabled APIs, Firestore DB list, IAM policy bindings), I have flagged a residual MEDIUM verification debt.

---

## Task 1 — drive-mcp WIF binding shape (HIGH confidence on shape; MEDIUM on live attribute_condition)

### FINDING — exact resource block

The drive-mcp module binds `roles/iam.workloadIdentityUser` to the deployer SA via `principalSet` keyed on `attribute.repository`. Use this verbatim with `github_repo = "PlaudNotes"`, `github_org = "jameshenning"` (note: PlaudNotes is owned by `jameshenning`, NOT the `RelevantSearch` org default), `wif_project_number = "547220269732"`, `wif_pool_id = "github-pool"`.

```hcl
# modules/workspace-integrations-mcp/iam.tf:94-98 — copy verbatim into new module
resource "google_service_account_iam_member" "deployer_wif_binding" {
  service_account_id = google_service_account.plaud_mcp_deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/projects/${var.wif_project_number}/locations/global/workloadIdentityPools/${var.wif_pool_id}/attribute.repository/${var.github_org}/${var.github_repo}"
}
```

EVIDENCE: `~/git/RS/main/rs_infra/modules/workspace-integrations-mcp/iam.tf:94-98`. Variables defined at `~/git/RS/main/rs_infra/modules/workspace-integrations-mcp/variables.tf:80-101`. Production-set values at `~/git/RS/main/rs_infra/environments/non-production/workspace-integrations/drive-mcp/terragrunt.hcl:54-57`.

### FINDING — pool's attribute_mapping accepts BOTH `attribute.repository` and `attribute.repository_owner`

```hcl
# modules/wif-github-actions/main.tf:135-142
attribute_mapping = {
  "google.subject"             = "assertion.sub"
  "attribute.actor"            = "assertion.actor"
  "attribute.repository"       = "assertion.repository"
  "attribute.repository_owner" = "assertion.repository_owner"
}
attribute_condition = "assertion.repository_owner == '${var.github_org}'"
```

EVIDENCE: `~/git/RS/main/rs_infra/modules/wif-github-actions/main.tf:135-146`.

### IMPLICATION — CRITICAL for PlaudNotes

The `attribute_condition` on the pool provider is `assertion.repository_owner == '${var.github_org}'`. The owner that managed the pool was `RelevantSearch` (per `terraform-github-actions` SA conventions and `feedback_*` references). PlaudNotes is owned by `jameshenning`, which means **the existing condition will REJECT OIDC tokens from `jameshenning/PlaudNotes`** unless one of:

1. The fork is moved/created under `RelevantSearch/PlaudNotes` (recommended, matches drive-mcp pattern), OR
2. The pool provider's `attribute_condition` is broadened to `assertion.repository_owner in ['RelevantSearch', 'jameshenning']`, OR
3. A second WIF provider is added to the pool with a `jameshenning` condition.

CONFIDENCE: HIGH on requirement that condition must allow the token's `repository_owner`. MEDIUM on the *live* condition string in `rs-infra` (sourced from IaC, not from `gcloud iam workload-identity-pools providers describe github-provider`). **Action**: confirm with `gcloud iam workload-identity-pools providers describe github-provider --location=global --workload-identity-pool=github-pool --project=rs-infra --format='value(attributeCondition)'` before Phase 1 PR. If a fork under `RelevantSearch/PlaudNotes` is the path forward, this verification becomes moot.

EVIDENCE for repo ownership: `gh repo view jameshenning/PlaudNotes --json defaultBranchRef,name,description` returned `{"defaultBranchRef":{"name":"main"},"description":"Plaude Notes to Claude MCP","name":"PlaudNotes"}`. Default branch is `main` (matches plan-1 assumption).

---

## Task 2 — LB log_config shape (HIGH)

### FINDING — drive-mcp does NOT redact URL paths today

```hcl
# modules/workspace-integrations-mcp/lb.tf:28-31 — copy verbatim
log_config {
  enable      = true
  sample_rate = 1.0
}
```

`optional_mode` and `optional_fields` are absent. Default behaviour applies.

EVIDENCE: `~/git/RS/main/rs_infra/modules/workspace-integrations-mcp/lb.tf:28-31`.

### FINDING — `optional_mode`/`optional_fields` cannot suppress URL paths

URL/path is part of the **required** `httpRequest` log entry field, which `optional_mode` does not control. Optional fields are limited to `tls.protocol`, `tls.cipher`, and ORCA load metrics.

EVIDENCE: <https://docs.cloud.google.com/load-balancing/docs/https/https-logging-monitoring> — "Optional fields...limited to `tls.protocol`, `tls.cipher`, ORCA load report elements." `optional_mode` accepts `INCLUDE_ALL_OPTIONAL`, `EXCLUDE_ALL_OPTIONAL` (default), `CUSTOM` with `optional_fields` as **allow-list**.

### IMPLICATION

If the plan calls for "suppress URL-bearing fields on access logs", this is a misunderstanding of GCP LB logging. URL paths are not configurable via `log_config`. Options:
- Disable LB access logs entirely (`enable = false`) — loses ALL access log telemetry.
- Use a Cloud Logging exclusion filter or log sink with `jsonPayload`/`httpRequest` redaction at the sink — out of scope of `google_compute_backend_service`.
- Accept the default and rely on the fact that `/messages`/`/sse` MCP routes don't carry secrets in path.

Recommend documenting this and matching drive-mcp shape (no redaction).

### FINDING — provider versions to pin

```hcl
# modules/workspace-integrations-mcp/versions.tf
required_version = ">= 1.8.0"
google       = "~> 6.0"
google-beta  = "~> 6.0"
sops         = "~> 1.1"
```

EVIDENCE: `~/git/RS/main/rs_infra/modules/workspace-integrations-mcp/versions.tf:1-17`. Pin identical in plaud module.

CONFIDENCE: HIGH.

---

## Task 3 — Firestore named-database support (HIGH)

### FINDING

- Multiple databases per project is GA in Native mode; up to 100 databases per project.
- Named DBs use the same SDKs (you pass `databaseId` when constructing the client).
- `google_firestore_database.name` accepts arbitrary database IDs (`(default)` or any other ID).
- No documented feature gaps, no documented pricing premium for named vs `(default)` (storage and ops priced identically per Firestore SKU).

EVIDENCE:
- <https://docs.cloud.google.com/firestore/docs/manage-databases> — "You can create multiple Firestore databases per project... maximum of 100 databases per project."
- <https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/firestore_database> — `name` is the DATABASE_ID (arbitrary string).
- Existing drive-mcp resource: `~/git/RS/main/rs_infra/modules/workspace-integrations-mcp/firestore.tf:1-18` uses `name = "(default)"`. The plaud module sets `name = "plaud-mcp"`, same project.

### Existing drive-mcp Firestore (reference shape)

```hcl
# firestore.tf:1-18 — pattern to mirror, swapping name = "plaud-mcp"
resource "google_firestore_database" "drive_mcp" {
  project                     = var.project_id
  name                        = "(default)"
  location_id                 = var.region
  type                        = "FIRESTORE_NATIVE"
  concurrency_mode            = "OPTIMISTIC"
  app_engine_integration_mode = "DISABLED"
  deletion_policy             = "DELETE_PROTECTION_ENABLED"
  cmek_config { kms_key_name = google_kms_crypto_key.key["firestore"].id }
  depends_on = [google_kms_crypto_key_iam_member.firestore_sa, google_project_service.project]
}
```

CONFIDENCE: HIGH on GA + provider support. MEDIUM on no-pricing-difference claim (no explicit GCP source; absence of evidence rather than confirmed parity).

---

## Task 4 — PlaudNotes upstream state (HIGH)

### FINDING — pinned deps (from `pyproject.toml`)

```toml
requires-python = ">=3.10"
dependencies = [
    "mcp>=1.0.0",
    "httpx>=0.27.0",
    "pydantic>=2.0.0",
    "python-dotenv>=1.0.0",
    "uvicorn>=0.27.0",
]
[project.optional-dependencies]
cli = ["mcp[cli]>=1.0.0"]
```

EVIDENCE: `gh api repos/jameshenning/PlaudNotes/contents/pyproject.toml`.

### FINDING — uses FastMCP from `mcp` SDK, NOT standalone `fastmcp` package

`server.py:17` imports `from mcp.server.fastmcp import FastMCP`. This is the in-tree FastMCP shipped inside the `mcp` Python SDK, not the separate `fastmcp` PyPI package (jlowin's project). FastMCP 3.x is a *separate* project. Upstream PlaudNotes is on `mcp>=1.0.0` only.

EVIDENCE: `gh api repos/jameshenning/PlaudNotes/contents/src/plaud_notes_mcp/server.py` line 17.

### FINDING — auth already present

PlaudNotes has its own `APIKeyMiddleware` in `server.py:30-58`: SHA-256 + HMAC constant-time Bearer-token check, env var `PLAUD_MCP_API_KEY`. Single shared key, not per-user OAuth — incompatible with drive-mcp's per-user OAuth pattern. The plaud-mcp-team plan needs to either keep this static-key model or replace with the FastMCP OAuth pattern from `spike/fastmcp-oauth/`.

EVIDENCE: `server.py:30-58` (gh api dump above).

### FINDING — Dockerfile defaults to HTTP transport on port 8000, non-root `mcp` user

EVIDENCE: `gh api repos/jameshenning/PlaudNotes/contents/Dockerfile`. Sets `PLAUD_TRANSPORT=http`, `PLAUD_MCP_PORT=8000`, `PLAUD_MCP_HOST=0.0.0.0`, runs as user `mcp`. Cloud Run-compatible (port 8000, listens on 0.0.0.0).

### FINDING — repo state

- Default branch: `main` (matches plan).
- Repo root: `.env.example`, `Dockerfile`, `LICENSE`, `Procfile`, `RAILWAY_TEMPLATE.md`, `README.md`, `SECURITY_SETUP.md`, `article/`, `docker-compose.yml`, `fly.toml`, `pyproject.toml`, `railway.json`, `railway.toml`, `src/`. **No CLAUDE.md, no CONTRIBUTING.md, no CHANGELOG.md.**
- `src/plaud_notes_mcp/`: `__init__.py`, `plaud_client.py`, `server.py` (563 lines).

EVIDENCE: `gh api repos/jameshenning/PlaudNotes/contents` and `gh api repos/jameshenning/PlaudNotes/contents/src/plaud_notes_mcp`.

### IMPLICATION — FastMCP version decision

If the plaud-mcp-team plan needs OAuth (`spike/fastmcp-oauth/` pattern), upgrade is required from `mcp.server.fastmcp` (in-tree, Bearer-only) to `fastmcp>=2.x` (jlowin's package, OAuth helpers) or write OAuth on top of `mcp` SDK manually. Note: FastMCP 3.x discussion is premature — the upgrade is from `mcp` in-tree FastMCP to the standalone `fastmcp` package; the standalone is currently 2.x GA.

CONFIDENCE: HIGH on what's pinned and how auth is wired today. MEDIUM on FastMCP 3.x roadmap (didn't query PyPI in this spike).

---

## Task 5 — rs-workspace-integrations project state (LOW-MEDIUM, IaC-sourced)

> gcloud blocked in this session. Below is sourced from Terragrunt inputs and committed IaC. **Phase 0 must run the gcloud commands before Phase 1 PR is opened** to confirm runtime state matches.

### FINDING — Project identity (from IaC)

- `project_id = "rs-workspace-integrations"`
- `folder_id = "685403508985"` (Non-Production)
- `org_id = "922644136992"`
- Project comment: "Project moved manually to Non-Production folder on 2026-04-19. Billing linked manually — not Terraform-managed (CI SA lacks perms)."

EVIDENCE: `~/git/RS/main/rs_infra/environments/non-production/workspace-integrations/drive-mcp/terragrunt.hcl:30-34, 17-18`.

### FINDING — CI service account

`terraform-github-actions@rs-infra-484217.iam.gserviceaccount.com` is the CI SA passed as `ci_service_account`. The drive-mcp module grants it: `roles/artifactregistry.admin` (project), `roles/iam.serviceAccountUser` on the runtime SA, `roles/compute.loadBalancerAdmin` (project).

EVIDENCE: `~/git/RS/main/rs_infra/modules/workspace-integrations-mcp/iam.tf:32-48`.

The plaud module needs the SAME three grants on the CI SA, plus whatever org-level roles already exist on this SA from `~/git/RS/main/rs_infra/modules/wif-github-actions/main.tf:54-87` (folderAdmin, projectCreator/Deleter, orgpolicy.policyAdmin, billing.user, compute.{xpnAdmin,networkAdmin,securityAdmin}, iam.securityAdmin, logging.admin, monitoring.admin, cloudkms.admin, cloudkms.cryptoKeyEncrypterDecrypter, storage.admin, firebase.admin, dns.admin, secretmanager.admin, serviceusage.serviceUsageAdmin, iam.serviceAccountCreator, resourcemanager.tagAdmin/tagUser, spanner.admin, run.admin, cloudscheduler.admin, pubsub.admin).

### RESIDUAL VERIFICATION DEBT (Phase 0 must close before Phase 1 PR)

Run these and capture output in this doc:

```bash
gcloud projects describe rs-workspace-integrations --format=json
gcloud services list --enabled --project=rs-workspace-integrations
gcloud firestore databases list --project=rs-workspace-integrations
gcloud projects get-iam-policy rs-workspace-integrations \
  --flatten=bindings --filter='bindings.members:terragrunt-ci'
gcloud iam workload-identity-pools providers describe github-provider \
  --location=global --workload-identity-pool=github-pool --project=rs-infra \
  --format='value(attributeCondition,attributeMapping)'
```

Per `feedback_preflight_new_gcp_projects` and `feedback_verify_with_citations`: do not assume IaC-declared state matches runtime state, especially given the manual-billing/manual-folder-move history.

CONFIDENCE: HIGH on what IaC declares; LOW on runtime drift until gcloud is run.

---

## Task 6 — Cloud Run revision update behaviour (HIGH)

### FINDING

- Adding an env var (incl. `secret_key_ref` reference) to a deployed Cloud Run v2 service creates a **new revision automatically**. "Any configuration change leads to the creation of a new revision."
- `--update-env-vars` is non-destructive (adds/modifies, preserves others). `--set-env-vars` is destructive (replaces full set). For Terraform's `google_cloud_run_v2_service`, the resource declares the full `env` list, so behaviour is equivalent to `--set-env-vars` semantics: declared list replaces previous list. Plan 1 Phase 6 must therefore declare ALL env vars (Phase 4 + Phase 6 additions), not just the new ones.
- Traffic routes to the new revision per the service's traffic block (default 100% latest).

EVIDENCE: <https://docs.cloud.google.com/run/docs/configuring/services/environment-variables>.

### IMPLICATION

Plan 1 Phase 6 must merge env vars into the existing `env { ... }` block of the Cloud Run resource and re-apply. No partial-config gotcha as long as the full env list is preserved in Terraform. Watch for: secret rotation latency (Secret Manager `latest` reference resolves at revision creation time, not at runtime — a rotated secret needs a new revision).

CONFIDENCE: HIGH.

---

## Summary — items the plan can rely on without further work

1. WIF binding block + `attribute_mapping` shape (paste verbatim from drive-mcp).
2. Provider pin (`google ~> 6.0`, `google-beta ~> 6.0`, `sops ~> 1.1`, TF `>= 1.8.0`).
3. Firestore named DB: GA, `name = "plaud-mcp"` works, no SDK gaps.
4. PlaudNotes default branch = `main`, uses in-tree `mcp.server.fastmcp.FastMCP`, has its own static-Bearer auth.
5. Cloud Run v2 auto-revisions on any env-var change; declare full env list in TF.

## Summary — items the plan must address before Phase 1 PR

1. **Pool `attribute_condition`** restricts `repository_owner`. Either fork PlaudNotes under `RelevantSearch` org, broaden the condition, or add a second provider. Decide before module is written.
2. **LB URL-path redaction** is not achievable via `log_config.optional_mode/optional_fields`. Drop the requirement or implement at the log sink level.
3. **Run the five gcloud commands** in Task 5 to capture runtime state of `rs-workspace-integrations`.
4. **Auth model**: PlaudNotes ships static-Bearer auth; reconcile with `spike/fastmcp-oauth/` pattern before the app PR (Plan 2).

## Changelog

### v1 — 2026-04-30

Initial Phase 0 verification: WIF block, LB log_config shape, Firestore named DB, PlaudNotes upstream state, workspace-integrations IaC-sourced state, Cloud Run revision behaviour. gcloud-blocked items flagged with residual verification debt.
