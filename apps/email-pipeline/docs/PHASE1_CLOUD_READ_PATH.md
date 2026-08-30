# Phase 1 — Cloud read path (OrigenLab Today)

**Status:** deployment readiness checklist (do not run until operator approves)  
**Prerequisite:** [Phase 0 local Postgres mirror proof](PHASE0_LOCAL_POSTGRES_MIRROR.md) — **green** (`apps/api` **200 passed**; equipment rows require direct ChileCompra refresh or explicit legacy/backfill reload).
**Scope:** Cloud Postgres read model + cloud GET-only API + static dashboard behind the read-only Worker proxy. **Manual mirror sync only** (no cron in Phase 1).

---

## Safety constraints (read first)

| Allowed | Forbidden in Phase 1 |
|---------|----------------------|
| Read-only SQLite on **local worker** during sync | Uploading/copying the ~128GB `emails.sqlite` to cloud |
| `mirror-dashboard` / `sync_dashboard_postgres_mirror.py` → **cloud Postgres only** | Gmail ingest (`05_workspace_gmail_imap_to_sqlite.py`) |
| `alembic upgrade head` on cloud Postgres | `build_business_mart.py --rebuild` |
| Deploy GET-only `apps/api` + static `apps/dashboard` + read-only dashboard proxy | Gmail mutation, sends, outreach writes |
| DNS for `api.*` / `dashboard.*` subdomains | Changes to HostGator marketing site (`apps/web`) |

**Truth model:** Postgres mirror is for **dashboard reads only**. Send/outreach approval remains **local SQLite** + operator scripts.

**Day-to-day mirror refresh (Render live):** [REFRESH_RENDER_DASHBOARD_ONCE.md](REFRESH_RENDER_DASHBOARD_ONCE.md) — incremental SQLite + `refresh_render_dashboard_once.sh` (no mart `--rebuild`, no Gmail by default).

---

## Architecture

```text
Local worker (unchanged)
  ORIGENLAB_SQLITE_PATH → read-only
  mirror-dashboard --live --apply -- --allow-non-scratch-postgres
  → Cloud Postgres (mart, outbound sidecars, commercial, reporting; NOT full archive)

Operator browser
  → https://dashboard.<domain>  (static SPA)
  → https://dashboard.<domain>/api  (Worker injects upstream auth)
  → https://api.<domain>            (FastAPI, ORIGENLAB_API_BACKEND=postgres)

origenlab.cl (HostGator) → public marketing only — separate from dashboard
```

---

## 1. Cloud Postgres (provider-neutral)

| Step | Action |
|------|--------|
| 1 | Provision a managed Postgres instance (e.g. 256MB–1GB starter tier). |
| 2 | Create database name e.g. `origenlab_dashboard`. |
| 3 | Save **external** connection URL for sync from laptop; save **internal** URL for API in same region/VPC if offered. |
| 4 | Enable TLS; restrict network access where possible. |

**Connection URL form:**

```text
postgresql+psycopg://USER:PASSWORD@HOST:PORT/DATABASE
```

Store as `ORIGENLAB_CLOUD_POSTGRES_URL` (sync) and `ORIGENLAB_POSTGRES_URL` (API). Never commit real passwords.

### Render example

- Blueprint: repo root [`render.yaml`](../../../render.yaml) → `origenlab-dashboard-db` (Postgres 16).
- Or: Render Dashboard → New Postgres → copy **External Database URL**.

### Railway example

- New PostgreSQL service → copy `DATABASE_URL` → ensure `postgresql+psycopg://` prefix if driver requires it.

---

## 2. Alembic migrations (cloud, from local worker)

Run **once** per new database (or after schema upgrades):

```bash
cd apps/email-pipeline
uv sync --group dev

export ORIGENLAB_POSTGRES_URL='postgresql+psycopg://USER:***@HOST/DB'
export ALEMBIC_DATABASE_URL="$ORIGENLAB_POSTGRES_URL"

uv run alembic -c alembic.ini upgrade head
```

**Expect:** head revision `20260519_0016`; schemas including `archive`, `ops`, `mart`, `leads`, `commercial`, `outbound`, `supplier`, `reporting`, `api` (views).

---

## 3. Manual mirror sync (local worker → cloud)

Uses **existing** local mart/classification state. Does **not** ingest Gmail or rebuild marts.

### Provider-neutral (scripted)

```bash
export ORIGENLAB_SQLITE_PATH="$HOME/data/origenlab-email/sqlite/emails.sqlite"
export ORIGENLAB_CLOUD_POSTGRES_URL='postgresql+psycopg://USER:***@HOST/DB'

cd apps/email-pipeline
./scripts/ops/sync_dashboard_mirror_to_cloud.sh
```

