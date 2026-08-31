# OrigenLab API (`apps/api`)

> **Operator handoff (v1 freeze):** [../dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md](../dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md)
>
> **Portfolio demo guide:** [docs/PORTFOLIO_DEMO_GUIDE.md](docs/PORTFOLIO_DEMO_GUIDE.md)

**Operator API** over SQLite, Postgres, and `reports/out/active/current`: read routes for the dashboard, read-only `/mirror/*` reporting, and the **durable commercial CRM commands under `POST /operations/*`** (trusted operator identity, Idempotency-Key, optimistic concurrency, append-only events). This app is separated from `apps/email-pipeline` so daily ingest, DNR refresh, and outbound CLIs stay unchanged. Canonical architecture: [`docs/architecture/CURRENT_SYSTEM_TRUTH.md`](../../docs/architecture/CURRENT_SYSTEM_TRUTH.md).

## Package layout

This app owns **`apps/api/src/origenlab_api`** (operator routes + `/mirror/*` Postgres reporting). The legacy email-pipeline FastAPI tree on port **8000** was **removed in API-3 Phase 6** — see [docs/API-3_PHASE6_LEGACY_REMOVAL_COMPLETE.md](docs/API-3_PHASE6_LEGACY_REMOVAL_COMPLETE.md).

**Always run tests and uvicorn from `apps/api`:**

```bash
cd apps/api
uv sync --group dev
uv run pytest tests -q
uv run uvicorn origenlab_api.main:app --host 127.0.0.1 --port 8001
```

`tests/conftest.py` prepends `apps/api/src` to `sys.path`. `tests/test_import_guard.py` asserts `origenlab_api.main` loads from **`apps/api/src`**.

## Local systemd (optional)

User-unit templates + install notes: [`docs/LOCAL_SYSTEMD.md`](docs/LOCAL_SYSTEMD.md) · tracked units under [`deploy/systemd/user/`](../../deploy/systemd/user/).

## Runtime truth

| Layer | Role |
|-------|------|
| **SQLite** (`ORIGENLAB_SQLITE_PATH`) | Authoritative for outbound safety, Sent memory, outreach sidecars |
| **This API** | Dashboard reads + `/mirror/*` reporting + durable `/operations/*` CRM commands (the only human write path) |
| **Postgres** | Rebuildable machine mirrors (published by `auto-mirror-dashboard`) **plus** the durable human CRM (`commercial.*` durable tables); mirror data is not send/outreach truth |
| **email-pipeline** | **Machine write path** — ingest, `refresh_outbound_safety_memory`, `mark_outreach_state`, mart rebuilds, Alembic migrations |

## What this API must **not** run

Outside the allowlisted `/operations/*` CRM commands and the explicit tender
annex import, the HTTP app is read-only. It does not invoke and must not grow
imports for:

| Forbidden operation | Typical entrypoint (stay in email-pipeline) |
|---------------------|---------------------------------------------|
| Gmail ingest | `scripts/ingest/05_workspace_gmail_imap_to_sqlite.py` |
| Safety memory refresh | `scripts/qa/refresh_outbound_safety_memory.py` |
| Postgres dashboard sync | `scripts/sync/sync_dashboard_postgres_mirror.py` |
| Alembic migrations | `alembic upgrade` |
| Send email | `scripts/qa/send_inline_html_email_via_gmail_api.py` |
| Queue regeneration | `scripts/qa/build_equipment_first_operator_queue.py` |
| Outreach state writes | `scripts/leads/mark_outreach_state.py --apply` |

CI: `tests/test_no_write_policy.py` enforces the exact write surface (only the enumerated `/operations/*` + annex-import POSTs) and scans `apps/api/src/origenlab_api` for forbidden script references.

## CORS and production mode

**Dashboard v1** uses the **Vite proxy** in dev (no CORS needed). Production dashboard builds call the same-origin Worker proxy at `https://dashboard.origenlab.cl/api`; the browser sends no API token, and the Worker injects upstream auth headers. Configure the API origin for production:

