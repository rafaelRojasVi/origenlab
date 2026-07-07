# Production API authentication

**Audience:** Operators deploying `apps/api` to Render, FastAPI Cloud, or any internet-facing host.

**Scope:** Read-only operator API. Authentication protects **read** routes; it does not add write/send capabilities.

---

## Summary

| Layer                                 | Purpose                                                                           | Not a substitute for                                   |
| ------------------------------------- | --------------------------------------------------------------------------------- | ------------------------------------------------------ |
| **`ORIGENLAB_API_AUTH_TOKEN`**        | Application bearer/API-key auth on private routes when `ORIGENLAB_ENV=production` | Login UI, OAuth, or outbound safety                    |
| **`ORIGENLAB_API_CORS_ORIGINS`**      | Browser cross-origin **policy** for allowed dashboard origins                     | Authentication                                         |
| **Cloudflare Access** (optional edge) | SSO / service-token gate on `api.origenlab.cl` and `dashboard.origenlab.cl`       | API bearer token when production token auth is enabled |

When `ORIGENLAB_ENV=production`, startup **requires** `ORIGENLAB_API_AUTH_TOKEN`. Without it, `create_app()` fails fast.

Implementation: `origenlab_api.http_security.ApiTokenAuthMiddleware`.

---

## Public vs protected routes

| Access             | Routes / methods                                                                                               |
| ------------------ | -------------------------------------------------------------------------------------------------------------- |
| **No token**       | `GET /health` (and `HEAD /health` for probes), `OPTIONS` on any path (CORS preflight)                          |
| **Token required** | All other routes: `/operator/*`, `/emails/*`, `/cases/*`, `/contacts/*`, `/opportunities/*`, `/mirror/*`, etc. |

Unauthorized callers receive **401** with the unified error envelope (`error.code`: `unauthorized`, `WWW-Authenticate: Bearer`, `X-Request-ID`).

---

## Accepted credentials

Preferred:

```http
Authorization: Bearer <ORIGENLAB_API_AUTH_TOKEN>
```

Optional fallback:

```http
X-OriginLab-API-Key: <ORIGENLAB_API_AUTH_TOKEN>
```

Use `secrets.compare_digest` at the server; never log or echo the expected token.

**Examples in docs and tests use placeholders only** (`test-token`, `<your-token>`, `generate-with-openssl-rand-hex-32`). Never commit or paste production values.

---

## Environment checklist

### Required in production (`ORIGENLAB_ENV=production`)

| Variable                      | Example / notes                                                |
| ----------------------------- | -------------------------------------------------------------- |
| `ORIGENLAB_ENV`               | `production`                                                   |
| `ORIGENLAB_API_BACKEND`       | `postgres`                                                     |
| `ORIGENLAB_POSTGRES_URL`      | Managed Postgres DSN (secret)                                  |
| `ORIGENLAB_API_CORS_ORIGINS`  | `https://dashboard.origenlab.cl` (no `*`)                      |
| `ORIGENLAB_API_AUTH_TOKEN`    | Long random secret — **required**                              |
| `ORIGENLAB_API_ALLOWED_HOSTS` | `api.origenlab.cl` (recommended on Render; see host allowlist) |
| `ORIGENLAB_API_DISABLE_DOCS`  | `true` (optional; docs also off in production)                 |

Example Render / FastAPI Cloud env (set in platform dashboard; never commit real secrets):

```bash
ORIGENLAB_ENV=production
ORIGENLAB_API_BACKEND=postgres
ORIGENLAB_POSTGRES_URL=postgresql+psycopg://USER:PASSWORD@HOST/DBNAME
ORIGENLAB_API_CORS_ORIGINS=https://dashboard.origenlab.cl
ORIGENLAB_API_ALLOWED_HOSTS=api.origenlab.cl
ORIGENLAB_API_DISABLE_DOCS=true
ORIGENLAB_API_AUTH_TOKEN=<generate-with-openssl-rand-hex-32>
# Do not set ORIGENLAB_SQLITE_PATH on cloud API (local worker only).
```

### FastAPI Cloud

| Setting                    | Value                                                                                    |
| -------------------------- | ---------------------------------------------------------------------------------------- |
| Application directory      | `apps/api`                                                                               |
| Entrypoint                 | `main.py` (shim → `origenlab_api.main:app`)                                              |
| `ORIGENLAB_ENV`            | `production`                                                                             |
| `ORIGENLAB_API_AUTH_TOKEN` | Set in platform secrets (generate locally; do not reuse Render token unless intentional) |
| Postgres + CORS            | Same as Render checklist                                                                 |

Public URLs (e.g. `*.fastapicloud.dev`) are reachable without Cloudflare Access — **token auth is the primary gate** on private routes.

### Render (`render.yaml`)

