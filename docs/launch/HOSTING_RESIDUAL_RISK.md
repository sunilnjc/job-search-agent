# Hosting residual risk (product gate)

Updated 17 September 2026.

## Verdict

**PARTIAL — EXTERNAL BLOCKER.** Public beta still depends on the founder Mac + Cloudflare Tunnel. No off-Mac host is approved or purchased.

## What is Mac-dependent today

| Component | Placement | If Mac sleeps / loses power / loses network |
| --- | --- | --- |
| Customer SPA (`/beta`, catch-all) | process on `:8842` via cloudflared | Site unavailable |
| Tenant API (`/api/mobile/*`, `/readyz`) | `jobagent.mobile.app` LaunchAgent `:8843` | API + readiness probe down |
| Privacy worker | LaunchAgent | Queue stalls |
| Auth / DB / Storage | Hosted Supabase | Data persists but app unreachable |

`/readyz` returning ready only proves the Mac API process and local config validation are up.

## Intended off-Mac shape (already in repo intent)

- Docker / `deploy/` packaging for `jobagent.mobile.web:app` (isolated SPA + `/readyz` healthcheck) and tenant API.
- Secrets as runtime files, not baked into images.
- Health probes pointed at container `/readyz`, not the founder SPA.

## What this gate is waiting on

| ID | Action |
| --- | --- |
| EXT-HOST-01 | Founder approves host (e.g. DigitalOcean App Platform) and budget |
| After approval | Build/push image, configure secrets, cut DNS/tunnel, prove golden journey with Mac services stopped |

## Gate status

**BLOCKED (external)** — residual Mac risk documented; no purchase or DNS cut without founder authorization.

## Explicit non-goals for this gate session

- Do not purchase hosting.
- Do not merge or cut DNS without founder authorization.
- Document residual Mac risk honestly for launch decision-makers.
