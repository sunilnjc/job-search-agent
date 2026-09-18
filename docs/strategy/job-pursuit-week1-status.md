# Job Pursuit — Week 1 status

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
