# Billing integration contract — test-only, disabled by default

Implementation is provider-neutral and disabled by default. No provider account,
price, customer, webhook destination, subscription or hosted migration was created.
The user has not answered the optional provider-choice question. Stripe test mode
is an **implementation inference**, following the later instruction to finish the
local adapter; it is not a commercial-provider selection or live-payment approval.
`StripeTestProvider` pins `https://api.stripe.com` and API `2024-06-20`, accepts only
`sk_test_...` keys, and rejects live/Connect/wrong-version webhook events. It is
constructed only for explicit server `MOBILE_BILLING_PROVIDER=stripe`; no provider
is selected and checkout is OFF with default configuration. No live-mode path or
environment fake exists. Other providers can implement the same trusted protocol.

## Main API wiring

Construct `BillingService(settings, store, provider)` with a reviewed server-owned
`BillingProvider` implementation. All HTTP transports are explicitly injectable.
No provider class, URL, credential, price or quota is loaded from request metadata.

`billing_capabilities(service=None)` is a synchronous configuration-only helper
for GET status. It performs no network requests; absent/invalid service credentials
or provider configuration return disabled, not an error. It reports
`provider,mode,checkout_enabled,portal_enabled,plan_keys,configuration_ready` only.
It never returns secrets, provider price IDs, caps or subscription assertions.
Main reads the fixed `mobile_billing_export` view with caller bearer, owner filter
and safe select when configuration is ready. Configuration readiness is **not**
migration health, proven payment or permission to call a billed model.

- `await billing.checkout(repo, plan_key)` returns `{"url": "..."}`. Use remotely
  verified `repo.user_id` and `repo.verified_email` after the existing private gate.
  Only an allowlisted logical plan key comes from the browser. The server chooses
  price, quantity (one), customer and redirect URLs. Existing subscriptions require
  portal/reconciliation rather than a second checkout.
- `await billing.portal(repo)` resolves the stored customer using verified identity.
  No caller customer ID or redirect URL is accepted. A cancelled user can still
  manage billing, so this route must not require an active paid membership.
- `await billing.webhook(raw_body, signature)` verifies the exact signed bytes
  before trusting IDs. Main must bound streamed bodies to 256 KiB and reject
  ambiguous/duplicate signature headers before calling it. Exempt only this route
  from user Auth/CSRF, not from body/rate limits or webhook signature verification.
- A checkout return/success URL only refreshes display state. It never invokes a
  grant, increments quota, or passes a "paid" flag to the server.

Errors are sanitized 400/403/409/422/503 as appropriate; missing schema/configuration
gives an actionable 503 mentioning `0009_billing.sql`. Provider/network failures do
not cause a fallback grant or a new idempotency key.

## Worker cancellation — compatible with 0010

`await cancel_for_erasure(user_id, request_id, *, service=None, transport=None,
store_transport=None)` is the privileged worker callback. `prepare_account_erasure`
is an alias with the **same UUID-only signature**. No `MinimalRepo`, bearer, email,
or provider PII is needed in the queue. It is not an API accepting arbitrary user
IDs. The caller must already hold the durable, verified-user erasure claim from
`0010_account_privacy.sql`. Main's `erase_user`/worker calls this before Auth delete.

Returns exactly one of:

```json
{"status":"ready","request_id":"00000000-0000-4000-8000-000000000001","code":"billing_cancellation_confirmed"}
{"status":"blocked","request_id":"00000000-0000-4000-8000-000000000001","code":"billing_cancellation_unconfirmed"}
```

`ready` means billing cancellation is confirmed, **not** that Auth, application
data, or legally retained payment-provider records were erased. No refunds,
proration, new invoices, outstanding-debt settlements or vendor customer deletion
are performed. The Stripe test adapter requests immediate subscription deletion
with `invoice_now=false,prorate=false`, expires open checkouts, then refetches to
confirm no billable subscriptions, open checkout sessions, unresolved creation
intents or future automatic collection. Pending invoice items/payment schedules
need explicit review; a cancellation acknowledgement alone is insufficient.

