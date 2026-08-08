# Multi-user Job Search Agent beta — UX specification

## Purpose and beta boundary

The beta turns the founder's current personal job-search dashboard into a secure, mobile-first application for individual job seekers. Each account sees only its own profile, documents, preferences, saved roles, application history, and AI-generated content.

The beta helps a user discover, assess, tailor, and prepare applications. It may pre-fill approved factual information, but it **must never submit an application to an external employer site without that user's explicit final confirmation**. CAPTCHA, OTP/2FA, sponsorship ambiguity, and subjective or unknown questions always become an action-required exception.

This is not yet a global LinkedIn/Indeed replacement: broad job ingestion, subscriptions, recruiter outreach automation, and generic ATS automation are deferred.

## Information architecture

### Mobile (primary)

Persistent bottom navigation:

- **Today** — recommended work for the day and eligible roles.
- **Applications** — pipeline and application history.
- **Profile** — personal facts, preferences, documents, and account settings.

Contextual controls:

- Job detail opens as a full-screen sheet with a clear close/back action.
- Application Studio is a focused full-screen flow with a back action and persistent save state.
- Global search is available from Today and Applications.

### Desktop

Use a left sidebar with the same three destinations; keep the primary workspace at 960–1200px wide. On desktop, a job detail can use a two-column layout: role information on the left, application actions/documents on the right. Do not hide mobile-critical actions behind hover-only controls.

## Core screens

### 1. Sign-in and onboarding

**Goal:** establish a private account and capture only the facts required to make safe recommendations.

**Sign-in**

- Brand mark and a one-line value proposition: “Find the right roles, prepare stronger applications.”
- Primary CTA: **Continue with email** (passwordless magic link).
- Secondary option, if enabled: **Continue with Google**.
- Explain that documents and answers remain private to the account.
- After requesting a link, show a success state with the email address, resend timer, and “use a different email.”

**Onboarding steps**

1. **Basics:** name, email (read-only if supplied by sign-in), phone, current city/country, LinkedIn URL optional.
2. **Job preferences:** target roles, seniority, preferred countries/regions, remote preference, relocation willingness, and sponsorship requirement.
3. **Work eligibility:** structured, factual answers only; never infer work authorization. Include an “I will need sponsorship” option.
4. **Documents:** upload a base resume; optional cover-letter template. Show accepted formats, size limit, upload progress, and replace/remove actions.
5. **Review:** editable summary and CTA **Start exploring roles**.

Save progress after each step. If a required fact is absent, explain exactly why a recommendation or application cannot proceed.

### 2. Today

**Goal:** answer “What should I work on now?” in under a minute.

**Header**

- Personal greeting and a compact count: “3 roles ready for you.”
- Search field: “Search jobs, companies, locations.”
- Filter button with active-filter count.

**Sections, in order**

1. **Action required** — cards with blocking items (e.g., “Confirm UK work eligibility”, “Answer one employer question”). Primary CTA: **Resolve**.
2. **Ready to review** — highest-quality eligible matches. Card content: score, role, company, location/work mode, eligibility badge, freshness, and primary CTA **Review application**.
3. **Strong matches** — roles worth inspecting but not yet prepared. CTA: **View role**.
4. **Recently added** — new eligible jobs, capped to avoid overwhelm.

**Direct Apply eligibility**

Show **Direct Apply** only when the role has a supported application path, validated availability, an appropriate document packet, and no unresolved eligibility question. Otherwise show a plain reason such as “Review required: sponsorship not confirmed.”

**Primary CTAs**

- Per-card: **Review application** or **Direct Apply**.
- Bulk action (only for explicitly eligible roles): **Prepare selected**. Never label it “submit.”

### 3. Job detail

**Goal:** make the decision to pursue a role transparent and fast.

**Header**

- Role title, company, location/work mode, score, source, and last validation time.
- External-link CTA: **View original posting**.
- Status chip: New, Matched, Ready, Applied, Excluded, or Action required.

**Tabs**

- **Overview:** concise description, responsibilities, requirements, company/source information.
- **Match:** why it matched, strengths, gaps, and eligibility result. Make inferred evidence visibly different from confirmed user facts.
- **Documents:** selected resume and cover letter, with preview/download/share controls.
- **Application:** readiness checklist, known employer questions, activity log, and direct-apply status.

**Footer actions**

- Primary: **Prepare application** or **Open Application Studio**.
- Secondary: **Exclude** (opens reason selector and optional note).
- For validated direct-apply jobs: **Fill application**. The final action remains **Confirm and submit** inside Application Studio.

### 4. Application Studio

