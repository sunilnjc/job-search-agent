# The Job Pursuit — native iOS

SwiftUI, iOS 17+, Xcode 26.6. This is a native app, not a WebView wrapper. Supabase is restored and migration 0002 manually applied/verified. **16 live hosted check groups** and **101 Python unit tests** passed; both synthetic QA accounts, fixtures and temporary credentials are cleaned up. Native OTP/email delivery, native test execution and production release remain open in [the launch tracker](../docs/ios-launch-plan.md).

## Profession-neutral expansion — implemented locally

Native editor/API/studio source includes freeform profession, experience level and up to 30 self-reported qualifications with explicit current/expired/in-progress/not-held/unknown status, jurisdiction and optional expiry. Local verification passes; hosted/device acceptance remains pending. User confirmation is **not independent credential verification**. Omitted `career_background` preserves stored data; explicit null clears it. Legacy and sparse records have safe defaults.

The native document default is `DocumentFocus.roleAligned` (wire value `role_aligned`), with `careerChange` (`career_change`) for supported transferable experience. These are the native picker choices. The backend additionally accepts explicit legacy `sse` / `fde` requests; no engineering profession or credential may be inferred for other users. Job title/description guide relevance, while schema validation and source-evidence checks constrain claims. Unknown eligibility remains a question, not automatic exclusion.

Read the [implementation and acceptance tracker](../docs/profession-neutral-implementation.md). **Migration 0003 is additive and not applied; do not replay deployed 0002.** Final checks: **155 Python tests in 10.413 seconds**, **161 native-model assertions**, signed simulator **build-for-testing**, and source scan **36 text files / 1 skipped / 0 findings / 0 errors**, all passing. The 101-unit/16-hosted results above predate this expansion; they do not cover its new database fields.

This work targets native iOS, not completed web-beta/founder migration. It still uses **manual job import**, not all-source discovery or autofilling/submitting every ATS. SMTP/email OTP, public route 404, native execution and TestFlight gates remain open. No hosted changes, live AI requests or key writes are performed in this review.

## Open and explore

Open `JobPursuit/JobPursuit.xcodeproj`, select the **JobPursuit** scheme and an iPhone simulator, then Run. Choose **Explore the design preview** to see the Today, Discover, Studio and Tracker journeys with clearly labelled fictional data. Preview cannot upload documents, use paid AI or submit applications.

The `--preview` launch argument opens that mode immediately. In DEBUG builds, adding `--preview-profile` opens the fictional profile editor for manual rendering QA. The flag's presence is not a completed UI check. Preview never reads or imports founder records.

## Connect the private beta

For a fresh checkout, copy the public-value placeholders in `JobPursuit/Config/Local.example.xcconfig` into a local, ignored `Local.xcconfig`, or use the app's Connection settings. The current local override is already configured with **public values only**, its Git-ignore status is verified, and signed `build-for-testing` passed with it. Supply only the HTTPS mobile API origin, Supabase project URL and public/publishable key. Never supply a service-role key or model-provider secret to the app, and never copy actual project key values into documentation.

The user logged into Supabase and approved continuing the existing beta setup. `the-job-pursuit-beta` was **paused**, which caused the old DNS `ENOTFOUND`; main resumed it and the dashboard explicitly confirmed restoration complete/back online. Auth health now returns **200** using the public key. The read-only baseline was **PostgreSQL 17.6**, the original **9 tables with RLS enabled**, and private `resumes` / `application-artifacts` buckets.

Main applied the **exact `supabase/migrations/0002_mobile_career_workspace.sql`** in SQL Editor under that approved plan; the first run returned **Success. No rows returned**. A fresh read-only query confirmed all three new tables (`candidate_context`, `mobile_questions`, `mobile_answers`), RLS enabled, and `owner_only` policies for `authenticated` with `auth.uid() = user_id` in both `USING` and `WITH CHECK`. This is **manual application, not CLI-ledger application**. Do not blindly rerun 0002 or run a migration push before checking actual schema/history and reconciling the manual record through the approved workflow.

### Current sign-in blocker: email delivery, not DNS

The native app expects a **numeric email OTP**. The inspected Supabase Auth → Emails → Magic link or OTP screen still has a default link-only `ConfirmationURL` template, with source editing disabled. The dashboard indicates custom SMTP is required to edit templates; **no SMTP is configured**. Upgrade Pro / Send Email hook alternatives are shown, but no upgrade, hook, template edit or mail-service change has been performed.

The user has been asked whether to reuse an existing email service or set one up. **No user OTP has been requested.** Wait for that decision and approved numeric-code delivery configuration before attempting a real native sign-in; do not repeatedly request emails. Restored Auth health does not prove email delivery or a user session.

### Local backend versus public availability

The root ignored `.env.mobile` now contains **only the public URL/key**; Git-ignore status is confirmed. The isolated `jobagent.mobile.app:app` server is running on `127.0.0.1:8843` using that file, not the founder API. This turn's verified checks:

| Target | Result | What it proves |
| --- | --- | --- |
| Local `/api/mobile/health` | **200** | Isolated mobile service is alive. |
| Local `/api/mobile/bootstrap`, no bearer | **401** | Unauthenticated workspace access is denied. |
| Local founder `/api/jobs` | **404** | Founder endpoint is not exposed by this ASGI service. |
| `https://www.thejobpursuit.com/api/mobile/health` | **404** | Mobile service is not available at the intended public route. |

