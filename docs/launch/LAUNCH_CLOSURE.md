# Job Pursuit — launch closure board

Updated 17 September 2026 (product-gates session). Studio UI iteration paused per Chief of Staff; gates first.

## 17 Sep 2026 product-gates addendum

| Gate | Status this session | Evidence |
| --- | --- | --- |
| `/readyz` routing | **DONE** (Mac interim) | Public `GET /readyz` → `200` JSON `{"status":"ready","service":"job-pursuit-mobile","scope":"configuration"}`; tunnel `^/readyz/?$` → `:8843`; unit `tests/test_mobile_readyz.py`. See `docs/launch/READYZ_ROUTING.md`. |
| Stripe sandbox | **CORE PATH DONE** | Checkout→webhook→entitlement proven in test mode 17 Sep (webhook secret rotated; founder membership converted manual→billing). Portal session OK. Renewal/fail/cancel + CI still open — `docs/launch/STRIPE_SANDBOX_VERIFICATION.md`. No live charges. |
| Mac-independent hosting | **BLOCKED (external)** | Still Mac + cloudflared. Residual risk in `docs/launch/HOSTING_RESIDUAL_RISK.md`. Waiting on EXT-HOST-01 (host + budget approval). |

Older rows below are retained as historical board state from 15 September 2026.

---


Updated 15 September 2026 (second closure session, tree based on `c41b886`).
Statuses: PASS / PARTIAL — EXTERNAL BLOCKER / FAIL — INTERNAL ISSUE.
No real charges, employer submissions, account deletions or founder-profile
changes were made. Evidence is sanitized; no secrets or customer data recorded.

## Summary

| Gate | Final state |
| --- | --- |
| 1 Deployed customer journey | FAIL — INTERNAL ISSUE (fixes below not yet deployed; assessment rubric and resume depth open from prior run) |
| 2 AI quality and truthfulness | FAIL — INTERNAL ISSUE (no fresh live six-persona pass on the current tree) |
| 3 Subscription and spending | PARTIAL — test credentials present (17 Sep); full sandbox lifecycle + CI checkout confirmation still open |
| 4 Mac-independent hosting | PARTIAL — EXTERNAL BLOCKER (no approved host) |
| 5 Full regression | FAIL — INTERNAL ISSUE until the current tree passes Linux CI |
| Security/privacy | FAIL — INTERNAL ISSUE items listed below; AI minimization gap fixed locally |

## Evidence categories used

- **Code inspection** — reading source/config.
- **Unit/mocked** — Python unittest with fake cloud/provider transports; node tests.
- **Local integration** — real ASGI + fake transports; synthetic React preview on 127.0.0.1:5187.
- **Live database** — disposable PostgreSQL migrations 0001–0014 (Linux CI only; Mac cannot allocate SysV shared memory).
- **Deployed read-only** — credential-free GETs against www.thejobpursuit.com.
- **Provider sandbox** — none this session (no credentials).

## Gate 1 — Deployed customer journey

- **Initial state:** prior session completed a synthetic signup → upload → facts → discovery (18 real postings) → save/refresh → assessment → four downloads on the deployed site. Assessment returned 7/10 with its requirement comparison discarded; resume sparse.
- **Work this session:** deployed read-only checks only. No new signed-in journey: the fixes below change prompt context and must be deployed first, which is an owner-confirmed production restart.
- **Tests (deployed read-only):** `/beta` 200; `/api/mobile/health` 200 `no-store`; anonymous `/api/mobile/bootstrap` 401 `no-store`; `/admin` and `/api/jobs` 302 to Cloudflare Access with private/no-store; webhook GET 405; version `2026-09-15-core-flow-v2`.
- **Finding:** `/.env`, `/.git/config`, `/jobagent.db`, `/readyz` and every unknown path return the same 1,114-byte `text/html` SPA shell (verified: no env-style lines). Not a leak, but production is still served by the founder static server's catch-all, not the isolated `jobagent.mobile.web` entrypoint (which 404s unknown paths and serves JSON `/readyz`).
- **Remaining:** deploy current tree; repeat full signed-in journey including assessment rubric validation and document depth.
- **Final:** FAIL — INTERNAL ISSUE.

## Gate 2 — AI quality and truthfulness

- **Initial state:** six-persona evaluation (12 live calls) recorded in `docs/testing/GATE2_WRITING_QUALITY.md`; composition safe but sparse; two studio/journey tests failing.
- **Work:**
  - Diagnosed the failures as stale expectations, not regressions: unattributed results intentionally render under "Selected Career Contributions" and a duplicate summary is suppressed; letters apply the audited first-person subject (`writing.letter_sentence`) and PDF extraction wraps lines. Tests now assert the actual contract (single occurrence, no summary duplication, whitespace-normalized match).
  - **Data minimization (Security D):** `studio._context` no longer sends name, email, phone, LinkedIn/GitHub/portfolio/website fields to the model. Free resume text (`career_text`, `unconfirmed_resume_text`) has emails, URLs, ≥9-digit phone numbers and the owner's literal profile name replaced with placeholders. Years, date ranges and percentages are preserved. `_identity` restores contact details only at render time. Postal addresses in free text are **not** reliably removed (documented residual).
- **Tests (unit/mocked):** new `test_model_payload_excludes_direct_contact_identifiers`; journey fixture provider now asserts name/email are absent from the model payload; identity-citation tests now require outright rejection. 247/247 across studio, journey, launch-quality, writing-quality, rubric, gate2, API, document-review, privacy probe/logging suites.
- **Remaining:** fresh bounded live six-persona run with SUPPORTED/REPHRASED/INFERRED/UNSUPPORTED/HALLUCINATED classification on the current tree; verify provider retention/training settings (no claim made).
- **Final:** FAIL — INTERNAL ISSUE.

