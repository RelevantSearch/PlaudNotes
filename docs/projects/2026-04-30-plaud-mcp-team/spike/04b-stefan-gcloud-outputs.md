---
version: 1
status: PLACEHOLDER — Stefan to populate before Phase 1 PR opens
---

# Phase 0 — Stefan's gcloud verification outputs

The Stream D agent was permission-blocked from running `gcloud` against `rs-infra` and `rs-workspace-integrations`. This file captures the empirical outputs Stefan runs once. Each section has a command and a paste target.

> **Why this exists:** `feedback_preflight_new_gcp_projects` and `feedback_verify_with_citations` — verify before writing IaC, never assume IaC and runtime are in sync. Per `feedback_no_compound_commands` each command is its own line.

## 1. WIF pool attribute_condition (verifies fork ownership requirement)

```bash
gcloud iam workload-identity-pools providers describe github-provider \
  --location=global \
  --workload-identity-pool=github-pool \
  --project=rs-infra \
  --format='value(attributeCondition)'
```

**Expected output:** `assertion.repository_owner == 'RelevantSearch'`

**If different from expected:** flag in chat — the fork shape needs adjustment.

**Stefan output (paste here):**

```
TODO
```

## 2. rs-workspace-integrations project state

```bash
gcloud projects describe rs-workspace-integrations --format=json
```

**Stefan output (paste here):**

```
TODO
```

## 3. Enabled APIs

```bash
gcloud services list --enabled --project=rs-workspace-integrations --format='value(NAME)'
```

**Required APIs (Plan 1 Phase 0 task 1):** `run.googleapis.com`, `firestore.googleapis.com`, `cloudkms.googleapis.com`, `artifactregistry.googleapis.com`, `compute.googleapis.com`, `dns.googleapis.com`, `secretmanager.googleapis.com`, `iamcredentials.googleapis.com`, `cloudresourcemanager.googleapis.com`, `serviceusage.googleapis.com`.

**If any missing:** manually `gcloud services enable <name>` per `project_api_enables_to_import` pattern, then add `google_project_service` resources to the new module + `terragrunt import` after Phase 1.

**Stefan output (paste here):**

```
TODO
```

## 4. Firestore databases

```bash
gcloud firestore databases list --project=rs-workspace-integrations --format='value(name)'
```

**Expected:** `(default)` only (drive-mcp's). New `plaud-mcp` will be creatable.

**Stefan output (paste here):**

```
TODO
```

## 5. CI service account roles

```bash
gcloud projects get-iam-policy rs-workspace-integrations \
  --filter='bindings.members:terraform-github-actions@rs-infra-484217.iam.gserviceaccount.com' \
  --flatten='bindings[].members' \
  --format='value(bindings.role)'
```

**Expected (per Stream D inference from drive-mcp inputs):** `artifactregistry.admin`, `iam.serviceAccountUser` on runtime SA, `compute.loadBalancerAdmin`, plus org-level roles via WIF.

**If gaps exist for KMS keyring create / Cloud Run create / Firestore create / IAM binding / Secret Manager / DNS:** add a pre-flight PR before Plan 1 Phase 1.

**Stefan output (paste here):**

```
TODO
```

## 6. Plaud `/user/me` empirical curl

```bash
curl -i -H "Authorization: Bearer eyJ..." https://api.plaud.ai/user/me
```

(Substitute your own `tokenstr` for `eyJ...` — capture from `web.plaud.ai` localStorage `tokenstr` key, strip the `bearer ` prefix.)

**Expected:** HTTP 200 + JSON profile body. 401 means token wrong / expired.

**Stefan output (paste here, redact token!):**

```
TODO
```

---

## Status

- [ ] Task 1 — WIF condition captured
- [ ] Task 2 — Project state captured
- [ ] Task 3 — Enabled APIs captured + gaps noted
- [ ] Task 4 — Firestore DBs captured
- [ ] Task 5 — CI SA roles captured + gaps noted
- [ ] Task 6 — Plaud `/user/me` confirmed 200

When all 6 ✓: Plan 1 Phase 1 PR can open.
