# Two-Day MVP Implementation Tracker

> **Status:** Local working tracker — intentionally uncommitted until the sprint plan is reviewed.
>
> **Sprint objective:** Transform the existing single-user Job Search Agent into a credible **multi-user MVP vertical slice**: a user can sign in, create a protected career profile, define job preferences, see a focused opportunity workspace, and prepare a grounded application package.
>
> **Time box:** Two focused days. The goal is a functional beta foundation, not a fully scaled LinkedIn/Indeed competitor.

## Scope contract

### Must be complete by the end of the sprint

- [ ] Product information architecture and visual direction documented.
- [ ] Multi-user data model and row-level isolation designed and migrated.
- [ ] Email magic-link authentication working in a beta environment.
- [ ] Onboarding captures profile, role preferences, location/remote policy, and sponsorship requirements.
- [ ] Resume upload/storage is private to the signed-in user.
- [ ] Mobile-first application shell has Today, Discover, Applications, and Profile navigation.
- [ ] Existing job board is adapted to show a user-scoped shortlist/workspace.
- [ ] Existing document/Ask-AI workflow is represented as a protected application studio.
- [ ] No user can access another user’s profile, documents, jobs, or applications.
- [ ] A clean README/blueprint/tracker handoff documents what is working and what is deferred.

### Explicitly deferred

- [ ] Global indexing of all company sites.
- [ ] Arbitrary website scraping and universal ATS submission automation.
- [ ] Paid subscriptions, invoicing, and product-managed credits.
- [ ] Full Google OAuth setup if email magic links are sufficient for beta.
- [ ] Native iOS/Android apps.
- [ ] Recruiter outreach automation at scale.
- [ ] Full production compliance, support tooling, and analytics warehouse.

## Definition of a successful beta slice

1. A new user opens the beta URL and signs in by email magic link.
2. They complete a profile and upload a resume.
3. They select target titles, locations, remote preference, and sponsorship needs.
4. They see an empty-but-correct personal workspace or a clearly scoped sample/demo feed.
5. They can open a job, see why it fits, and create/review a grounded application package.
6. The application is usable on an iPhone.
7. An attempted cross-user request is blocked by database policy and backend authorization.

## Architecture decision

```text
React + TypeScript PWA
        │
Supabase Auth (email magic link)
        │
FastAPI application API / workers
        ├── Supabase Postgres (tenant-scoped data + RLS)
        ├── Supabase Storage (private resumes/documents)
        ├── direct ATS adapters
        ├── model router (provider/model/budget policy)
        └── application preparation workers
```

The existing local SQLite application remains the personal/founder environment until the Supabase path passes the beta acceptance tests. Do not mix user data in the existing shared SQLite database.

## Day 1 — Foundation and experience

### 1.1 Repository and architecture audit

- [x] Identify reusable API routes, data models, source adapters, and drafting services.
- [x] Identify single-user assumptions that must not leak into the multi-user path.
- [x] Define a migration boundary: existing founder data stays local; beta users start with isolated data.
- [x] Record the proposed module boundaries and API contracts.

**Acceptance:** Complete. The current React UI, FastAPI routes, source adapters, ATS handlers, drafting services, and Telegram integration are reusable. SQLite storage, global settings/profile files, output folders, and unauthenticated API routes need tenant-aware replacements.

### 1.2 Product UX specification

- [x] Document desktop navigation: Today, Discover, Applications, Documents, Profile, Settings.
- [x] Document mobile navigation: Today, Discover, Applications, Profile.
- [x] Define five core screens: onboarding, Today, job detail, application studio, applications tracker.
- [x] Define design tokens: typography, spacing, colors, score/eligibility states, empty states, loading states, and error states.
- [x] Define the primary call to action on each screen.

**Acceptance:** Complete. See `docs/mvp-ux-spec.md`; a developer can implement screens without making product decisions ad hoc.

### 1.3 Supabase foundation — user action required