Missing provider configuration with a customer or pending billing operation stays
blocked. A never-billed identity can be fenced and confirmed entirely in SQL.
That proof requires an extant locked Auth user, no customer, no subscription, no
operation intents, and `status=none`, followed by a durable erasure tombstone.
If Auth is already absent, only a matching confirmed request tombstone is ready;
an absent mapping alone is blocked. All customer/checkout creations must pass the
durable journal, and out-of-band provider customers require operator reconciliation.
An older uncertain creation intent must be operator-reconciled, not replayed past
the provider's idempotency retention period. Unknown billing state is never "free".

Service-only RPCs (all denied to `PUBLIC`, `anon`, `authenticated`):

| RPC | Inputs | Purpose |
| --- | --- | --- |
| `mobile_billing_erasure_status` | `p_user_id uuid` | Read `account_id`, status/lifecycle, cancellation status, `requires_cancellation`; informational only, not an erasure fence. |
| `mobile_billing_cancel_claim` | `p_user_id uuid, p_request_id uuid` | Create/claim irreversible cancellation fence and disable this user's grants; return account/lease/operations or `ready`. |
| `mobile_billing_cancel_finish` | `p_account_id uuid, p_request_id uuid, p_lease_token uuid, p_receipt jsonb` | Confirm only a matching live lease/request with a definitive authoritative receipt. |

Receipt keys are `customer_id`, `cancellation_confirmed:true`,
`no_future_collection:true`, `open_subscription_ids:[]`, `open_checkout_ids:[]`,
`unresolved_operation_ids:[]`. Only the trusted adapter generates this receipt.
An operator cancellation workflow must verify these facts through the provider
before invoking the service-only finish RPC; a manual "done" flag is not proof.

`0009` adds a database Auth-delete guard. Mapped accounts cannot lose their Auth
principal until cancellation is confirmed. The account's `user_id` then becomes
NULL, retaining the minimal internal cancellation receipt. Retries after a lost
Auth-delete acknowledgement find that receipt by unique erasure request UUID.
Protect and review retention of internal provider identifiers; do not expose them
in the account export or claim they have been deleted at the provider.

## Configuration and secret handling

No dotenv/configuration files are read and no keys are logged or included in repr.
`MOBILE_BILLING_CHECKOUT_ENABLED` defaults to `false`; only exact `true`/`false`
are accepted. `MOBILE_BILLING_MODE` is test-only; `live` is rejected. Additional
variables are `MOBILE_BILLING_PROVIDER`, `MOBILE_BILLING_SUCCESS_URL`,
`MOBILE_BILLING_CANCEL_URL`, `MOBILE_BILLING_PORTAL_RETURN_URL` and
`MOBILE_BILLING_PLANS`. The plan map shape is:

```json
{"operator_selected_key":{"price_id":"price_OperatorSupplied","period_limit":100,"daily_limit":10}}
```

These numeric limits are illustrative budget units, **not a commercial plan or
price**. Select real server values only after model-cost and pricing review. No
product/price IDs or prices are hardcoded. Caps must bound all possible billed work.

Stripe additionally requires `MOBILE_BILLING_STRIPE_SECRET_KEY` (test only),
`MOBILE_BILLING_STRIPE_WEBHOOK_SECRET`, and, for checkout/portal,
`MOBILE_BILLING_STRIPE_PORTAL_CONFIGURATION` (`bpc_...`, operator-created in test
mode). Portal configuration is retrieved before creating a portal session: it
must be active, test mode, and have `features.subscription_update.enabled=false`.
The launch portal cannot change plans or initiate prorations. HTTPS success,
cancel and portal-return URLs are server-owned. Do not point test configuration
at production customers or create provider objects while running local tests.

The trusted store uses `MOBILE_BILLING_SUPABASE_URL` plus
`MOBILE_BILLING_SERVICE_ROLE_KEY`. If neither is present it uses the privacy
worker's **pair** `MOBILE_PRIVACY_SUPABASE_URL` and
`MOBILE_PRIVACY_SUPABASE_SECRET_KEY`. A partial billing pair fails closed; keys
from two projects are never mixed. New `sb_secret_...` keys go only in `apikey`;
legacy service-role JWTs also use `Authorization`. No user session is elevated.

