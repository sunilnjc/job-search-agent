# Public website deployment

This package moves the customer web UI, tenant API and privacy worker onto an
approved always-on Linux host. It does not choose or purchase a provider. Hosting
access and the domain cutover are still required; a Dockerfile is not deployment
evidence. The current Mac deployment remains untouched by these files.

## Boundaries

- `jobagent.mobile.web:app` serves the production React build and `/api/mobile`.
  It never imports the founder HTTP server. `/admin`, `/api/jobs` and unknown
  paths return 404; they do not fall through to the founder SPA.
- Hosted Supabase remains the source of truth for Auth, RLS, jobs, preferences,
  source files, generated documents, usage controls and subscription state.
- The web process receives only the public Supabase pair and server AI key.
  Configure billing's trusted credential separately when billing is enabled.
- The privacy process receives its separate Supabase secret; it has no inbound
  port. Its durable queue/heartbeat remain in Supabase across container restarts.
- Founder SQLite, resumes, `.env` files, browser sessions, Telegram automation
  and tunnel credentials are not build inputs. `.dockerignore` is deny-by-default.
- No browser-driven employer submission is part of this public deployment.

## Prepare an existing approved host

1. Confirm the host, capacity, cost and region with its account owner. Inspect
   existing services before using ports or disk space. Do not replace another app.
2. Install supported Docker Engine and Compose on that host if absent. Use a
   dedicated deployment directory and immutable reviewed release checkout.
3. Create `/etc/job-pursuit/web.env` and `/etc/job-pursuit/privacy.env` as mode 600
   files readable by the deployment operator. Populate from the existing approved
   secret store; the example files contain placeholders only. Never commit them.
4. Supply `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` and `RELEASE_TAG` to Compose.
   The two Vite values are public client configuration, never a secret/service key.
5. Run `docker compose -f deploy/compose.yaml build web`, then
   `docker compose -f deploy/compose.yaml up -d`. Do not deploy a UI fixture build.

Both containers have memory/process bounds, read-only roots, bounded temporary
storage, dropped Linux capabilities, no-new-privileges and bounded JSON logs.
The API has one worker intentionally: its request/concurrency limiter is per
process; AI reservation and spending enforcement are database-backed. Add a
reviewed shared ingress limit before scaling replicas. Proxy headers are not
trusted by default. Review trusted ingress addresses before enabling them.

## Verify before changing the public route

- `curl --fail http://127.0.0.1:8843/readyz` checks configuration/build presence;
  it deliberately does not claim Supabase/AI/payment health.
- `curl --fail http://127.0.0.1:8843/api/mobile/health` checks API liveness.
- `/api/mobile/bootstrap` without a user token must return 401.
- `/admin`, `/api/jobs`, `/.env` and traversal requests must not return private data.
- Verify the privacy heartbeat through its owner-scoped account API, then a
  synthetic export. Never test deletion with a real account.
- Restart both containers and repeat these checks and the golden UI journey.

Only then configure the existing Cloudflare domain/ingress for the reviewed
origin. Do not attach differently configured connectors to the same tunnel:
traffic can reach either connector. Preserve the existing owner-only admin
security boundary in a separately reviewed route plan; do not route `/admin` to
the tenant API or expose the old founder API as a migration convenience.

## Monitoring and rollback

Monitor HTTPS availability and the tenant `/api/mobile/health` endpoint outside
the hosting machine. The privacy heartbeat must remain younger than five minutes.
Monitor server error rate, discovery provider warnings, failed model runs and
webhook reconciliation failures without storing auth headers or resume contents.
Docker logs are bounded; the existing privacy logger redacts recognized secrets.
An external alert destination must be configured and tested before paid launch.

Keep the previous immutable image tag and origin configuration. A rollback is an
explicit image/origin switch, not a database downgrade. Supabase data and queued
operations persist; never erase them to roll back a release. Preserve old hashed
frontend assets during an in-place static-only update, or retain an old release
asset route while clients finish using their already-open page.
