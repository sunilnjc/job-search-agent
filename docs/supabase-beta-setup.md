# Supabase beta setup

This document creates a clean multi-user beta environment. It does **not** migrate
or expose the founder's current local SQLite database, resume folders, Telegram bot,
or browser automation profile.

## 1. Create the project

1. Sign in at [Supabase](https://supabase.com/dashboard).
2. Create a project named `the-job-pursuit-beta` in the region closest to the initial
   beta users.
3. Set a strong database password and store it in a password manager. Do not paste it
   into source code or chat.
4. Wait until the project is ready.

## 2. Configure authentication

In **Authentication → URL Configuration**:

- Set the Site URL to the beta URL when it exists; use `http://localhost:5173` while
  developing locally.
- Add these Redirect URLs:
  - `http://localhost:5173/**`
  - `https://www.thejobpursuit.com/**`
  - `https://thejobpursuit.com/**`

In **Authentication → Providers → Email**:

- Enable Email.
- Enable magic-link sign-in.
- Keep email confirmation enabled for the beta.
- Use Supabase's default email sender initially. A branded sender/domain can be added
  after the workflow is proven.

## 3. Create the database and private document storage

1. Open **SQL Editor** in the new project.
2. Run `supabase/migrations/0001_beta_multi_tenant.sql` from this repository.
3. Confirm the `resumes` and `artifacts` storage buckets are **private**.
4. Create two test users with different email addresses and verify that one cannot
   browse or download the other user's records/files.

## 4. Local configuration

Copy the following values from **Project Settings → API** into the local `.env` file.
Never commit them.

```dotenv
# Multi-user beta API (safe to leave blank while the personal local app is still running)
BETA_SUPABASE_URL=
BETA_SUPABASE_JWT_ISSUER=
BETA_SUPABASE_JWT_AUDIENCE=authenticated
# Server-only secret. Never expose this to the browser or commit it.
SUPABASE_SERVICE_ROLE_KEY=
```

The browser-only values belong in `web/.env` as `VITE_SUPABASE_URL` and
`VITE_SUPABASE_ANON_KEY`; do not put a service-role key in any `VITE_` variable.
`BETA_SUPABASE_JWT_ISSUER` must equal `BETA_SUPABASE_URL/auth/v1`.
`SUPABASE_SERVICE_ROLE_KEY` is for trusted server-side maintenance only. Normal
application requests must use the user's access token and RLS policies; they must not
bypass RLS through the service role.

## 5. Completion check

- A user can request a magic link at the beta URL.
- The redirect returns to the app and creates a session.
- A signed-out visitor cannot access app data.
- Two signed-in test users see only their own records and private files.

## What to send back to the implementation work

Tell the team only that the Supabase project is created. Keep URLs and keys in the
local `.env` file; do not paste secrets into chat. Once it exists, the next work item
is wiring the protected routes, profile onboarding, and RLS-backed data client.
