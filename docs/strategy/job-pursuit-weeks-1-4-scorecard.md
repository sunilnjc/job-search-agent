# Job Pursuit — weeks 1–4 status scorecard

**As of:** 2026-09-18  
**Authority:** `job-pursuit-focus-plan.md` §4–§9 · `job-pursuit-handoff-brief.md`  
**Week 1 detail:** `job-pursuit-week1-status.md`  
**Honest scope:** Weeks 2–4 are planned tracking slices derived from a focus plan that treated “2–4 weeks” as one bucket. Only Week 1 has verification evidence so far. No inventing shipped work.

## How weeks are sliced (derived, not in the focus plan)

Focus-plan §4 lists five outcomes for a single 2–4 week horizon. §5 freeze, §6 success criteria, §7 non-goals, §8 founder-vs-product, and §9 monetization (Stripe off) sit alongside that bucket — they do not define week boundaries.

This scorecard derives a week-by-week sequence so founders can track progress without pretending the original doc already scheduled W1–W4:

| Week | Derived focus | Maps to |
| --- | --- | --- |
| W1 | Deploy wedge SPA+API, golden smoke, live eligibility, API health/readyz + auth path for invited beta | §4 #1–2, #5 (source→live); §5 hide/demote live |
| W2 | Grounding bar as merge blocker; first 3–5 ICP invites; partner-facing babysit-free fixes | §4 #1, #3, #4 (start); §6 prep |
| W3 | Grow toward ~10 partners; weekly loop check-ins; demote frozen nav if still noisy live | §4 #1, #4; §5 live polish |
| W4 | Push toward §6 (≥10 partners × weekly loop); start 3-week streak clock; monetization SKU decision ready, Stripe still off | §4 #4 close; §6 streak start; §9 decision-only |

### Suggested sequencing (execution order)

1. **W1:** deploy wedge SPA+API · golden smoke · live eligibility · API readyz/auth path
2. **W2:** grounding bar as merge blocker · first 3–5 ICP invites · partner-facing babysit-free fixes
3. **W3:** grow toward 10 partners · weekly loop check-ins · demote frozen nav if still noisy live
4. **W4:** push toward §6 (≥10 partners × weekly loop; start 3-week streak clock) · monetization SKU decision ready · Stripe off

## §4 outcomes × week owner × status

