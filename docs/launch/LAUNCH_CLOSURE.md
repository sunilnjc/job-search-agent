# Launch closure board

Prepared: 18 September 2026  
Environment: authorized cloud clone of `github.com/sunilnjc/job-search-agent` (not the founder Mac).  
This file is source-of-truth for this run. The 17 September Mac-local review/handoff files were never committed.

## Repository state verified

| Layer | Value |
| --- | --- |
| Remote | `https://github.com/sunilnjc/job-search-agent` |
| Base reviewed | `main` at `6ea6652` (PR #10, after the handoff HEAD `93cfd1f`) |
| This branch | `cursor/launch-closure-website-blockers-0e5e` |
| Fix commit | `f9069bc` |
| Working tree at start | clean; no uncommitted Mac docs |
| Secrets in this VM | none (`.env` absent; `.env.example` placeholders only) |

Distinguish: **saved locally** (this cloud workspace) → **committed** on the branch → **pushed** for review → **not deployed**. A GitHub push does not update https://www.thejobpursuit.com.

Open PRs that overlap this work and were **not** merged into `main`: #7 (contact/requirements + UI density), #8 (studio visual), #9 (`/readyz` docs + Stripe notes). This branch rebases the internally solvable product fixes onto current `main` without taking unrelated UI redesigns.

## Reproduction (current `main`, before this branch)

Executed against `6ea6652`:

1. `_without_contact("Phone 971501234567")` returned the number unchanged. A profile `summary` containing `synthetic@example.invalid` and `+971 50 123 4567` reached `_context` payload JSON. Preference notes, answers, qualification `evidence_note`, and chat/history were also unfiltered.
2. `_requirements("Requirements:\n- python\n- kafka")` returned one merged clause `("Requirements: python kafka", "required")` because list markers were stripped before lowercase-continuation detection.
3. Catalog tie-break with a specialist (1 software tag) vs a generalist (7 tags), same US geography: generalist sorted first. Comments said specialist preference; the sort key used `-min(len(tags), 8)`.
4. `BetaApp` billing `useState` initializer called `history.replaceState` while `StrictMode` is on (`web/src/main.tsx`). Not reproduced as a production checkout failure; treated as a purity bug.
5. Deployed `/readyz` on 17 September was reported as HTML. **Superseded on 18 September:** live `GET https://www.thejobpursuit.com/readyz` returns `200 application/json` `{"status":"ready","service":"job-pursuit-mobile","scope":"configuration"}` with `cache-control: no-store` and `x-request-id`. Live `/api/mobile/readyz` is `404`. Live `/healthz` is still SPA HTML.

## Concrete changes (`f9069bc`)

- Studio: strip bare phones; scrub names/emails/phones/links in every `_context` fact, qualifications dump, answers, resume, chat message, and chat history. `_identity` still renders original profile contact on authorized documents.
- Discovery segmentation: detect list markers (`-`, `•`, `1.`) before stripping; do not join a new bullet or a colon heading as a wrapped line.
- Catalog: on score+geography ties, fewer profession tags win (specialist first). Geography still outranks tag-count.
- Billing UI: `billingReturnLocation` is a pure function; React state only *reads* the query; `replaceState` happens in `useEffect`.
- Readiness: `/readyz` and `/api/mobile/readyz` on the mobile API. Combined web process still requires the static build (`scope=configuration_and_static_build`).

Residual by design: postal addresses are not stripped. Do not claim anonymization, E2EE, or legal compliance.

---

## Gate A — Security and privacy

**Initial state:** Ownership/RLS/privacy-probe tests already exist. AI path still sent structured contact. No live two-user isolation in this environment.

**Changes:** Contact minimization across assessment (`rank_job`), chat, and document generation payloads (all use `_context`; extraction is local PDF/DOCX, not a model call).

**Tests:** `LaunchClosureContactMinimizationTests`; full gate 1085/1085 including privacy SQL (7+16). Cross-tenant mocked API tests remain.

**Evidence:** commit `f9069bc`; regression `/tmp/regression-result-f9069bc.json` (copied into this doc). Live anonymous: `/api/mobile/bootstrap` `401` JSON `no-store`; `/admin` Cloudflare Access `302` `private, no-store`; `/.env` returns SPA HTML (not a secret file).

**Remaining:** Deploy this commit. Live User A ↛ User B isolation, backup restore, incident alerts, administrator MFA, and AI-provider retention/training settings are not verified here. Privileged privacy-worker credentials were not found in the public frontend source; they were not proven absent from the live host.

**Result: PARTIAL — EXTERNAL BLOCKER** (live isolation / provider retention / ops) plus **deploy of this fix** (owner). Internally solvable contact leakage is fixed in source, not yet on the website.

---

## Gate B — Complete deployed customer journey

**Initial state:** Anonymous `/beta` `200` HTML. No authenticated session in this VM.

**Changes:** none to the golden path itself; billing return init no longer mutates history during render.

**Tests:** frontend `104` passed + production build; mocked journey/privacy probes in the Python gate.

**Evidence:** live `/beta` `200` `text/html`, `last-modified: Thu, 17 Sep 2026 17:11:51 GMT`, workflow meta `2026-09-15-core-flow-v2`. Browser (anonymous, 18 Sep): passwordless create-account/sign-in landing loads; headline “Start with your career, not another form.” No signup/profile/resume/discovery/prepare/download was executed (no disposable invited account).

**Remaining:** Owner-authorized synthetic customer on the **deployed** site after this API/frontend is released. Mobile layout and failure recovery not browser-tested while signed in.

**Result: PARTIAL — EXTERNAL BLOCKER** (authenticated hosted journey). Not an internal implementation gap for the anonymous shell.

---

## Gate C — AI quality (six personas)

**Initial state:** Studio grounding/rubric tests exist; no live six-persona eval in this run.

**Changes:** Contact minimization only. No change to factuality classifiers.

**Tests:** studio `44` passed (offline stubs). No live OpenAI/Anthropic calls (no keys).

**Remaining:** Bounded live calls for the six personas, with SUPPORTED/REPHRASED/INFERRED/UNSUPPORTED/HALLUCINATED scoring against source facts. That needs provider credentials and spend approval.

**Result: PARTIAL — EXTERNAL BLOCKER**

---

## Gate D — Payments

**Initial state:** Stripe test provider, webhook allow-list, clock-skew, and reconcile logic already on `main`. Handoff billing run had two PostgreSQL class-setup errors.

**Changes:** Pure billing return URL helper (view hint only; still never payment proof).

**Tests:** `test_mobile_billing*.py` **96 passed, 0 skipped** with disposable PostgreSQL 16. SQL class `BillingPostgresTests` **13/13**. Frontend billing tests **12/12**.

**Evidence:** local SQL + mocks only. This VM has no Stripe secret, webhook secret, or price IDs. No sandbox charge, webhook, cancellation, or renewal was attempted.

**Remaining:** Owner Stripe **test-mode** lifecycle on a disposable account (checkout → signed webhook → durable entitlement → logout/login → cancel). Never live charges. Confirm current test keys in the host secret store without pasting them into git.

**Result: PARTIAL — EXTERNAL BLOCKER** (provider sandbox). Internally, billing SQL coverage now runs; that was an engineering gap on the 17 September Mac run, not an owner blocker.

---

## Gate E — Hosting

**Initial state:** `deploy/` Docker/Compose scaffolding; DigitalOcean purchase not confirmed. Public site behind Cloudflare.

**Observed live (18 Sep 2026), anonymous:**

| Path | Status | Body |
| --- | ---: | --- |
| `/beta` | 200 | HTML SPA (`last-modified` 17 Sep 17:11:51 GMT) |
| `/` | 200 | same SPA HTML (no redirect to `/beta`) |
| `/api/mobile/health` | 200 | JSON `{"status":"ok","service":"job-pursuit-mobile"}` `no-store` |
| `/api/mobile/version` | 200 | JSON workflow `2026-09-15-core-flow-v2` `no-store` |
| `/api/mobile/bootstrap` | 401 | JSON, Bearer, `no-store` |
| `/api/mobile/readyz` | 404 | JSON `{"detail":"Not Found"}` until this branch is deployed |
| `/readyz` | 200 | JSON ready/configuration `no-store` + `x-request-id` (API, not SPA) |
| `/healthz` | 200 | SPA HTML (catch-all; not FastAPI `/healthz`) |
| `/admin` | 302 | Cloudflare Access |
| `/api/jobs` | 302 | Cloudflare Access (founder API still gated, not tenant 404) |
| `/.env` | 200 | SPA HTML, not env contents |

Placement: Cloudflare in front of a **split** SPA host (catch-all HTML, including `/healthz` and `/.env`) and a FastAPI mobile API (`/api/mobile/*` and `/readyz`). Origin OS (Mac vs Linux) is not visible through Cloudflare. Mac independence is **not proven**. `deploy/` remains scaffolding, not a cutover.

**Changes:** API `/readyz` + `/api/mobile/readyz` in source so `main` matches the live `/readyz` contract and the API prefix can be probed without a special CF rule.

**Remaining:** Owner must merge/deploy this API, then keep `/readyz` routed to FastAPI (not the SPA). Always-on Linux host, secret scoping, worker recovery, restart drill, and budget/purchase remain owner decisions. Do not buy DigitalOcean/Oracle/K8s from this agent.

**Result: PARTIAL — EXTERNAL BLOCKER**

---

## Gate F — Regression

**Initial state:** GitHub `workflow_dispatch` run `34979249804` failed on old `c41b886`. Later success `35248936555` was on `fix/studio-ui-density`, not `main`.

**This run (Linux, disposable PostgreSQL 16.15):**

```
python3 -B scripts/regression_gate.py --summary /tmp/regression-result-f9069bc.json --timeout 600
```

| Field | Result |
| --- | --- |
| gate | **PASS** |
| discovered / executed / passed | **1085 / 1085 / 1085** |
| skipped / failures / errors | **0 / 0 / 0** |
| SQL suites | all listed classes discovered and passed (2+13+7+16+8+12+3) |
| elapsed | 44.505s |
| commit | `f9069bc` |

Targeted (same commit):

| Suite | Result |
| --- | --- |
| `test_discovery*.py` | 62 passed |
| `test_mobile_studio.py` | 44 passed |
| `test_moments.py` | 5 passed |
| `test_mobile_billing*.py` | 96 passed (includes real SQL) |
| `web` `node --test tests/*.test.mjs` | 104 passed, 0 skipped |
| `web` `npm run build` | pass; `index-DVw6zrq7.js` 368.71 kB / 106.15 kB gzip |

Missing GitHub Actions dispatch on this branch is owner/CI permission, not a local skip. Do not treat the stale failed `main` workflow as this commit.

**Result: PASS** (this commit, this Linux gate). Hosted CI on `main` after merge is still owner-triggered (`workflow_dispatch` only).

---

## Launch recommendation

**Do not launch publicly on the current website.**

Justified by remaining evidence gaps, not by unfinished contact/requirements/catalog/billing-init code on this branch:

1. These source fixes are **not deployed**. Production frontend is dated 17 Sep 17:11:51 GMT; `/api/mobile/readyz` is still 404.
2. Authenticated golden journey, six-persona live AI eval, and Stripe **test-mode** lifecycle were not run (no invited user, no provider/sandbox keys in this VM).
3. Hosting is still a Cloudflare split (SPA catch-all + API). Mac independence, backup restore, and worker recovery are unverified.
4. Cross-user isolation is proven in mocked tests, not with two live disposable accounts.

After merge + deploy of this commit, re-probe `/api/mobile/readyz` and `/readyz`, then run the owner-only list below. Until those hosted proofs exist, launch stays **no**.

## Owner-only actions

1. Review and merge this PR; deploy the API **and** frontend together; confirm `/api/mobile/readyz` is `200` JSON and `/readyz` remains JSON (not SPA HTML).
2. Dispatch GitHub “Full offline regression gate” on the merged SHA (workflow is manual).
3. Invited disposable account: signup → profile → resume → preferences → discovery → save → refresh → assessment → tailored resume + cover letter download (no real employer submit).
4. Stripe **test mode** only: checkout → webhook → entitlement → logout/login → cancel. No live charges.
5. Confirm AI-provider retention/training settings before any privacy wording.
6. Decide/approve always-on Linux hosting; do not assume the Mac tunnel is gone.
7. Two disposable users: prove A cannot read B’s artifacts via API IDs/paths.
8. Backup restore drill, alerting destination, administrator MFA — if not already done off-repo.
9. Keep `/admin` and founder `/api/jobs` on Cloudflare Access; do not route them to the tenant API.
