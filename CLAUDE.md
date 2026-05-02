# CLAUDE.md — RelevantSearch/PlaudNotes

Fork of `jameshenning/PlaudNotes` adapting the Plaud Notes MCP server for team-wide hosting on Cloud Run with OAuth 2.1 authentication.

## Universal rules

This repo inherits the universal rules from the monorepo root at `~/git/RS/CLAUDE.md`: worktree strategy, PR strategy, post-PR monitoring, TDD, critical review subagent, doc structure, CI/CD only deploys, doc tracker. Read that file before any non-trivial change.

## Repo-specific notes

- **Default branch is `main`** (NOT `master`). All CI gates and `gh` defaults assume `main`.
- **Forking requirement:** must remain under the `RelevantSearch` org. The rs_infra WIF pool's `attribute_condition` is `assertion.repository_owner == 'RelevantSearch'`; any other owner's OIDC token would be rejected by the deployer-SA WIF binding.
- **Python:** 3.12 pinned. Lint: `ruff` + `black`. Tests: `pytest` + `pytest-asyncio`.
- **MCP framework:** `fastmcp>=3.2.4` (standalone package, NOT in-tree `mcp.server.fastmcp`). The OAuth `OAuthProxy` and `GoogleProvider` primitives we depend on live in this package. The original upstream uses in-tree `mcp.server.fastmcp.FastMCP`; this fork's Phase 1 PR migrated the import path.

## Deployment modes

The server has two modes selected by `PLAUD_DEPLOYMENT_MODE`:

| Mode | Auth model | Use case |
|---|---|---|
| `team` (this fork's default in Cloud Run) | OAuth 2.1 with Google IdP via `GoogleProvider`; per-user Plaud tokens in Firestore | Hosted multi-user team server |
| unset / `local` | Single-tenant `PLAUD_TOKEN` env var (upstream behavior) | stdio mode for local Claude Desktop / Claude Code |

The 12 existing MCP tools have zero behavior changes between modes. Tool handlers read a per-request `ContextVar` populated either by team-mode auth middleware or by single-tenant fallback.

## Local development

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Project documentation

`docs/projects/2026-04-30-plaud-mcp-team/` mirrors the canonical project docs from `rs_infra`. Design, plan, spike outputs all live there.

## Related

- Canonical plan docs: `~/git/RS/main/rs_infra/docs/projects/2026-04-30-plaud-mcp-team/`
- Sibling MCP: `RelevantSearch/google-drive-mcp` — same architectural pattern (OAuth wrapper with Google IdP), different upstream (TypeScript)
- Upstream: `jameshenning/PlaudNotes`
