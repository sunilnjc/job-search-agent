# Founder auth and material-grounding boundary

Scoped implementation for JP-TEST-007 and JP-TEST-005. This is not a hosted
deployment acceptance report and does not supersede the launch testing reports.

## Deployment configuration (required; not applied by this change)

Keep the existing Cloudflare Access owner-only policy, tunnel deny rules and
origin isolation in place. Do not open founder APIs to solve beta feature gaps.
The founder listener now independently validates signed Access application JWTs.
The separate mobile listener and its exact ingress routing are unchanged; there
is no unauthenticated `/api/mobile*` exception on the founder listener.

Configure these variables in the server environment or existing ignored `.env`:

| Variable | Required value |
| --- | --- |
| `FOUNDER_AUTH_MODE` | `cloudflare` (default; no disabled mode) |
| `FOUNDER_ACCESS_ISSUER` | Exact `https://<team>.cloudflareaccess.com` issuer |
| `FOUNDER_ACCESS_AUDIENCE` | Exact founder Access application's AUD, not the beta audience |
| `FOUNDER_OWNER_EMAIL` | One confirmed owner email; no wildcard or multi-user list |
| `FOUNDER_ALLOWED_ORIGINS` | Comma-separated exact HTTPS browser origins, without trailing slash/path; include the owner dashboard origin even when same-origin |

An empty origin list allows only requests without an Origin header; it does not
enable wildcard CORS. Origins are operator-configured, never inferred from Host
or proxy headers. Browser requests with other origins are rejected before actions.
Allowed preflights carry no data and never invoke founder handlers.

Install the existing declared `PyJWT[crypto]>=2.8,<3` dependency using a supported
runtime and compatible cryptography wheel. This checkout's existing `.venv` lacked
PyJWT during implementation. Tests used a temporary dependency directory (PyJWT
2.14.0, cryptography 45.0.7 on Python 3.9), without modifying the shared `.venv`.
Production dependency installation/compatibility remains an operator requirement.

Allow server HTTPS access to the configured issuer's `/cdn-cgi/access/certs` and
keep its clock synchronized. Public keys are cached for five minutes with a
five-second fetch timeout. RS256 signature, exact issuer, audience, expiry/issued
time, optional not-before, application-token type, subject and signed owner email
are checked. Keys are never fetched from token-provided issuer/JWK URLs. Missing
configuration/dependencies deny access (503); invalid/missing tokens deny (401);
non-owner tokens and disallowed origins deny (403). Protected responses use
`Cache-Control: no-store`.

Cloudflare normally forwards `Cf-Access-Jwt-Assertion`. An explicitly supplied
Bearer Access JWT is also verified, not a beta bearer or arbitrary secret. The
email header and `CF_Authorization` cookie alone are not accepted. Retain Access
at the perimeter: this code does not change Cloudflare settings or prove origin
isolation, owner usability, deployment dependencies, or live key rotation.
If `/admin*` and `/api*` currently use different Access applications/AUDs, the
operator must intentionally align the founder application scope before enabling
owner access; do not add a beta/wildcard audience to make a token pass.

This follows [Cloudflare's JWT validation guidance](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/)
and the [PyJWT verification API](https://pyjwt.readthedocs.io/en/latest/api.html).

## Explicit local development only

`FOUNDER_AUTH_MODE=loopback-dev` additionally requires a strong random
`FOUNDER_DEV_TOKEN` of at least 32 characters supplied as a Bearer token on each
request. Both the actual peer and Host must be loopback; Cloudflare/Forwarded/
X-Forwarded headers are denied. Set any allowed local browser origins explicitly.
Use a loopback-bound, non-proxied development listener, and never configure this
mode on a tunnel/public listener. Merely originating from a local reverse proxy
does not authenticate a request. There is no unauthenticated local shortcut.

## Grounding behavior and limits

Generated resume summaries and 4–6 distinct highlights are validated before
returning Markdown. The existing section titles, `(summary, highlights)` parser
contract and PDF/DOCX builder calls are preserved. Generated profile summaries,
job requirements, user chat instructions and earlier AI drafts are not evidence.
Only complete original-resume source units may be selected/reordered, preserving
negation, numbers, qualification and attribution. Ambiguous wrapping is grouped
conservatively. Prompt budgets exclude whole facts, never truncate their qualifiers.

Chat tool batches validate all proposed resume and cover-letter edits before any
write callback. API callbacks repeat validation before touching existing files.
Rejected output returns an unsupported-edit explanation; no provider continuation
is allowed to claim the rejected edit was saved. Malformed/duplicate JSON, wrong
types, fabricated credentials/metrics and missing grounding context also reject.
Callers of `run_application_chat` must pass an explicit `GroundingContext` to edit.

Initial `draft_cover_letter` generation now uses that same cover-letter grounding
contract before returning any text to the service/API write paths. Its prompt
selects complete original-resume facts and explicitly permitted neutral structure;
AI-extracted profile fields and inferred preference/job-based claims are not sources.
Existing date normalization runs before validation, and complete source-backed
name/contact blocks retain their text and line breaks. Forged contact, licence,
achievement, sponsorship and relocation claims reject before PDF or Markdown writes.
The draft API returns a grounding rejection as HTTP 422 instead of a generic error.

This intentionally rejects unverified paraphrases, even plausible ones, and may
require the owner to improve or confirm source facts first. It is an extractive
grounding boundary, not semantic entailment or independent credential verification.
Fixed founder resume sections/taglines and ordinary conversational advice are not
validated or certified by this change. Existing saved artifacts are not
retroactively validated. Successful rendering/storage failure recovery is separate
from rejection-before-write safety.

## Focused offline regression command

With the declared dependencies installed:

```sh
PYTHONPATH=src:tests PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m unittest test_founder_auth test_founder_grounding test_founder_initial_cover_letter -v
```

Tests use locally generated RSA keys, stub JWKS/provider responses and temporary
synthetic files; socket/DNS access is blocked and config `.env` loading is suppressed.
Real owner accounts, resumes, database, OpenAI calls, emails and submissions are
not used. Historical expected-failure audit probes are not modified: their old
failure annotations/exception expectations may need coordinator-owned updates.

## JP-TEST-029 follow-up: explicit submission mode

`AUTOPILOT_AUTO_SUBMIT` now defaults to `false` when absent. An existing explicit
`true` override is preserved; no real `.env` was inspected for its value or edited.
Preparation mode stops at `ready_for_submission` without calling submit. Explicit
automatic mode may submit supported Greenhouse forms without another per-attempt
confirmation; the separate per-attempt confirmation path remains available in
preparation mode. This is not a universal "never submits" promise. See the README's
submission-mode section before any live run; effective deployed mode is unverified.

`tests/test_autopilot_submission_mode.py` tests fresh settings and the single-job
decision boundary with fake ATS/preflight/submit callbacks, fake documents and
mocked persistence. No queue/direct-apply worker, real employer page, browser, AI
provider, email or application is run. Network/DNS/subprocess calls are blocked.
Run the focused regression with:

```sh
PYTHONPATH=src:tests PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m unittest test_autopilot_submission_mode -v
```
