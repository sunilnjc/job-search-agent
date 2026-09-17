# Stripe sandbox verification (product gate)

Updated 17 September 2026.

**Hard rules:** no live charges, no live-mode keys in the Mac `.env.mobile`, no employer submissions.

## Current Mac config (presence only; values never logged)

As of 17 Sep 2026 the Mac `.env.mobile` has Stripe **test** configuration loaded:

| Variable | Present | Kind observed |
| --- | --- | --- |
| `MOBILE_BILLING_MODE` | yes | `test` |
| `MOBILE_BILLING_PROVIDER` | yes | set (`stripe`) |
| `MOBILE_BILLING_CHECKOUT_ENABLED` | yes | bool true |
| `MOBILE_BILLING_STRIPE_SECRET_KEY` | yes | `sk_test…` |
| `MOBILE_BILLING_STRIPE_WEBHOOK_SECRET` | yes | `whsec_…` |
| `MOBILE_BILLING_PLANS` | yes | set |
| `MOBILE_BILLING_SUCCESS_URL` / `CANCEL_URL` / `PORTAL_RETURN_URL` | yes | set |
| `MOBILE_BILLING_STRIPE_PORTAL_CONFIGURATION` | yes | set |
| `MOBILE_BILLING_STRIPE_WEBHOOK_API_VERSIONS` | yes | set |
| `MOBILE_BILLING_SUPABASE_URL` / `MOBILE_BILLING_SERVICE_ROLE_KEY` | yes | set |

`BillingSettings.from_env()` constructs with `mode=test`, `provider=stripe`, `checkout_enabled=True`. `StripeTestProvider.from_env()` succeeds. `StripeLiveProvider.from_env()` fail-closes (expected).

This clears the old “no sandbox credentials on disk” blocker for **config presence**. It does **not** clear full sandbox lifecycle proof.

## Fail-closed checks already green (deployed)

| Check | Result |
| --- | --- |
| `GET /api/mobile/billing/webhook` | `405` |
| `POST /api/mobile/billing/webhook` with no signature | `400` `One valid billing signature header is required.` |

## Remaining sandbox checklist (do not skip)

Run only against Stripe **test** mode. Prefer Stripe CLI / Dashboard test clock; never switch `MOBILE_BILLING_MODE` to live.

1. **Products & prices** — confirm Dashboard test products match `MOBILE_BILLING_PLANS`.
2. **Checkout** — signed-in founder (or golden QA once membership exists) starts Checkout; land on success/cancel URLs; UI must not grant access from the query string alone.
3. **Webhook delivery** — Stripe CLI forward to `https://www.thejobpursuit.com/api/mobile/billing/webhook` with signing secret matching env; deliver `checkout.session.completed` test event; confirm signature accept + SQL lifecycle write.
4. **Renewal / failed payment / cancel+expiry** — test-clock or Dashboard simulations; confirm access revoked after expiry (migration 0014 double gate).
5. **Portal** — customer portal opens with configured portal configuration; return URL works.
6. **CI** — re-run billing checkout suite on Linux CI under pinned FastAPI; prior failure was checkout `503` on unpinned deps.


## 17 Sep 2026 sandbox evidence (test mode only)

Hard rules held: no live charges; keys remain `sk_test` / `whsec`; no employer submissions.

| Checklist item | Result | Evidence (sanitized) |
| --- | --- | --- |
| 1. Products & prices | **DONE** | Catalog `pro` → Stripe test price active, `aed` 1900/month, product "Job Pursuit Pro", `livemode=false`. |
| 2. Checkout | **DONE** (prior same-day test sub) | Stripe test Checkout session `cs_test_…` completed/paid; billing operations `customer` + `checkout` both `complete` for founder billing account. |
| 3. Webhook delivery | **DONE** after secret repair | Dashboard endpoint signing secret had drifted (POST `/api/mobile/billing/webhook` → 400). Rotated to new test endpoint `we_…` (old disabled), updated Mac `.env.mobile`, restarted mobile LaunchAgent. Fired `customer.subscription.updated` in test mode; account `updated_at` advanced; membership limits refreshed. |
| 4. Entitlement | **DONE** with operator note | After converting founder `mobile_usage_memberships.grant_source` from `manual` → `billing` (manual grants are intentionally never rewritten by webhooks), webhook apply set `enabled=true`, `period_limit=100`, `daily_limit=10`, `expires_at`/`paid_through` through mid-Oct 2026, `plan_key=pro`, account `status=active`. |
| 5. Portal | **DONE** (API) | Stripe Billing Portal session created for test customer; URL host `billing.stripe.com`; return URL matches env. |
| 6. Renewal / fail / cancel | **OPEN** | Not simulated this session (test clock / payment-fail / cancel+expiry still outstanding). |
| 7. CI checkout suite | **OPEN** | Billing unit module import path still broken locally (`test_mobile_api` import); Linux CI not re-run here. |

### Gate status

**PARTIAL → core path DONE.** Checkout → webhook → entitlement proven in Stripe **test** mode on Mac+tunnel. Remaining: renewal/fail/cancel simulations + CI. Hosting stays Mac+tunnel per founder (EXT-HOST-01 deferred). Studio UI remains paused.

### Operator notes

- Do not commit `.env.mobile` secrets.
- Manual memberships must be explicitly converted before billing webhooks can entitle that user (by design).
- Keep only one enabled Stripe webhook endpoint for `www.thejobpursuit.com/api/mobile/billing/webhook` and keep its `whsec` aligned with Mac env.
