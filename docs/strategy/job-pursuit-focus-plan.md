# Job Pursuit — focus plan (wedge)

**Date:** 2026-09-18  
**Authority:** Prefer with `job-pursuit-handoff-brief.md`. Do not invent a broader roadmap.

## Outcome for this wedge

Ship a babysit-free **Discover → Rank → Prepare → Review** loop for sponsorship-aware / international software candidates using ATS-direct shortlists. Hide or demote frozen surfaces in the beta UI without deleting code.

## Product loop

1. **Discover** — search configured ATS boards (Greenhouse / Lever / Ashby) and optional manual posting paste. No AI credits for board search.
2. **Rank** — saved shortlist ordered by work-rights honesty and readiness. Eligibility is first-class; AI fit is secondary and never work-rights proof.
3. **Prepare** — Application Studio: confirm career facts, record work rights, assess fit, generate grounded packet drafts.
4. **Review** — Final check + application records. User opens the employer site and applies externally. Product never submits.

## Beta navigation (ship)

| Tab id | Label | Surface |
| --- | --- | --- |
| `discover` | Discover | Board search + add posting link |
| `rank` | Rank | Saved roles, eligibility-first grouping |
| `prepare` | Prepare | Source resumes + open Application Studio |
| `review` | Review | Packet / application records for external apply |

Default signed-in destination after onboarding: **Discover**.

## Hide / demote (keep code)

- Live Stripe / billing checkout: dormant operator path under Profile only; remove from reconnect / loading primary CTAs.
- Today home as a fifth destination: folded into Rank coaching, not a primary nav tab.
- Tracker CRM depth / interviewing theater: Review copy emphasizes external apply, not pipeline CRM.
- iOS nav parity: web may diverge while iOS feature expansion is frozen.
- Auto-apply, LinkedIn, outreach, interview-prep product UI: no new work.

## Eligibility honesty bar

- Rank and Prepare surfaces must show work-rights status in plain language before AI match scores.
- Unknown / needs_review / ineligible are never implied eligible.
- AI match copy must state it is not ATS score, hiring probability, or work-rights verification.

## Non-goals for this wedge

- Monetization / live Stripe offers
- Auto-apply productization
- LinkedIn automation
- Interview-prep product surface
- iOS / TestFlight feature push
- Broad CRM or marketplace features

## Done when

- Primary beta nav matches Discover → Rank → Prepare → Review.
- Billing is demoted from the reconnect shell.
- Rank + Prepare show eligibility before AI fit.
- Frontend unit tests cover nav labels and eligibility helpers.
- No expansion of freeze-list surfaces.