### Provider-neutral (explicit flags)

```bash
cd apps/email-pipeline
export ORIGENLAB_SQLITE_PATH="$HOME/data/origenlab-email/sqlite/emails.sqlite"
export ORIGENLAB_POSTGRES_URL="$ORIGENLAB_CLOUD_POSTGRES_URL"

uv run origenlab mirror-dashboard --alembic --live --apply \
  --operator "<operator-id>" \
  --reason "Phase 1 initial cloud mirror" \
  -- \
  --allow-non-scratch-postgres \
  --json-out /tmp/phase1_cloud_mirror_sync.json

uv run python scripts/qa/verify_dashboard_postgres_mirror.py
```

**Post-sync expectations (from Phase 0 baseline; re-check after cloud run):**

| Check | Expected |
|-------|----------|
| `archive.emails` | **0** (lightweight mirror; no full archive replica) |
| `commercial.warm_case` | **> 0** |
| `api.v_equipment_opportunity_current` | **> 0** only after direct ChileCompra refresh or explicit legacy/backfill equipment reload |
| `reporting.dashboard_sync_run` | Latest row `status = success` |

**Equipment canonical behavior (PHASE W1, 2026-08):** direct Postgres publication now requires `uv run origenlab auto-refresh-chilecompra-equipment --once --apply --publish-read-model` — the flag defaults `false` and the tracked cron wrapper explicitly disables it, so the scheduled refresh no longer writes this table family; those rows are frozen for observation. The CSV mirror flag `--include-equipment-opportunities` remains legacy/backfill only.

---

## 4. API deployment (`apps/api`)

### Runtime (provider-neutral)

- Process: `uvicorn origenlab_api.main:app --host 0.0.0.0 --port 8001`
- Docker: [`apps/api/Dockerfile`](../../api/Dockerfile), build context = **monorepo root**
- Health: `GET /health` → `"backend": "postgres"`, `"mode": "operator-postgres-mirror-readonly"`

### Required environment variables

| Variable | Value | Notes |
|----------|--------|--------|
| `ORIGENLAB_ENV` | `production` | Enables production guards |
| `ORIGENLAB_API_BACKEND` | `postgres` | **Required** in production (not SQLite) |
| `ORIGENLAB_POSTGRES_URL` | Cloud DSN | From managed Postgres |
| `ORIGENLAB_API_CORS_ORIGINS` | `https://dashboard.origenlab.cl` | Comma-separated; **no `*`**; **not auth** |
| `ORIGENLAB_API_AUTH_TOKEN` | Long random secret | **Required** in production; bearer on private routes |
| `ORIGENLAB_API_ALLOWED_HOSTS` | `api.origenlab.cl` | Rejects raw `*.onrender.com` Host |
| `ORIGENLAB_API_DISABLE_DOCS` | `true` | Optional; docs also off when `ORIGENLAB_ENV=production` |

**Do not set** `ORIGENLAB_SQLITE_PATH` on cloud API.

