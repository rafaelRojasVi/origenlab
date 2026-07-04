# Dashboard read-only API proxy (Cloudflare Worker)

Same-origin **GET-only** proxy for production dashboard builds. The browser calls `https://dashboard.origenlab.cl/api/*`; the Worker strips `/api`, checks an allowlist, and forwards to `apps/api` with upstream auth headers from Worker secrets.

**No browser token.** `ORIGENLAB_API_AUTH_TOKEN` is a Worker secret only — never `VITE_*`.

## Upstream auth (Worker → API)

The Worker adds **two independent layers** when forwarding to a production upstream like `https://api.origenlab.cl`:

| Layer | Worker secret / header | Required when |
|-------|------------------------|---------------|
| **Cloudflare Access (edge)** | `CF-Access-Client-Id` + `CF-Access-Client-Secret` from `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` | Upstream hostname is behind Cloudflare Access (production `api.origenlab.cl` today) |
| **API origin token** | `X-OriginLab-API-Key` from `ORIGENLAB_API_AUTH_TOKEN` | Always (production `ORIGENLAB_ENV=production`) |

Plain `curl https://api.origenlab.cl/health` returns **302** to Access login — the Worker must present a **Cloudflare Access service token** to reach the origin. The API token alone is not enough for that upstream.

For **unprotected** upstreams (local dev, internal URL, FastAPI Cloud without Access), `CF_ACCESS_*` secrets are **not** required — only `ORIGENLAB_API_AUTH_TOKEN` when the API enforces origin auth.

## Routes (allowlist)

| Upstream path | Purpose |
|---------------|---------|
| `/health` | Public health |
| `/operator/status`, `/operator/automation-status` | Operator panels |
| `/cases/warm` | Warm cases |
| `/contacts/*` | Contact drilldown |
| `/opportunities/equipment` | Equipment table |
| `/mirror/*` | Postgres mirror reads |

Mutating methods (`POST`, `PUT`, `PATCH`, `DELETE`) return **405**.

## Environment

| Name | Where | Required |
|------|-------|----------|
| `ORIGENLAB_API_UPSTREAM` | `wrangler.toml` `[vars]` | yes — production: `https://api.origenlab.cl` |
| `ORIGENLAB_API_AUTH_TOKEN` | Worker secret | **always** (same value as Render API) |
| `CF_ACCESS_CLIENT_ID` | Worker secret | **yes** when upstream is behind Cloudflare Access (production default) |
| `CF_ACCESS_CLIENT_SECRET` | Worker secret | **yes** when upstream is behind Cloudflare Access (production default) |

Both CF Access secrets must be set together. The Worker never sends a lone `CF-Access-Client-Id` without the matching secret.

### Deploy (production — `api.origenlab.cl` upstream)

```bash
cd apps/dashboard-proxy
npm ci
npx wrangler secret put ORIGENLAB_API_AUTH_TOKEN
npx wrangler secret put CF_ACCESS_CLIENT_ID
npx wrangler secret put CF_ACCESS_CLIENT_SECRET
npx wrangler deploy
```

Route in Cloudflare: `dashboard.origenlab.cl/api*` → this Worker (see `wrangler.toml` comment).

## Failure modes (browser → `/api/*`)

| Symptom | Likely cause |
|---------|----------------|
| **302 / 403** (HTML Access page) | Worker missing/wrong `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` for Access-protected upstream |
| **401** JSON (`unauthorized`) | Worker missing/wrong `ORIGENLAB_API_AUTH_TOKEN` (request passed Access but failed API origin auth) |
| **403** `path_not_allowed` | Route not on dashboard read allowlist |
| **405** | Mutating HTTP method |

Secrets are never logged or returned in Worker responses.

## Dashboard build

```bash
VITE_ORIGENLAB_API_BASE_URL=https://dashboard.origenlab.cl/api npm run build
```

Browser fetches stay same-origin (`credentials: include` for Cloudflare Access on the dashboard host). The Worker adds upstream auth — not the browser.

## Tests

```bash
npm ci
npm run validate
```

## Related

- [`../dashboard/docs/PRODUCTION_API_AUTH.md`](../dashboard/docs/PRODUCTION_API_AUTH.md)
- [`../../docs/CLOUDFLARE_ACCESS_DASHBOARD_SECURITY.md`](../../docs/CLOUDFLARE_ACCESS_DASHBOARD_SECURITY.md)
