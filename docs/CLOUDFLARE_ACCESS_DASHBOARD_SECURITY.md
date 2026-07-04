# Cloudflare Access — dashboard and API protection

Runbook and **production record** for protecting **dashboard.origenlab.cl** and **api.origenlab.cl** with Cloudflare Zero Trust (Access).

**Status (2026-05-26):** Access is **enabled in production**. Operators must authenticate before reaching the dashboard or API on the custom domains.

## Production result (2026-05-26)

### Cloudflare zone

| Item | Value |
|------|--------|
| Zone | **origenlab.cl** — **Active** |
| Nameservers | `damiete.ns.cloudflare.com` |
| | `robin.ns.cloudflare.com` |

### Protected Access applications

| Application | Hostname |
|-------------|----------|
| Dashboard | `dashboard.origenlab.cl` |
| API | `api.origenlab.cl` |

### Access policy

| Field | Value |
|-------|--------|
| Policy name | **Allow OrigenLab admins** |
| Action | Allow |
| Include | Email |
| Allowed emails | `rafarojasv6@gmail.com` |
| | `tvivancob@gmail.com` |

### Application configuration (verified)

| Component | Setting |
|-----------|---------|
| Dashboard API base | `VITE_ORIGENLAB_API_BASE_URL=https://api.origenlab.cl` |
| API CORS | `ORIGENLAB_API_CORS_ORIGINS` includes `https://dashboard.origenlab.cl` |
| API bearer auth | `ORIGENLAB_API_AUTH_TOKEN` set on Render / FastAPI Cloud (production required) |
| API Host allowlist | `ORIGENLAB_API_ALLOWED_HOSTS=api.origenlab.cl` |

### Verification (production)

| Check | Result |
|-------|--------|
| `https://dashboard.origenlab.cl` in browser (incognito) | Cloudflare Access login / code flow appears |
| `https://api.origenlab.cl` in browser (incognito) | Cloudflare Access login / code flow appears |
| `curl -i https://api.origenlab.cl/health` | **HTTP/2 302** redirect to Cloudflare Access login (not anonymous 200) |

### Note: `HEAD /health` and 405

After authenticating through Access, `HEAD /health` may return **405 Method Not Allowed** because the API only allows **GET** on `/health`. This is expected and **not** an Access misconfiguration. Use **GET** for health checks through Access.

```bash
# Example (after Access session cookie or service token):
curl -i -X GET https://api.origenlab.cl/health
```

## API Host allowlist (Render origin hardening)

Cloudflare Access protects **custom domains** only. Traffic to the raw Render hostname can still reach the origin unless the API rejects it.

**Render env (production):**

```bash
ORIGENLAB_API_ALLOWED_HOSTS=api.origenlab.cl
```

When `ORIGENLAB_ENV=production` and `ORIGENLAB_API_ALLOWED_HOSTS` is set:

- Requests with `Host: api.origenlab.cl` → allowed (after Access at the edge).
- Requests with `Host: origenlab.onrender.com` → **403** `{"detail":"Forbidden"}` (no route body leak).
- `localhost` / `127.0.0.1` are **not** auto-allowed in production (loopback only works when not in production mode).

**Verify after deploy:**

```bash
# Should 302 to Access (custom domain) or 403 (if hitting origin with wrong Host after deploy):
curl -i https://api.origenlab.cl/health

# Should NOT return 200 JSON health on raw Render host once allowlist is live:
curl -i -H "Host: origenlab.onrender.com" https://origenlab.onrender.com/health
curl -i https://origenlab.onrender.com/health
```

Implementation: `apps/api/src/origenlab_api/http_security.py` (`AllowedHostMiddleware`).

## API bearer token (production origin auth)

Since 2026-07, `ORIGENLAB_ENV=production` requires **`ORIGENLAB_API_AUTH_TOKEN`**. Private API routes (`/operator/*`, `/cases/*`, `/mirror/*`, etc.) return **401** without:

```http
Authorization: Bearer <token>
```

(or `X-OriginLab-API-Key`).

| Layer | Protects | Does **not** replace |
|-------|----------|----------------------|
| **Cloudflare Access** | Custom domains (`api.origenlab.cl`, `dashboard.origenlab.cl`) | API bearer token on the origin |
| **CORS** | Browser cross-origin policy for dashboard origin | Authentication |
| **`ORIGENLAB_API_AUTH_TOKEN`** | Every private read route at the FastAPI app | Cloudflare SSO for operators |

**Public without token:** `GET /health`, `OPTIONS` preflight.

**FastAPI Cloud** (`*.fastapicloud.dev`) has no Cloudflare Access edge — rely on API token auth for private routes.

**Smoke / curl:** When Access and API token auth are both enabled, send Cloudflare service-token headers **and** an API token header (`X-OriginLab-API-Key` in shell examples). See [`apps/api/docs/PRODUCTION_AUTH.md`](../apps/api/docs/PRODUCTION_AUTH.md).

