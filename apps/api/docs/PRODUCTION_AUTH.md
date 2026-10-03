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
- **The only write is the session row.** The callback reads `platform.operator` through the
  read-only repository and records one `platform.auth_session` row
  (`sign_in_kind = 'google_account'`, `20260928195000`); no `/v2` route became writable. No
  row, no cookie.
- **Sessions are revocable rows.** The signed cookie names its row by a random identifier (the
  database holds only a keyed hash of it) and binds the operator's `version`. Every request
  re-reads the row joined to the operator: a revoked, expired or missing row, or a changed
  operator `version` (any role, status or address change, by trigger), ends the session. Logout
  revokes the row before the cookie is cleared, so a copy taken earlier fails on its next
  request, on every API instance.
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
   | Authorized redirect URIs | `https://dashboard.origenlab.cl/api/auth/google/callback` | `http://localhost:5173/auth/google/callback`, plus one per other local port actually used (below) |

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
| `ORIGENLAB_AUTH_SESSION_SECRET` | when enabled, and always in production with V2 — **secret**, ≥ 32 chars (it also keys `address_ref`) | own random value (unset: a random per-process ref key) | own random value — startup refuses it missing |
| `ORIGENLAB_AUTH_SESSION_TTL_SECONDS` | no — default `28800` (8 h) | | |
| `ORIGENLAB_DEV_LOGIN_ENABLED` | no — default `false` | `true` only for the header login | **never** (startup refuses it) |
| `ORIGENLAB_V2_DATABASE_URL` | yes — the operator lookup needs it — **secret** | loopback DSN from `api-login` | `postgresql://origenlab_api:…@<host>:<port>/<db>` — no query string |
| `ORIGENLAB_V2_DATABASE_REMOTE` | no — default `false` (loopback only) | unset | `true` |
| `ORIGENLAB_V2_DATABASE_EXPECTED_HOST` | when remote | unset | the exact host name in the DSN |
| `ORIGENLAB_V2_DATABASE_SSLROOTCERT` | when remote | unset | absolute path of the provider's CA PEM (Render secret file, `/etc/secrets/…`) |

Generate a session secret with
`python -c 'import secrets; print(secrets.token_urlsafe(48))'`. Rotating it signs every
operator out. The API refuses to start on a missing client ID or secret, a secret shorter than
32 characters, an `http://` base URL anywhere but loopback, an `http://` base URL at all in
production, a production base URL whose path is not exactly `/api`, a consumer domain such as
`gmail.com`, or Google login without a V2 database.

**The exact URLs.** The Worker route is `dashboard.origenlab.cl/api*` and it strips `/api`
before forwarding to `https://api.origenlab.cl` (`apps/dashboard-proxy/src/allowlist.ts`,
`stripApiPrefix`). So:

| | Production | Local (Vite proxies `/auth` with no prefix) |
|---|---|---|
| `ORIGENLAB_AUTH_PUBLIC_BASE_URL` | `https://dashboard.origenlab.cl/api` | `http://localhost:5173` |
| Redirect URI registered in Google | `https://dashboard.origenlab.cl/api/auth/google/callback` | `http://localhost:5173/auth/google/callback` |
| Path the API receives | `/auth/google/callback` | `/auth/google/callback` |
| Where the browser lands after sign-in | `https://dashboard.origenlab.cl/` | `http://localhost:5173/` |

**Which local callback each setup supports.** The callback is not a property of the dashboard
code: it is whatever origin the browser uses, plus `/auth/google/callback`, and it works only
when three things agree — the Vite process proxies `/auth` to the API that will receive it,
that API's `ORIGENLAB_AUTH_PUBLIC_BASE_URL` is that origin, and the URI is registered on the
local Google client. `apps/dashboard/vite.config.ts` pins `server.port: 5173` with
`strictPort` and proxies `/auth` (and `/v2`) with no prefix; it has no `preview` block, so
`vite preview` inherits the same proxy (Vite 8 `preview.proxy ?? server.proxy`) on its own
default port.