| Variable | Production example |
|----------|-------------------|
| `ORIGENLAB_ENV` | `production` |
| `ORIGENLAB_API_BACKEND` | `postgres` |
| `ORIGENLAB_POSTGRES_URL` | Cloud Postgres DSN |
| `ORIGENLAB_API_CORS_ORIGINS` | `https://dashboard.origenlab.cl` (no `*`; still useful for direct API-origin smokes/clients) |
| `ORIGENLAB_API_ALLOWED_HOSTS` | `api.origenlab.cl` (rejects raw `*.onrender.com` Host in production) |
| `ORIGENLAB_API_AUTH_TOKEN` | Long random secret — **required** when `ORIGENLAB_ENV=production` (see [docs/PRODUCTION_AUTH.md](docs/PRODUCTION_AUTH.md)) |
| `ORIGENLAB_API_DISABLE_DOCS` | `true` (optional; docs also off when `ORIGENLAB_ENV=production`) |

**CORS is not authentication.** `ORIGENLAB_API_CORS_ORIGINS` is a browser policy for allowed origins; private routes still require `Authorization: Bearer` (or `X-OriginLab-API-Key`) in production. Public unauthenticated routes: **`GET /health`** and **`OPTIONS`** preflight only.

CORS middleware allows **GET, HEAD, OPTIONS** only. For the production dashboard, deploy [`../dashboard-proxy`](../dashboard-proxy/README.md) and set the dashboard build base to `https://dashboard.origenlab.cl/api`; do not place `ORIGENLAB_API_AUTH_TOKEN` in any `VITE_*` value. See [`../email-pipeline/docs/PHASE1_CLOUD_READ_PATH.md`](../email-pipeline/docs/PHASE1_CLOUD_READ_PATH.md) and [docs/PRODUCTION_AUTH.md](docs/PRODUCTION_AUTH.md) (env checklist and deployment runbook).

### Production authentication

When `ORIGENLAB_ENV=production`, startup fails without `ORIGENLAB_API_AUTH_TOKEN`. Private read routes require:

```http
Authorization: Bearer <token>
```

Optional: `X-OriginLab-API-Key: <token>`. **`GET /health`** remains public for load balancers.

**Local production-like auth test:**

```bash
cd apps/api
uv run pytest tests/test_http_security.py -q -k "production and auth"
```

**Manual curl (after starting uvicorn with production env vars):**

```bash
curl -sS http://127.0.0.1:8001/health          # 200 without token
curl -sS http://127.0.0.1:8001/operator/status   # 401 without token
TOKEN="local-dev-placeholder-not-for-production"
curl -sS -H "X-OriginLab-API-Key: ${TOKEN}" http://127.0.0.1:8001/operator/status
```

Use placeholder tokens locally only; never commit production secrets. Full checklist: [docs/PRODUCTION_AUTH.md](docs/PRODUCTION_AUTH.md).

### FastAPI Cloud

| Setting | Notes |
|---------|--------|
| Application directory | `apps/api` |
| Entrypoint | `main.py` → `origenlab_api.main:app` |
| `ORIGENLAB_ENV` | `production` |
| `ORIGENLAB_API_AUTH_TOKEN` | Platform secret (required) |
| Postgres + CORS | Same as Render checklist above |

Public `*.fastapicloud.dev` URLs rely on API token auth for private routes (no Cloudflare Access at edge).

## Endpoints

All routes are **GET-only** except the enumerated `POST /operations/*` CRM commands and the tender annex `preview`/`import` uploads. Production serves Postgres read models when `ORIGENLAB_API_BACKEND=postgres`; local dev can fall back to SQLite or CSV fixtures.

### Endpoint groups

| Group | Paths | Purpose |
|-------|-------|---------|
| **Health & operator** | `/health`, `/operator/status`, `/operator/automation-status` | Liveness, operator verdict, automation loop health |
| **Commercial read models** | `/cases/warm`, `/opportunities/commercial`, `/emails/recent` | Dashboard Today, lifecycle opportunities, Bandeja |
| **Contacts** | `/contacts/{email}` | Read-only contact drilldown (Today side panel) |
| **Mirror reporting** | `/mirror/*` | Postgres mirror metadata, deals, catalog, suppressions, audits |
| **Durable CRM commands** | `POST /operations/*` (+ GET work-queue/detail routes) | Sales-opportunity promote/stage, PR3 operator state, activities, tasks, customer quotes + Drive workspace (CRM-Q1) — trusted operator identity + Idempotency-Key + expected_version |
| **Procurement** | `/operator/procurement/*` | W1 institution/tender read models + explicit annex-bundle preview/import; dashboard actionable-opportunity summary and Licitaciones/equipos |