## Database authority and ordering

Fixed tables: `mobile_billing_accounts`, `mobile_billing_events`,
`mobile_billing_operations`. `mobile_billing_export` is a security-invoker,
owner-RLS projection with these columns only:

`account_id,user_id,provider,mode,status,plan_key,period_start,paid_through,cancel_at_period_end,cancellation_status,updated_at`

It omits vendor IDs, checkout URLs, event data, payment methods and email. `0010`
should snapshot this view with its explicit user filter; obtain email from Auth.

`0007` membership rows gain `grant_source` (existing rows default to `manual`)
and `billing_account_id`. Webhooks touch quota only when **both** identify this
billing account. An administrator must explicitly adopt a manual membership into
billing control; neither checkout nor an unrelated subscription event does so.

Explicit adoption example, documentation only; replace both dummy UUIDs following
operator identity review. Run with a reviewed privileged SQL session, not a browser.
This changes ownership only: it neither resets counters nor grants access.

```sql
-- psql variables, DUMMY identifiers; never copy these as real account IDs.
\set user_uuid '00000000-0000-4000-8000-000000000001'
\set billing_account_uuid '00000000-0000-4000-8000-000000000002'
BEGIN;
SELECT id FROM public.mobile_billing_accounts
 WHERE id=:'billing_account_uuid'::uuid AND user_id=:'user_uuid'::uuid
   AND lifecycle='open' FOR UPDATE;
UPDATE public.mobile_usage_memberships m
 SET grant_source='billing', billing_account_id=a.id
 FROM public.mobile_billing_accounts a
 WHERE m.user_id=:'user_uuid'::uuid AND m.grant_source='manual'
   AND a.id=:'billing_account_uuid'::uuid AND a.user_id=m.user_id AND a.lifecycle='open'
 RETURNING m.user_id,m.grant_source,m.billing_account_id;
-- Expect exactly ONE affected row; otherwise ROLLBACK and investigate.
COMMIT;
```

Signed events are durable wakeups, not entitlement truth. Claim a per-account
180-second lease **before** fetching provider state. Apply under a matching token,
unexpired lease, account lifecycle and event ownership; an expired worker cannot
overwrite a later snapshot. Event IDs deduplicate processing; event timestamps
are not an ordering authority. Duplicate events after commit are acknowledged
without another grant. Failed/uncertain applies are retriable, never marked applied
before the quota transaction commits.

Only an active subscription with confirmed paid-through evidence and allowlisted
price grants access. Scheduled cancellation stays valid through the earlier of
the paid-through and current-period end. Final cancellation, unpaid/past-due,
paused, trialing, expired, ambiguous or unsupported-price states do not grant paid
entitlements. Expiry still blocks in `0007` even if webhooks stop arriving.

The initial Stripe adapter supports one licensed recurring price, quantity one,
card Checkout, no trial/pause/schedule/pending update. A current invoice must be
paid, not out-of-band, with positive payment and exactly one non-prorated line
matching the subscription, item, price and exact current service period. Its
charge is separately fetched and must be captured, successful, unrefunded and
undisputed, for that customer/invoice. Zero-value/credit-only or mixed invoices,
multiple active subscriptions and unsupported structures are conservatively
ineligible. Invoice/charge bodies are transient and never persisted/exported.

Supported webhook wakeups are subscription create/update/delete/pause/resume and
pending-update events, invoice paid/payment-failed/action-required/voided/
marked-uncollectible/updated, Checkout completed/expired, charge refunded/updated.
Unknown signed event types are ignored. Dispute-specific events without customer
routing are not supported yet; operational dispute reconciliation is a remaining
release gate. Do not claim all Stripe billing products or dispute workflows work.

Every provider list is scoped to the stored customer, validates test mode and
customer on every object, and pages at most ten 100-item pages. Missing/malformed,
duplicate/cyclic, over-limit or cross-customer results fail closed. Every response
is capped at 1 MiB, webhook body at 256 KiB, signature header at 4 KiB. Connections
time out at five seconds, requests at twenty seconds; redirects, environment
proxies and automatic retries are disabled. An expired 180-second database lease
cannot apply an entitlement/cancellation receipt. Erasure never recreates an
uncertain customer/session; pending creation intents require operator reconciliation.