- [ ] Create a Supabase project for the beta.
- [ ] Enable email magic-link authentication.
- [ ] Add allowed local and beta redirect URLs.
- [ ] Create a dedicated project API key/configuration for server-side use.
- [ ] Keep all Supabase credentials in local `.env`; do not commit them.

**Acceptance:** A test user can receive a magic-link email and return to the application.

### 1.4 Tenant-safe schema and storage

- [x] Create `profiles`, `job_preferences`, `resumes`, `jobs`, `job_scores`, `applications`, `artifacts`, `model_runs`, and `application_attempts` tables.
- [x] Include `user_id` ownership on every user-owned record.
- [x] Enable Row Level Security (RLS) on all user-owned tables.
- [x] Write RLS policies: users may only select/insert/update/delete their own records.
- [x] Create private storage buckets/policies for resume and artifact files.
- [ ] Add a migration/seed strategy for local development using synthetic data only.

**Acceptance:** Migration authored at `supabase/migrations/0001_beta_multi_tenant.sql`; it must be applied in the beta project and tested with two accounts before this task is complete.

### 1.5 App shell and onboarding

- [x] Add session-aware routing and protected routes.
- [x] Implement sign-in/sign-out screen.
- [x] Implement profile setup wizard.
- [x] Implement role, location, remote, and sponsorship inputs.
- [x] Implement private base-resume upload plus a documents workspace for upload, default selection, download, and deletion (AI extraction remains a later step).

**Acceptance:** The opt-in beta shell is implemented in `web/src/beta/` and builds successfully. It needs Supabase project configuration plus a live two-account test before this is marked complete.

## Day 2 — Valuable workflow and hardening

### 2.1 Today and Discover experience

- [x] Implement Today dashboard with a personal summary, readiness counts, and a user-scoped manual job capture path.
- [ ] Implement Discover search/filter UI with an explicit “direct source” indicator.
- [ ] Implement job-detail view with source, freshness, eligibility, evidence, risks, and original link.
- [ ] Implement useful loading, empty, and error states for mobile.

**Acceptance:** In progress. Today is user-scoped and supports manual direct-job capture; source ingestion, validation, and ranking remain to be connected.

### 2.2 Application studio

- [x] Create a job-scoped protected workspace with Overview, Documents, Questions, and Final Review tabs.
- [ ] Ensure every generation request is scoped to authenticated `user_id` and selected `job_id`.
- [x] Display generated-material readiness and explicit action-required states without fabricating content.
- [x] Add private resume download controls suitable for mobile; tailored artifact download is pending generation integration.
- [ ] Keep actual premium-model calls behind a model-router interface; use test/mock mode where provider credentials are unavailable.

**Acceptance:** In progress. A user can open a tenant-scoped studio, create a private application record, inspect artifacts/readiness, and record their own confirmed submission. Tailoring, Ask AI, and artifact generation are the next integration.

### 2.3 Application tracker

- [ ] Implement saved/prepared/applied/interview/rejected/offer status transitions.
- [ ] Add a simple event timeline and next-action field.
- [ ] Preserve application artifacts and final audit result by job/user.
- [ ] Add a manual “open employer application” handoff.

**Acceptance:** A user can track a job from recommendation through submitted application.

### 2.4 Security, quality, and beta readiness

- [ ] Verify no `.env`, storage keys, tunnel credentials, resumes, or generated documents are staged in Git.
- [ ] Test RLS with a second account.
- [ ] Test mobile Safari/PWA layout, sign-in, upload, download/share, and sign-out.
- [ ] Test broken URL, no-sponsorship, unknown eligibility, and missing-file states.
- [ ] Add model spend-cap placeholders and visible “premium action” confirmation.
- [ ] Write a beta test script and known-limitations list.

**Acceptance:** The beta can be demonstrated safely to a trusted user without exposing founder data.

## Work sequence