### Route reference

Responses include `X-Request-ID` plus read-only timing headers `Server-Timing` / `X-Process-Time-Ms` (duration only; timing middleware does not log request bodies, query strings, or identifier-bearing paths).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness + backend mode |
| GET | `/operator/status` | Operator verdict (`operator_status_report`) |
| GET | `/operator/automation-status` | Mail refresh + dashboard mirror + SQLite storage observation (`sqlite_storage`) |
| GET | `/cases/warm` | Warm commercial case queue (`api.v_warm_case` in production) |
| GET | `/opportunities/commercial` | Canonical PR3 commercial opportunity list |
| GET | `/opportunities/commercial/{opportunity_id}` | Opportunity lifecycle detail with events/evidence/conflicts |
| GET | `/emails/recent` | Recent canonical Gmail rows (`api.v_recent_email`) |
| GET | `/contacts/{email}` | Contact profile + outreach read model |
| GET | `/mirror/*` | Postgres mirror reporting (summary, meta, deals, catalog, …) |
| POST | `/operations/sales-opportunities/{id}/quotes` | CRM-Q1: create a durable customer quote (transactional quote number, revision 1, Drive workspace provisioning; Idempotency-Key) |
| GET | `/operations/sales-opportunities/{id}/quotes` | CRM-Q1: quotes for a sales opportunity |
| GET | `/operations/customer-quotes/{quote_id}` | CRM-Q1: quote detail with Drive workspace state |
| POST | `/operations/customer-quotes/{quote_id}/drive-workspace` | CRM-Q1: idempotent Drive provisioning retry (expected_version) |

**Warm cases read-model boundary:** production serves `api.v_warm_case` through `PostgresWarmCaseRepository` when `ORIGENLAB_API_BACKEND=postgres`. Remote contract checks live in `scripts/remote_response_audit.py` (`require_warm_cases_contract`).

**Recent emails read-model boundary:** production serves `api.v_recent_email` through `PostgresEmailRecentRepository` when `ORIGENLAB_API_BACKEND=postgres`. Remote contract checks live in `scripts/remote_response_audit.py` (`require_recent_emails_contract`).

**Legacy equipment HTTP route retired:** `GET /opportunities/equipment` is no longer part of this API. The dashboard's actionable-opportunity summary and Licitaciones/equipos page now source from `/operator/procurement/status` (W1). **PHASE W1 (2026-08):** the underlying `commercial.equipment_opportunity*` writer/read model is now legacy/manual-backfill opt-in and no longer runs on schedule — see [`../email-pipeline/docs/architecture/EQUIPMENT_READ_MODEL_BOUNDARY.md`](../email-pipeline/docs/architecture/EQUIPMENT_READ_MODEL_BOUNDARY.md) and the operator runbook [`../email-pipeline/docs/runbooks/EQUIPMENT_READ_MODEL_RUNBOOK.md`](../email-pipeline/docs/runbooks/EQUIPMENT_READ_MODEL_RUNBOOK.md).

### Quick `curl` examples (local dev)

With the API on `http://127.0.0.1:8001` and SQLite backend (default):

```bash
curl -sS 'http://127.0.0.1:8001/health' | jq .
curl -sS 'http://127.0.0.1:8001/operator/status' | jq .
curl -sS 'http://127.0.0.1:8001/operator/automation-status' | jq .
curl -sS 'http://127.0.0.1:8001/cases/warm?limit=5' | jq '.meta, (.items | length)'
curl -sS 'http://127.0.0.1:8001/emails/recent?limit=5' | jq '.total_returned, (.items | length)'
curl -sS 'http://127.0.0.1:8001/contacts/buyer%40example.cl' | jq '.contact.email'
```

Production (`https://api.origenlab.cl`) may sit behind **Cloudflare Access** (browser SSO) **and** requires **`ORIGENLAB_API_AUTH_TOKEN`** on private routes when `ORIGENLAB_ENV=production`. CORS allowlisting is separate from auth — see [docs/PRODUCTION_AUTH.md](docs/PRODUCTION_AUTH.md).

