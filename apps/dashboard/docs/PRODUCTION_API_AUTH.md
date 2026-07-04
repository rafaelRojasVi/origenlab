# Production dashboard → API authentication

**Audience:** Operators and engineers deploying the read-only dashboard against a production `apps/api` host.

**Status:** **Cloudflare Worker proxy** in [`apps/dashboard-proxy`](../../dashboard-proxy/README.md) — deploy Worker + set `VITE_ORIGENLAB_API_BASE_URL=https://dashboard.origenlab.cl/api`.

---

## Summary

| Layer | What it does | What it does **not** do |
|-------|--------------|-------------------------|
| **Cloudflare Access** (edge) | SSO / session cookies for `dashboard.origenlab.cl` | Origin API bearer auth |
| **`credentials: "include"`** (browser fetch) | Forwards Cloudflare Access session cookies on same-origin `/api/*` calls | Send `ORIGENLAB_API_AUTH_TOKEN` to the browser |
| **Dashboard Worker proxy** (`/api/*`) | GET-only allowlist; injects `X-OriginLab-API-Key` upstream | Replace operator Access login |
| **`ORIGENLAB_API_AUTH_TOKEN`** (Worker secret + API origin) | Protects private API routes when `ORIGENLAB_ENV=production` | Belong in `VITE_*` or static JS |

When production API token auth is enabled, **private routes return 401** unless the caller presents `Authorization: Bearer <token>` or `X-OriginLab-API-Key: <token>`.

The dashboard browser uses **`fetch(..., { credentials: "include" })`** only — **no** bearer/API-key headers in client code. The **Worker** adds `X-OriginLab-API-Key` when forwarding to `apps/api`.

See also: [`apps/api/docs/PRODUCTION_AUTH.md`](../../api/docs/PRODUCTION_AUTH.md).

---

## Production layout (recommended)

```
Browser → https://dashboard.origenlab.cl/api/operator/status  (same origin)
       → Cloudflare Worker (apps/dashboard-proxy)
       → https://api.origenlab.cl/operator/status
          + X-OriginLab-API-Key (Worker secret)
          + optional CF-Access-Client-* (Worker secrets, if upstream API uses Access)
```

Dashboard build:

```bash
VITE_ORIGENLAB_API_BASE_URL=https://dashboard.origenlab.cl/api npm run build
```

Worker deploy: see [`apps/dashboard-proxy/README.md`](../../dashboard-proxy/README.md).

---

## Current dashboard client behavior

Active read-only clients live under `src/api/`:

- `operatorClient.ts` — `/health`, `/operator/*`, `/cases/warm`, `/contacts/{email}`, `/opportunities/equipment`
- `mirrorCommercialClient.ts`, `mirrorCatalogClient.ts`, `mirrorLeadIntelClient.ts`, `mirrorAuditClient.ts` — `/mirror/*`

All use:

```ts
fetch(url, {
  method: "GET",
  credentials: "include",
  headers: { Accept: "application/json" },
});
```

**No** `Authorization` header. **No** `X-OriginLab-API-Key` header in browser code.

`VITE_ORIGENLAB_API_BASE_URL` sets the **public API base** (host + optional `/api` prefix). It is not a secret.

`operatorApiUrl("/operator/status")` with base `https://dashboard.origenlab.cl/api` resolves to `https://dashboard.origenlab.cl/api/operator/status` (path prefix preserved).

---

## Failure modes

| Symptom | Typical cause |
|------|----------------|
| **302 / 403** at edge (HTML) | Cloudflare Access — operator not logged in |
| **401** JSON from `/api/*` | Worker missing/invalid `ORIGENLAB_API_AUTH_TOKEN` secret, or upstream API token mismatch |
| **403** `path_not_allowed` from Worker | Route not on dashboard read allowlist |
| **405** from Worker | Mutating HTTP method (not supported) |

Direct cross-origin calls to `https://api.origenlab.cl` without the Worker still require browser token auth (not implemented — by design).

---

## Do **not** fix with static browser secrets

**Never** add any of the following:

- `VITE_ORIGENLAB_API_AUTH_TOKEN` (or similar) in `.env`, `vite-env.d.ts`, or client code
- Build-time injection of bearer tokens into the static JS bundle
- Copying `ORIGENLAB_API_AUTH_TOKEN` into dashboard Render env for the **browser** build

Vite `VITE_*` variables are embedded in client JavaScript at build time. Anyone who can load the dashboard can extract the token from the bundle or network tab.

Regression guards: `src/test/dashboard0Safety.test.ts` (production API auth section).

---

## Alternatives

### Option B — API validates Cloudflare Access JWT at origin

Configure `apps/api` to validate Cloudflare Access JWTs (`Cf-Access-Jwt-Assertion`) on private routes. Requires API middleware work — not the current default path.

### Option C — CLI / CI smoke only

**Cloudflare Access service tokens** and `ORIGENLAB_API_AUTH_TOKEN` in shell env are correct for:

- `apps/email-pipeline/scripts/qa/smoke_dashboard_api_readiness.py`
- `curl` / operator scripts

They are **not** appropriate for browser JavaScript.

---

## Local development (unchanged)

Leave `VITE_ORIGENLAB_API_BASE_URL` **unset** in `npm run dev`. Vite proxies to `http://127.0.0.1:8001` where the API typically runs with `ORIGENLAB_ENV` unset (no token required).

---

## Related docs

- Worker proxy: [`apps/dashboard-proxy/README.md`](../../dashboard-proxy/README.md)
- API production auth: [`apps/api/docs/PRODUCTION_AUTH.md`](../../api/docs/PRODUCTION_AUTH.md)
- Production smoke checklist: [`docs/dashboard/PRODUCTION_DASHBOARD_SMOKE_CHECKLIST.md`](../../../docs/dashboard/PRODUCTION_DASHBOARD_SMOKE_CHECKLIST.md)
- Cloudflare Access: [`docs/CLOUDFLARE_ACCESS_DASHBOARD_SECURITY.md`](../../../docs/CLOUDFLARE_ACCESS_DASHBOARD_SECURITY.md)