**Dashboard browser:** `credentials: include` carries Cloudflare cookies only. Production dashboard builds that call a token-protected API need a follow-up to attach the bearer token (server-side proxy or build-time secret) — CORS alone is insufficient.

## Remaining hardening (follow-up)

| URL | Mitigation |
|-----|------------|
| `https://origenlab.onrender.com` | **API:** `ORIGENLAB_API_ALLOWED_HOSTS` (above) |
| `https://origenlab-dashboard.onrender.com` | Render custom-domain only; do not publish raw URL |

**Optional later:**

- Cloudflare Access JWT validation at origin (`CF-Access-Jwt-Assertion`).
- Render private service / disable default `onrender.com` hostname where platform allows.

## Goals

- Require identity (Google via Access) for operator access to the commercial dashboard and API.
- Allow only approved operator emails; deny everyone else by default.
- Avoid leaving the API reachable on the public internet while only the dashboard is protected.

## Preconditions (completed for production)

| Check | Status |
|--------|--------|
| `dashboard.origenlab.cl` DNS → Render **origenlab-dashboard** | Done |
| `api.origenlab.cl` DNS → Render **origenlab-api** | Done |
| API **CORS** allows `https://dashboard.origenlab.cl` | Done |
| Access apps on dashboard + API hostnames | Done |
| Operator emails in Allow policy | Done |

### CORS reminder

The dashboard calls `https://api.origenlab.cl` after Access login. Production must keep `ORIGENLAB_API_CORS_ORIGINS` including `https://dashboard.origenlab.cl` (not `*`).

**CORS is not authentication.** Private routes also require `ORIGENLAB_API_AUTH_TOKEN` when `ORIGENLAB_ENV=production`.

## Cloudflare Access setup (reference)

1. [Cloudflare Zero Trust](https://one.dash.cloudflare.com/) → **Access** → **Applications**.
2. **Application type:** Self-hosted.
3. Two applications (production):
   - `dashboard.origenlab.cl`
   - `api.origenlab.cl`
4. **Session duration:** 12h or 24h (operator preference).
5. **Identity provider:** Google.
6. **Policy — Allow OrigenLab admins:** Include emails listed above; no catch-all Allow.

### Protect both dashboard and API

If only **dashboard.origenlab.cl** is behind Access, **api.origenlab.cl** (and any raw Render API URL) may remain directly callable. Production protects **both** hostnames.

## Verification checklist (ongoing)

| Step | Expected |
|------|----------|
| Open `https://dashboard.origenlab.cl` in incognito | Redirect to Cloudflare Access / login |
| Log in with **allowed** email | Dashboard loads; API calls succeed (no CORS errors) |
| Log in with **disallowed** email | Blocked by Access |
| `curl -i https://api.origenlab.cl/health` (no session) | **302** to Access login |
| Dashboard → network tab after login | Requests to `api.origenlab.cl` succeed |
| `GET /health` after login | **200** (not HEAD unless API adds HEAD) |
| Raw Render URLs | Confirm blocked or mitigated (see **Remaining hardening**) |

## Rollback

1. Zero Trust → Access → Application → **Disable** or relax the Allow policy temporarily.
2. Leave Render services and Postgres **unchanged** (no redeploy required for Access-only rollback).
3. Do **not** modify SQLite or Postgres for Access rollback.
4. Re-enable policy after operators confirm login flow.

## Deployment interaction

| Change type | Action |
|-------------|--------|
| Cloudflare Access policies / DNS on zone | Cloudflare UI only |
| API CORS env | Redeploy **origenlab-api** on Render |
| Dashboard `VITE_ORIGENLAB_API_BASE_URL` | Redeploy **origenlab-dashboard** |
| Postgres mirror | **Not** required for Access |

**Access itself does not require** a Render redeploy, DB changes, sends, or Postgres sync.

## Safety

- Enabling or changing Access policies does **not** mutate Gmail, SQLite operational data, outreach tables, or Postgres mirror content.
- No mart rebuild required for Access verification.
- Commercial deal ledger and email-pipeline operator writes remain independent of this layer.

## Related docs

- [`SECURITY_AUDIT_RENDER_DASHBOARD.md`](SECURITY_AUDIT_RENDER_DASHBOARD.md) — Render exposure audit and hardening backlog
- [`apps/email-pipeline/docs/REFRESH_RENDER_DASHBOARD_ONCE.md`](../apps/email-pipeline/docs/REFRESH_RENDER_DASHBOARD_ONCE.md) — mirror refresh after data fixes
- [`apps/email-pipeline/docs/PHASE1_CLOUD_READ_PATH.md`](../apps/email-pipeline/docs/PHASE1_CLOUD_READ_PATH.md) — cloud read path and URLs
