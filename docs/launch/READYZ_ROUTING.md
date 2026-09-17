# `/readyz` routing (product gate)

Updated 17 September 2026.

## Goal

`GET https://www.thejobpursuit.com/readyz` must return **JSON readiness**, not the founder SPA HTML shell.

## Live topology (Mac interim)

| Path | Upstream |
| --- | --- |
| `/api/mobile/*` | `jobagent.mobile.app` on `127.0.0.1:8843` |
| `/readyz` | same mobile API on `8843` |
| everything else for `www` | founder SPA / API on `127.0.0.1:8842` |

Cloudflare Tunnel ingress (`~/.cloudflared/config.yml`, not in git) includes this rule **before** the SPA catch-all:

```yaml
- hostname: www.thejobpursuit.com
  path: ^/readyz/?$
  service: http://127.0.0.1:8843
```

## Code

- Public JSON `/readyz` on `jobagent.mobile.app` (configuration check via `SupabaseSettings.validate()`).
- Isolated `jobagent.mobile.web` also exposes `/readyz` for Docker/off-Mac; live public `/readyz` does not use that process today.
- Python 3.9: do **not** annotate the handler return as `JSONResponse | dict` (FastAPI/Pydantic evaluation fails and the LaunchAgent never binds `:8843`).

## Verification (17 Sep 2026)

- Local: `GET http://127.0.0.1:8843/readyz` → `200 application/json`
- Public: `GET https://www.thejobpursuit.com/readyz` → `200 application/json`
  `{"status":"ready","service":"job-pursuit-mobile","scope":"configuration"}`
- `/beta` still `200`
- Unit: `tests/test_mobile_readyz.py`

## Gate status

**DONE** for the Mac-tunnel interim (public JSON probe works).

## Residual

Until Mac-independent hosting ships, readiness still depends on the Mac LaunchAgent + cloudflared. Green `/readyz` means the tenant API process and config validation are up, not that an off-Mac host is serving traffic.
