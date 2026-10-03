# Cloudflare Access — API origin protection

> **2026-10-03.** Cloudflare Access no longer fronts `dashboard.origenlab.cl`. The dashboard's
> sign-in is the API's Google Workspace login with operator profiles
> (`apps/api/docs/PRODUCTION_AUTH.md`). This document now covers only `api.origenlab.cl`, which
> the Worker reaches with a service token. Decision: `docs/MIGRATION.md` §11 row 5.
>
> The decision is recorded; removing the dashboard Access application is a Phase B operator
> step (`docs/OPERATIONS.md` §1.2 step 10) and had not been executed when this was written. The
> 2026-05 and 2026-07 sections below are the production record of the two-application setup.

Runbook and **production record** for protecting **api.origenlab.cl** with Cloudflare Zero Trust (Access). **dashboard.origenlab.cl** is protected the same way until its application is removed.

**Status (2026-05-26):** Access was **enabled in production** on both custom domains. After Phase B only the API hostname keeps an Access application.

## Production update (2026-07)

The dashboard **read-only Worker proxy** is live at `dashboard.origenlab.cl/api*` ([`apps/dashboard-proxy`](../apps/dashboard-proxy/README.md)). Merged hardening:

| PR | Behavior |
|----|----------|
| #342 | Worker GET-only allowlist; upstream auth injection |
| #345 | Credentialed CORS for `https://dashboard.origenlab.cl` |
| #347 | Upstream 3xx → 502 JSON `upstream_redirect_blocked`; `X-OriginLab-Proxy` / `X-OriginLab-Upstream-Status` |
| #348 | Strip upstream `Set-Cookie` / `Set-Cookie2` (prevents API Access cookies overwriting dashboard session) |
| #349 | Strip upstream CORS headers (Worker policy wins) |

**Secrets:** Cloudflare Access service tokens and `ORIGENLAB_API_AUTH_TOKEN` were **rotated after accidental exposure** (2026-07). Store only in Render / Wrangler secrets — never in git, chat, or `VITE_*`.

**Two-layer auth (as of 2026-07):**

1. **Cloudflare Access** — operator SSO on `dashboard.origenlab.cl` and `api.origenlab.cl` (the dashboard application is retired at Phase B; the API one stays and the Worker presents a service token to it).
2. **`ORIGENLAB_API_AUTH_TOKEN`** — API origin auth; Worker injects `X-OriginLab-API-Key` upstream.

**Production smoke (2026-07-06):** 8/8 GET routes via `https://dashboard.origenlab.cl/api` returned HTTP 200 JSON with expected CORS and proxy diagnostic headers. See [`docs/dashboard/PRODUCTION_DASHBOARD_SMOKE_CHECKLIST.md`](dashboard/PRODUCTION_DASHBOARD_SMOKE_CHECKLIST.md).

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
| API | `api.origenlab.cl` |

The dashboard application (`dashboard.origenlab.cl`) is in this record and is retired at Phase B (2026-10-03 decision; `docs/OPERATIONS.md` §1.2 step 10).

### Access policy

| Field | Value |
|-------|--------|
| Policy name | **Allow OrigenLab admins** |
| Action | Allow |
| Include | Email |
| Allowed emails | *(two operator accounts — not listed here)* |
| | See the live allowlist in the Cloudflare Zero Trust dashboard: |
| | **Access → Applications → OrigenLab Dashboard → Policies** (recorded under the dashboard application; before that application is deleted, confirm which policy the API application uses). |

### Application configuration (verified)

| Component | Setting |
|-----------|---------|
| Dashboard API base (production) | `VITE_ORIGENLAB_API_BASE_URL=https://dashboard.origenlab.cl/api` (same-origin Worker proxy) |
| Dashboard proxy | Cloudflare Worker — [`apps/dashboard-proxy`](../apps/dashboard-proxy/README.md) on `dashboard.origenlab.cl/api*`; the only caller that holds the service token |
| API upstream (Worker `[vars]`) | `ORIGENLAB_API_UPSTREAM=https://api.origenlab.cl` |
| API origin token (Worker secret) | `ORIGENLAB_API_AUTH_TOKEN` — same value as Render API |
| Cloudflare Access service token (Worker secrets) | `CF_ACCESS_CLIENT_ID` + `CF_ACCESS_CLIENT_SECRET` — **required** for Worker → `api.origenlab.cl` (Access-protected upstream) |
| API CORS | `ORIGENLAB_API_CORS_ORIGINS` includes `https://dashboard.origenlab.cl` |
| API bearer auth (Render API) | `ORIGENLAB_API_AUTH_TOKEN` on Render / FastAPI Cloud |
| API Host allowlist | `ORIGENLAB_API_ALLOWED_HOSTS=api.origenlab.cl` |

### Verification (production)

| Check | Result |
|-------|--------|
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
| **Cloudflare Access** | The API custom domain (`api.origenlab.cl`) | API bearer token on the origin |
| **CORS** | Browser cross-origin policy for dashboard origin | Authentication |
| **`ORIGENLAB_API_AUTH_TOKEN`** | Every private read route at the FastAPI app | Cloudflare SSO for operators |

**Public without token:** `GET /health`, `OPTIONS` preflight.

**FastAPI Cloud** (`*.fastapicloud.dev`) has no Cloudflare Access edge — rely on API token auth for private routes.

