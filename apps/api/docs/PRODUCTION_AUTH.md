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

## Google Workspace login (dashboard, V2 boundary)

Operators sign in to the dashboard with their Google Workspace account (for example
`contacto@origenlab.cl`). Only verified accounts that are **members of the `origenlab.cl`
Workspace** and that have an **active `platform.operator` row** get a session.

**Status.** Built and tested; usable **locally** against a loopback V2 database. **Not
deployable to production yet**: it maps the signed-in address to `platform.operator`, which
lives only in the V2 database, and `ORIGENLAB_V2_DATABASE_URL` accepts a loopback DSN only
while the hosted phase is frozen ([`docs/STATUS.md`](../../../docs/STATUS.md) §2.8). The
production values below are the ones the deployment configuration already fixes
(`render.yaml`, `apps/dashboard-proxy/wrangler.toml`), recorded so nothing has to be
re-derived at activation.

**Relation to [`docs/ARCHITECTURE.md`](../../../docs/ARCHITECTURE.md) §5.** That section names
Supabase Auth (ES256 JWT, JWKS) as the V2 operator identity, and it is unchanged. This login
is a separate adapter behind the same `IdentityPort` (`origenlab_api/v2/identity.py`), and
`ORIGENLAB_V2_JWKS_URL` still wins whenever it is set. Whether it stands in until slice 1 or
replaces Supabase Auth for the dashboard is an owner decision this document does not make.

### Flow

```text
browser ──GET /auth/google/login──▶ API: new state, nonce, PKCE verifier
                                     → signed HttpOnly sign-in cookie (10 min)
        ◀──302 accounts.google.com (scope=openid email profile, S256 challenge, hd=origenlab.cl)
browser ──account picker / 2FA at Google──▶
        ──GET /auth/google/callback?code&state──▶ API:
            1. state == cookie state (constant-time)       else login_error=invalid_state
            2. code → token endpoint (server-to-server, client secret + PKCE verifier)
            3. ID token claims: iss, aud, azp, exp, iat, nonce, sub
            4. email_verified is true                       else email_unverified
            5. email ends exactly in @origenlab.cl AND hd == origenlab.cl   else wrong_domain
            6. platform.operator by normalized email        else unknown_operator
            7. status active                                else operator_disabled
            → signed HttpOnly session cookie (8 h), 303 to the dashboard root
browser ──GET /v2/*, /auth/session (cookie)──▶ API re-reads platform.operator every request
```

| Route | Method | Mounted when |
|---|---|---|
| `/auth/google/login` | GET | `ORIGENLAB_GOOGLE_AUTH_ENABLED=true` |
| `/auth/google/callback` | GET | `ORIGENLAB_GOOGLE_AUTH_ENABLED=true` |
| `/auth/session` | GET — 200 with the operator, 401 with sign-in options | the V2 boundary is mounted |
| `/auth/logout` | POST — clears the session cookie | the V2 boundary is mounted |

**What it guarantees, and what it does not:**

- **Scopes are `openid email profile` only.** No Gmail, Drive, Calendar or other Google API
  access; no refresh token (`access_type` stays `online`); the access token is discarded.
- **`hd` is enforced on the claims, not trusted as a UI hint.** A consumer Google account
  registered with an `@origenlab.cl` address has a verified email but no `hd`, and is refused.
- **The ID token signature is not verified locally** — by design, per OpenID Connect Core
  §3.1.3.7: the token comes straight from Google's token endpoint over TLS in exchange for
  the client secret and the PKCE verifier, never through the browser. Every claim is still
  checked. See the module docstring in `origenlab_api/v2/google_oidc.py`.
- **No operator is ever created by signing in.** Unknown addresses are refused.
- **Nothing is written to the database.** The callback reads `platform.operator` through the
  read-only repository; no `/v2` route became writable.
- **Sessions are stateless signed cookies.** Logout clears the cookie in that browser but
  cannot revoke a copy taken earlier; the 8-hour lifetime bounds that. Disabling the operator
  (`status = 'disabled'`) ends every session on its next request.
- **The operator-email header is never trusted by the V2 boundary in production.** The header
  login (`ORIGENLAB_DEV_LOGIN_ENABLED`) is refused at startup when `ORIGENLAB_ENV=production`.
  The V1 `/operations/*` commands are unchanged: they still take the operator from the header
  the Worker rebuilds from Cloudflare Access.

### Google Cloud Console setup

Do this signed in as an `origenlab.cl` Workspace administrator (or a user allowed to create
projects in the `origenlab.cl` organization). Console section names are those of the
**Google Auth Platform** pages (formerly "OAuth consent screen").

