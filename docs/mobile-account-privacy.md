# Account privacy lifecycle

Implemented locally; **not deployed or a claim of hosted deletion compliance**.
Migration `0010_account_privacy.sql` follows reviewed `0001`–`0009`. It deletes no
accounts. Existing founder and retained QA users must not be used for erasure QA.

## User flow

Account privacy is available to a signed-in, email-confirmed account even when
paid membership has expired or an invitation has been revoked. The isolated API
still remotely verifies Auth on every request. SQL independently checks the live
confirmed Auth user; stale JWT claims alone cannot recreate a deleted workspace.

1. Request an export. A recent worker heartbeat is required before acceptance.
2. Refresh request status. Download appears only after processing and verified
   private Storage persistence. An expired export cannot be downloaded.
3. If deleting, download your export **first**, then review the warning and type
   `DELETE MY ACCOUNT`. The API and SQL match the verified account email.
4. Accepted erasure immediately freezes workspace access. It queues irreversible
   processing, not a reversible toggle or an immediate “everything is deleted”
   claim. There is no user cancellation after queueing; contact the operator if
   processing is blocked. No automatic retry in the web UI.

Endpoints: `GET /api/mobile/account`, `POST /account/exports {}`,
`GET /account/exports/{request_id}/download`, and `POST /account/erasure` with
`confirmation` and `email`, all under `/api/mobile`. None accepts a target user
ID, provider customer, callback URL, storage path, or administrator key.

## Separate privileged worker

Run `scripts/privacy_worker.py` in a separate trusted server process with:

```text
MOBILE_PRIVACY_SUPABASE_URL=<same reviewed Supabase HTTPS project origin>
MOBILE_PRIVACY_SUPABASE_SECRET_KEY=<server-only secret, never web or iOS>
```

Default execution is a configuration-only dry run: **no network or heartbeat**.
`--execute` consumes one already-authorized queued request. An existing operator
supervisor should run it at least once per minute and alert on blocked results.
`--execute --request-id <reviewed-request-uuid>` resumes a blocked job; it does
not create a request. Do not run against hosted data until synthetic database,
Storage and subscription lifecycle acceptance has passed.

The worker holds a database lease; stale completion receipts cannot overwrite a
later claim. It calls only the configured project and the reviewed billing
adapter. Origins for billing and privacy must match. Account APIs never load the
worker secret. Never store this environment in Git or expose it to the browser.

Export writes also require the current lease in the Storage `x-metadata` header.
A database trigger checks the exact owner/request path, locks the live Auth user,
and serializes against erasure. Queueing erasure revokes pending export leases;
late writes remain rejected after the anonymous receipt is pruned. Normal
access-time bookkeeping is not blocked after an export completes. A lost request
acknowledgement is explicitly reported as **unconfirmed**, never as proof that
deletion has not started; refresh the retained request status before retrying.

This is a metadata-publication fence, **not proof of immediate object-store byte
erasure**. Supabase uploads bytes before its final metadata transaction and
schedules failed-upload cleanup separately. Validate the deployed Storage
version's permission-probe metadata and failed-upload cleanup in hosted synthetic
acceptance before enabling this worker for real users. Do not introduce a trigger
bypass to accommodate a mismatched deployment.

Export scope: safe Auth contact fields; profile and career context; preferences;
jobs and scores; resumes; application records/attempts/questions/confirmed answers;
model-run records; resume/artifact recovery journals; usage ledger and safe billing
projection. The ZIP includes original and generated private Storage files,
including orphan files that no longer have a document row. It includes a SHA-256
manifest. Password hashes, sessions, tokens, provider credentials and internal
billing customer/event payloads are excluded. Exports themselves are not nested
inside later exports.

Erasure ordering:

1. Claim the trusted queue target; establish the durable billing cancellation
   fence. Unknown or active billing without authoritative cancellation is blocked.
2. Remove all managed source, generated, orphan and export files using the Storage
   API, **not** direct deletion of Storage metadata. Verify no objects remain.
3. Hard-delete the exact Auth user. SQL foreign keys cascade tenant records and
   usage/recovery records. Verify Auth absence and remaining Storage ownership.
4. Remove associated privacy-request history and scrub the target UUID from the
   completed receipt. Only an anonymous operational receipt remains temporarily.

Lost upload acknowledgments reconcile the fixed worker-only export path. Lost
Auth-delete acknowledgments resume using the request/cancellation tombstone.
Unexpected buckets/ownership layouts block for operator review rather than
deleting outside the exact managed user prefix. No refunds, new invoices,
payment-provider customer deletion, or real employer submissions occur here.

## Retention and limits — explicit operational boundaries

- Completed export download: seven days. The worker removes expired ZIPs through
  Storage and clears the path; private RLS blocks downloads after expiry even if
  the worker is down. Operators must monitor actual cleanup, not only expiry.
- Anonymous erasure completion receipts: seven days, pruned on worker heartbeat.
- Active workspace/recovery data persists until explicitly removed or account
  erasure completes; there is no inactivity-based automatic account deletion.
- Billing cancellation tombstones/provider identifiers remain restricted in the
  billing system. Provider financial records, external AI retention, backups,
  infrastructure logs and user-downloaded files are **not** erased by this flow.
  Set/document provider and backup retention separately before public launch.
- Export bounds: 5,000 rows per reviewed table, 2,000 objects, 16 MiB SQL JSON
  payload and 64 MiB ZIP. Oversize accounts need an operator-assisted export;
  never silently truncate. Unknown ownership/scope is an explicit blocked state.
  The bounded inventory/snapshot also gates automatic erasure; unusually large
  accounts need a reviewed batch-cleanup procedure, not an incomplete success.
- API request/concurrency limits remain process-local outside durable AI budget
  enforcement; production needs shared gateway limits and Storage/row quotas.

## Acceptance

`tests/test_mobile_account_privacy.py` executes API checks and real ZIP assembly
against a synthetic Auth/Storage/SQL transport. It checks membership-independent
privacy, owner isolation, confirmation, expiry, worker absence, source/orphan
inclusion, billing and Storage failure stops, and lost Auth-delete acknowledgments.
`tests/test_mobile_privacy_sql.py` executes actual migrations in a disposable
PostgreSQL cluster when a local runtime is available. Auth/Storage schemas are
minimal platform stand-ins; this is not hosted Supabase or payment-provider proof.
`tests/test_mobile_privacy_lifecycle_sql.py` additionally executes two-session
races, stale-lease upload rejection, expiry/pruning and post-erasure write denial.

Primary references: [Supabase Auth user lifecycle](https://supabase.com/docs/guides/auth/managing-user-data),
[Auth admin API](https://github.com/supabase/auth/blob/master/openapi.yaml),
[Storage removal contract](https://github.com/supabase/storage-js/blob/master/src/packages/StorageFileApi.ts),
[Storage upload and failed-version cleanup](https://github.com/supabase/storage/blob/master/src/storage/uploader.ts).