**Goal:** prepare a grounded, reviewable application packet and handle exceptions safely.

Use a stepper with persistent draft saving:

1. **Role fit:** key requirements, selected resume type, and factual match notes.
2. **Documents:** preview/select tailored resume and cover letter. Allow regenerate, edit, and download; record the source version and generation time.
3. **Questions:** present one question at a time with the agent's proposed answer, evidence/source links, edit control, and “Needs my answer” state. Never invent facts.
4. **Review:** show all factual profile answers, documents, fields to be filled, and external site destination.
5. **Submission:**
   - CTA **Open and fill application** for supported forms.
   - Before any external submit, display a distinct confirmation: “You are about to submit this application to [Company].”
   - Final CTA: **Confirm and submit**.
   - On success, show confirmation evidence and CTA **View in Applications**.

If an ATS encounters CAPTCHA, OTP/2FA, a new subjective question, or an upload problem, stop automation and show **Action required** with the exact next step. Never attempt to bypass a protection mechanism.

### 5. Applications tracker

**Goal:** give the user a reliable source of truth after they begin applying.

**Header**

- Search and filters for status, company, date, location, source, and score.
- Optional compact summary: Ready, Applied, Interviewing, Closed.

**Pipeline tabs / filter chips**

- Ready
- In progress
- Applied
- Interviewing
- Closed / Not pursuing

**Application row/card**

- Company, role, date updated, location, status, and next action.
- Submission state must be explicit: Draft only, Prepared, Filled awaiting confirmation, Submitted, Submission failed, or Action required.
- Opening an item shows the activity timeline, documents sent/prepared, exact approved answers, employer link, and status-change controls.

Marking an application as applied must be idempotent: disable the control while saving, show a success state, and prevent duplicate records or duplicate external submission attempts.

## System states

### Loading

Use skeleton cards and a concise label such as “Checking your roles…”; retain existing content during refresh. Do not leave the user on an indefinite “Loading jobs…” screen—show retry/help after a bounded timeout.

### Empty

- **No matches:** explain the current filters and provide **Edit preferences** and **Clear filters**.
- **No ready roles:** explain that the agent is monitoring matches and link to Strong matches.
- **No applications:** CTA **Explore roles**.

### Error and offline

State what failed in plain language, preserve unsaved typed content locally where possible, and offer **Try again**. Never expose provider keys, raw stack traces, or internal URLs.

### Eligibility and availability

Use clear, non-judgmental labels:

- **Eligible** — meets confirmed preferences/work eligibility.
- **Needs review** — missing or ambiguous sponsorship, location, or authorization information.
- **Not eligible** — conflicts with a confirmed preference or stated requirement; include reason and allow the user to override/save with a note.
- **Unavailable** — posting expired, inaccessible, or no longer verified; keep it out of Ready and preserve the reason in history.

## Visual system

Use the current dark product direction but improve contrast and hierarchy.

| Token | Default |
| --- | --- |
| Background | `#12131A` |
| Surface | `#1B1D27` |
| Elevated surface | `#242735` |
| Primary | `#B56DFF` |
| Primary pressed | `#9C4FE8` |
| Text primary | `#F7F5FA` |
| Text secondary | `#B8B4C2` |
| Success | `#38A169` |
| Warning | `#D69E2E` |
| Danger | `#E05D5D` |
| Radius | 12px cards; 10px controls; 999px chips |
| Spacing | 4px base; common increments 8, 12, 16, 24, 32 |

Use a 16px minimum body font, 44×44px minimum touch targets, one primary CTA per view, and a fixed bottom action bar only when it does not cover browser/PWA safe areas. Respect `env(safe-area-inset-bottom)`.

## Accessibility and interaction requirements

- Meet WCAG 2.1 AA contrast; color alone must not communicate score, eligibility, or status.
- Every icon-only control has an accessible name and visible focus state.
- Support keyboard navigation, logical focus order, Escape/back dismissal, and screen-reader announcements for save/submission state changes.
- Use semantic headings, buttons, labels, form validation messages, and status regions.
- Avoid timed confirmations that disappear before the user can read them.
- Require explicit confirmation for destructive status changes and final external submission.

## Beta acceptance checklist

- A new user can sign in, complete onboarding, upload a resume, and reach Today on mobile.
- A user can understand why each role is eligible, needs review, or excluded.
- A user can prepare documents and review/edit factual answers before any external interaction.
- Every external submission has a separate final confirmation and auditable result.
- A user sees only their own data across Today, job detail, Application Studio, and Applications.
- The experience remains usable from a 375px-wide phone viewport and a desktop browser.
