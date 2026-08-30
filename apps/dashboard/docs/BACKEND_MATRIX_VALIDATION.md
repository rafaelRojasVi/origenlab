# Dashboard v1–v2 — API backend matrix validation

Prove **Dashboard v1** and **Dashboard-2 contact drilldown** work against **`apps/api`** in both read backends. The dashboard browser never touches SQLite, Postgres, CSV, or pipeline scripts — only **GET** routes via `apps/api` (direct or Vite proxy).

## Active vs legacy API

| Stack | Port (typical) | Role |
|-------|----------------|------|
| **`apps/api`** | **8001** | **Active** Dashboard API (`/health`, `/operator/status`, `/cases/warm`, `/operator/procurement/status`, `/contacts/{email}`) |
| **Legacy email-pipeline API** | **8000** | **Removed** (API-3 Phase 6). Use **`apps/api`** `GET /mirror/*` on **8001**. |

Dashboard v1 must not call `/mirror/*`. The obsolete pre-v1 dashboard client has been removed.

## Routes under test (GET only)

| Route | Dashboard use |
|-------|----------------|
| `GET /health` | Backend chip (`sqlite` vs `postgres`) |
| `GET /operator/status` | Verdict panel |
| `GET /cases/warm` | Warm cases table |
| `GET /operator/procurement/status` | W1 procurement status (Licitaciones/equipos, actionable-opportunity summary) |
| `GET /contacts/{email}` | Read-only contact profile drilldown (Dashboard-2 side panel) |

No `POST` / `PUT` / `PATCH` / `DELETE`. Contact drilldown has no send/draft/archive/mark-contacted/status-write actions and must not render raw bodies or filesystem paths.

### Dashboard-2 contact smoke (`scripts/smoke-v1.mjs`)

| Command | When to use |
|---------|-------------|
| `npm run smoke:contacts` | Default base `http://127.0.0.1:8001` — same as `npm run smoke` / `smoke:sqlite` plus contact route |
| `npm run smoke:sqlite` | Assert `health.backend=sqlite` + contact smoke |
| `EXPECT_BACKEND=postgres npm run smoke:contacts` | Assert postgres mirror labels + contact smoke |
| `EXPECT_BACKEND=postgres npm run smoke:postgres` | Alias for postgres backend assertion |
| `SMOKE_BASE_URL=http://127.0.0.1:5173 npm run smoke:proxy` | Through Vite dev proxy |
| `SMOKE_BASE_URL=http://127.0.0.1:5173 EXPECT_BACKEND=postgres npm run smoke:proxy` | Proxy + postgres labels |

If warm rows have no `contact_email`, contact smoke logs **WARN** and skips (not a failure). Smoke scripts must not call `/dashboard/*` or `/classification/*`.

### Dashboard-2 freeze validation (recorded)

| Backend | Status | Evidence |
|---------|--------|----------|
| SQLite | **Passed** | `smoke:sqlite`, `smoke:contacts`, `smoke:proxy`; `dashboard_v1_http_smoke.py --expect-backend sqlite` |
| Disposable Postgres (`:5433`) | **Passed** | Fresh DB `origenlab_dashboard2_test`; `EXPECT_BACKEND=postgres` smokes; UI panel + mirror truth copy |

Gmail, production SQLite, and production/scratch Postgres were **not** mutated during recorded validation.

---

## A) SQLite backend (default)

**API**

```bash
cd apps/api
uv sync
export ORIGENLAB_SQLITE_PATH="$HOME/data/origenlab-email/sqlite/emails.sqlite"
# ORIGENLAB_API_BACKEND unset or sqlite

uv run uvicorn origenlab_api.main:app --host 127.0.0.1 --port 8001 --reload
```

**Dashboard** (second terminal)

```bash
cd apps/dashboard
# .env: leave VITE_ORIGENLAB_API_BASE_URL unset → Vite proxy
npm run dev -- --host 127.0.0.1
```