Unauthenticated `GET /health` may return **HTTP 302** to Cloudflare Access on the custom domain, or **401** on private routes without a bearer/API-key token. Use Cloudflare service tokens **plus** API origin auth for production private-route smokes. The email-pipeline smoke (`uv run python scripts/qa/smoke_dashboard_api_readiness.py`) sends `X-OriginLab-API-Key` when `ORIGENLAB_API_AUTH_TOKEN` is set. The local `scripts/remote_smoke.sh` and the remote audit scripts are Cloudflare-service-token checks only; they do not inject the origin token and should not be treated as full production private-route readiness when token auth is enabled.

### OpenAPI / interactive docs

| Environment | `/docs` | Notes |
|-------------|---------|-------|
| **Local dev** | Available at `http://127.0.0.1:8001/docs` when `ORIGENLAB_ENV` is not `production` | Swagger UI for route discovery |
| **Production** | **Disabled** | Set `ORIGENLAB_API_DISABLE_DOCS=true` or `ORIGENLAB_ENV=production`; API is not publicly browsable behind Access |

Do not assume production exposes Swagger or ReDoc. Treat [`docs/API_RESPONSE_CONTRACT.md`](docs/API_RESPONSE_CONTRACT.md) and the remote response audit as the contract source for deployed shape.

## Setup

```bash
cd apps/api
uv sync --group dev
```

Requires editable `../email-pipeline` (`origenlab-email-pipeline`). Business logic is imported from `origenlab_email_pipeline` — not duplicated here.

## Environment

| Variable | Default |
|----------|---------|
| `ORIGENLAB_SQLITE_PATH` | From email-pipeline `load_settings()` |
| `ORIGENLAB_SQLITE_IMMUTABLE_RO` | `false` — recovery only; **insufficient alone** |
| `ORIGENLAB_SQLITE_CONFIRM_OFFLINE_COPY` | `false` — required with immutable recovery |
| `ORIGENLAB_SQLITE_COMPACTION_MANIFEST` | unset — required completed compaction manifest for recovery |
| `ORIGENLAB_ACTIVE_CURRENT` | `../email-pipeline/reports/out/active/current` |

Postgres URL is **not** required.

**CRM-Q1 quote workspace + numbering (all fail closed until configured; see [.env.example](.env.example)):**

| Variable | Purpose |
|----------|---------|
| `ORIGENLAB_DRIVE_QUOTES_ROOT_FOLDER_ID` | Drive folder ID of the canonical quotations root (e.g. `Cotizaciones`); verified read-only by preflight, never a creation target itself |
| `ORIGENLAB_DRIVE_QUOTES_PENDING_FOLDER_ID` | Drive folder ID under which every new quote workspace folder is created (e.g. `Cotizaciones/Pendientes`); must be a direct child of the root |
| `ORIGENLAB_DRIVE_QUOTES_SENT_FOLDER_ID` | Optional Drive folder ID for quotes after being sent (e.g. `Cotizaciones/Enviadas`); verified read-only by preflight when set. No `sent` lifecycle exists yet, so nothing writes here today |
| `ORIGENLAB_DRIVE_QUOTE_TEMPLATE_FILE_ID` | Master quotation spreadsheet template file ID |
| `ORIGENLAB_DRIVE_AUTH_MODE` | `authorized_user_my_drive` or `service_account_shared_drive` — see below |
| `ORIGENLAB_DRIVE_CREDENTIALS_FILE` | Credentials JSON path for the configured auth mode |
| `ORIGENLAB_DRIVE_EXPECTED_PRINCIPAL_EMAIL` | Expected Drive identity (e.g. `contacto@origenlab.cl`); the preflight check fails closed as `drive_principal_mismatch` if the credentials belong to any other account |
| `ORIGENLAB_DRIVE_SHARED_DRIVE_ID` | Shared Drive ID; **required** in `service_account_shared_drive` mode, optional in `authorized_user_my_drive` mode |
| `ORIGENLAB_QUOTE_DOCUMENT_PREFIX` / `_SERIAL_PAD_WIDTH` / `_SEED_NEXT_SERIAL` | The recorded quote-numbering business decision; quote creation returns `quote_numbering_not_configured` (503) until all three are set. The seed applies only on the first allocation; the durable `commercial.customer_quote_number_series` row is the counter truth afterwards. |

**Quote numbering (CRM-Q1D):** one allocated serial powers two distinct
identifiers -- they are never the same string:

