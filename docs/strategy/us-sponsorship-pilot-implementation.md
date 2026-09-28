# US sponsorship candidate pilot — local implementation

28 September 2026. Isolated sponsorship-pilot branch prepared for GitHub; not deployed. Existing unrelated dirty UI changes excluded and preserved in the original checkout. No migration, new identity field, invitation, AI call, employer submission or payment change.

## Implemented

- Optional onboarding US sponsorship search preset using existing saved preference fields. Explicitly replaces location/region/remote-country preferences; preserves roles, residence, work-rights notes, workplace choice and strictness settings. Does not infer visa status. User still reviews and saves through normal onboarding.
- Posting excerpts surfaced in Discover and saved job details. These are bounded verbatim keyword excerpts, not a classifier or verified employer commitment. Unknown wording is not refusal. No historical sponsor badge without data.
- Privacy guidance: no passport/receipt numbers or visa documents requested. No immigration advice or guaranteed sponsorship/interviews.
- Discovery wording correction: existing authorization alone is unknown sponsorship, not refusal. Combined positive sponsorship and authorization requirements remain conditional so strict search does not pass them as unconditional support.

## Verification

- Isolated branch: 112 frontend tests pass; TypeScript passes. The combined local checkout previously passed 113 tests, including an unrelated UI test excluded from this branch.
- Six new Python pilot regression tests pass.
- Related discovery suite: 60 reported, 59 passed and 1 skipped. The skipped SQL coverage is not a pass.
- Production-format frontend built to a separate temporary directory, not the live `web/dist`.
- Signed-in live Discover, Rank, Prepare and Review navigation checked. The new pilot UI is not deployed, so no visual pass for these additions or complete AI journey is claimed.

## Still required before inviting people

1. Unlock Mac; visually verify onboarding preset and evidence at desktop/mobile widths; run invited golden loop. Check preferences persist and source links work.
2. Review existing strict sponsorship filters with real representative H-1B postings; transfer-specific support is not yet normalized or verified.
3. Integrate official historical employer data only with dated provenance/entity matching; keep historical filings separate from current vacancy support. No such feed is included in this change.
4. Identify approved opt-in candidates privately and provision bounded AI allowances; do not scrape immigration status or publish a roster.
5. Review and deploy the combined dirty worktree deliberately. No automatic production restart was performed.

Freeze unchanged: LinkedIn, auto-apply, iOS, live Stripe.