1. **Project.** In [console.cloud.google.com](https://console.cloud.google.com), select or
   create a project **inside the `origenlab.cl` organization** — an *Internal* app is only
   possible for a project owned by the Workspace organization. No API needs enabling: sign-in
   uses only the OpenID Connect endpoints.
2. **Google Auth Platform → Branding.** App name `OrigenLab Dashboard`; user support email
   `contacto@origenlab.cl`; authorized domain `origenlab.cl`; developer contact
   `contacto@origenlab.cl`.
3. **Google Auth Platform → Audience.** User type **Internal**. Only accounts in the
   `origenlab.cl` Workspace can then complete consent at all, no Google verification review is
   needed, and there is no test-user list to maintain. (The API enforces the domain on its own
   regardless — Internal is the second lock, not the only one.)
4. **Google Auth Platform → Data Access.** Add only the three non-sensitive scopes `openid`,
   `.../auth/userinfo.email`, `.../auth/userinfo.profile`. **Do not add any Gmail, Drive,
   Calendar or other scope.** The dashboard's sign-in client must never be the same OAuth
   client as the Drive quote workspace or any Gmail tooling.
5. **Google Auth Platform → Clients → Create client**, application type **Web application**.
   Create **two clients**, so the production secret never sits on a laptop:

   | | Production — `OrigenLab Dashboard (production)` | Local — `OrigenLab Dashboard (local)` |
   |---|---|---|
   | Authorized JavaScript origins | `https://dashboard.origenlab.cl` | `http://localhost:5173` |
   | Authorized redirect URIs | `https://dashboard.origenlab.cl/api/auth/google/callback` | `http://localhost:5173/auth/google/callback` |

   The redirect URI must equal `ORIGENLAB_AUTH_PUBLIC_BASE_URL` + `/auth/google/callback`
   byte for byte. Production goes through the dashboard's own origin because the browser
   reaches the API only via the Worker on `dashboard.origenlab.cl/api*`
   (`apps/dashboard-proxy/wrangler.toml`), and the session cookie must be set on the
   dashboard host. The JavaScript origins are not used by this server-side flow; listing them
   is harmless and keeps the client's intent readable.
6. Copy each client's **Client ID** and **Client secret** straight into the secret store for
   that environment. Never into Git, `.env.example`, a ticket or a chat.

### Environment variables

| Variable | Required | Local value | Production value |
|---|---|---|---|
| `ORIGENLAB_GOOGLE_AUTH_ENABLED` | yes, to turn it on | `true` | `true` |
| `ORIGENLAB_GOOGLE_CLIENT_ID` | when enabled | local client's ID | production client's ID |
| `ORIGENLAB_GOOGLE_CLIENT_SECRET` | when enabled — **secret** | local client's secret | production client's secret |
| `ORIGENLAB_GOOGLE_WORKSPACE_DOMAIN` | no — default `origenlab.cl` | `origenlab.cl` | `origenlab.cl` |
| `ORIGENLAB_AUTH_PUBLIC_BASE_URL` | when enabled | `http://localhost:5173` | `https://dashboard.origenlab.cl/api` |
| `ORIGENLAB_AUTH_SESSION_SECRET` | when enabled — **secret**, ≥ 32 chars | own random value | own random value |
| `ORIGENLAB_AUTH_SESSION_TTL_SECONDS` | no — default `28800` (8 h) | | |
| `ORIGENLAB_DEV_LOGIN_ENABLED` | no — default `false` | `true` only for the header login | **never** (startup refuses it) |
| `ORIGENLAB_V2_DATABASE_URL` | yes — the operator lookup needs it | loopback DSN from `api-login` | blocked: loopback only today |

Generate a session secret with
`python -c 'import secrets; print(secrets.token_urlsafe(48))'`. Rotating it signs every
operator out. The API refuses to start on a missing client ID or secret, a secret shorter than
32 characters, an `http://` base URL anywhere but loopback, an `http://` base URL at all in
production, a consumer domain such as `gmail.com`, or Google login without a V2 database.

With `ORIGENLAB_V2_DATABASE_URL` set, **either** Google login **or** the development header
login must be switched on; with neither, the API refuses to start rather than serving a `/v2`
that refuses every request. The dashboard itself needs no new variable.

### Local setup

1. Build the clean room with your real address as its operator — sign-in never creates one:
   `OL_CLEAN_OPERATOR_EMAIL=contacto@origenlab.cl supabase/scripts/cleanroom_db.sh build --force`,
   then `supabase/scripts/cleanroom_db.sh api-login` for the DSN file.
2. Append the Google variables (local column above) to that DSN file, which is outside Git.
3. Start the API from `apps/api` with that file's variables exported, on `127.0.0.1:8001`.
4. Start the dashboard with `npm run dev` in `apps/dashboard`, and open
   **`http://localhost:5173`** — exactly that origin, not `127.0.0.1:5173`. The session cookie
   belongs to the host the browser used, and it must be the host of the redirect URI.
5. Choose **Iniciar sesión con Google**. The dashboard shows the address and a
   **Cerrar sesión** button in the header once signed in.

The header login still exists for work that does not need Google: set
`ORIGENLAB_DEV_LOGIN_ENABLED=true` on the API and `ORIGENLAB_DEV_OPERATOR_EMAIL` on the Vite
process (`apps/dashboard/README.md`). With both logins on, a Google session wins, and an
invalid or expired session is refused — it is never rescued by the header.

### Production activation (not yet possible)

1. The hosted V2 project is adopted and `ORIGENLAB_V2_DATABASE_URL` accepts it (blocked).
2. Every operator who should sign in has an `active` `platform.operator` row.
3. Set the production column above as Render secrets on `origenlab-api`.
4. Deploy `apps/dashboard-proxy`: it already lists `/auth/*` and passes exactly the two
   `__Host-` cookies and the two checked redirects (`apps/dashboard-proxy/src/auth.ts`).
   Cloudflare Access can stay in front of `dashboard.origenlab.cl`; the Worker never forwards
   Access's `CF_Authorization` cookie upstream.
5. Deploy the dashboard. Until step 4 the dashboard treats the Worker's `path_not_allowed` on
   `/auth/session` as "no sign-in here" and behaves exactly as before.

---

## Related docs

- [API response contract](API_RESPONSE_CONTRACT.md)
- [apps/api README](../README.md)
- [Phase 1 cloud read path](../../email-pipeline/docs/PHASE1_CLOUD_READ_PATH.md)
- [Cloudflare Access runbook](../../../docs/CLOUDFLARE_ACCESS_DASHBOARD_SECURITY.md)