For a later deliberate restart from the repository root (do not start a second instance on the occupied port):

```sh
PYTHONPATH=src .venv/bin/python -m uvicorn jobagent.mobile.app:app \
  --env-file .env.mobile --host 127.0.0.1 --port 8843
```

No public deployment or Cloudflare change has occurred. A later approved routing step must expose `/api/mobile/*` with app bearer authentication while keeping `/admin` and legacy APIs founder-protected. The native app requires HTTPS; the loopback checks do not make its configured public API origin usable. Registering the domain with Cloudflare does not replace Supabase services.

### Verified hosted workflows and cleanup

[scripts/mobile_hosted_check.py](../scripts/mobile_hosted_check.py) passed **16 live check groups** against real Supabase + the local mobile API, using two authorized synthetic password-auth identities. Coverage includes profiles/context/preferences, manual job import/dedup/update, private DOCX upload/download/text, question answers/scoped memory and private artifact metadata/download. The artifact was **synthetic, not AI-generated**. API/direct REST and both private Storage buckets were tested for isolation **in both directions**, plus owner resume deletion and fixture cleanup.

The initial deletion assertion saw a stale **200/CDN HIT**; a fresh nonce/no-cache GET returned **`NoSuchKey`**, and an empty listing proved actual deletion. Cache-busting was added **without weakening deny checks**, and the full rerun passed. Supabase documents invalidation propagation and a nonce-based cache bypass; this is cache-handling guidance, not a claim about this project's subscription tier. [Supabase Smart CDN](https://supabase.com/docs/guides/storage/cdn/smart-cdn)

The agent verified run-owned test tables/storage prefixes empty. Main then deleted **both synthetic Auth accounts** after exact-email confirmation; a fresh Auth grid showed only the original founder account. The ignored QA password JSON and ephemeral transport private key were removed, `ios/build` is empty, and no QA credentials remain persisted. No founder data was changed. This validation/cleanup turn sent **no emails**, made **no AI calls** and incurred **no model-provider charges**.

The ignored public-only `.env.mobile` / `Local.xcconfig` and loopback server **8843 remain available for continuation**. Password-based hosted checks do not validate native email OTP, native test execution or TestFlight. The SMTP question is still awaiting the user's reply; public mobile health remains 404 and no Cloudflare/deployment change occurred.

## Build and tests

```sh
cd ios/JobPursuit
xcodegen generate
xcodebuild -project JobPursuit.xcodeproj -scheme JobPursuit \
  -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath SignedDerivedData CODE_SIGN_IDENTITY=- build-for-testing
```

For execution, use an installed simulator's actual ID and the same build directory for `build-for-testing` and `test-without-building`. Retain `CODE_SIGN_IDENTITY=-`. **The final signed simulator build passed.** XCTest stalled in installation/`testmanagerd` handshake, with **no cases started**. Only the owned test process was stopped after about three minutes (exit 75); this is not a runtime pass or assertion failure. Ad-hoc simulator signing is not distribution signing.

### Offline native model contract checks

From the repository root:

```sh
contract_check_dir="$(mktemp -d /tmp/job-pursuit-profession-checks.XXXXXX)"
swiftc -parse-as-library ios/JobPursuit/Sources/Models.swift \
  ios/ContractChecks/ProfessionContractChecks.swift \
  -o "$contract_check_dir/profession-contract-checks"
"$contract_check_dir/profession-contract-checks"
```

Final run: **161 assertions passed** against actual shipping models, including six professions × five statuses, sparse/legacy decoding, date-only values and neutral document choices. The reviewer independently reproduced the earlier 158 checks. No network or credentials are used. This is not XCTest, rendering, authenticated sign-in or TestFlight evidence; legacy variants are covered separately in backend tests.

Manual QA: installation finished but launch stalled. Restarting only the owned simulator reported **`DataMigrationFailed`**. No erase or other-device action occurred. **Visual QA remains unverified.** Final generic and arm64 simulator builds passed, but do not prove runtime behavior.

### Backend checks

Final integrated result: **155 Python tests passed in 10.413 seconds**, including API, studio, profession and offline hosted-harness tests. No paid provider requests were made.

Server tests, from the repository root:

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_mobile*.py' -q
python3 -B scripts/mobile_security_check.py
```

Previously verified baseline: **101 tests in 24.082 seconds** and 16 real hosted groups. These predate the expansion and do not validate migration 0003. Python tests do not prove native execution, public deployment or ATS submission. Rerunning the live harness requires an explicitly scoped synthetic-account/fixture plan.

## Implemented boundary

- Email OTP, Keychain session storage, refresh and sign-out.
- Profile/preferences and explicit review of imported resume facts.
- Private PDF/DOCX upload, document preview and native Save/Share.
- Manual job import, fit analysis and grounded coaching; source now includes default role-aligned/career-change document versions plus explicit legacy SSE/FDE compatibility, with expansion acceptance in progress.
- Unresolved questions, scoped remembered answers and user-reported application tracking.

Automatic job discovery, arbitrary ATS form submission, production account deletion, subscriptions and App Store release are **not** completed. Generated factual content is deliberately extractive and needs quality evaluation before wider use. The launch tracker contains the remaining work and deployment gates.