| # | Outcome | Owner week | Status (2026-09-18) |
| --- | --- | --- | --- |
| 1 | Wedge-tight beta loop (babysit-free Discover→Rank→Prepare→Review) | W1 ship live + smoke; W2–W3 partner proof | **Partial** — source nav on `main` (PR #13); live SPA pre-wedge; no partner loop proof |
| 2 | Eligibility first-class in UI | W1 | **Partial** — source Done-ish; not on live `/beta` |
| 3 | Grounding quality bar (invented claims flagged; prepare regressions = blockers) | W2 (process); code exists pre-W1 | **Partial** — review path exists; no post-handoff bar/process; not partner-proven |
| 4 | 10–20 ICP design partners + weekly loop check-ins | W2 start (3–5); W3 grow; W4 ≥10 | **Not started** |
| 5 | Public mobile API healthy for web beta (`/health` + auth) | W1 | **Partial** — live `/api/mobile/health` 200; `/api/mobile/readyz` 404; invite-only auth; SPA auth UX lag until deploy |

Related §5 freeze/hide (not a §4 row): **Partial on main only** — live still shows Today/Tracker until deploy.

## Week 1 — summary

**Status: Partial (not complete).** Strategy handoff ≠ week-1 ship.

| Checkpoint | Status |
| --- | --- |
| Wedge SPA+API deployed live | **Not done** — production SPA last-modified 17 Sep 2026; still Today/Tracker/Documents nav |
| Golden Discover→Rank→Prepare→Review smoke (invited account) | **Not done** — PR #13 owner smoke unchecked |
| Live eligibility in Rank + Prepare | **Partial** — on `main`, not live |
| `/api/mobile/health` + auth path for invited beta | **Partial** — health green; readyz 404; auth not proven for new externals |
| Babysit-free design partner | **Not started** |

Evidence & ordered leftovers: `job-pursuit-week1-status.md`.

### What “complete” means (W1 exit)

1. Current `main` (through wedge merge) frontend and API deployed together.
2. Live `/beta` shows Discover / Rank / Prepare / Review (no Today/Tracker as primary nav).
3. `/api/mobile/readyz` returns 200 (or documented equivalent) after deploy.
4. Owner golden smoke: full loop on invited account without founder intervention.
5. Live Rank/Prepare show eligibility chips / work-rights path.
6. Magic-link success/failure UX live for invite allowlist.

## Week 2 — planned

**Status: Not started** (blocked on W1 deploy + golden smoke).

### Planned outcomes

1. Treat grounding / document-review / prepare regressions as merge blockers; record at least one live packet review pass with invented-claim flags.
2. Invite first 3–5 ICP design partners (sponsorship-aware / international software); magic-link allowlist.
3. Fix partner-facing friction so a partner can finish the loop with minimal founder babysitting (bugs from first sessions only — not new features).
4. Continue §5: no LinkedIn / auto-apply / Stripe / iOS expansion.

| Item | Status |
| --- | --- |
| Grounding bar as CI/merge process | Not started |
| First 3–5 ICP invites | Not started |
| Partner babysit-free loop proof | Not started — blocked on W1 live wedge |

### What “complete” means (W2 exit)

1. Documented grounding bar: failing invent-claim / prepare regression blocks merge.
2. ≥3 ICP partners invited and able to auth on live beta.
3. ≥1 partner completes full loop once with ≤ light founder help (or issues logged and fixed without productizing freeze list).
4. W1 exit criteria still green on live.

## Week 3 — planned

**Status: Not started.**

### Planned outcomes

1. Grow design-partner roster toward ~10 ICP (still weekly loop completion, not signups).
2. Establish weekly check-in cadence: did they finish Discover→Rank→Prepare→Review this week?
3. If frozen surfaces (Tracker depth, billing theater, Today leftovers) are still noisy on live, demote/hide further — freeze ≠ delete.
4. Harden partner-reported grounding/eligibility honesty issues only.

| Item | Status |
| --- | --- |
| ~10 partners recruited | Not started |
| Weekly loop check-in ritual | Not started |
| Live freeze nav demotion pass | Not started (W1 deploy may clear primary nav; revisit if noise remains) |

### What “complete” means (W3 exit)

1. Partner count approaching 10 ICP; weekly loop check-ins running (artifact: roster + completion notes).
2. Live beta still wedge-tight; frozen nav not competing with core loop.
3. No expansion of §5 / §7 non-goals.

## Week 4 — planned

**Status: Not started.**

### Planned outcomes

1. Push toward focus-plan §6: ≥10 design partners completing ≥1 full loop/week.
2. Start the 3-week streak clock once ≥10 partners are on the weekly loop (streak itself spans into following weeks — W4 starts the clock, does not finish §6 alone).
3. Zero critical grounding failures in partner packets during active check-ins (or open severity process).
4. Monetization SKU decision ready per §9: default packet credit packs, alternate 30-day search pass — decision documented; Stripe remains off.
5. Partners cite eligibility honesty + grounded packet as return reason (qualitative check-in), not “more jobs” / auto-apply.

| Item | Status |
| --- | --- |
| ≥10 partners on weekly loop | Not started |
| 3-week streak clock started | Not started |
| §9 SKU decision (Stripe off) | Planned only — recommendation already in focus plan; no live billing |
| §6 fully met (3 consecutive weeks) | Out of W4 alone — W4 starts streak; completion is post-W4 |

### What “complete” means (W4 exit)

1. ≥10 ICP partners with weekly loop check-ins in place.
2. Streak week 1 of 3 recorded toward §6 (or explicit gap list if under 10).
3. Written SKU choice (credits default / 30-day alternate); Stripe still dormant.
4. No live Stripe / GTM theater; freeze list intact.

## §6 / §9 reminder (beyond any single week)

| Criterion | Tracking note |
| --- | --- |
| ≥10 partners × ≥1 loop/week × 3 consecutive weeks, no founder hotfixing | Streak starts in W4 at earliest; verify in weeks after |
| Return reason = eligibility + grounded packet | Check-in question from W3 onward |
| Zero critical grounding failures in window | Depends on W2 bar + W3–W4 partner traffic |
| Stripe off until §6 holds | W4 and earlier: Stripe off |

## Risks carrying into W2–W4

| Risk | Impact on later weeks |
| --- | --- |
| Deploy lag | Blocks all partner work; W2–W4 stay Not started |
| `/api/mobile/readyz` 404 | Undermines “API healthy for web beta” until hosting fix |
| Invite-only auth | Recruitment needs allowlist ops, not open signup |
| No partner proof yet | §4 #1 and #4 cannot close from UI merge alone |
| Grounding bar unowned | §6 zero-failure window cannot start |

**Intentionally frozen (do not schedule into W2–W4):** LinkedIn, product auto-apply, iOS push, live Stripe, outreach growth, CRM parity, marketplace vision.
