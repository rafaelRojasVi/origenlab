# OrigenLab API (`apps/api`)

> **Operator handoff (v1 freeze):** [../dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md](../dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md)
>
> **Portfolio demo guide:** [docs/PORTFOLIO_DEMO_GUIDE.md](docs/PORTFOLIO_DEMO_GUIDE.md)

Read-only **operator API** over SQLite and `reports/out/active/current`. This app is separated from `apps/email-pipeline` so daily ingest, DNR refresh, and mutation CLIs stay unchanged.

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
| **This API** | GET-only HTTP for **Dashboard Today** (`apps/dashboard`) and operator tooling |
| **Postgres mirror** | **Read-only reporting target** when `auto-mirror-dashboard` publishes; not send/outreach truth |
| **email-pipeline** | **Write path** — ingest, `refresh_outbound_safety_memory`, `mark_outreach_state`, mart rebuilds |

## What this API must **not** run

API-0 is **read-only**. The HTTP app does not invoke and must not grow imports for:

| Forbidden operation | Typical entrypoint (stay in email-pipeline) |
|---------------------|---------------------------------------------|
| Gmail ingest | `scripts/ingest/05_workspace_gmail_imap_to_sqlite.py` |
| Safety memory refresh | `scripts/qa/refresh_outbound_safety_memory.py` |
| Postgres dashboard sync | `scripts/sync/sync_dashboard_postgres_mirror.py` |
| Alembic migrations | `alembic upgrade` |
| Send email | `scripts/qa/send_inline_html_email_via_gmail_api.py` |
| Queue regeneration | `scripts/qa/build_equipment_first_operator_queue.py` |
| Outreach state writes | `scripts/leads/mark_outreach_state.py --apply` |

CI: `tests/test_no_write_policy.py` checks GET-only routes and scans `apps/api/src/origenlab_api` for forbidden script references.

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

All routes are **GET-only**. Production serves Postgres read models when `ORIGENLAB_API_BACKEND=postgres`; local dev can fall back to SQLite or CSV fixtures.

### Endpoint groups

| Group | Paths | Purpose |
|-------|-------|---------|
| **Health & operator** | `/health`, `/operator/status`, `/operator/automation-status` | Liveness, operator verdict, automation loop health |
| **Commercial read models** | `/cases/warm`, `/opportunities/equipment`, `/emails/recent` | Dashboard Today, Bandeja, Licitaciones/equipos |
| **Contacts** | `/contacts/{email}` | Read-only contact drilldown (Today side panel) |
| **Mirror reporting** | `/mirror/*` | Postgres mirror metadata, deals, catalog, suppressions, audits |

### Route reference

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness + backend mode |
| GET | `/operator/status` | Operator verdict (`operator_status_report`) |
| GET | `/operator/automation-status` | Mail refresh + dashboard mirror local state |
| GET | `/cases/warm` | Warm commercial case queue (`api.v_warm_case` in production) |
| GET | `/opportunities/equipment` | Equipment-first operator queue |
| GET | `/emails/recent` | Recent canonical Gmail rows (`api.v_recent_email`) |
| GET | `/contacts/{email}` | Contact profile + outreach read model |
| GET | `/mirror/*` | Postgres mirror reporting (summary, meta, deals, catalog, …) |

**Warm cases read-model boundary:** production serves `api.v_warm_case` through `PostgresWarmCaseRepository` when `ORIGENLAB_API_BACKEND=postgres`. Remote contract checks live in `scripts/remote_response_audit.py` (`require_warm_cases_contract`).

**Recent emails read-model boundary:** production serves `api.v_recent_email` through `PostgresEmailRecentRepository` when `ORIGENLAB_API_BACKEND=postgres`. Remote contract checks live in `scripts/remote_response_audit.py` (`require_recent_emails_contract`).

**Equipment read-model boundary:** production serves `api.v_equipment_opportunity_current` when `ORIGENLAB_API_BACKEND=postgres`. See [`../email-pipeline/docs/architecture/EQUIPMENT_READ_MODEL_BOUNDARY.md`](../email-pipeline/docs/architecture/EQUIPMENT_READ_MODEL_BOUNDARY.md) and the operator runbook [`../email-pipeline/docs/runbooks/EQUIPMENT_READ_MODEL_RUNBOOK.md`](../email-pipeline/docs/runbooks/EQUIPMENT_READ_MODEL_RUNBOOK.md).

### Quick `curl` examples (local dev)

With the API on `http://127.0.0.1:8001` and SQLite backend (default):

```bash
curl -sS 'http://127.0.0.1:8001/health' | jq .
curl -sS 'http://127.0.0.1:8001/operator/status' | jq .
curl -sS 'http://127.0.0.1:8001/operator/automation-status' | jq .
curl -sS 'http://127.0.0.1:8001/cases/warm?limit=5' | jq '.meta, (.items | length)'
curl -sS 'http://127.0.0.1:8001/opportunities/equipment?limit=5' | jq '.meta, (.items | length)'
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
| `ORIGENLAB_ACTIVE_CURRENT` | `../email-pipeline/reports/out/active/current` |

Postgres URL is **not** required.

## Tests

Default local pre-PR check (frozen sync + full pytest, same shape as CI):

```bash
cd apps/api
./scripts/validate.sh
```

`./scripts/validate.sh` first runs a **Render-style no-dev runtime import smoke** (`uv sync --frozen --no-dev`, then imports `psycopg` and `origenlab_api.main` without Postgres or network). That catches missing runtime dependencies before production deploy. It then runs **`scripts/check_runtime_dependency_boundary.py`**, which inspects effective `uv tree --no-dev` and `uv tree --group dev` output and fails if ML-heavy packages (for example `torch`, `transformers`, `faiss-cpu`) appear in those trees.

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

Uses the same response contract checks as the local audit (`x-request-id`, JSON envelopes, list `meta`/`items`, warm-cases, recent-emails, and equipment current-view contracts, forbidden path/secret leaks). **Current limitation:** this script sends Cloudflare Access service-token headers only, not `ORIGENLAB_API_AUTH_TOKEN`; for production private routes protected by origin token auth, use `apps/email-pipeline/scripts/qa/smoke_dashboard_api_readiness.py` with `ORIGENLAB_API_AUTH_TOKEN` set. Not part of `./scripts/validate.sh` (requires network + secrets).

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

`dashboard_v1_http_smoke.py` also calls **`GET /contacts/{email}`** (email from warm/equipment rows; skips with WARN if none).

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