| Setup | How it is started | `ORIGENLAB_AUTH_PUBLIC_BASE_URL` on the API it proxies to | Redirect URI to register |
|---|---|---|---|
| Documented default | `npm run dev` → `:5173`, proxy to `:8001` | `http://localhost:5173` | `http://localhost:5173/auth/google/callback` |
| Current local preview (clean room) | `ORIGENLAB_DEV_API_TARGET=http://127.0.0.1:8051 npx vite --port 5251 --strictPort` — the dev server on another port, not `vite preview` | `http://localhost:5251` on the `:8051` API | `http://localhost:5251/auth/google/callback` |
| Built bundle | `npm run build && npm run preview` → `:4173` | `http://localhost:4173` | `http://localhost:4173/auth/google/callback` |

The `:5173` callback does **not** serve the `:5251` preview: Google would send the browser to
`:5173`, which is a different Vite process proxying to a different API (or nothing). Browser
cookies are scoped to the host, not the port, and local (`http://`) cookies carry no `__Host-`
prefix, so two local dashboards on `localhost` share one cookie jar; a session from one API is
refused by the other (different session secret), and signing in on one signs the other out.
Run one sign-in setup at a time.

### Remote V2 database

Off by default: `ORIGENLAB_V2_DATABASE_URL` must be a literal loopback address unless
`ORIGENLAB_V2_DATABASE_REMOTE=true` (`src/origenlab_api/v2/remote_database.py`). Remote, the API
refuses to start unless the DSN names exactly `ORIGENLAB_V2_DATABASE_EXPECTED_HOST` (a DNS name),
carries no query string or fragment, does not name port 6543 (Supavisor transaction mode, refused
by name as the Slice 0 audit route refuses it — the reviewed route is the session pooler or the
direct connection, both on 5432; `docs/OPERATIONS.md` §4.2), logs in as `origenlab_api` (or the
pooler spelling `origenlab_api.<project-ref>`), and `ORIGENLAB_V2_DATABASE_SSLROOTCERT` is a readable CA PEM with
no private key. Every connection then uses `sslmode=verify-full` with that CA, passed as keyword
arguments so neither the DSN nor a `PGSSLMODE` in the environment can weaken it. Before serving,
one read-only session must prove it is `origenlab_api` on the named database, holds no
SUPERUSER/BYPASSRLS/CREATEROLE/CREATEDB/REPLICATION, and is not a member of `origenlab_owner`,
`origenlab_migrator`, `postgres`, or any role holding one of those attributes (a hosted project
has roles the list cannot name — `service_role` holds BYPASSRLS). The development header login
still refuses a remote database, and the import/rehearsal tools keep their own loopback-only
guards.

**Transaction behaviour.** Every repository opens one connection per unit of work, and every
setting it makes is transaction-local (`set transaction read only`, `set local
statement_timeout`, `set constraints all immediate`); nothing relies on session state, advisory
locks, `LISTEN` or temporary tables. Session mode is still the selected route because it is the
one reviewed end to end, and because psycopg's automatic server-side prepared statements are
not a guaranteed fit for a transaction pooler.