Set `ORIGENLAB_API_AUTH_TOKEN` in the Render dashboard (secret; not committed). Blueprint marks the key with `sync: false`.

Health checks use `GET /health` (no token) — compatible with Render `healthCheckPath: /health`.

### Cloudflare Access interaction

Cloudflare Access and API token auth are **complementary**:

1. **Edge (Access):** Blocks unauthenticated browsers/curl before they reach the origin on custom domains.
2. **Origin (API token):** Required for every private route once production mode is enabled — including callers that bypass Access (raw Render hostname blocked by host allowlist, FastAPI Cloud public URL, compromised edge config).

**Service-token smoke scripts** (`CF-Access-Client-Id` / `CF-Access-Client-Secret`) satisfy Cloudflare only. When production API token auth is enabled, also send an API token header (prefer `X-OriginLab-API-Key` in shell examples to avoid embedding bearer strings in curl):

```bash
export ORIGENLAB_API_AUTH_TOKEN='<your-token>'   # from secret store, not committed
TOKEN="${ORIGENLAB_API_AUTH_TOKEN}"
curl -sS -H "X-OriginLab-API-Key: ${TOKEN}" \
  -H "CF-Access-Client-Id: ${CF_ACCESS_CLIENT_ID}" \
  -H "CF-Access-Client-Secret: ${CF_ACCESS_CLIENT_SECRET}" \
  "https://api.origenlab.cl/operator/status"
```

Production HTTP clients may also use `Authorization: Bearer <token>`; see **Accepted credentials** above.

**Dashboard browser clients:** production builds call same-origin `/api/*` through [`apps/dashboard-proxy`](../../dashboard-proxy/README.md). The browser uses `credentials: include` for the dashboard host but **never** receives or sends `ORIGENLAB_API_AUTH_TOKEN`; the Worker reads that value from a Worker secret and forwards it upstream as `X-OriginLab-API-Key` (plus Cloudflare Access service-token headers when the API origin is Access-protected). Do **not** inject API tokens into `VITE_*`, static JS, or build-time browser env.

See also: [`docs/CLOUDFLARE_ACCESS_DASHBOARD_SECURITY.md`](../../../docs/CLOUDFLARE_ACCESS_DASHBOARD_SECURITY.md).

---

## Local production-like testing

No Gmail, SQLite send-truth, or Postgres writes required for auth smoke tests.

### 1. Fail-fast validation

```bash
cd apps/api
ORIGENLAB_ENV=production \
ORIGENLAB_API_BACKEND=postgres \
ORIGENLAB_POSTGRES_URL='postgresql+psycopg://u:p@127.0.0.1:5432/db' \
ORIGENLAB_API_CORS_ORIGINS=https://dashboard.origenlab.cl \
uv run python -c "from origenlab_api.main import create_app; create_app()"
# Expect: ValueError: ORIGENLAB_ENV=production requires ORIGENLAB_API_AUTH_TOKEN
```

### 2. Middleware behavior (TestClient)

```bash
cd apps/api
uv run pytest tests/test_http_security.py -q -k "production and auth"
```

Uses placeholder token `test-token` only — not a real secret.

### 3. Manual curl against local server

Terminal A:

```bash
cd apps/api
export ORIGENLAB_ENV=production
export ORIGENLAB_API_BACKEND=postgres
export ORIGENLAB_POSTGRES_URL='postgresql+psycopg://u:p@127.0.0.1:5432/db'
export ORIGENLAB_API_CORS_ORIGINS=https://dashboard.origenlab.cl
export ORIGENLAB_API_AUTH_TOKEN='local-dev-placeholder-not-for-production'
uv run uvicorn origenlab_api.main:app --host 127.0.0.1 --port 8001
```

Terminal B:

```bash
curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8001/health
# 200

curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8001/operator/status
# 401

TOKEN="local-dev-placeholder-not-for-production"

curl -sS -H "X-OriginLab-API-Key: ${TOKEN}" \
  http://127.0.0.1:8001/operator/status | head -c 200
# not 401 (may be 200/503 depending on Postgres reachability)
```

---

## CORS is not authentication

`ORIGENLAB_API_CORS_ORIGINS` controls which browser origins may read responses and send credentialed preflights. It does **not** prove caller identity.

Production still requires `ORIGENLAB_API_AUTH_TOKEN` on private routes regardless of CORS configuration.

---

## Related docs

- [API response contract](API_RESPONSE_CONTRACT.md)
- [apps/api README](../README.md)
- [Phase 1 cloud read path](../../email-pipeline/docs/PHASE1_CLOUD_READ_PATH.md)
- [Cloudflare Access runbook](../../../docs/CLOUDFLARE_ACCESS_DASHBOARD_SECURITY.md)