* **`quote_number`** (human, customer-facing): `<padded serial>-<2-digit
  issue year>`, e.g. `01183-26`. The issue year is the business-local
  (`America/Santiago`) calendar date at allocation time, never UTC's.
* **`document_number`** (Drive artifact stem, currently internal):
  `<document_prefix><padded serial>`, e.g. `CN01183`.

`ORIGENLAB_QUOTE_DOCUMENT_PREFIX` seeds only `document_number` -- it is
never part of `quote_number`.

**Drive hierarchy (CRM-Q1D):** the quotations root is a fixed container that
is never written to directly. Every new quote workspace folder is created
under the configured Pendientes container instead, and the two Drive
artifacts are named from the two distinct identifiers above:

```text
Cotizaciones/                              <- ORIGENLAB_DRIVE_QUOTES_ROOT_FOLDER_ID
├── Pendientes/                            <- ORIGENLAB_DRIVE_QUOTES_PENDING_FOLDER_ID
│   └── 01183-26 — Cliente — Producto/     <- folder: human quote_number
│       └── CN01183 — Cliente — Producto   <- copied template: document_number
└── Enviadas/                              <- ORIGENLAB_DRIVE_QUOTES_SENT_FOLDER_ID (optional)
```

Preflight verifies Pendientes/Enviadas are each a direct child of the root;
the runtime provisioning path (`verify_destination`) verifies the same for
Pendientes on every attempt, before any mutation. A future `sent` lifecycle
transition would move the same folder (same ID, same Drive history) from
Pendientes to Enviadas — never copy or recreate it. That transition is not
implemented yet.

Two Drive authentication modes are supported, because a bare service account
has no My Drive storage quota and cannot own files there:

- **`authorized_user_my_drive`** — an authorized-user credentials JSON with
  an offline refresh token, acting as a human Google identity. This is the
  only supported mode when the quotations root/template live in someone's
  personal My Drive (the current production destination).
- **`service_account_shared_drive`** — a service-account credentials JSON,
  valid only when `ORIGENLAB_DRIVE_SHARED_DRIVE_ID` is also set and the
  configured root folder actually lives inside that Shared Drive. Pairing a
  service account with a My Drive destination fails closed as
  `drive_auth_mode_incompatible` before any Drive mutation.

Before activating either mode in production, run the read-only preflight
check (never mutates Drive, never makes an HTTP call in tests):

```bash
cd apps/api
uv run python scripts/drive_preflight.py
```

Without Drive configuration, quote creation still succeeds durably and the
workspace records a redacted `failed` category (`drive_not_configured`) that
the dashboard can retry after activation. The production Docker image
installs the optional `google-auth` dependency (`uv sync --extra drive`,
see [Dockerfile](Dockerfile)); an environment that runs without it (e.g. a
stale image, a non-Docker deployment) still fails closed as
`drive_dependency_missing` rather than silently reporting
`drive_not_configured`. No test or default code path calls Google APIs.

### Production activation (contacto@origenlab.cl) — supervised setup

**Not yet performed.** This records the exact steps for the human operator
who will run them; nothing here is automated. Quote numbering also stays
`quote_numbering_not_configured` until the owner separately records the
numbering decision — see the table above.

1. Confirm `contacto@origenlab.cl` is a real, Drive-enabled Google account
   (not an alias, not a distribution list). It is unrelated to any personal
   Gmail account used elsewhere (e.g. for a ChatGPT Drive connection).
2. In Google Cloud Console, create (or reuse) a project for this OAuth
   client. If `contacto@origenlab.cl` belongs to a Google Workspace, prefer
   creating the OAuth consent screen as an **Internal** application over
   leaving it in external **Testing** state: apps left in Testing can issue
   refresh tokens that expire after roughly 7 days, which would silently
   break quote creation; Internal (Workspace) or a verified External/
   Production app does not have that limitation.
3. Enable the Google Drive API for that project.
4. Create an OAuth 2.0 **Desktop app** client and download its client
   secrets JSON. Keep it outside git, exactly like any other credential.