Open [http://127.0.0.1:5173](http://127.0.0.1:5173). Header should show **`API: (Vite proxy)`**. Backend chip: **SQLite**.

**Smoke (API direct)**

```bash
cd apps/dashboard
npm run smoke
# or: SMOKE_BASE_URL=http://127.0.0.1:8001 npm run smoke
```

**Smoke (via Vite proxy — optional matrix check)**

```bash
# With npm run dev running on 5173:
SMOKE_BASE_URL=http://127.0.0.1:5173 npm run smoke
```

**API-only smoke (Python)**

```bash
cd apps/api
uv run python scripts/dashboard_v1_http_smoke.py
```

**Expected**

- `health.backend` = `sqlite`
- `health.mode` = `operator-sqlite-readonly`
- Warm `meta.data_source` = `sqlite` (not `postgres_mirror`)
- Dashboard banner: read-only; **no** “Postgres mirror is not send/outreach truth” line (unless backend is postgres)
- Contact panel: `GET /contacts/{email}` **200** when email picked from warm row; read-only copy; no forbidden fields in JSON

**Contact smoke**

```bash
npm run smoke:contacts
# or: npm run smoke:sqlite  # includes contact route
```

---

## B) Postgres mirror backend (disposable DB only)

Use a **scratch/disposable** Postgres instance only. Do **not** point at production or shared `origenlab_scratch` unless you own that risk.

### 1. Prepare disposable Postgres

Example (adjust credentials):

```bash
export ORIGENLAB_TEST_POSTGRES_URL='postgresql://origenlab:origenlab@127.0.0.1:5433/origenlab_matrix_test'
# For running API + sync, also set:
export ORIGENLAB_POSTGRES_URL="$ORIGENLAB_TEST_POSTGRES_URL"
```

Create empty DB, then from **email-pipeline** (writes mirror/read-model tables only — not Gmail, not production SQLite):

```bash
cd apps/email-pipeline
uv sync --group postgres --group api
export ORIGENLAB_POSTGRES_URL="$ORIGENLAB_TEST_POSTGRES_URL"
export ORIGENLAB_SQLITE_PATH="$HOME/data/origenlab-email/sqlite/emails.sqlite"

uv run origenlab mirror-dashboard --alembic --live --apply \
  --operator matrix-validation \
  --reason "dashboard backend matrix smoke"
```

Requires operator approval in production workflows; safe on disposable DB. `--live` covers warm cases, commercial deals, and operator snapshots. **PHASE W1 (2026-08):** direct Postgres publication of equipment opportunities is legacy/manual-backfill opt-in — `uv run origenlab auto-refresh-chilecompra-equipment --once --apply --publish-read-model` when Postgres is configured; the tracked cron wrapper explicitly disables it. Use `mirror-dashboard --live -- --include-equipment-opportunities` only for explicit legacy/backfill CSV reloads.

### 2. Start `apps/api` on postgres backend

```bash
cd apps/api
uv sync --group postgres
export ORIGENLAB_API_BACKEND=postgres
export ORIGENLAB_POSTGRES_URL="$ORIGENLAB_TEST_POSTGRES_URL"

uv run uvicorn origenlab_api.main:app --host 127.0.0.1 --port 8001 --reload
```

### 3. Dashboard (unchanged proxy)

```bash
cd apps/dashboard
# VITE_ORIGENLAB_API_BASE_URL still unset
npm run dev -- --host 127.0.0.1
```

**Expected UI**

- Backend chip: **Postgres mirror** (read-only)
- Banner includes: **“Postgres mirror is not send/outreach truth.”**
- Warm table loads when the warm mirror is populated.
- Contact drilldown: side panel **read-only**; **“Postgres mirror is not send/outreach truth.”** visible; `GET /contacts/{email}` via proxy returns **200**

**Smoke**

```bash
cd apps/dashboard
EXPECT_BACKEND=postgres npm run smoke:postgres
EXPECT_BACKEND=postgres npm run smoke:contacts

# Optional proxy path (requires npm run dev on :5173):
SMOKE_BASE_URL=http://127.0.0.1:5173 EXPECT_BACKEND=postgres npm run smoke:proxy
```

```bash
cd apps/api
ORIGENLAB_API_BACKEND=postgres ORIGENLAB_POSTGRES_URL="$ORIGENLAB_TEST_POSTGRES_URL" \
  uv run python scripts/dashboard_v1_http_smoke.py --expect-backend postgres
```

### 4. Targeted integration tests (disposable Postgres)

```bash
cd apps/api
export ORIGENLAB_TEST_POSTGRES_URL="$ORIGENLAB_TEST_POSTGRES_URL"
uv run pytest tests/test_postgres_warm_cases.py::test_postgres_warm_cases_integration_against_mirror \
  tests/test_postgres_contact.py::test_postgres_contact_integration_against_mirror -q
```

```bash
cd apps/email-pipeline
uv run pytest tests/test_sync_dashboard_postgres_mirror.py \
  tests/test_load_equipment_opportunity_mirror.py \
  tests/test_warm_case_promotion.py \
  tests/test_db1_preflight_static.py -q
```

Tests **skip** when `ORIGENLAB_TEST_POSTGRES_URL` is unset or DB unreachable.

---

## C) Return to SQLite after Postgres validation

**Warning:** Leaving `ORIGENLAB_API_BACKEND=postgres` on `uvicorn` after tearing down disposable Postgres breaks the dashboard (including contact drilldown). Return to SQLite for daily work.

After Mode B, stop the postgres-backed `uvicorn` on **:8001** and clear backend env vars:

```bash
unset ORIGENLAB_API_BACKEND
unset ORIGENLAB_POSTGRES_URL
unset ORIGENLAB_TEST_POSTGRES_URL
unset ALEMBIC_DATABASE_URL

cd apps/api
export ORIGENLAB_SQLITE_PATH="$HOME/data/origenlab-email/sqlite/emails.sqlite"
uv run uvicorn origenlab_api.main:app --host 127.0.0.1 --port 8001 --reload
```

Expected: `GET /health` → `backend: sqlite`, `mode: operator-sqlite-readonly`. Contact drilldown still uses `GET /contacts/{email}` against SQLite.

Dashboard: keep `VITE_ORIGENLAB_API_BASE_URL` unset; restart `npm run dev` if you changed `.env`.

Full operator narrative: [V1_FREEZE_OPERATOR_HANDOFF.md](./V1_FREEZE_OPERATOR_HANDOFF.md).

---

## CI / agent checklist

### Default (SQLite) — clears stale Postgres env

```bash
cd apps/dashboard
./scripts/run-v1-freeze-checklist.sh
```

Unsets `ORIGENLAB_API_BACKEND`, `ORIGENLAB_POSTGRES_URL`, `ORIGENLAB_TEST_POSTGRES_URL`, and `ALEMBIC_DATABASE_URL` in the script environment so email-pipeline CLI tests do not hit a dead `:5437` URL from your shell.

| Step | In default script |
|------|-------------------|
| API unit tests | yes |
| Dashboard tests + build | yes |
| API sqlite smoke | yes |
| Email-pipeline mirror unit tests | yes (`-m 'not integration'`) |

### Optional Postgres matrix

```bash
export ORIGENLAB_TEST_POSTGRES_URL='postgresql://…:5433/…'
./scripts/run-v1-postgres-matrix-check.sh
```

| Step | In postgres script |
|------|---------------------|
| Connectivity probe | yes (fail early) |
| API postgres smoke | yes |
| Dashboard `smoke:postgres` / `smoke:contacts` | yes (needs API on :8001, includes contact route) |
| Integration pytest subsets | yes (may skip if DB empty) |

---

## Safety constraints (matrix runs)

- No Gmail mutations
- No production SQLite mutations (read-only `ORIGENLAB_SQLITE_PATH` for API/sync source)
- No production/scratch Postgres unless explicitly chosen
- No write HTTP from dashboard smoke
- Legacy email-pipeline API on :8000 **removed** (Phase 6)
