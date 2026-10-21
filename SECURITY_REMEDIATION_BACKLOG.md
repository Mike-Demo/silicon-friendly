# Security audit remediation backlog

This document contains issue drafts for the findings in [parent issue #4](https://github.com/Mike-Demo/silicon-friendly/issues/4). Issues could not be created from this session, so the drafts below are ready to file.

## Priority order

The order weighs severity and reachability from a lower-trust caller first, then favors smaller, safer fixes. Findings have distinct root causes and are not grouped.

1. **[high] Dodo webhook forgery** — unauthenticated callers can forge events and create paid entitlements without paying.
2. **[high] Stored XSS through chat usernames** — public signup can persist markup that executes in chat viewers' browsers on the site origin.
3. **[high] Crypto transaction replay** — a genuine transfer can be reused across payment requests, multiplying entitlements.
4. **[medium] Unauthenticated CheckJob disclosure** — anyone can enumerate job IDs to retrieve reports and PDFs.
5. **[medium] Checker SSRF** — an authenticated checker user can target internal services and read fetched responses.
6. **[medium] Cross-customer payment use** — any signed-in user can consume another customer's completed payment quota.
7. **[medium] MCP verification gate bypass** — a silicon-token holder can submit unchecked criteria that feed directory state.
8. **[medium] Sybil verification threshold** — creating twelve low-friction accounts can manufacture a verified listing.
9. **[medium] Deactivated-account Google sign-in** — a deactivated carbon with a valid Google identity can regain access.
10. **[low] Search quota race** — concurrent requests can perform multiple searches while deducting only one credit.

## Remediation issue drafts

### 1. `[security][high] Prevent Dodo webhook forgery`

**Parent:** #4

**Finding:** `dodo-webhook-forgery-completes-payment`

**Evidence:** `DodoWebhookView` is unauthenticated and trusts caller-supplied request data. It checks the public product ID and event type or status, loads the payment named in `metadata.payment_id`, marks it completed, and creates a `VerificationRequest`. No Dodo signature verification, pending-state guard, or idempotency guard is present.

**Root cause:** The webhook handler does not authenticate the event or constrain the payment state transition, so a forged or replayed body can mint entitlements.

**Smallest fix:** Verify the Dodo signature against the raw body and webhook secret before parsing. Reject invalid events, and only transition pending payments, with idempotency keyed on the Dodo event or payment ID.

### 2. `[security][high] Prevent stored XSS through chat usernames`

**Parent:** #4

**Finding:** `stored-xss-via-chat-username`

**Evidence:** Both API signup paths strip and lowercase usernames but do not restrict their characters. The chat renderer interpolates the author into anchor HTML, a profile URL, and a single-quoted `onclick` argument, then assigns the result to `innerHTML`; it escapes the message body but not the author.

**Root cause:** User-controlled usernames reach an HTML/JavaScript rendering sink without context-appropriate encoding.

**Smallest fix:** Restrict usernames to lowercase letters, digits, and underscores at signup and model level, escape author text in the renderer, and apply JavaScript-string escaping to the server-rendered `onclick` argument.

### 3. `[security][high] Bind crypto transaction hashes to one payment`

**Parent:** #4

**Finding:** `crypto-tx-hash-replay-across-payments`

**Evidence:** `tx_hash` is non-unique; submission accepts hashes without checking prior use; and `verify_crypto_payment` checks the USDC contract, recipient, and amount range but neither the sender nor prior consumption. The polling task can complete multiple pending payments submitted with the same genuine transfer.

**Root cause:** Payment proof is neither uniquely bound to a payment nor consumed atomically when a payment completes.

**Smallest fix:** Add a database uniqueness constraint for `tx_hash`, reject hashes already attached to any payment, and record hash consumption atomically when a payment completes.

### 4. `[security][medium] Require ownership for CheckJob reports and downloads`

**Parent:** #4

**Finding:** `checkjob-status-and-pdf-idor-unauthenticated`

**Evidence:** `check_status_api` and `report_download_view` look up `CheckJob` by sequential integer ID without authentication or ownership checks, exposing report Markdown and generated PDFs. Other repository controls are owner-only.

**Root cause:** Both views retrieve jobs by ID and status alone rather than requiring and scoping to the owning carbon.

**Smallest fix:** Require the carbon session in both views and scope each lookup to the caller; return 404 when the job is not theirs.

### 5. `[security][medium] Block private and redirected targets in checker fetches`

**Parent:** #4

**Finding:** `checker-ssrf-via-domain-fetch`

**Evidence:** An authenticated carbon controls the website-check domain. Normalization strips only the scheme and path, leaving IP literals, ports, and internal hostnames. The fetch helper follows redirects and retries over plain HTTP without validating public IPs, ports, or redirect destinations; fetched content is returned through a readable checker job.

**Root cause:** The server-side fetch path does not validate targets or redirect hops before making requests.

**Smallest fix:** Resolve and reject non-public IP addresses and non-80/443 ports before fetching; disable automatic redirects or revalidate every hop; remove the HTTP downgrade fallback.

### 6. `[security][medium] Scope verification requests to the payment owner`

**Parent:** #4

**Finding:** `verification-request-spends-other-users-payment`

**Evidence:** `CreateVerificationRequestView` authenticates the caller but selects the latest completed `PaymentRequest` for a website without matching the payment to that caller. It then creates a request charged against that payment's quota and records the attacker as requester.

**Root cause:** The completed-payment lookup is scoped by website and status, not by the requesting carbon or silicon.

**Smallest fix:** Filter the payment lookup by the caller's ownership (payment requested by that carbon or silicon) and return 403 or 404 when no owned completed payment exists.

### 7. `[security][medium] Apply HTTP verification gates to MCP submissions`

**Parent:** #4

**Finding:** `mcp-verify-bypasses-report-gates`

**Evidence:** MCP `verify_website` accepts caller-supplied booleans with a free silicon token, silently defaults absent fields to false, and persists a `WebsiteVerification` without the detailed report, minimum-field, quality-attestation, or rate-limit checks enforced by the HTTP verification endpoint. The created row is consumed by the daily verification crunch and awards search quota.

**Root cause:** The MCP entry point does not share the HTTP endpoint's validation before persisting verification data.

**Smallest fix:** Route MCP submissions through the same validation as the HTTP endpoint (including a report of at least 500 characters, four quality attestations, and the per-silicon rate limit), or share a verification service between both entry points.

### 8. `[security][medium] Prevent Sybil accounts from manufacturing verified listings`

**Parent:** #4

**Finding:** `sybil-verifications-manufacture-verified-listing`

**Evidence:** Silicon signup requires only email, username, and password, with no email verification or identity proofing, and is rate-limited only per IP. Each verification earns search quota. The daily crunch counts each verification equally and marks a site verified when a trusted verification exists or the count reaches twelve; twelve Sybil verifications with all-true criteria can satisfy that threshold.

**Root cause:** Trust aggregation treats accounts as independent principals without an independence signal, so newly created accounts can satisfy the verification threshold.

**Smallest fix:** Require a stronger independence signal before crowd verifications can flip verified state (for example, exclude self- or related-account verifications, verify email, or apply tenure weighting), and ensure unverified-email accounts alone cannot reach the twelve-verification threshold.

### 9. `[security][medium] Reject deactivated accounts in Google sign-in`

**Parent:** #4

**Finding:** `google-auth-ignores-is-active`

**Evidence:** `google_auth_complete` validates a Google token and verified email, then loads `Carbon` by email without an `is_active` filter and creates a session. Password login and token middleware require active accounts.

**Root cause:** The Google sign-in account lookup omits the active-account check enforced by other authentication paths.

**Smallest fix:** Require `is_active` in the Google-path account lookup and return an error for deactivated accounts, matching password and token authentication.

### 10. `[security][low] Deduct search quota atomically`

**Parent:** #4

**Finding:** `search-quota-check-then-deduct-race`

**Evidence:** Both search surfaces check quota and deduct it non-atomically from a per-request ORM instance. The web surface retains the stale value during the external search call; concurrent requests can all proceed and save the same decremented value.

**Root cause:** Quota accounting uses a check-then-deduct on stale in-memory state rather than an atomic conditional update.

**Smallest fix:** Before searching, atomically decrement quota with a conditional update and proceed only when exactly one row was updated.

## Needs-validation issue draft

### `[security][needs-validation] Audit leads to validate`

**Parent:** #4

Validate each lead below. These are not confirmed findings; the exact missing fact must be established before severity or remediation is determined.

- [ ] **CORS middleware reflects any Origin with credentials** (`cors-reflect-any-origin-with-credentials`)  
  **Claimed root cause:** The CORS middleware grants credentialed cross-origin access to every origin without an allowlist.  
  **Missing fact:** The deployed session cookie attributes (SameSite and Secure) and whether production overrides `SESSION_COOKIE_SAMESITE` from the source default of Lax; without this fact it cannot be determined whether a cross-origin fetch with credentials include actually carries a logged-in carbon session cookie.

- [ ] **DEBUG defaults on and ALLOWED_HOSTS defaults to wildcard** (`debug-and-wildcard-hosts-default-on`)  
  **Claimed root cause:** Production configuration defaults are insecure: DEBUG on and wildcard hosts unless the deployment environment explicitly overrides them.  
  **Missing fact:** The deployed environment values for `DJANGO_DEBUG` and `ALLOWED_HOSTS` in production; no deployment env file or infrastructure config in the repository states the production values.

- [ ] **Target-site content is interpolated into Claude checker prompts** (`prompt-injection-into-claude-checker`)  
  **Claimed root cause:** Untrusted fetched content is interpolated into the evaluator prompt without delimiting or instruction hierarchy, and model output is trusted as authoritative directory state.  
  **Missing fact:** Whether the Claude evaluator used by the checker in fact follows instructions embedded in fetched homepage, robots, or llms content to flip criterion pass values in its JSON output; this requires a bounded local fixture run of the checker pipeline, which was not executed in this static pass.

- [ ] **IP rate limits keyed on unvalidated X-Forwarded-For** (`xff-spoofing-bypasses-ip-rate-limits`)  
  **Claimed root cause:** IP-based rate limiting trusts a client-supplied forwarding header as the client identity.  
  **Missing fact:** The ingress proxy X-Forwarded-For handling in front of gunicorn (unix socket): whether the deployed proxy overwrites or strips client-supplied X-Forwarded-For or passes it through to Django.