5. Run the bootstrap helper, signing in specifically as
   `contacto@origenlab.cl` when the browser consent screen appears:
   ```bash
   cd apps/api
   uv sync --extra drive-bootstrap
   uv run python scripts/authorize_drive_user.py \
     --client-secrets-file /secure/path/oauth-client.json \
     --output-file /secure/path/origenlab-drive-credentials.json \
     --expected-email contacto@origenlab.cl
   ```
   The script refuses to write anywhere inside this repository and refuses
   to overwrite an existing file without `--replace-existing`; it never
   prints the token, refresh token, client secret, or credential contents.
6. Store the resulting authorized-user JSON outside git. It later becomes a
   production secret file (e.g. a Render secret file), referenced by
   `ORIGENLAB_DRIVE_CREDENTIALS_FILE` — never committed, never logged.
7. While still signed in as `contacto@origenlab.cl` in Drive, create the
   quotations root folder (e.g. `Cotizaciones`) and, inside it, the
   `Pendientes` and `Enviadas` subfolders (see the hierarchy diagram above).
8. Share the root folder manually with Tatiana and Rafael (Drive's own
   sharing UI) — this backend never manages Drive permissions itself (see
   the file-header note on `apps/api/src/origenlab_api/drive/google_drive.py`).
   `Pendientes`/`Enviadas` inherit that sharing as children of the root.
9. Copy the master quotation template into `contacto@origenlab.cl`'s Drive
   so the template is owned there too (preferred, not strictly required —
   `scripts/drive_preflight.py` reports template ownership status without
   blocking on a mismatch, since the template may legitimately still be
   shared from elsewhere for a while).
10. Configure `ORIGENLAB_DRIVE_QUOTES_ROOT_FOLDER_ID`,
    `ORIGENLAB_DRIVE_QUOTES_PENDING_FOLDER_ID`,
    `ORIGENLAB_DRIVE_QUOTES_SENT_FOLDER_ID`,
    `ORIGENLAB_DRIVE_QUOTE_TEMPLATE_FILE_ID`,
    `ORIGENLAB_DRIVE_AUTH_MODE=authorized_user_my_drive`,
    `ORIGENLAB_DRIVE_CREDENTIALS_FILE`, and
    `ORIGENLAB_DRIVE_EXPECTED_PRINCIPAL_EMAIL=contacto@origenlab.cl`.
11. Run the read-only preflight check and confirm it reports
    `principal_email: contacto@origenlab.cl` and `ok`:
    ```bash
    uv run python scripts/drive_preflight.py
    ```
12. Conduct the first real end-to-end test only against a disposable
    Postgres (never the production database) and a clearly marked test
    folder/template inside the quotations root — never against real
    customer data or the eventual production template until that first
    test is reviewed.

## Tests

Default local pre-PR check (frozen sync + full pytest, same shape as CI):

```bash
cd apps/api
./scripts/validate.sh
```

`./scripts/validate.sh` first runs a **Render-style no-dev runtime import smoke** (`uv sync --frozen --no-dev --extra drive`, matching [Dockerfile](Dockerfile) exactly, then imports `psycopg`, `google.oauth2.credentials`/`service_account`, and `origenlab_api.main` without Postgres or network). That catches missing runtime dependencies before production deploy. It then runs **`scripts/check_runtime_dependency_boundary.py`**, which inspects effective `uv tree --no-dev` and `uv tree --group dev` output and fails if ML-heavy packages (for example `torch`, `transformers`, `faiss-cpu`) appear in those trees.

**Runtime dependency boundary:** `apps/api` must remain ML-free at runtime and in dev test dependencies. Optional ML groups from `origenlab-email-pipeline` may still appear in `uv.lock`, but CI validates **effective** dependency trees — not raw lockfile entries — so Dependabot torch bumps are not merged blindly when they would enter the API install graph.

It then restores dev deps and runs the full pytest suite.

Targeted pytest is fine while developing; run `./scripts/validate.sh` before opening or merging API PRs. The validate script keeps both sync and test execution frozen so local validation does not rewrite `uv.lock`. `./scripts/validate.sh` runs tests in a deterministic SQLite-only mode, even if local `apps/api/.env` contains `ORIGENLAB_POSTGRES_URL` for mirror-page smoke testing.

```bash
cd apps/api
uv run pytest tests -q
```

### Inspect response shapes

For a human-readable snapshot of real `TestClient` responses against a minimal local fixture (no live server), run:

```bash
cd apps/api
uv run python scripts/audit_response_contract.py
```

