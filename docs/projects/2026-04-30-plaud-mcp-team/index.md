---
version: 3
---

# Team Plaud Notes MCP on Cloud Run

**Started:** 2026-04-30
**Status:** Draft

## Summary

Deploy a team-wide Plaud Notes MCP server on Cloud Run in `rs-workspace-integrations`, exposed to claude.ai as a custom connector via OAuth 2.1. Architecturally a sibling of [drive-mcp](../2026-04-15-drive-mcp-team/): the MCP server acts as its own OAuth Authorization Server using FastMCP's `GoogleProvider` (an `OAuthProxy` pre-configured for Google), gated to the `@relevantsearch.com` Workspace via Internal-type consent.

Per-user Plaud bearer tokens are stored in Firestore (CMEK + KMS envelope encryption), captured via a separate just-in-time `/admin` endpoint that surfaces when a user's first MCP tool call returns "no Plaud token registered." The structured MCP error includes a registration URL; the `/admin` page does its own Google sign-in (cheap — user is already signed in from the OAuth dance), captures the Plaud `tokenstr`, validates it against Plaud's `/user/me`, persists it under the user's Google `sub`. claude.ai's connector configuration never changes across re-keys.

Forks `jameshenning/PlaudNotes` (Python, MIT, default branch `main`) into `RelevantSearch/PlaudNotes`. Migrates the upstream from in-tree `mcp.server.fastmcp.FastMCP` (`mcp>=1.0.0`) to the standalone `fastmcp>=3.2.4` package, which ships the OAuth primitives we need.

## Why this design (changed in v3)

Four parallel research streams (FastMCP spike, claude.ai OAuth behavior, Plaud API + TOS, GCP infra) closed the v2 unknowns. Key decisions baked into v3:

- **`GoogleProvider` directly, no bespoke OAuth code.** The spike confirmed FastMCP's `OAuthProxy` is exactly the AS-with-external-IdP-delegation pattern we need. Plan 2 LOC budget drops from ~600-800 to ~300-400.
- **Just-in-time `/admin` for Plaud token capture** instead of a mid-OAuth-flow form. Keeps `GoogleProvider` vanilla (no FastMCP internals coupling), separates the "claude.ai connection" concern from the "Plaud token registration" concern.
- **Plaud official OAuth API exists in private beta** ([waitlist](https://support.plaud.ai/hc/en-us/articles/56061278749209-FAQs-for-Plaud-OAuth-API)). Long-term migration target — `tokenstr` proxy is a strictly time-boxed bridge.
- **claude.ai has an active OAuth connector bug** ([anthropics/claude-code#46140](https://github.com/anthropics/claude-code/issues/46140), open as of 2026-04-29). Mitigations: dev subdomain `plaud-mcp-dev.relevantsearch.com` for iteration to avoid burning the prod domain's connector cache state, always emit `WWW-Authenticate` on 401s.
- **TOS is ambiguous in places** but the user-supplied-credential-proxy pattern is standard SaaS-integration practice; not gated on legal review. Plaud OAuth API access is the long-term migration target.

## Documents

- [Design](./2026-04-30-plaud-mcp-team-design.md)
- [Plan 1 — Infrastructure (rs_infra)](./2026-04-30-plaud-mcp-team-plan-1-infra.md) — execute first
- [Plan 2 — App (fork + GoogleProvider + just-in-time /admin + deploy)](./2026-04-30-plaud-mcp-team-plan-2-app.md) — execute after Plan 1 is merged
- Spike outputs: [`spike/`](./spike/)

## Related

- [`docs/projects/2026-04-15-drive-mcp-team/`](../2026-04-15-drive-mcp-team/) — sibling MCP, shared `rs-workspace-integrations` project, debugged through claude.ai's OAuth behavior already
- ADR-003 — CMEK / Autokey encryption
- ADR-007 — Terragrunt state management
- ADR-008 — Workload Identity Federation
- ADR-010 — SOPS secrets management
- Memory: `reference_claude_ai_mcp_auth.md` — claude.ai accepts only OAuth or no-auth

## Changelog

### v3 — 2026-04-30
Synthesizes findings from four parallel research streams (FastMCP spike, claude.ai behavior, Plaud API + TOS, GCP infra). Switches to `GoogleProvider` directly (drops bespoke OAuth code). Replaces mid-OAuth-flow Plaud-paste with just-in-time `/admin`. Adds dev subdomain mitigation for claude.ai OAuth bug. Documents Plaud OAuth waitlist as long-term target. Drops "URL log redaction" goal (provider doesn't support it; moot for OAuth design anyway).

### v2 — 2026-04-30
Switched from capability-URL to OAuth wrapper after architect review revealed LOC parity.

### v1 — 2026-04-30
Initial draft (capability-URL design).
