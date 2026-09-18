# Handoff brief — for the Job Pursuit / job-search-agent Cursor tab

**From:** Sunil Bot Project (strategy)  
**To:** whatever agent is already building in `~/2026/agents/job-search-agent`  
**Date:** 2026-09-18  
**Authority:** Prefer this brief + linked docs over older “build everything / LinkedIn / auto-apply” chat history.

## Status (2026-09-18)

**UI bug gate: CLEARED.** Founder confirmed recorded beta UI bugs are already finished (other Cursor tab). Do not wait on further bugfix sequencing before wedge work.

**Next:** execute this brief + `job-pursuit-focus-plan.md` outcomes (hide/demote frozen surfaces, harden Discover → Rank → Prepare → Review). Historical inventory: `job-pursuit-bug-queue.md`.

In-repo copies (Job Pursuit tab): `docs/strategy/` in this repository.

## How to use this

Paste this whole file (or open the in-repo `docs/strategy/` copies) into the Job Pursuit Cursor chat and say:

> Recorded UI bugs are done (founder confirmed 2026-09-18). Treat this as the current product brief. Align new work to it. Ask me only if something conflicts with in-repo reality.

**In-flight rule (historical — satisfied as of 2026-09-18, founder confirmed):** Bugfixes and polish on the existing beta UI were in scope and took priority over pivot work. That queue is closed. The freeze list still blocks new feature expansion (LinkedIn, auto-apply productization, interview product, iOS push, live Stripe); it does not block wedge hide/demote or hardening the core loop.

## Canonical docs

| Doc | In-repo mirror |
| --- | --- |
| Focus plan | `docs/strategy/job-pursuit-focus-plan.md` |
| Product verdict | `docs/strategy/job-pursuit-product-verdict.md` |
| Bug queue (cleared) | `docs/strategy/job-pursuit-bug-queue.md` |
| This brief | `docs/strategy/job-pursuit-handoff-brief.md` |

## Decisions (locked)

1. Pursue Job Pursuit with a **hard pivot** — not as a broad AI career platform / LinkedIn competitor.
2. **Wedge:** ATS-direct shortlist (Greenhouse / Lever / Ashby) + fact-grounded application packets for sponsorship-aware / international software candidates.
3. **Core loop only:** Discover → Rank → Prepare → Review → user applies externally (product never submits to employers).
4. **Monetization later:** packet credit packs (default), 30-day search pass (alternate). No forever subscription required. Stripe stays off until design partners prove the loop.
5. **LinkedIn automation / auto-apply:** parked — ban/ToS risk; do not productize.
6. **Freeze ≠ delete:** do not delete built features. Stop expanding non-wedge work; hide/demote in beta UI/nav. Keep code.

## What to keep visible and harden (ship)

| Area | Repo targets |
| --- | --- |
| Beta web loop | `web/src/beta/*` |
| Discovery / rank | `src/jobagent/mobile/discovery*.py`, fit/eligibility |
| Prepare + review | `studio.py`, `document_review.py`, `readiness.py`, `writing.py` |
| Auth / profile / privacy | Supabase + `account_privacy.py` |
| Eligibility honesty in UI | rank + prepare surfaces |

**Next 2–4 weeks outcomes:** babysit-free loop; eligibility first-class in UI; grounding quality bar; 10–20 ICP design partners; public `/api/mobile/health` + auth reliable for web beta.

## What to freeze / hide (do not expand)

| Area | Action | Repo |
| --- | --- | --- |
| Auto-apply / Playwright submit | Founder-only; never productize | `src/jobagent/applying/*` |
| LinkedIn | No new work | — |
| Interview-prep product | Chat mode enough; no new prep UI | beyond `studio.answer_chat` |
| iOS / TestFlight push | Park feature expansion | `ios/JobPursuit/*` |
| Live Stripe / GTM theater | Keep dormant | `mobile/billing.py` |
| Outreach | Stub; don’t grow | `outreach.py` |
| Broad CRM parity | Tracking supports; doesn’t sell | extra tracker depth |
| Marketplace vision doc | Not a roadmap driver | `docs/local-future-product-vision.md` |

## Founder agent vs product

| Surface | Role |
| --- | --- |
| SQLite / Telegram / `/admin` / autopilot | Personal dogfood only |
| Beta web + `jobagent.mobile` | SaaS product surface |

Do not merge founder autopilot into the multi-tenant product.

## Success criteria (before monetization)

- ≥10 design partners in ICP complete ≥1 full loop/week for 3 consecutive weeks without founder hotfixes.
- Partners return for eligibility honesty + grounded packets, not “more jobs” or auto-apply.
- Zero critical grounding failures in that window.
- Then turn on Stripe for credits / 30-day pass only.

## Questions the building agent should answer back (once)

1. Does current beta nav already match Discover → Rank → Prepare → Review, or what should be hidden first?
2. What’s the smallest PR to make eligibility visible in rank + prepare?
3. Is `/api/mobile/health` + magic-link auth green for an external design partner today?
4. Any in-flight work that conflicts with this freeze list?

Do not invent a new roadmap. Execute or flag conflicts against this brief.
