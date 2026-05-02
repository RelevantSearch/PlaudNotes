# Spike 03 — Plaud API & TOS findings

**Date:** 2026-04-30
**Source repo:** [`jameshenning/PlaudNotes`](https://github.com/jameshenning/PlaudNotes) — `src/plaud_notes_mcp/plaud_client.py`

---

## Q1 — Token-validation ("/me-equivalent") endpoint

**FINDING:** `GET /user/me` on the user's region base URL. Returns the authenticated user's profile; `401` on bad/expired token.

**EVIDENCE:**
- File: [`src/plaud_notes_mcp/plaud_client.py`](https://github.com/jameshenning/PlaudNotes/blob/main/src/plaud_notes_mcp/plaud_client.py) — endpoint path `/user/me` (~line 247); 401 handler emits `"Authentication failed. Your token may be expired."` (~lines 106-109).
- WebFetch of raw file confirmed `/user/me` is the auth-validation endpoint.

**CONFIDENCE:** High (verified via direct read of source). Stefan should curl-confirm before relying on it (see "must-curl" below).

---

## Q2 — Rate limits

**FINDING (unofficial `tokenstr` API):** No published numbers. `plaud_client.py` does **not** handle HTTP 429 — only retries 5xx (3 attempts) and handles a custom `-302` region-redirect code. No exponential backoff.

**FINDING (official Plaud Dev API):** Rate limits **are** enforced — token-bucket, Redis-backed, applied per `(client_id, user_id, IP)` over minute/hour/day windows. `429 Too Many Requests` on exceed. **Specific numbers are not published on the public docs index** — gated behind private beta.

**EVIDENCE:**
- [`docs.plaud.ai/api_guide/api_intro/rate_limit`](https://docs.plaud.ai/api_guide/api_intro/rate_limit) — "rate limits are applied per unique key (client_id, user_id, or IP address)... exceeding any limit will result in a 429 Too Many Requests response" (per search snippet).
- `plaud_client.py` `_request` method (~lines 134-191): `for attempt in range(3)` retries 5xx only; no 429 branch.

**CONFIDENCE:** Medium-high on existence of 429 limits; Low on actual numeric thresholds (need account on official API to read full docs page, or empirical probe of unofficial API).

---

## Q3 — TOS: is multi-user `tokenstr` proxying allowed?

**FINDING:** TOS contains clauses that materially threaten this design pattern. Specifically: India/regional terms explicitly prohibit account/credential sharing across persons; global User Agreement prohibits reverse engineering and bypassing security mechanisms; users must keep credentials confidential and are liable for all activity under their account.

**EVIDENCE (quotes via search snippets — full text not retrieved, WebFetch denied for plaud.ai):**

- India regional terms ([`in.plaud.ai/pages/terms-conditions`](https://in.plaud.ai/pages/terms-conditions)):
  > "No Member may share, assign, or permit the use of your Member account, ID or password by another person outside of the Member's own business entity."
- General User Agreement ([`web.plaud.ai/user-agreement`](https://web.plaud.ai/user-agreement)):
  > "Modify, make derivative works of, decompile, disassemble, decrypt, reverse compile or reverse engineer any part of the App or Services."
  > "users are not permitted to... disrupt the normal operation of the APP or devices, and must not reverse-engineer, modify, or attempt to bypass the APP's security mechanisms."
- Account-confidentiality clauses (general TOS, [`www.plaud.ai/policies/terms-of-service`](https://www.plaud.ai/policies/terms-of-service)):
  > "users are responsible for maintaining the confidentiality of their account and password... must not disclose account information such as passwords and device pairing credentials to any third party."
  > "agree to accept responsibility for any and all activities or actions that occur under their account and/or password"
- License grant ([`web.plaud.ai/user-agreement`](https://web.plaud.ai/user-agreement)):
  > "limited, non-exclusive, perpetual, revocable, and non-transferable license... strictly for personal or internal business purposes"

**CONFIDENCE:** Medium. Snippets are second-hand via search. Stefan/Bari must read full TOS before relying on this verdict — WebFetch was blocked for `plaud.ai` domains in this session.

---

## Q4 — Official API / partner program

**FINDING:** **YES — Plaud Developer Platform exists, with an OAuth API in private beta.** This is the long-term path; should replace `tokenstr` proxy as soon as we get access.

**EVIDENCE:**
- [`www.plaud.ai/pages/developer-platform`](https://www.plaud.ai/pages/developer-platform) — "Plaud Developer Platform delivers full-stack APIs and SDKs... SOC 2, HIPAA, GDPR, EN18031 compliance"
- [`docs.plaud.ai/api_guide/api_intro/authorization`](https://docs.plaud.ai/api_guide/api_intro/authorization) — OAuth 2.0 with `client_id` + `client_secret`, Bearer access tokens + refresh tokens
- [`support.plaud.ai/hc/en-us/articles/56061278749209`](https://support.plaud.ai/hc/en-us/articles/56061278749209-FAQs-for-Plaud-OAuth-API) — "Plaud OAuth API is currently in private beta... only available to Unlimited Users... fill out the waitlist form"
- [`www.plaud.ai/blogs/news/plaud-developer-platform`](https://www.plaud.ai/blogs/news/plaud-developer-platform) — confirms platform launch
- Zapier integration also exists: [`web.plaud.ai/zapier-doc`](https://web.plaud.ai/zapier-doc)

**CONFIDENCE:** High.

**ACTION:** Stefan should submit waitlist form today. Multi-user proxy via OAuth is the architecturally correct pattern.

---

## Q5 — Region routing (US/EU)

**FINDING:** Confirmed current. Two regions hard-coded in `plaud_client.py`:

```
"us": "https://api.plaud.ai"
"eu": "https://api-euc1.plaud.ai"
```

A third domain `api-use1.plaud.ai` appears in an allowed-domains list (~lines 21-24). Auto-detection is via custom status code `-302` triggering a one-shot region redirect (~line 158).

**EVIDENCE:** [`src/plaud_notes_mcp/plaud_client.py`](https://github.com/jameshenning/PlaudNotes/blob/main/src/plaud_notes_mcp/plaud_client.py) (verified via WebFetch of raw file).

**CONFIDENCE:** High.

---

## TOS Verdict

**AMBIGUOUS — needs Bari Rascoe (Polsinelli) review.**

The pattern (KMS-encrypt user-pasted `tokenstr`, proxy API calls on user's behalf via team-hosted MCP server) is **not unambiguously prohibited** by the global TOS, but several clauses create real risk:

1. **Reverse-engineering prohibition** — the `tokenstr` itself was discovered by inspecting localStorage on the web app. Strict reading: this is reverse engineering. Permissive reading: it's user-supplied data the user could paste into anything.
2. **Credential-confidentiality clause** — user is told to keep password/credentials private; pasting `tokenstr` into our service is debatably a disclosure. We mitigate via KMS-at-rest, but TOS doesn't carve out "encrypted storage by an agent of the user."
3. **Account-sharing prohibition (India regional)** — explicit ban on sharing account across persons. Each user uses only their own token, so this likely doesn't apply, but the principle echoes through other regional TOS variants.
4. **Existence of an official OAuth API in private beta** — strongest signal. Plaud is publicly directing developers to OAuth. Building a `tokenstr`-proxy product **while** an official path exists materially weakens any "no alternative" defense.

**Recommendation:** Treat the unofficial-API design as a **strictly time-boxed bridge**. Apply for OAuth waitlist now; plan migration the moment access lands. Bari should bless the bridge in writing before we onboard non-Stefan users.

---

## Must-curl-empirically (Stefan, with own `tokenstr`)

Every claim below is unverified by this spike. Run before locking the design:

1. **`/user/me` happy path** — `curl -H "Authorization: Bearer $TOKENSTR" https://api.plaud.ai/user/me` → expect 200 + JSON profile body. Capture exact response shape (need it for onboarding UX: "Welcome, $name").
2. **`/user/me` 401 path** — same curl with `$TOKENSTR=garbage` → expect 401. Confirm we can distinguish 401 from network errors.
3. **Region detection** — same curl from EU IP (or with EU-region account) → expect `-302`/region-mismatch behavior; capture the response that signals "use EU base URL."
4. **EU base URL** — same curl against `https://api-euc1.plaud.ai/user/me` with EU-region token → expect 200.
5. **Rate-limit ceiling probe** — fire `/user/me` in a loop (e.g. 60 req/min, 600 req/min) until 429. Record the threshold and the `Retry-After` header (if any). This gives us the empirical limit for the unofficial API.
6. **Token TTL sanity** — decode `$TOKENSTR` as JWT (`base64 -d` middle segment) → confirm `exp` claim and the ~10-month TTL claim from PlaudNotes README.
7. **TOS full-text capture** — load [`www.plaud.ai/policies/terms-of-service`](https://www.plaud.ai/policies/terms-of-service) and [`web.plaud.ai/user-agreement`](https://web.plaud.ai/user-agreement) in a browser, save full HTML, hand to Bari. WebFetch was blocked in this spike; we worked from search snippets only.
8. **OAuth waitlist** — submit the form at [`support.plaud.ai/hc/en-us/articles/56061278749209`](https://support.plaud.ai/hc/en-us/articles/56061278749209-FAQs-for-Plaud-OAuth-API). This is the migration target.

---

## Sources

- [PlaudNotes plaud_client.py](https://github.com/jameshenning/PlaudNotes/blob/main/src/plaud_notes_mcp/plaud_client.py)
- [Plaud Developer Platform](https://www.plaud.ai/pages/developer-platform)
- [Plaud Dev API: Authorization](https://docs.plaud.ai/api_guide/api_intro/authorization)
- [Plaud Dev API: Rate limits](https://docs.plaud.ai/api_guide/api_intro/rate_limit)
- [FAQs for Plaud OAuth API](https://support.plaud.ai/hc/en-us/articles/56061278749209-FAQs-for-Plaud-OAuth-API)
- [Plaud Terms of Service](https://www.plaud.ai/policies/terms-of-service)
- [Plaud Web User Agreement](https://web.plaud.ai/user-agreement)
- [Plaud India Terms & Conditions](https://in.plaud.ai/pages/terms-conditions)
- [Zapier <> Plaud Integration](https://web.plaud.ai/zapier-doc)