## Gate 3 — Subscription and spending controls

- **Initial state:** Stripe test adapter, webhook signature verification, SQL lifecycle and live-activation double gate (migration 0014) exist; return helper suspected unwired.
- **Work:**
  - Confirmed `billingRouteRequested` was exported and unit-tested but never called: a Checkout success/cancel return landed on the default workspace. `BetaApp` now opens the billing view for `?billing=return|cancelled|plans`, strips the parameter (refresh does not reopen), and the panel reads server status. The query grants nothing.
  - Inspected env names only: no `MOBILE_BILLING_*` values in any env file or the running API; no Stripe CLI.
  - Linux CI (`run 34979249804`, pushed `c41b886`) failed `test_complete_customer_lifecycle_and_fail_closed_controls` with checkout 503. CI resolved FastAPI 0.141.1 / Starlette 1.6.0, while every local pass uses 0.128.8 / 0.49.3, and the privacy probe failed on the same new `_IncludedRouter` route type. The Dockerfile installs the same unpinned extra, so a production image would have shipped untested versions. `pyproject.toml` now caps `fastapi<0.129`; the probe flattens nested routes.
- **Tests (local integration):** billing browser harness 15/15 PASS (4 new return/cancel scenarios at 1440/390px: one status GET, zero writes, no access, query stripped). Frontend 100/100, `tsc -b` clean.
- **Not verified:** real sandbox checkout, signed webhook delivery, renewal, failed payment, cancellation/expiry against Stripe; the CI checkout 503 under pinned versions (needs a CI run of this tree).
- **Final:** PARTIAL — EXTERNAL BLOCKER (plus the CI confirmation above).

## Gate 4 — Mac-independent hosting

Verified from `launchctl`/`ps` this session:

| Component | Placement | If the Mac is off |
| --- | --- | --- |
| Public site + founder SPA catch-all | `jobagent.api.main` :8842 via `cloudflared` tunnel | Site down |
| Tenant API, discovery, AI orchestration, rendering | `jobagent.mobile.app` :8843 (launchd) | Customer actions down |
| Isolated web entrypoint | `jobagent.mobile.web` :8844 running, not the public route | n/a |
| Privacy worker | launchd every 60 s; 155 runs, last exit 0 | Queue stops; heartbeat expires |
| Payment webhook | Tenant API route, unconfigured | No events processed |
| Auth, DB, private Storage | Hosted Supabase | Persist; unreachable via app |
| Founder Telegram/prepare jobs | launchd, founder-only | Founder tools down; out of tenant scope |

- **Work:** re-inspected `deploy/` (read-only root, dropped caps, 1 GB API / 512 MB worker, secrets as runtime files). Docker CLI present, no running daemon; no image built this session.
- **Remaining:** owner approves provider and cost; then deploy, health checks, restart, repeat golden journey, prove no Mac fallback.
- **Final:** PARTIAL — EXTERNAL BLOCKER.

## Gate 5 — Full regression

- **Initial (local, this session):** 973 passed; 3 failures; 2 SQL setup errors and 5 SQL skips (Mac `shmget ENOMEM`).
- **Linux CI on `c41b886`:** 1,032 passed, 0 skipped; **all six SQL suites passed 58/58** (2+13+7+16+8+12); 4 failures + 1 error (three stale tests above, billing checkout 503, privacy-probe route type).
- **Final local run on the current tree:** 977/977 executed tests PASS; the remaining 61 are SQL suites that cannot start on this Mac and are **not** counted as passed.
- **Remaining:** push and dispatch CI for this tree; require zero failures/skips.
- **Final:** FAIL — INTERNAL ISSUE (pending CI).

## Security and privacy status

| Area | State | Evidence / remaining |
| --- | --- | --- |
| A Private by default | Partial | Authenticated/API responses `no-store`; founder routes behind Cloudflare Access. Bucket policies not re-queried this session |
| B Multi-user isolation | Partial | Real-ASGI foreign-ID denial tests and SQL RLS suites (Linux CI). Fresh hosted two-user direct-API run not performed |
| C Privileged access | Open | Privacy key isolated in its own env file/process. Admin MFA, secret rotation and incident runbook need owner verification |
| D AI minimization | Fixed locally | Contact identifiers removed from model context (above); not yet deployed; provider retention unverified |
| E Upload/download | Partial | Active-PDF blocking and bounded parser exist. No malware scanning strategy implemented |
| F Export/deletion | Partial | Hosted export verified earlier; destructive erasure only against disposable SQL |
| G Logging | Partial | Correlation-ID telemetry excludes URLs/headers/bodies; production log review not repeated |
| H Recovery | Open | Backup restore, dependency/secret scans, alerts not executed this session |

## External owner actions

| ID | Action |
| --- | --- |
| EXT-PAY-01 | Sign in to Stripe, create sandbox products/prices and a portal configuration, and place test keys into the server secret store (not chat). Then the sandbox lifecycle can run |
| EXT-HOST-01 | Approve a host (e.g. DigitalOcean App Platform) and budget after rechecking current pricing |
| EXT-DEPLOY-01 | Approve restarting the Mac tenant API with this tree (interim) and pushing it so Linux CI can verify |
| EXT-SEC-01 | Confirm admin MFA on Supabase, Cloudflare, Stripe, GitHub; confirm AI provider data-retention settings; schedule external penetration test and privacy/legal review |