**Smoke / curl:** When Access and API token auth are both enabled, send Cloudflare service-token headers **and** an API token header (`X-OriginLab-API-Key` in shell examples). See [`apps/api/docs/PRODUCTION_AUTH.md`](../apps/api/docs/PRODUCTION_AUTH.md).

**Dashboard browser:** `credentials: include` carries the dashboard session cookie on same-origin `/api/*` calls (no Cloudflare cookie once the dashboard Access application is gone). The **Cloudflare Worker** (`apps/dashboard-proxy`) forwards to `api.origenlab.cl` with **two upstream auth layers**:

1. **Cloudflare Access service token** (`CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` Worker secrets) — required because `api.origenlab.cl` is behind Access. Without these, upstream returns **302/403** (plain `curl /health` behaves the same).
2. **`X-OriginLab-API-Key`** (`ORIGENLAB_API_AUTH_TOKEN` Worker secret) — API origin auth. Without it, private routes return **401** JSON after Access succeeds.

Never expose either secret in `VITE_*` or client JS. See [`apps/dashboard/docs/PRODUCTION_API_AUTH.md`](../apps/dashboard/docs/PRODUCTION_API_AUTH.md) and [`apps/dashboard-proxy/README.md`](../apps/dashboard-proxy/README.md).

## Remaining hardening (follow-up)

| URL | Mitigation |
|-----|------------|
| `https://origenlab.onrender.com` | **API:** `ORIGENLAB_API_ALLOWED_HOSTS` (above) |
| `https://origenlab-dashboard.onrender.com` | Render custom-domain only; do not publish raw URL |

**Optional later:**

- Cloudflare Access JWT validation at origin (`CF-Access-Jwt-Assertion`).
- Render private service / disable default `onrender.com` hostname where platform allows.

## Goals

- Keep `api.origenlab.cl` unreachable except by the Worker (service token) and approved operator tooling.
- Allow only approved identities; deny everyone else by default.
- The dashboard hostname is no longer an Access goal: its identity is the API's Google Workspace session ([`apps/api/docs/PRODUCTION_AUTH.md`](../apps/api/docs/PRODUCTION_AUTH.md)), with MFA as Google 2-step verification on the shared account.

## Preconditions (completed for production)

| Check | Status |
|--------|--------|
| `dashboard.origenlab.cl` DNS → Render **origenlab-dashboard** | Done |
| `api.origenlab.cl` DNS → Render **origenlab-api** | Done |
| API **CORS** allows `https://dashboard.origenlab.cl` | Done |
| Access app on the API hostname | Done |
| Operator emails in Allow policy | Done |

### CORS reminder

The dashboard calls **`https://dashboard.origenlab.cl/api/*`** (same origin); the Worker forwards to `https://api.origenlab.cl` with the Access service token and origin token auth. Production must keep `ORIGENLAB_API_CORS_ORIGINS` including `https://dashboard.origenlab.cl` for any direct API calls (smoke/CLI).

**CORS is not authentication.** Private routes also require `ORIGENLAB_API_AUTH_TOKEN` when `ORIGENLAB_ENV=production`.

## Cloudflare Access setup (reference)

1. [Cloudflare Zero Trust](https://one.dash.cloudflare.com/) → **Access** → **Applications**.
2. **Application type:** Self-hosted.
3. One application (production): `api.origenlab.cl`.
4. **Session duration:** 12h or 24h (operator preference).
5. **Identity provider:** Google.
6. **Policy — Allow OrigenLab admins:** Include emails listed above; no catch-all Allow.

The Worker reaches the application with a Cloudflare Access service token (`CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` Worker secrets).

## Verification checklist (ongoing)

| Step | Expected |
|------|----------|
| `curl -i https://api.origenlab.cl/health` (no session) | **302** to Access login |
| Dashboard (after sign-in) → network tab | Requests to `dashboard.origenlab.cl/api/*` return 200 (not 302 Access HTML, not 401 JSON) |
| `GET /health` through the Worker | **200** (not HEAD unless API adds HEAD) |
| Raw Render URLs | Confirm blocked or mitigated (see **Remaining hardening**) |

Dashboard-hostname checks (no Access screen, `403 path_not_allowed` on V1 paths, `401` on a V2 read without a cookie) are in `docs/OPERATIONS.md` §1.2 step 10.

## Rollback

1. Zero Trust → Access → Application → **Disable** or relax the Allow policy temporarily. (Rolling back the 2026-10-03 dashboard decision is a different action: re-create the Access application for `dashboard.origenlab.cl` and `npx wrangler rollback`, per `docs/OPERATIONS.md` §1.2 step 10.)
2. Leave Render services and Postgres **unchanged** (no redeploy required for Access-only rollback).
3. Do **not** modify SQLite or Postgres for Access rollback.
4. Re-enable policy after operators confirm login flow.

## Deployment interaction

| Change type | Action |
|-------------|--------|
| Cloudflare Access policies / DNS on zone | Cloudflare UI only |
| API CORS env | Redeploy **origenlab-api** on Render |
| Dashboard `VITE_ORIGENLAB_API_BASE_URL` | Redeploy **origenlab-dashboard** (use `https://dashboard.origenlab.cl/api`) |
| Dashboard Worker proxy | Deploy **`apps/dashboard-proxy`** via Wrangler with **all three** secrets (`ORIGENLAB_API_AUTH_TOKEN`, `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET`); route `dashboard.origenlab.cl/api*` |
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