Same-period/replayed/prorated/overlapping snapshots preserve `period_reserved`.
Only a strictly later, nonoverlapping paid cycle resets that period counter.
Daily usage and the reservation ledger are never reset. Ineligible snapshots
preserve dates/counters so cancel/reactivate cannot recover already spent quota.

`0009` dynamically detects the later `0010` erasure queue (no forward DDL
dependency). Queued erasure blocks new checkout/reconciliation, while `0010`'s
membership trigger and `mobile_has_access` tombstone independently prevent
re-enablement. In-flight external creation may still need receipt reconciliation;
the cancellation worker cannot declare success while such an intent is unresolved.

## Verification and remaining release gates

`tests/test_mobile_billing.py` uses explicit MockTransport plus actual local API
contracts and static SQL checks. Its RPC simulation is **not SQL execution**.
`tests/test_mobile_billing_sql.py` executes the unmodified 0001..0010 migrations
in a disposable native PostgreSQL cluster with minimal auth/storage test stubs.
It tests real locks, transaction rollback, concurrent claims/replay, quota cycles,
manual members, RLS/grants, queued-erasure fencing, Auth deletion and lost-ACK
tombstone recovery. This is local SQL runtime evidence, **not hosted Supabase
parity or live payment/provider proof**. The required billing SQL gate errors if
the runtime is absent; no skips or runtime environment fake bypass were added.

Run with existing local PostgreSQL binaries (no DSN accepted):

```sh
PYTHONPATH=src:tests .venv/bin/python -B -m unittest -v test_mobile_billing
JOBPURSUIT_POSTGRES_BIN=/absolute/path/to/postgres/bin \
JOBPURSUIT_PSQL=/absolute/path/to/psql \
PYTHONPATH=src:tests .venv/bin/python -B -m unittest -v test_mobile_billing_sql
```

Remaining: commercial-provider/live approval; trusted secrets, server price/cap map,
portal and pinned-version webhook configuration; hosted deployment/grant review;
authorized end-to-end test-provider lifecycle/receipt validation and dispute handling.
Do not enable live checkout until these gates and explicit live authorization exist.

## Primary references verified

- [Stripe webhook delivery, duplicates, ordering and raw-body signatures](https://docs.stripe.com/webhooks)
- [Stripe signature troubleshooting](https://docs.stripe.com/webhooks/signature)
- [Stripe hosted Checkout creation](https://docs.stripe.com/api/checkout/sessions/create?api-version=2024-06-20)
- [Stripe customer portal session](https://docs.stripe.com/api/customer_portal/sessions/create)
- [Stripe authoritative subscription retrieval](https://docs.stripe.com/api/subscriptions/retrieve?api-version=2024-06-20)
- [Stripe complete subscription listing](https://docs.stripe.com/api/subscriptions/list?api-version=2024-06-20)
- [Stripe invoice payment evidence](https://docs.stripe.com/api/invoices/object?api-version=2024-06-20)
- [Stripe invoice service-period lines](https://docs.stripe.com/api/invoice-line-item/object?api-version=2024-06-20)
- [Stripe captured/refunded/disputed charge fields](https://docs.stripe.com/api/charges/object?api-version=2024-06-20)
- [Stripe checkout expiry](https://docs.stripe.com/api/checkout/sessions/expire?api-version=2024-06-20)
- [Stripe schedules](https://docs.stripe.com/api/subscription_schedules/list?api-version=2024-06-20)
- [Stripe pending invoice items](https://docs.stripe.com/api/invoiceitems/list?api-version=2024-06-20)
- [Stripe cancellation parameters and outstanding invoice caveats](https://docs.stripe.com/api/subscriptions/cancel)
- [Stripe idempotency retention](https://docs.stripe.com/api/idempotent_requests)
- [Supabase secret-key headers](https://supabase.com/docs/guides/getting-started/api-keys)
