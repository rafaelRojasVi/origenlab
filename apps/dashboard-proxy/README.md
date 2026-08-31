# Dashboard API proxy (Cloudflare Worker)

Same-origin, method+path allowlisted proxy for production dashboard builds. The browser calls `https://dashboard.origenlab.cl/api/*`; the Worker strips `/api`, checks an allowlist, and forwards to `apps/api` with upstream auth headers from Worker secrets. GET is allowlisted for dashboard reads; POST is allowlisted narrowly for the durable commercial-operations commands and the tender annex import — this Worker is the trust boundary for both, not a pure read-only pass-through.

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

**GET** (dashboard reads):

| Upstream path | Purpose |
|---------------|---------|
| `/health` | Public health |
| `/operator/status`, `/operator/automation-status` | Operator panels |
| `/operator/procurement/*` | W1 institution/tender queues + T1 term detail |
| `/cases/warm` | Warm cases |
| `/contacts/*` | Contact drilldown |
| `/opportunities/commercial`, `/opportunities/commercial/o_<32hex>` | PR3 machine-proposed opportunity intake (read-only) |
| `/operations/work-queue`, `/operations/sales-opportunities/sales_<32hex>[/activities\|/tasks\|/quotes]`, `/operations/customer-quotes/quote_<32hex>`, `/operations/opportunities/o_<32hex>/[state\|activities\|tasks]` | Durable CRM reads |
| `/mirror/*` | Postgres mirror reads |

**POST** (the only human write path — trusted operator identity, `Idempotency-Key`, optimistic concurrency; each ID format is regex-constrained, no wildcard route):

| Upstream path | Purpose |
|---------------|---------|
| `/operations/opportunities/o_<32hex>/state` | PR3 operator confirm/reject |
| `/operations/sales-opportunities/promote`, `/sales_<32hex>/stage` | Durable sales-opportunity lifecycle |
| `/operations/activities`, `/operations/tasks`, `/operations/tasks/task_<32hex>/[complete\|cancel]` | Durable activities/tasks |
| `/operations/sales-opportunities/sales_<32hex>/quotes`, `/operations/customer-quotes/quote_<32hex>/drive-workspace` | CRM-Q1 customer-quote create + Drive workspace retry |
| `/operator/procurement/tenders/<code>/annex-bundle/[preview\|import]` | Explicit tender annex evidence upload |

All other POST requests, and all `PUT`, `PATCH`, and `DELETE` requests, return **405**.

## Response hardening

The Worker is deliberately stricter than a generic pass-through proxy:

- Upstream **3xx** responses are not forwarded to the browser. They become **502** JSON: `{ "error": { "code": "upstream_redirect_blocked" } }`.
- Upstream `Location`, `Set-Cookie`, `Set-Cookie2`, and upstream CORS headers are stripped before the dashboard sees the response.
- Allowed dashboard origins receive credentialed CORS headers and `Access-Control-Expose-Headers: X-Request-ID`.
- Responses include `X-OriginLab-Proxy: dashboard-proxy`; forwarded upstream responses also include `X-OriginLab-Upstream-Status`.

This keeps Cloudflare Access redirects/cookies and upstream CORS policy from leaking through `/api/*`.

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
| **502** JSON (`upstream_redirect_blocked`) | Upstream returned a redirect (often Cloudflare Access login); fix Worker Access service-token secrets |
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
