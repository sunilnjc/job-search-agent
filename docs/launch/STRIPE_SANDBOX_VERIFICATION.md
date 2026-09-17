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

## Gate status

**PARTIAL** — test credentials present + webhook fail-closed verified; full sandbox lifecycle (checkout → webhook → entitlement) still open. No live charges attempted.

## Owner actions still open

- Complete checklist items 1–6 and attach sanitized evidence to this file (status codes, event types, no customer PII, no secrets).
- Keep live-mode keys out of chat and out of the Mac pilot env until an explicit go-live decision.
