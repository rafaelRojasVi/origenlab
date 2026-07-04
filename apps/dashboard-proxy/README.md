# Dashboard read-only API proxy (Cloudflare Worker)

Same-origin **GET-only** proxy for production dashboard builds. The browser calls `https://dashboard.origenlab.cl/api/*`; the Worker strips `/api`, checks an allowlist, and forwards to `apps/api` with `X-OriginLab-API-Key` from a Worker secret.

**No browser token.** `ORIGENLAB_API_AUTH_TOKEN` is a Worker secret only — never `VITE_*`.

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
| `ORIGENLAB_API_UPSTREAM` | `wrangler.toml` `[vars]` | yes — e.g. `https://api.origenlab.cl` |
| `ORIGENLAB_API_AUTH_TOKEN` | Worker secret | yes |
| `CF_ACCESS_CLIENT_ID` | Worker secret | optional (upstream behind Access) |
| `CF_ACCESS_CLIENT_SECRET` | Worker secret | optional |

```bash
cd apps/dashboard-proxy
npm ci
npx wrangler secret put ORIGENLAB_API_AUTH_TOKEN
# optional:
# npx wrangler secret put CF_ACCESS_CLIENT_ID
# npx wrangler secret put CF_ACCESS_CLIENT_SECRET
npx wrangler deploy
```

Route in Cloudflare: `dashboard.origenlab.cl/api*` → this Worker (see `wrangler.toml` comment).

## Dashboard build

```bash
VITE_ORIGENLAB_API_BASE_URL=https://dashboard.origenlab.cl/api npm run build
```

Browser fetches stay same-origin (`credentials: include` for Cloudflare Access on the dashboard host). The Worker adds origin auth upstream.

## Tests

```bash
npm ci
npm run validate
```

## Related

- [`../dashboard/docs/PRODUCTION_API_AUTH.md`](../dashboard/docs/PRODUCTION_API_AUTH.md)
- [`../../docs/CLOUDFLARE_ACCESS_DASHBOARD_SECURITY.md`](../../docs/CLOUDFLARE_ACCESS_DASHBOARD_SECURITY.md)
