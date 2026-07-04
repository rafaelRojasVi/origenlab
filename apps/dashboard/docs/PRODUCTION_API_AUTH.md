# Production dashboard → API authentication gap

**Audience:** Operators and engineers deploying the read-only dashboard against a production `apps/api` host.

**Status:** Documented gap — **not yet implemented** in dashboard runtime code.

---

## Summary

| Layer | What it does | What it does **not** do |
|-------|--------------|-------------------------|
| **Cloudflare Access** (edge, optional) | SSO / session cookies for `dashboard.origenlab.cl` and `api.origenlab.cl` | Origin API bearer auth |
| **`credentials: "include"`** (browser fetch) | Forwards Cloudflare Access session cookies on cross-origin API calls | Send `ORIGENLAB_API_AUTH_TOKEN` |
| **`ORIGENLAB_API_AUTH_TOKEN`** (origin) | Protects private API routes when `ORIGENLAB_ENV=production` | Replace Cloudflare Access |

When production API token auth is enabled, **private routes return 401** unless the caller presents `Authorization: Bearer <token>` or `X-OriginLab-API-Key: <token>`.

The dashboard browser client today uses **`fetch(..., { credentials: "include" })`** only. That is **Cloudflare cookie forwarding**, not API token auth.

See also: [`apps/api/docs/PRODUCTION_AUTH.md`](../../api/docs/PRODUCTION_AUTH.md).

---

## Current dashboard behavior

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

**No** `Authorization` header. **No** `X-OriginLab-API-Key` header.

`VITE_ORIGENLAB_API_BASE_URL` sets the **public API host** for production builds. It is not a secret and must not carry the API token.

---

## Expected production failure today

If `ORIGENLAB_ENV=production` and `ORIGENLAB_API_AUTH_TOKEN` is set on the API:

| Route | Without proxy / JWT at origin |
|-------|-------------------------------|
| `GET /health` | **200** (public) |
| `GET /operator/status`, `/cases/warm`, `/mirror/*`, etc. | **401** `unauthorized` |

Symptoms in the dashboard UI: Today/Negocio/Catálogo panels show fetch errors or empty error states while `/health` may still succeed in smoke scripts.

This is **distinct** from Cloudflare Access failures:

| HTTP | Typical cause |
|------|----------------|
| **302 / 403** at edge | Missing or invalid Cloudflare Access session / service token |
| **401** from API JSON body | Missing `ORIGENLAB_API_AUTH_TOKEN` at origin |

---

## Do **not** fix with static browser secrets

**Never** add any of the following:

- `VITE_ORIGENLAB_API_AUTH_TOKEN` (or similar) in `.env`, `vite-env.d.ts`, or client code
- Build-time injection of bearer tokens into the static JS bundle
- Copying `ORIGENLAB_API_AUTH_TOKEN` into dashboard Render/Cloudflare env for the **browser** build

Vite `VITE_*` variables are embedded in client JavaScript at build time. Anyone who can load the dashboard can extract the token from the bundle or network tab. That defeats origin token auth.

Regression guards: `src/test/dashboard0Safety.test.ts` (production API auth section).

---

## Recommended future fixes (pick one)

### Option A — Same-origin read-only BFF / proxy (preferred for dashboard)

Deploy a **server-side** read-only proxy on the dashboard origin (e.g. Cloudflare Worker, small Node handler, or platform middleware) that:

1. Validates the operator’s Cloudflare Access session (or same-origin cookie).
2. Forwards **GET-only** requests to `apps/api` with `X-OriginLab-API-Key` from a **server secret** (never exposed to the browser).
3. Keeps the browser calling `/api/...` same-origin with `credentials: "include"`.

### Option B — API validates Cloudflare Access JWT at origin

Configure `apps/api` to validate Cloudflare Access JWTs (`Cf-Access-Jwt-Assertion`) on private routes, in addition to or instead of bearer token for browser traffic. Requires API middleware work and Cloudflare team/app configuration.

### Option C — CLI / CI smoke only (already supported)

**Cloudflare Access service tokens** and `ORIGENLAB_API_AUTH_TOKEN` in shell env are correct for:

- `apps/email-pipeline/scripts/qa/smoke_dashboard_api_readiness.py`
- `curl` / operator scripts

They are **not** appropriate for browser JavaScript.

---

## Local development (unchanged)

Leave `VITE_ORIGENLAB_API_BASE_URL` **unset** in `npm run dev`. Vite proxies to `http://127.0.0.1:8001` where the API typically runs with `ORIGENLAB_ENV` unset (no token required).

---

## Related docs

- API production auth: [`apps/api/docs/PRODUCTION_AUTH.md`](../../api/docs/PRODUCTION_AUTH.md)
- Production smoke checklist: [`docs/dashboard/PRODUCTION_DASHBOARD_SMOKE_CHECKLIST.md`](../../../docs/dashboard/PRODUCTION_DASHBOARD_SMOKE_CHECKLIST.md)
- Cloudflare Access: [`docs/CLOUDFLARE_ACCESS_DASHBOARD_SECURITY.md`](../../../docs/CLOUDFLARE_ACCESS_DASHBOARD_SECURITY.md)