The audit fails on contract violations including forbidden secret/path leaks (`/home/`, `/mnt/`, database URLs, etc.) anywhere in audited JSON responses. See [docs/API_RESPONSE_CONTRACT.md](docs/API_RESPONSE_CONTRACT.md).

Remote response audit (live API behind Cloudflare Access; skips with exit 0 when service token env vars are unset):

```bash
cd apps/api
CF_ACCESS_CLIENT_ID=... CF_ACCESS_CLIENT_SECRET=... \
  uv run python scripts/remote_response_audit.py
```

Optional env for cold Render / Cloudflare starts (network timeouts and connection errors are retried; contract failures are not):

| Variable | Default | Purpose |
|----------|---------|---------|
| `ORIGENLAB_REMOTE_AUDIT_TIMEOUT_SECONDS` | `30` | Per-request timeout; use `90` on cold instances if needed |
| `ORIGENLAB_REMOTE_AUDIT_RETRIES` | `2` | Retries after `TimeoutError` / `URLError` / `OSError` only |
| `ORIGENLAB_REMOTE_AUDIT_RETRY_BACKOFF_SECONDS` | `2.0` | Sleep between network retries |

Uses the same response contract checks as the local audit (`x-request-id`, JSON envelopes, list `meta`/`items`, warm-cases and recent-emails contracts, forbidden path/secret leaks). **Current limitation:** this script sends Cloudflare Access service-token headers only, not `ORIGENLAB_API_AUTH_TOKEN`; for production private routes protected by origin token auth, use `apps/email-pipeline/scripts/qa/smoke_dashboard_api_readiness.py` with `ORIGENLAB_API_AUTH_TOKEN` set. Not part of `./scripts/validate.sh` (requires network + secrets).

**Remote latency audit** (read-only GET timing; warm-run budgets; skips with exit 0 without CF credentials):

```bash
cd apps/api
CF_ACCESS_CLIENT_ID=... CF_ACCESS_CLIENT_SECRET=... \
  uv run python scripts/remote_latency_audit.py
```

| Variable | Default | Purpose |
|----------|---------|---------|
| `ORIGENLAB_REMOTE_LATENCY_RUNS` | `3` | Warm runs after one cold-start probe per endpoint |
| `ORIGENLAB_REMOTE_LATENCY_TIMEOUT_SECONDS` | `30` | Per-request timeout |
| `ORIGENLAB_REMOTE_LATENCY_BUDGET_MS` | `2500` | Fail if any warm run exceeds this latency |
| `ORIGENLAB_REMOTE_LATENCY_COLD_START_BUDGET_MS` | `45000` | Advisory warning only for a successful but slow cold probe |

The first (cold) probe is advisory: timeouts and non-200 responses print a stderr warning and the script continues to warm runs. Warm runs enforce HTTP 200 and the warm latency budget. With `ORIGENLAB_REMOTE_LATENCY_RUNS=0`, only the cold probe runs and must return HTTP 200.

Prints per-endpoint `status`, `first_ms`, warm `min_ms` / `avg_ms` / `max_ms`, and `request_id`. It has the same Cloudflare-only auth limitation as `remote_response_audit.py`; use the email-pipeline readiness smoke when origin token auth must be validated. Does not print response bodies except short safe error snippets on failure. Not part of `./scripts/validate.sh`.

GitHub Actions workflow: [`.github/workflows/api.yml`](../../.github/workflows/api.yml) runs `./scripts/validate.sh` for `apps/api` changes and `apps/email-pipeline` dependency changes.

### Render (native runtime)

| Setting | Value |
|---------|-------|
| `PYTHON_VERSION` | `3.12.11` |
| Build command | `uv sync --frozen --no-dev` |
| Start command | `uv run --no-sync uvicorn origenlab_api.main:app --host 0.0.0.0 --port ${PORT:-10000}` |

CI `./scripts/validate.sh` mirrors the build step with a no-dev import smoke before pytest.

### Remote production smoke

`./scripts/remote_smoke.sh` checks a deployed API (default `https://api.origenlab.cl`) behind Cloudflare Access. It does not send `ORIGENLAB_API_AUTH_TOKEN`; use it for Access/protection checks, not full token-auth private-route readiness.