| Order | Work item | Dependency | Owner/status |
|---|---|---|---|
| 1 | Audit current project and freeze scope | None | Complete — founder data stays local; beta begins with isolated users |
| 2 | UX specification and component map | 1 | Complete — `docs/mvp-ux-spec.md` |
| 3 | Supabase project + auth settings | User creates project | Blocked until project exists |
| 4 | Schema migrations + RLS | 3 | In progress — migration authored; awaiting beta project execution |
| 5 | Auth/protected routes/onboarding | 3, 4 | In progress — opt-in client shell and fail-closed backend auth foundation implemented |
| 6 | User-scoped Today/Discover/job details | 4, 5 | Pending |
| 7 | User-scoped application studio/tracker | 4, 5 | Pending |
| 8 | Mobile/security/second-user QA | 6, 7 | Pending |
| 9 | Beta handoff and deployment guide | 8 | Pending |

## Decisions required from the founder

| Decision | Recommended default | Why |
|---|---|---|
| Backend database/auth | Supabase | Fastest secure multi-user foundation for this stack |
| First authentication method | Email magic link | No password storage and quickest beta setup |
| First beta audience | 5–10 invited job seekers | Manageable feedback and support load |
| Job coverage promise | Direct ATS/watchlist only | Reliability over unrealistic global coverage |
| Application action | User confirmation required | Prevents accidental/unsafe submissions |
| Premium model use | Explicitly shown, budget-capped | Quality with transparent spend |
| Existing founder data | Remains private/local | Avoids risky migration during MVP sprint |

## Risks and controls

| Risk | Control |
|---|---|
| Scope expands into a full job marketplace | Keep the MVP promise to a curated shortlist and application studio |
| Authentication/data leakage | Supabase RLS, protected API routes, two-account test |
| Premium model spend grows unexpectedly | Per-run limits, visible model use, caps, no auto-reload |
| Job sources are stale | Direct ATS priority + validation immediately before preparation |
| ATS automation is unreliable | Supported handlers only + exception queue + user confirmation |
| Two-day sprint is too tight | Deliver vertical slice; defer broad source coverage and payment systems |

## End-of-sprint demo script

1. New user opens the beta URL and signs in using an email magic link.
2. User completes job preferences and uploads a resume.
3. User opens Today and sees an explainable shortlist.
4. User opens a role and sees source, eligibility, fit explanation, and risks.
5. User opens Application Studio and reviews tailored resume, cover letter, Ask AI, and audit tabs.
6. User marks the package prepared and opens the employer application.
7. A second account proves it cannot see the first user’s information.

## Sprint notes

- Do not commit this tracker until explicitly requested.
- Do not add real API keys, real resumes, or real user data to seed files.
- When a task is complete, change its checkbox and record verification evidence below it.
- Any feature that makes external changes (submission, sending email, paid provider spend) requires an explicit confirmation flow.

## Audit findings — 8 August 2026

### Reusable now

- React/TypeScript responsive board, job detail modal, mobile navigation, search/filter UI, document sharing, and Ask AI experience.
- FastAPI application shape and background run/status pattern.
- Greenhouse, Lever, Ashby, manual-URL, validation, eligibility, drafting, and ATS preparation modules.
- Existing domain concepts: jobs, scores, status transitions, exclusions, outreach drafts, and application attempts.

### Must be replaced or wrapped for multi-user beta

- SQLite `jobs`, `match_scores`, application attempts, and outreach records have no `user_id` ownership.
- `settings.preferences_path`, `settings.answers_path`, `settings.resumes_dir`, and `settings.output_dir` are global local paths.
- `get_profile()` is a process-global cached founder profile.
- API endpoints have no authenticated identity or ownership checks.
- Current job URLs are globally unique and cannot represent distinct users saving the same job independently.
- Telegram and Playwright sessions are founder-specific; they stay out of the first public beta path.

### Safe migration boundary

- The existing local SQLite database, founder resume/configuration, generated documents, Telegram bot, and browser profile remain private and unchanged.
- Supabase Postgres/Storage starts as a clean beta environment using synthetic/demo data and newly onboarded beta users.
- Shared canonical job records may be introduced later; the first beta can use user-scoped saved/recommended job records to keep isolation simple.