Env template and deployment checklist: [`apps/api/docs/PRODUCTION_AUTH.md`](../../api/docs/PRODUCTION_AUTH.md#environment-checklist).

### CORS (implemented in code)

- Middleware: **GET, HEAD, OPTIONS** only.
- Startup fails if `ORIGENLAB_ENV=production` without CORS origins or with `postgres` backend missing.
- **CORS allowlists browser origins; it does not authenticate callers.** See [`apps/api/docs/PRODUCTION_AUTH.md`](../../api/docs/PRODUCTION_AUTH.md).
- See [`apps/api/src/origenlab_api/http_security.py`](../../api/src/origenlab_api/http_security.py).

### Production API token auth

When `ORIGENLAB_ENV=production`, `ORIGENLAB_API_AUTH_TOKEN` is **required**. Private routes need `Authorization: Bearer <token>`. Public: `GET /health` and `OPTIONS` only.

Full runbook: [`apps/api/docs/PRODUCTION_AUTH.md`](../../api/docs/PRODUCTION_AUTH.md).

### Render example

- Web service from [`render.yaml`](../../../render.yaml) → `origenlab-api` (Docker).
- Env: link `ORIGENLAB_POSTGRES_URL` from `origenlab-dashboard-db`.
- Custom domain: `api.origenlab.cl` → health check `/health`.

---

## 5. Dashboard deployment (`apps/dashboard`)

### Build (provider-neutral)

```bash
cd apps/dashboard
npm ci
VITE_ORIGENLAB_API_BASE_URL=https://dashboard.origenlab.cl/api npm run build
```

Publish directory: `apps/dashboard/dist` (static files only).

Template: [`apps/dashboard/.env.production.example`](../../dashboard/.env.production.example)

### Render example

- Static site `origenlab-dashboard` in [`render.yaml`](../../../render.yaml).
- Build env: `VITE_ORIGENLAB_API_BASE_URL=https://dashboard.origenlab.cl/api`
- SPA rewrite: `/*` → `/index.html`
- Custom domain: `dashboard.origenlab.cl`

### UI safety (frozen)

- Read-only Today page; no send/draft/archive/write controls.
- No `mailto` on warm cases table.
- `meta.data_source: postgres_mirror` when mirror is populated.

---

## 6. DNS (provider-neutral)

| Hostname | Target | Notes |
|----------|--------|--------|
| `origenlab.cl` | HostGator (unchanged) | `apps/web` marketing |
| `api.origenlab.cl` | Cloud API service | CNAME to provider |
| `dashboard.origenlab.cl` | Cloud static site | CNAME to provider |

Enable HTTPS at provider (automatic on Render/Railway).

---

## 7. Auth (production)

| Layer | Role |
|-------|------|
| **`ORIGENLAB_API_AUTH_TOKEN`** | Origin bearer auth on private read routes (required when `ORIGENLAB_ENV=production`) |
| **Cloudflare Access** (recommended for custom domains) | Edge SSO / service tokens for `dashboard.*` and `api.*` |
| **CORS** | Browser cross-origin policy only — **not** authentication |

API has no built-in operator login UI. Combine edge auth (Access) with API bearer token for defense in depth. FastAPI Cloud public URLs rely primarily on API token auth.

Runbook: [`apps/api/docs/PRODUCTION_AUTH.md`](../../api/docs/PRODUCTION_AUTH.md) · Cloudflare: [`docs/CLOUDFLARE_ACCESS_DASHBOARD_SECURITY.md`](../../../docs/CLOUDFLARE_ACCESS_DASHBOARD_SECURITY.md).

---

## 8. Smoke tests (after deploy approval)

### API (provider-neutral)

```bash
curl -sS https://api.origenlab.cl/health | jq .

cd apps/api
export ORIGENLAB_API_BACKEND=postgres
export ORIGENLAB_POSTGRES_URL="$ORIGENLAB_CLOUD_POSTGRES_URL"
uv run python scripts/dashboard_v1_http_smoke.py --expect-backend postgres
```

For live HTTPS URL from laptop, use dashboard smoke (below) or extend smoke to use `httpx` against public base URL.

### Dashboard (provider-neutral)

```bash
cd apps/dashboard
EXPECT_BACKEND=postgres \
SMOKE_BASE_URL=https://dashboard.origenlab.cl \
npm run smoke:postgres

EXPECT_BACKEND=postgres npm run smoke:contacts
```

### Local production-mode sanity (pre-cloud)

```bash
export ORIGENLAB_ENV=production
export ORIGENLAB_API_BACKEND=postgres
export ORIGENLAB_POSTGRES_URL='postgresql+psycopg://…local or cloud…'
export ORIGENLAB_API_CORS_ORIGINS=https://dashboard.origenlab.cl
export ORIGENLAB_API_AUTH_TOKEN=test-token
cd apps/api && uv run pytest tests/test_http_security.py -q
```

---

## 9. Rollback

1. Disable or scale down cloud API and dashboard services.  
2. Remove or repoint DNS for `api` / `dashboard` subdomains.  
3. Operators continue on **Phase 0 local** stack (`ORIGENLAB_SQLITE_PATH` + optional local `:5433` Postgres).  
4. Cloud Postgres mirror is **disposable**; no SQLite restore required.

---

## 10. Phase 1 readiness gate (operator sign-off)

Before first production traffic:

- [ ] Phase 0 doc followed; local mirror sync + smokes passed  
- [ ] `apps/api` full suite green (**200 passed** on readiness date)  
- [ ] Cloud Postgres created; `alembic upgrade head` OK  
- [ ] Manual cloud sync OK; verify script expectations met  
- [ ] API env: `postgres` + CORS + **`ORIGENLAB_API_AUTH_TOKEN`** + docs disabled  
- [ ] Dashboard built with correct `VITE_ORIGENLAB_API_BASE_URL`  
- [ ] Auth layer chosen and configured  
- [ ] Post-deploy smokes planned  
- [ ] **Explicit approval** to deploy (this checklist does not deploy by itself)

---

## 4. W1 institution-prospect file read model — Render persistent disk

The W1 institution-prospect bundle is **not stored in Postgres**. The API reads it
directly from the filesystem on every request. In production (Render), the bundle lives
on a persistent disk mounted at `/var/data`.

### Architecture

```text
Local worker
  uv run origenlab auto-refresh-chilecompra-equipment --once --apply --publish-institution-prospects
  → reports/out/active/current/institution_prospects/   (canonical local bundle)

sync_institution_prospects_to_cloud.sh   (opt-in, default 0 in refresh script)
  → scp archive → Render persistent disk /var/data
  → ssh: extract to /var/data/institution_prospects.TIMESTAMP.staging/
  → Python validation (same read_model loader the API uses)
  → atomic symlink promotion: ln -s STAGING NEXT + mv NEXT CANONICAL  (rename(2))
  → Render API resolves symlink on every request — no restart required

Render API
  ORIGENLAB_INSTITUTION_PROSPECT_DIR=/var/data/institution_prospects  (env var)
  → Settings.resolved_institution_prospect_dir()  (resolves symlink each call)
  → load_published_read_model(dir)
  → success: reduced_mode=false
  → missing/malformed: reduced_mode=true  (graceful degradation)
```

### Required SSH configuration (do not commit values)

Set these in your local `.env` or shell before running the sync:

```bash
ORIGENLAB_RENDER_SSH_HOST=ssh.oregon.render.com   # Render SSH gateway
ORIGENLAB_RENDER_SSH_USER=srv-xxxxxxxxxxxx         # Service username from Render dashboard
ORIGENLAB_RENDER_SSH_KEY=/path/to/ssh/private/key  # Path only — contents never echoed
```

### One-time infrastructure setup (Render dashboard)

1. The `render.yaml` blueprint already declares the persistent disk (`name: w1-data`,
   `mountPath: /var/data`, `sizeGB: 1`). Apply via `render blueprint launch` or the
   Render dashboard "Blueprints" tab.
2. Add your SSH public key to the Render service under **Settings → SSH Keys**.
3. Verify with: `ssh -i $ORIGENLAB_RENDER_SSH_KEY $ORIGENLAB_RENDER_SSH_USER@$ORIGENLAB_RENDER_SSH_HOST ls /var/data`

### Recurring sync

```bash
# After a successful local ChileCompra auto-refresh:
RUN_W1_CLOUD_SYNC=1 bash scripts/ops/refresh_render_dashboard_once.sh

# Or standalone:
bash scripts/ops/sync_institution_prospects_to_cloud.sh
```

**Failure is non-fatal.** If the sync fails (network, SSH, remote validation), the
local W1 publication remains authoritative and the Render API degrades gracefully
(`reduced_mode=true`) rather than serving corrupt data. The local ChileCompra
auto-refresh transaction is never aborted due to W1 cloud sync failure.

### Verification

```bash
curl -H "Authorization: Bearer $TOKEN" https://api.origenlab.cl/operator/procurement/status | jq '.meta'
# expect: meta.reduced_mode = false
# expect: meta.canonical_reason = "institution_prospect_read_model"
```

### Rollback

The sync script retains `ORIGENLAB_W1_KEEP_SNAPSHOTS` (default 2) old versioned
staging directories on the remote disk. To roll back:

```bash
# On the Render persistent disk (via ssh):
ls /var/data/institution_prospects.*.staging   # find previous snapshot
ln -s /var/data/institution_prospects.PREV.staging /var/data/institution_prospects.next
mv /var/data/institution_prospects.next /var/data/institution_prospects
```

---

## 11. Related artifacts

| Artifact | Path |
|----------|------|
| Phase 0 local proof | [PHASE0_LOCAL_POSTGRES_MIRROR.md](PHASE0_LOCAL_POSTGRES_MIRROR.md) |
| Cloud sync script | [`scripts/ops/sync_dashboard_mirror_to_cloud.sh`](../scripts/ops/sync_dashboard_mirror_to_cloud.sh) |
| W1 cloud sync script | [`scripts/ops/sync_institution_prospects_to_cloud.sh`](../scripts/ops/sync_institution_prospects_to_cloud.sh) |
| Verify script | [`scripts/qa/verify_dashboard_postgres_mirror.py`](../scripts/qa/verify_dashboard_postgres_mirror.py) |
| Render blueprint | [`render.yaml`](../../../render.yaml) |
| API README (CORS/production) | [`apps/api/README.md`](../../api/README.md) |
| Dashboard freeze handoff | [`apps/dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md`](../../dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md) |