Unauthenticated `GET /health` often returns **HTTP 302** to `cloudflareaccess.com` when Access is enabled — that is expected protection, not an API outage. Authenticated checks use Cloudflare **service tokens** (`CF-Access-Client-Id` / `CF-Access-Client-Secret` headers). Configure a **Service Auth** policy in Cloudflare Access for the token used by this script.

```bash
cd apps/api
./scripts/remote_smoke.sh
```

Protection-only (no production secrets; exits 0 after Check A):

```bash
cd apps/api
./scripts/remote_smoke.sh
```

Authenticated health (requires service token env vars):

```bash
cd apps/api
CF_ACCESS_CLIENT_ID=... \
CF_ACCESS_CLIENT_SECRET=... \
./scripts/remote_smoke.sh
```

Optional operator route (still read-only; adds `GET /operator/status`):

```bash
cd apps/api
ORIGENLAB_REMOTE_SMOKE_OPERATOR=1 \
CF_ACCESS_CLIENT_ID=... \
CF_ACCESS_CLIENT_SECRET=... \
./scripts/remote_smoke.sh
```

Override base URL for staging or local smoke:

```bash
ORIGENLAB_API_BASE_URL=http://127.0.0.1:8001 ./scripts/remote_smoke.sh
```

## Dashboard v1–v2 backend matrix

Dashboard v1 + **Dashboard-2 contact drilldown** use **this app only** (`apps/api` on port **8001**).

| Backend | Env | Smoke |
|---------|-----|-------|
| SQLite (default) | `ORIGENLAB_API_BACKEND` unset or `sqlite` | `dashboard_v1_http_smoke.py --expect-backend sqlite` |
| Postgres mirror | `ORIGENLAB_API_BACKEND=postgres` + disposable `ORIGENLAB_POSTGRES_URL` | `--expect-backend postgres` |

`dashboard_v1_http_smoke.py` also calls **`GET /contacts/{email}`** (email from warm rows; skips with WARN if none).

Full procedure: [`../dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md`](../dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md) · matrix detail: [`../dashboard/docs/BACKEND_MATRIX_VALIDATION.md`](../dashboard/docs/BACKEND_MATRIX_VALIDATION.md).

**Freeze validation:** SQLite and disposable Postgres (`:5433`, fresh DB) contact smokes **passed**. Gmail / production scratch Postgres not used.

```bash
# SQLite smoke (TestClient + contact route)
uv run python scripts/dashboard_v1_http_smoke.py --expect-backend sqlite

# Postgres smoke (disposable ORIGENLAB_POSTGRES_URL only)
ORIGENLAB_API_BACKEND=postgres ORIGENLAB_POSTGRES_URL='postgresql+psycopg://…@127.0.0.1:5433/origenlab_dashboard2_test' \
  uv run python scripts/dashboard_v1_http_smoke.py --expect-backend postgres
```

Dashboard HTTP smokes: `npm run smoke:contacts`, `EXPECT_BACKEND=postgres npm run smoke:contacts`, `npm run smoke:proxy` — see dashboard README.

**After postgres validation:** unset `ORIGENLAB_API_BACKEND` and postgres URLs; restart this app on SQLite (`ORIGENLAB_SQLITE_PATH` only).

## API-3 mirror relocation (Phase 6 complete)

Postgres mirror reporting lives under **`GET /mirror/*`** on this app. Legacy email-pipeline `:8000` API **removed** — [docs/API-3_PHASE6_LEGACY_REMOVAL_COMPLETE.md](docs/API-3_PHASE6_LEGACY_REMOVAL_COMPLETE.md). **Strict gate:** `scripts/api3_phase6_grep_gate.sh`.

Mirror reporting smoke (GET only; requires this app on :8001 + disposable `ORIGENLAB_POSTGRES_URL`):

```bash
cd apps/dashboard && npm run smoke:mirror
```

**Live mirror smoke** (disposable Postgres on `:5433`; `:8001` only):

```bash
apps/api/scripts/run_mirror_dual_server_parity.sh
```

Report (historical): [docs/archive/api3/API-3_PHASE3B_LIVE_PARITY_REPORT.md](docs/archive/api3/API-3_PHASE3B_LIVE_PARITY_REPORT.md).

```bash
cd apps/api
uv run python scripts/mirror_parity_smoke.py --mirror-base http://127.0.0.1:8001
```