**What is proven where.** Against an ordinary PostgreSQL 17 (a throwaway container with a
throwaway CA — the `ORIGENLAB_V2_TLS_TEST_*` tests): verify-full refuses a wrong CA and a wrong
host name, the probe accepts the plain runtime role, refuses a superuser session, and refuses
membership in an unnamed BYPASSRLS role. **Not proven by anything in this repository: the API
path against a real hosted pooler** — that the pooler login `origenlab_api.<ref>` reaches
`current_user = origenlab_api`, that verify-full passes against the pooler's certificate with the
CA the provider actually uses (the Slice 0 audit's first hosted run stopped exactly there), that
`origenlab_api` on the hosted project passes the membership probe, and that nothing between the
pooler and the database is covered by this TLS check at all — verify-full authenticates the
client-to-pooler hop only.

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

### Production activation (Phase B, 2026-10)

1. A remote V2 database is chosen and adopted (an owner decision), `origenlab_api` has a
   password there, and the remote variables above are set (code ready, not exercised against
   any real host).
2. Every operator who should sign in has an `active` `platform.operator` row — via
   `scripts/operator_roster.py`, run as the migrator (`ORIGENLAB_V2_PROVISIONING_DATABASE_URL`,
   a login that may `SET ROLE origenlab_owner`): review the plan, then `--apply
   --confirm-changes <N> --confirm-database <name>` with what that plan printed. The runtime
   API role cannot write `platform.operator` at all.
3. Set the production column above as Render secrets on `origenlab-api`.
4. Deploy `apps/dashboard-proxy`; Cloudflare Access is removed from `dashboard.origenlab.cl`
   after the first end-to-end sign-in (decision: `docs/MIGRATION.md` §11 row 5, runbook:
   `docs/OPERATIONS.md` §1.2 steps 9 and 10). The Worker lists `/auth/*` and passes exactly the
   two `__Host-` cookies and the two checked redirects (`apps/dashboard-proxy/src/auth.ts`); it
   never forwards Access's `CF_Authorization` cookie upstream, and it never derives an operator
   header. The `api.origenlab.cl` Access application stays.
5. Deploy the dashboard. The gate fails closed: if the Worker answers `path_not_allowed` (or
   anything but a session answer) on `/auth/session`, `AuthGate.tsx` renders «No se pudo
   verificar la sesión» rather than opening the dashboard — it does not read the refusal as
   "no sign-in here".

## Shared Workspace login with operator profiles

One Google Workspace account (the shared company mailbox) is used by several people. Google
proves the **account**; it cannot say which **person** is at the keyboard. So the two are
separate facts (`docs/ARCHITECTURE.md` §5.1, `docs/DOMAIN.md` §7.3):

| Concept | Where | What it is |
|---|---|---|
| **Principal** | `platform.auth_principal` | the Google account: its address **and** its stable issuer + `sub` (required in production). Grants nothing by itself |
| **Operator** | `platform.operator`, `sign_in_kind = 'shared_profile'` | the person who acts: display name, role (`admin` / `sales` / `viewer`), status. **Has no email address** — no invented or duplicate mailbox is created per person |
| **Profile** | `platform.operator_profile` | links one operator to one principal, with that person's PIN as an Argon2id hash |
| **Audit** | `platform.auth_event` | append-only: profile selected / refused / locked / cleared, logout, and a migrator's early lockout clear |
| **Session** | `platform.auth_session` | one row per shared-sign-in session: a keyed hash of the cookie's identifier, the principal, the selected operator, expiry, revocation |

Individual Google accounts keep working unchanged: an operator with `sign_in_kind =
'google_account'` still signs in with their own address and never sees the profile screen.
The two can coexist; a principal's address can never also be an operator's address.

**Status.** Built and tested against disposable databases; **not applied to any real database
and not deployed.** Off unless `ORIGENLAB_PROFILE_LOGIN_ENABLED=true`.

### Flow

```text
Google sign-in (unchanged up to the claims check)
  └─ address or account (issuer + sub) is a platform.auth_principal? ──no──▶ operator by email (unchanged)
        │yes: exactly one principal is both; its pinned issuer + sub match the token's
        │     (production: a principal with no pinned account is refused); principal active
        ▼
  session row recorded (keyed hash of a random identifier, expiry = cookie expiry)
  signed session { session identifier, principal id, address, Google issuer + sub,
                   principal version, auth time }
  state = profile_required  — every /v2 read, workspace and command route answers 401
                              "profile_required"; the dashboard shows the profile screen
        │
  GET  /auth/profiles        → the principal's usable profiles: id, display name, role label
  POST /auth/profile/select  {profile_id, pin}
        │  one transaction:
        │  DB:  begin_pin_attempt locks principal row, then profile row; returns the lock,
        │       Argon2id parameters + salt (decoy if unknown/foreign/unusable) and a one-use
        │       attempt id + nonce — never the stored hash
        │  API: one Argon2id derivation under the pepper (empty input if locked/malformed);
        │       proof = HMAC-SHA256(output, attempt ‖ principal ‖ profile ‖ nonce)
        │  DB:  finish_pin_attempt compares digests with the stored verifier, writes the
        │       verdict, counters, lock and exactly one audit event
        ▼
  old session row revoked (profile_selected); successor row + cookie with a new identifier and
  { operator id, operator version, profile version, selection time }, same auth time and the
  same expiry — switching never extends a sign-in
        │
  every request: principal + session row + profile + operator re-read in one statement;
  session row revoked / expired / missing → signed out; any version, status, role or link
  mismatch → 401 profile_required (principal change → signed out)
        │
  POST /auth/profile/clear   → row revoked (profile_cleared), successor without a profile, same
                               expiry: back to profile_required ("Cambiar perfil"; Google stays)
  POST /auth/logout          → row revoked (logout) and audited, then the cookie is cleared
                               ("Cerrar sesión"); 503 logout_not_recorded if the revocation
                               could not be written — and then NO cookie is cleared: the
                               session is still live, the dashboard stays signed in and shows
                               "No se pudo cerrar la sesión de forma segura. Intenta
                               nuevamente."; a retry that succeeds revokes the same row
```

### Security properties

- **The Google account, not the address.** A principal is pinned to the verified ID token's
  issuer (stored in its canonical `https://accounts.google.com` spelling) and `sub`. The
  callback admits a token only as the one principal that is both its address and that account;
  every request re-checks the pair signed into the session against the row. A Google account
  deleted and recreated under the same address has a new `sub` and is refused (sign-in and
  every existing session). **In production a principal with no pinned account cannot sign in
  at all** — the callback refuses it and logs the observed `sub`, for comparison with the
  directory `id` only; nothing is ever pinned on first use. Only the migrator roster tool can
  pin or re-pin (the runtime role has no column grant; the tool never unpins), from the Admin
  SDK Directory `id` read with a read-only scope; a re-pin moves the principal's version and so
  ends every session of the old account.
- **The server selects the operator.** The browser sends a profile id and a PIN; the operator,
  its role and the principal come from the database and the signed cookie. No header, cookie
  field or body field can name an operator (`X-OriginLab-Operator-Email` stays refused in
  production). A profile of another principal is simply not found.
- **PIN storage.** Argon2id (RFC 9106: 64 MiB, t=3, p=4) from `cryptography` ≥ 44 — no new
  dependency — with a per-hash salt and the pepper passed as Argon2's secret input. The
  column refuses anything that is not an Argon2id PHC string. A PIN is never stored, returned,
  logged, put in an exception or an audit row; the select body is parsed by hand so a
  validation error cannot echo it.
- **The database decides the PIN, not the API process** (`20260929100000`). The runtime role
  cannot read `operator_profile.pin_hash` (the table-level `SELECT` is revoked and every other
  column granted by name, because a column revoke would not override it). A selection is one
  transaction: `platform.begin_pin_attempt` returns the attempt's Argon2id parameters and salt
  and a one-use attempt id and 32-byte nonce; the API derives Argon2id(PIN, salt, pepper) and
  sends only HMAC-SHA256 of that output over the attempt, the principal, the profile and the
  nonce; `platform.finish_pin_attempt` recomputes it from the stored verifier, compares SHA-256
  digests of the two, and itself writes the verdict, the counters, the lock and exactly one
  audit event. It accepts only the attempt begun in the same transaction for the same pair, once.
  The caller supplies no verdict, counter, timestamp, lock duration or event type, so a SQL-only
  attacker — an injection, a leaked runtime password, a bug — cannot declare a success, reset a
  throttle, skip the audit, or replay a captured proof (it names an attempt that has ended). The
  raw Argon2id output never leaves the API process, and no proof is stored or logged.
- **The API host is inside the trust boundary, and this design does not protect against its
  compromise.** Whoever holds the pepper can derive a proof for any guess and roll a failed
  attempt back instead of committing it, so the throttle bounds only callers who commit; whoever
  holds the session secret can mint a cookie for a session row they insert (the runtime role may
  insert `platform.auth_session` rows — that is how sign-in works) and act as any operator. A
  SQL-only attacker without that secret cannot: a row names a cookie only through
  HMAC-SHA256(session secret, identifier), and the cookie itself is HMAC-signed
  (`test_v2_pin_attempt_boundary.py` proves an inserted row authenticates nothing). Keep the
  pepper and the session secret in the secret store, distinct, and rotate both if the host is
  suspected.
- **One public refusal.** Unknown profile, another principal's profile, wrong PIN, malformed
  PIN, disabled profile or operator, and a lock all answer `401 {"detail":
  "profile_selection_failed"}` — the same status, body and (absent) cookie. The reason is
  recorded in the audit only.
- **Timing is not constant, and is not claimed to be.** One Argon2id derivation is spent on
  every refusal — on the submitted PIN; on an empty input when the attempt is locked or the PIN
  is malformed (the submitted PIN is never used then); at the profile's own parameters, or the
  principal's first profile's for an unknown, foreign or unusable one (decoy salt) — so the
  dominant cost does not depend on the reason. The database work does: a locked attempt writes
  no counters, an unknown profile id locks one row instead of two. No `/auth/*` response —
  success, refusal, lockout, unknown profile, session check, callback, clear or logout — carries
  `Server-Timing` or `X-Process-Time-Ms` (the API omits them and the Worker drops them), but the
  wall-clock time of a request stays observable. A caller may therefore tell by timing that a
  profile is locked, or that an id is not a profile. Neither reveals a PIN; the lock is what bounds
  guessing.
- **Throttle and lockout (persistent, shared by every worker):**

  | Rule | Value |
  |---|---|
  | Consecutive failures that lock one profile | 5 |
  | Consecutive failures that lock the whole principal (all profiles, unknown ids included) | 10 |
  | Lock duration | 15 min, doubling with each lockout remembered, capped at 24 h |
  | A failure stops counting after | 1 h without another failure |
  | A lockout stops doubling the next after | 24 h without a failure |
  | Attempt while locked | refused, **not counted**; the submitted PIN is never used (an Argon2 derivation of an empty input at the profile's parameters is still spent) |
  | Malformed PIN | a **counted** failure (`malformed_pin`), like a wrong one |
  | Audit | exactly one event per attempt: `profile.selected`, `profile.selection_refused`, or — for the failure that starts a lock — `profile.locked` / `principal.locked`, carrying the refusal reason; written only by `platform.finish_pin_attempt`, never insertable by the runtime role |
  | Success | resets the selected profile's counters and the principal's failure count; keeps the principal's lockout history (so one known PIN cannot reset the principal-wide backoff); never touches another profile's |

  Counters live on the principal and profile rows; `begin_pin_attempt` takes both `FOR UPDATE`
  in principal → profile order before the PIN is derived, and they stay held until the attempt's
  transaction ends, so concurrent attempts across API instances serialize and none is lost. A lock is cleared early only with the migrator tool
  (`profile_auth_admin.py clear-lockout`, below), which records a `lockout.cleared` audit event
  the runtime role cannot write; it is never a dashboard or API action.
- **Session binding and revocation.** The cookie is HMAC-signed (unchanged key), HttpOnly,
  SameSite=Lax, and `Secure` + `__Host-` over HTTPS. It names a `platform.auth_session` row
  by a random 256-bit identifier; the table stores only HMAC-SHA256(session secret, identifier),
  so a database copy yields no usable cookie. Every request requires that row to exist, belong
  to the principal, carry the operator the cookie names, and be neither revoked nor expired by
  the database clock. **Logout revokes the row before clearing the cookie**, so a copied cookie
  fails on its next request on every API instance. "Cambiar perfil" and every selection revoke
  the current row and insert its successor with the **same `expires_at`** (a trigger forbids
  ever extending one, and makes revocation final). Database triggers also bump
  `operator.version` on any role / status / address change, `operator_profile.version` on any
  PIN / link / key / status change, and `auth_principal.version` on any address / issuer /
  subject / status change — whoever writes the row. The per-request compare therefore ends every
  affected session on its next request, including a Karla-promoted-to-admin session (it must
  be re-selected with the PIN). Throttle updates bump nothing. Sessions of an individual's own
  Google account (no profile) are rows in the same table (`sign_in_kind = 'google_account'`,
  `20260928195000`), required on every request and revoked by logout the same way.
- **CSRF.** The Worker refuses every sign-in POST without an allowed `Origin` or with a
  cross-site `Sec-Fetch-Site`, and the two profile POSTs without `application/json` or above
  1 KiB. The API independently requires `application/json` on both and refuses
  `Sec-Fetch-Site: cross-site`.
- **Least privilege.** `origenlab_api` only reads `platform.operator`: it cannot create an
  operator or change a role, status or sign-in kind (INSERT and UPDATE revoked by
  `20260928192000`; no route ever used them). It reads the four sign-in tables — every column
  but `operator_profile.pin_hash` — appends the `profile.cleared` and `session.logout` audit
  events, reaches the PIN throttle only through `platform.begin_pin_attempt` /
  `platform.finish_pin_attempt` (`20260929100000`, above; it holds no UPDATE on either throttle
  table and cannot insert a PIN outcome event), and inserts and revokes session rows (it
  can never extend, reopen or delete one). It cannot create a principal or profile, relink one or
  write a PIN hash. `origenlab_worker` has no access to them at all.

### Environment variables

| Variable | Required | Local value | Production value |
|---|---|---|---|
| `ORIGENLAB_PROFILE_LOGIN_ENABLED` | to turn it on | `true` | `true` when activated |
| `ORIGENLAB_PROFILE_PIN_PEPPER` | when enabled — **secret**, ≥ 32 chars, ≥ 12 distinct, never equal to the session secret | own random value | own random value in the secret store |
| `ORIGENLAB_DEV_PROFILE_PRINCIPAL_EMAIL` | no — local only | an invented address under `.test` / `.invalid` / `example.com` | **never** (startup refuses it) |

The API refuses to start when profile login is on and the pepper is missing, short,
low-variety or equal to `ORIGENLAB_AUTH_SESSION_SECRET`; without `ORIGENLAB_V2_DATABASE_URL`
or the session secret; or with no way to sign in (Google off and no local shortcut).
Generate the pepper with `python -c 'import secrets; print(secrets.token_urlsafe(48))'`.
**Changing the pepper invalidates every PIN** — re-provision all of them in the same change.

### Provisioning (`scripts/profile_roster.py`)

The roster is JSON **outside the repository**:

```json
{
  "principal": {"email": "<shared address>@origenlab.cl", "provider_subject": "<Google sub>"},
  "profiles": [
    {"key": "<slug>", "display_name": "<Nombre>", "role": "admin", "sort_order": 1},
    {"key": "<slug>", "display_name": "<Nombre>", "role": "sales", "sort_order": 3}
  ]
}
```

PINs (6–12 digits; no repeated digit, no ascending/descending run) are **never** in the roster
and never on the command line: `--prompt-pin <key>` asks for each twice on a hidden terminal
prompt, or `--pin-file` reads `{"<key>": "<PIN>"}` from a file outside the repository that is
mode `0600` and owned by the caller. A new profile needs a PIN; an existing one gets a new
PIN only when one is given. The plan masks addresses and never prints a PIN or hash.

The DSN comes from `ORIGENLAB_V2_PROVISIONING_DATABASE_URL` (another name with `--dsn-env`):
a login that may `SET ROLE origenlab_owner` — the migrator — because the runtime role cannot
write these rows. The pepper comes from `ORIGENLAB_PROFILE_PIN_PEPPER` and must be the
API's.

```bash
# plan: read-only, prints the target database and the exact change count
uv run python scripts/profile_roster.py --roster ~/data/<dir>/profiles.json --pin-file ~/data/<dir>/pins.json
# apply exactly the reviewed plan
uv run python scripts/profile_roster.py --roster … --pin-file … \
    --apply --confirm-changes <N> --confirm-database <database name the plan printed>
```

`provider_subject` is the shared account's stable Google subject, obtained and verified as in
*Obtaining the shared account's Google subject* below — read from the Admin SDK Directory, not
taken from a sign-in. Omitting `provider_subject` leaves a pinned one unchanged (the tool never
unpins); a different value is shown as a `re-pin` in the plan. A plan whose principal would
stay unpinned ends with a `WARNING` line: production refuses that principal.

### Obtaining the shared account's Google subject (read-only, before the roster is applied)

**Pinning stays a migrator decision, never a sign-in side effect.** Nothing pins an account on
first use: the runtime role holds no grant on `provider_subject` / `provider_issuer`, the API
never writes them, and a production sign-in to an unpinned principal is refused outright. The
subject is read beforehand from the Google Workspace directory, by an administrator, with a
read-only scope, and checked before `profile_roster.py --apply`.

For a Google Workspace user, the ID token's `sub` is the Admin SDK Directory API user `id`
(a decimal string). Read it without creating any client, service account, key or delegation:

1. As a Workspace **administrator** of `origenlab.cl`, open the Directory API reference page for
   `users.get` (`developers.google.com/admin-sdk/directory/reference/rest/v1/users/get`) and use
   its **Try this method** panel (the Google APIs Explorer).
2. `userKey` = the shared address exactly as it will appear in the roster. `fields` =
   `id,primaryEmail,suspended,archived,creationTime,isEnrolledIn2Sv`. Under *Credentials* keep
   **Google OAuth 2.0** only and select **only** the read-only scope
   `https://www.googleapis.com/auth/admin.directory.user.readonly` — clear every other scope the
   panel proposes. This call can read one user record and change nothing.
3. Execute, and read the answer on screen. Check, before copying anything:
   - `primaryEmail` equals the roster's `principal.email`, character for character (lowercase);
     an alias resolving to another primary address is the wrong account;
   - `suspended` and `archived` are `false`;
   - `creationTime` is when the shared account was actually created. A recent date on a
     long-lived mailbox means it was deleted and recreated — the new `id` is then correct, but
     confirm with the Workspace owner that the recreation was theirs before pinning it;
   - `id` is digits only (the roster refuses anything outside `[A-Za-z0-9._-]`).
4. Copy **only** `id` into the roster file, which lives outside the repository (`~/data/…`).
   Never paste it into a commit, an issue or a chat: the repository is public, and an account
   id joined to its address is identifying.
5. `uv run python scripts/profile_roster.py --roster … --pin-file …` (plan, read-only). The
   plan must show `provider_subject` on the principal (`insert`, or `re-pin` only when a re-pin
   is intended) and no `WARNING` line. Then apply with the exact count and database name.

**Optional cross-check, never the source.** After the roster is applied, a production sign-in
by the Workspace owner either succeeds (the pin is right) or is refused and logged with the
token's `observed subject …`. A logged subject is never pinned by itself: it can come from
anyone who reached the callback with that address, so it is only compared with the directory
`id`. If the two differ, stop — pin neither, and find out why (a recreated account, a wrong
address, a sign-in that was not the owner's).

**Never printed, pasted or stored:** the APIs Explorer access token (it stays in that browser
tab; do not copy it into a shell, a `curl` command or a file), any refresh token, the session
secret, the PIN pepper, and any PIN. The Directory call needs none of them from this
repository, and `profile_roster.py` never prints a PIN, a hash or the pepper.

To disable a person, set their `"status": "disabled"` and apply (their session ends on the
next request). Profiles the roster omits are reported and left untouched. Delete the PIN file
after applying.

### Migrator maintenance (`scripts/profile_auth_admin.py`)

Two operations, both as the migrator and both plan-first; neither is ever a route.

**Clearing a lockout early.** A lock expires by itself (15 min, doubling). To lift one sooner —
a person locked out who has proven who they are — name the principal and what to clear: one or
more profiles (`--profile <key>`) and/or the principal-wide throttle (`--principal`):

```bash
uv run python scripts/profile_auth_admin.py clear-lockout --principal-email <shared address> --profile <key>
uv run python scripts/profile_auth_admin.py clear-lockout --principal-email <shared address> --profile <key> \
    --apply --confirm-changes <N> --confirm-database <name>
```

The plan shows each target's state (`LOCKED until …`, failures, lockouts) and the exact count.
Applying resets that target's four throttle columns and appends one `platform.auth_event`
`lockout.cleared` (principal, and the profile's operator when a profile was cleared) per target
— an event a trigger refuses from the runtime role. It moves no version (nobody is signed out)
and never touches a PIN; a forgotten PIN is re-provisioned with `profile_roster.py`.

**Pruning sessions.** Ended session rows grant nothing, but the runtime role cannot delete them.
The migrator prunes rows that expired more than a day ago (`--older-than-days`, at least 1):

```bash
uv run python scripts/profile_auth_admin.py prune-sessions
uv run python scripts/profile_auth_admin.py prune-sessions --apply \
    --confirm-changes <N> --confirm-database <name> --confirm-cutoff <timestamp the plan printed>
```

Same DSN rule as provisioning (`ORIGENLAB_V2_PROVISIONING_DATABASE_URL`, a login that may
`SET ROLE origenlab_owner`). It is never a route.

### Local setup with an invented profile

1. A disposable or local loopback database with the migrations applied.
2. Provision an **invented** principal and profiles with invented PINs, e.g. principal
   `perfiles@origenlab.test` (roster `--domain origenlab.test`).
3. Start the API with `ORIGENLAB_PROFILE_LOGIN_ENABLED=true`, a pepper,
   `ORIGENLAB_AUTH_SESSION_SECRET`, `ORIGENLAB_DEV_LOGIN_ENABLED=true` and
   `ORIGENLAB_DEV_PROFILE_PRINCIPAL_EMAIL=perfiles@origenlab.test` (Google may stay off).
   The shortcut `POST /auth/dev/principal-session` mounts only then — never in production,
   never on a non-loopback database, never for an address outside a reserved test domain —
   and its sessions are marked `dev`, which a production API refuses to honour. The Worker
   never forwards it.
4. The login screen shows **Entrar con la cuenta de prueba local**; the profile screen and the
   PIN check are then exactly the production ones.

### Production procedure (future; not performed)

1. Prerequisites of *Production activation* above (remote V2 database adopted, Google client).
2. Apply the `20260928180000`–`20260928195000` slice-1 sign-in migrations with the normal
   migration procedure (`docs/OPERATIONS.md`). They seed nothing.
3. Generate the pepper; store it as a Render secret `ORIGENLAB_PROFILE_PIN_PEPPER` (distinct
   from the session secret). Do not set it anywhere else.
4. Obtain and verify the shared account's Google `sub` — its Admin SDK Directory user `id`, read
   with the read-only scope — exactly as in *Obtaining the shared account's Google subject*,
   and put it in the roster as `provider_subject`. Without it the plan warns and production
   refuses the principal. Never pin a subject taken only from a sign-in log.
5. Each person chooses a PIN and enters it themselves at the hidden prompt of
   `profile_roster.py` run by the administrator, or the administrator writes a `0600` PIN file
   on an encrypted local disk, applies, and deletes it. Plan, review, apply with the exact
   count and database name — as the migrator login, never the runtime login.
6. Set `ORIGENLAB_PROFILE_LOGIN_ENABLED=true` on `origenlab-api`; deploy the Worker (it lists
   `/auth/profiles` and `/auth/profile/{select,clear}`), then the dashboard.
7. Verify: sign in with the shared account → profile screen; each person selects with their
   PIN; the sales profile gets 403 on an admin command; `platform.auth_event` records it.
8. **Rollback:** set `ORIGENLAB_PROFILE_LOGIN_ENABLED=false` (the shared address then has no
   operator and is refused at sign-in; individual operators are unaffected). The tables can
   stay; they grant nothing while the switch is off.

---

## Related docs

- [API response contract](API_RESPONSE_CONTRACT.md)
- [apps/api README](../README.md)
- [Phase 1 cloud read path](../../email-pipeline/docs/PHASE1_CLOUD_READ_PATH.md)
- [Cloudflare Access runbook](../../../docs/CLOUDFLARE_ACCESS_DASHBOARD_SECURITY.md)
