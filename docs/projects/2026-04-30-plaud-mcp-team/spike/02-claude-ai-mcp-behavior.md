---
version: 1
---

# Spike: claude.ai Custom Connector OAuth Behavior

**Date:** 2026-04-30
**Status:** Findings (web research; empirical confirmation pending)
**Parent:** [../index.md](../index.md)

Per `feedback_verify_with_citations`: every claim cites a source. Source-spelunking is not empirical observation — items requiring deploy-time confirmation are listed in the [must-test-empirically](#must-test-empirically) section.

## Q1. Multi-hop browser flow tolerance

**VERDICT:** PARTIAL — claude.ai itself only owns the `/authorize` start and the `redirect_uri` callback. Everything in between (Google sign-in, our HTML form for the Plaud token paste, our `/authorize/finalize` POST) happens entirely in the user's browser and is invisible to claude.ai. claude.ai validates `state` and `code` only on the final redirect to its `redirect_uri`. **CONFIDENCE:** Medium for the spec; Low for claude.ai's tolerance of long redirect chains specifically.

**EVIDENCE:**
- MCP authorization spec uses standard OAuth 2.1 redirect-back-to-client; nothing constrains intermediate hops. ([MCP Authorization 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization))
- Spec only mandates: `state` round-trip, exact `redirect_uri` match, PKCE S256. ([Open Redirection / Authorization Code Protection sections](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization))
- The drive-mcp design (which already shipped) uses one external IdP hop (Google) successfully — but does NOT include an interstitial form, so the form-paste hop is novel and untested in this org. ([~/git/RS/main/rs_infra/docs/projects/2026-04-15-drive-mcp-team/2026-04-15-drive-mcp-team-design.md, lines 60-66](file:///home/stefan/git/RS/main/rs_infra/docs/projects/2026-04-15-drive-mcp-team/2026-04-15-drive-mcp-team-design.md))
- Browser-driven hops + same-origin form POST are spec-neutral; claude.ai has no signal they happen.

**RISK:** the form-paste hop crosses origins (our `/oauth/google/callback` → render form → POST `/authorize/finalize` → 302 to claude.ai). If the form's POST handler is on the same origin as `/authorize`, it works the same as drive-mcp. If popup-windowed, browser may block the final redirect to claude.ai.

## Q2. .well-known/oauth-authorization-server caching and required fields

**VERDICT:** UNKNOWN cache TTL; spec mandates several fields. **CONFIDENCE:** High on required fields; Low on caching.

**EVIDENCE:**
- MCP spec requires `code_challenge_methods_supported` to be present, listing at least `S256`; if absent the client MUST refuse to proceed. ([Authorization Code Protection](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization))
- `registration_endpoint` advertises DCR support; `client_id_metadata_document_supported: true` advertises CIMD. ([Discovery section](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization))
- claude.ai prefers Client ID Metadata Document over DCR if both advertised. ([Client Registration Approaches](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization))
- One field operator reports "any domain that has ever exposed `/.well-known/oauth-authorization-server` appears to be permanently cached as 'OAuth-required' in Anthropic's proxy" — anecdotal but consistent with multiple reporters. ([daveladouceur comment, claude-code#46140](https://github.com/anthropics/claude-code/issues/46140#issuecomment-4230040203))
- Spec does NOT mandate any cache TTL; that's claude.ai-internal.

**Listing only `S256` in `code_challenge_methods_supported`:** Spec-compliant and effectively forces PKCE. No evidence claude.ai rejects this.

## Q3. MCP spec version and RFC 9728 support

**VERDICT:** YES — claude.ai speaks `mcp-client-2025-11-20` (current beta header) and the connector handler implements RFC 9728 discovery. **CONFIDENCE:** High.

**EVIDENCE:**
- Current beta header: `anthropic-beta: mcp-client-2025-11-20`; deprecated `mcp-client-2025-04-04`. ([platform.claude.com MCP connector](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector))
- Probe bodies captured by reporter show `protocolVersion: "2025-11-25"` and `clientInfo.name: "Anthropic"` / `"Anthropic/ClaudeAI"`. ([theaboutbox comment, claude-ai-mcp#217](https://github.com/anthropics/claude-ai-mcp/issues/217#issuecomment-4304359219))
- claude.ai sends `MCP-Protocol-Version: 2025-11-25` header. ([same comment](https://github.com/anthropics/claude-ai-mcp/issues/217#issuecomment-4304359219))
- RFC 9728 (Protected Resource Metadata) is REQUIRED by the spec; claude.ai parses `WWW-Authenticate: Bearer resource_metadata="..."` headers (when it works — see Q4). ([Authorization Server Discovery](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization))
- Probes include `extensions: {"io.modelcontextprotocol/ui": {"mimeTypes": ["text/html;profile=mcp-app"]}}` indicating UI extension support. ([theaboutbox comment](https://github.com/anthropics/claude-ai-mcp/issues/217#issuecomment-4304359219))

**Streamable HTTP session IDs:** spec uses `Mcp-Session-Id` header from POST initialize response; required by claude.ai-served MCP. ([Streamable HTTP transport spec](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization))

## Q4. Known issues / blockers — CRITICAL FINDINGS

**VERDICT:** Multiple OPEN, active, production-blocking bugs. **CONFIDENCE:** High.

**EVIDENCE (open as of 2026-04-30):**

| Issue | State | Bug |
|---|---|---|
| [anthropics/claude-code#46140](https://github.com/anthropics/claude-code/issues/46140) | OPEN (last updated 2026-04-28) | OAuth completes, Bearer token never sent on `/mcp`. Same server works on Claude Code CLI. |
| [anthropics/claude-ai-mcp#155](https://github.com/anthropics/claude-ai-mcp/issues/155) | OPEN (2026-04-27) | Same — Bearer not attached after token issuance. |
| [anthropics/claude-ai-mcp#217](https://github.com/anthropics/claude-ai-mcp/issues/217) | OPEN (2026-04-29) | claude.ai skips OAuth discovery entirely: 3× POST `/mcp` → 401 → silent abandon. No `WWW-Authenticate` follow-up. |
| [anthropics/claude-ai-mcp#215](https://github.com/anthropics/claude-ai-mcp/issues/215) | OPEN (2026-04-23) | Auth code issued; claude.ai never calls `/token`. |
| [anthropics/claude-ai-mcp#227](https://github.com/anthropics/claude-ai-mcp/issues/227) | OPEN (2026-04-30) | "Couldn't reach the MCP server" — zero traffic at edge. |
| [anthropics/claude-ai-mcp#219](https://github.com/anthropics/claude-ai-mcp/issues/219) | Reported FIXED 2026-04-24 | Case-sensitive `WWW-Authenticate` lookup broke on HTTP/2 (Cloudflare etc.). Per ronnyTodgers, "fixed the case sensitivity bug as far as I can see". ([comment](https://github.com/anthropics/claude-ai-mcp/issues/217#issuecomment-4314185788)) |

**Pattern:** Same MCP server passes on Claude Code CLI, MCP Inspector, ChatGPT connector, curl — fails ONLY on claude.ai web's "Add custom connector" path. ([sxalexander, theaboutbox, bwvogler, multiple comments on #217](https://github.com/anthropics/claude-ai-mcp/issues/217)) Failure rate is non-deterministic.

**A possible-domain-blacklist signal:** moving to a fresh subdomain works ~ once, then can break too — suggests server-side caching of the connector's "OAuth-required" status that may be sticky. ([daveladouceur #46140 comment](https://github.com/anthropics/claude-code/issues/46140#issuecomment-4230040203))

## Q5. Re-auth UX on tool error

**VERDICT:** PARTIAL — token-expiry handling is via re-clicking "Reconnect" in claude.ai settings; structured re-auth re-trigger from a tool-error response is NOT documented. **CONFIDENCE:** Medium.

**EVIDENCE:**
- Spec defines `HTTP 401` ⇒ MCP server SHOULD return `WWW-Authenticate: Bearer resource_metadata="..."`; client SHOULD restart auth flow. ([MCP spec error handling](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization))
- Per truefoundry guide: when token expires, "the fix is to re-authenticate with the service through the MCP connector by clicking 'Reconnect'". ([truefoundry MCP auth guide](https://www.truefoundry.com/blog/mcp-authentication-in-claude-code))
- Returning a 401 from `/mcp` is the spec-compliant signal. Whether claude.ai web auto-reopens the consent popup on a runtime 401 vs. just showing a generic error is undocumented and not consistent across reports. (Inference, not citation — [must-test-empirically](#must-test-empirically).)
- The MCP UI extension (`io.modelcontextprotocol/ui` declared in claude.ai's probe) suggests in-tool elicitation may be available, but the Plaud-token paste flow doesn't naturally fit that model anyway. ([theaboutbox capture](https://github.com/anthropics/claude-ai-mcp/issues/217#issuecomment-4304359219))

## Q6. Sec-Fetch-Site / Referer / strict redirect URI matching

**VERDICT:** Spec mandates EXACT redirect URI matching; no documented Referer/Sec-Fetch-Site enforcement. **CONFIDENCE:** Medium.

**EVIDENCE:**
- Spec: "Authorization servers **MUST** validate exact redirect URIs against pre-registered values to prevent redirection attacks." ([Open Redirection](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization))
- Spec requires `state` parameter use and verification by client. ([same](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization))
- One reporter notes "redirect from non-www to www can strip headers and break the OAuth flow" and "the consent page should use an anchor tag linking to the approve URL rather than a form submission" — implies claude.ai may be sensitive to exactly how the final redirect is generated. ([referenced via search summary; not direct linked source — flagged Low confidence](https://www.buildwithmatija.com/blog/oauth-mcp-server-claude))
- No Sec-Fetch-Site documentation found.

## Blockers

1. **claude.ai-side OAuth bug (#46140 / #155 / #217)** is OPEN and actively breaks production OAuth-protected MCP servers as of 2026-04-28. Drive-mcp already works in our org, which is a useful counterexample, but the bug is reportedly non-deterministic and may surface during Plaud-MCP rollout. **This invalidates the "should just work" assumption.**
2. **The form-paste hop has no precedent in our shipped MCP servers.** Drive-mcp has Google → callback → done. Plaud-MCP adds an interstitial HTML form. Cross-origin POSTs and 302s through that form may behave differently from a pure redirect chain. **Empirical only.**
3. **Sticky domain caching of "OAuth-required" state** ([#46140 anecdote](https://github.com/anthropics/claude-code/issues/46140#issuecomment-4230040203)) — if real, we cannot iterate on the OAuth flow against `plaud-mcp.relevantsearch.com` without burning the domain. Plan should include staging on a throwaway subdomain.

## Must-test-empirically

These cannot be verified without deploying:

1. **Does the form-paste hop work?** Specifically: does claude.ai's connector tolerate `/authorize` → 302 → Google → 302 → our HTML form (200 OK) → POST `/authorize/finalize` → 302 → claude.ai `redirect_uri`? (Q1, Q6)
2. **What is the `.well-known/oauth-authorization-server` cache TTL on claude.ai's side?** Test: deploy, change a metadata field, observe propagation delay. (Q2)
3. **Does claude.ai re-trigger `/authorize` automatically on a 401 from `/mcp` mid-session, or does it just show "tool failed"?** Test: rotate signing key, force token to fail validation, observe UX. (Q5)
4. **Does our exact redirect chain trigger any of the open #46140 / #217 failure modes?** This may require multiple attempts — the bug is reportedly non-deterministic.
5. **Is `Mcp-Session-Id` strictly required?** Test: omit, observe behavior.
6. **Does `code_challenge_methods_supported: ["S256"]` (S256-only) work?** Spec says yes; claude.ai not empirically confirmed.

## Recommendations for design doc

- Use a throwaway test subdomain (e.g., `plaud-mcp-dev.relevantsearch.com`) for OAuth iteration before pointing the real DNS at the service. Burns domains are reported real.
- Mirror drive-mcp's redirect chain as closely as possible: have `/authorize/finalize` issue the 302 directly, no extra hops. The form-paste step should render at `/oauth/google/callback` (our origin) and POST same-origin to `/authorize/finalize`.
- Make our metadata advertise BOTH `registration_endpoint` (DCR) AND avoid CIMD complexity for v1 — claude.ai prefers CIMD but DCR works on drive-mcp today.
- Plan a fallback: if the OAuth bug bites Plaud-MCP, the ChatGPT-connector / MCP Inspector / Claude Code CLI paths are confirmed working escape hatches.
- Add a `WWW-Authenticate` header on every 401 from `/mcp` per RFC 9728 — required for re-auth.
