# Job Pursuit — Week 1 status

## Current verification — 27 September 2026

**W1: PARTIAL. W2 not started.** This section supersedes the historical 18 September deployment/404 notes below. Freeze remains: no LinkedIn, auto-apply, iOS or live Stripe.

### Deployment provenance

- Fetched `origin/main`: `a7c9b42fa188632c6d099c37d9afc07f0b880a2d`.
- Host checkout HEAD: `9de103105bcc84a12be6d9b83a10d5393ca002b6` (PR #13). It is one README-only commit behind fetched main; no application changes in that committed delta.
- Host checkout contains pre-existing uncommitted web changes. Preserved without pull, build or restart.
- Live index and referenced JS `index-BWYL5vBE.js` and CSS `index-u6_sTfTQ.css` are byte-identical to local `web/dist`. Bundle includes Discover/Rank/Prepare/Review; owner also reports these tabs. This is not a fresh signed-in browser observation.
- API version returns only workflow contract `2026-09-15-core-flow-v2`, no Git SHA. Exact deployed backend HEAD and source-to-build provenance are **unproven**. Last-Modified and asset parity do not prove clean-commit deployment.

### Actual read-only live checks

| Request | Observed result |
| --- | --- |
| `/beta` | 200 HTML, Last-Modified `Fri, 18 Sep 2026 16:24:57 GMT` |
| JS/CSS referenced by index | Match local build bytes |
| `/api/mobile/health` | 200 JSON, status ok |
| `/api/mobile/readyz` | 200 JSON, status ready, scope configuration |
| `/api/mobile/version` | 200 JSON, workflow contract only |
| `/api/mobile/bootstrap` unauthenticated | 401 JSON |

Configuration readiness does not prove live database/AI access or a completed authenticated journey.

### Golden invited-account smoke — BLOCKED, not passed

Attempted browser discovery with the available computer-use tool. It returned no browsers because the Mac is locked and automatic unlock failed. Owner confirmed they are away from the laptop. No alternate authenticated session was fabricated and no auth protections were bypassed.

Still unexecuted this session: Discover → Rank → Prepare → Review, live eligibility checks and magic-link success/expired-link recovery. No AI requests or employer submissions were made.

### Current exit checklist

- [ ] Exact deployed build/backend SHA recorded (checkout lag is README-only, live SHA unavailable).
- [ ] Fresh signed-in observation of the four wedge tabs (bundle/owner evidence currently available).
- [x] `/api/mobile/readyz` returns 200 JSON.
- [ ] Invited synthetic-account full loop, packet review and opened downloads.
- [ ] Work-rights uncertainty displayed honestly in Rank/Prepare.
- [ ] Invited-account auth success and failure recovery verified.

### Next actions

1. Owner unlocks Mac and opens beta in an existing invited synthetic golden account. Do not modify the founder's real profile.
2. Run the full loop, check persistence/eligibility, prepare one bounded test packet and inspect grounding/downloads. No employer submission.
3. Establish exact deployed source/build identity without overwriting the dirty checkout.
4. Only if W1 exit is green, begin W2 grounding merge-blocker and first 3–5 ICP invites. Obtain actual approved recipients; do not invent contacts or publish their private email addresses in Git.

This status refresh uses an isolated documentation worktree. No production restart, deployment, database mutation, invitation or freeze-list expansion. Merging the documentation PR does not mark W1 complete.

---

## Historical baseline — 18 September (superseded above)

**As of:** 2026-09-18 (re-probed from Cursor cloud clone)  
**Authority:** `job-pursuit-weeks-1-4-scorecard.md` · `job-pursuit-focus-plan.md` §4–§5  
**Source SHA on `main`:** `9de1031` (merge of PR #13 focus-plan wedge)

## Verdict

**Week 1 is Partial — not complete.** Source wedge is on `main`. Live site is still the 17 Sep SPA. Cloud agents cannot deploy the Mac/Cloudflare Tunnel host.

## Live probes (2026-09-18)

| Probe | Result |
| --- | --- |
| `GET /beta` | `200 text/html`; `last-modified: Thu, 17 Sep 2026 17:11:51 GMT`; meta `job-pursuit-workflow` = `2026-09-15-core-flow-v2` |
| `GET /api/mobile/health` | `200` `{"status":"ok","service":"job-pursuit-mobile"}` |
| `GET /api/mobile/readyz` | `404` `{"detail":"Not Found"}` |
| `GET /readyz` | `200` JSON ready / configuration |
| `GET /api/mobile/version` | `workflow_contract: 2026-09-15-core-flow-v2` |

## Source vs live

| Item | `main` (9de1031) | Live |
| --- | --- | --- |
| Primary nav | Discover / Rank / Prepare / Review | Pre-wedge (Today / Discover / Documents / Tracker) |
| Eligibility-first Rank | Present | Not live |
| Billing demoted to dormant operator details | Present | Not live |
| Magic-link `otp_expired` UX (PR #12) | On `main` | Not live until SPA deploy |
| `/api/mobile/readyz` route | On `main` (PR #11) | Still 404 |

## W1 exit checklist

- [ ] Deploy current `main` SPA **and** API together on the host behind Cloudflare Tunnel
- [ ] Live `/beta` shows Discover / Rank / Prepare / Review
- [ ] `GET /api/mobile/readyz` → `200` JSON
- [ ] Owner golden smoke on invited account: Discover → Rank → Prepare → Review (no employer submit)
- [ ] Live Rank/Prepare show work-rights eligibility before AI fit
- [ ] Magic-link success/failure UX live for invite allowlist

## Ordered leftovers (execution)

1. **Owner (Mac / tunnel host):** `git pull origin main` and restart the usual FastAPI + static build path Codex used. Cloud Cursor cannot SSH that host.
2. **Owner or cloud after deploy:** re-probe the table above; fail if SPA `last-modified` is still 17 Sep or nav still shows Today/Tracker.
3. **Owner:** golden invited-account smoke; log blockers only (no freeze-list features).
4. **Then:** open Week 2 (grounding bar process + first ICP invites).

## Explicit non-claims

- No babysit-free design partner.
- No claim that merge of PR #13 updated production.
- No Linux cutover via `deploy/` Compose (scaffolding only; Mac deployment remains current).
