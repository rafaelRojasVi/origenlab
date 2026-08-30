# Dashboard / API v1–v2 — freeze & operator handoff

> **Status: historical (2026-05 freeze record).** This documents the retired
> read-only era. The stack now includes durable commercial CRM writes via
> `POST /operations/*` and a multi-section dashboard. Current truth:
> [`docs/architecture/CURRENT_SYSTEM_TRUTH.md`](../../../docs/architecture/CURRENT_SYSTEM_TRUTH.md).
> The smoke/matrix procedures below remain useful as historical validation
> recipes only.
>
> **2026-08-30 correction:** `GET /opportunities/equipment`, the
> `EquipmentOpportunitiesTable` dashboard client, and legacy Today equipment
> consumption described below are **retired**. W1 procurement status
> (`/operator/procurement/*`) is canonical. Equipment mentions elsewhere in
> this document are historical only.

**Frozen state (2026-05):** Dashboard v1 + `apps/api` read-only operator plane. **Dashboard-2** adds read-only contact drilldown (`GET /contacts/{email}`) on the **Today** page. **Dashboard-2.3** adds client-side table polish; **Dashboard-2.5** adds read-only operator usability (internal-contact filter, warning email drilldown, human labels, outreach field guide). Validated on **SQLite** and **disposable Postgres mirror**; no send/write features and no Dashboard-3 scope in this freeze.

| Component | Status |
|-----------|--------|
| **`apps/api`** (port **8001**) | **Active** dashboard API — GET only |
| **`apps/dashboard`** (port **5173**) | **Active** read-only UI (`Today` page) |
| **Legacy email-pipeline API (:8000)** | **Removed** (API-3 Phase 6) — use `apps/api` `/mirror/*` |
| Gmail / production SQLite / scratch Postgres | **Out of scope** for casual matrix runs — no mutations |

---

## Architecture (read this first)

```text
Operator browser (:5173)
  → Vite proxy (dev) or same-origin Worker proxy at /api (prod build)
  → apps/api (:8001)  GET only
       ├─ backend=sqlite  → SQLite file (ORIGENLAB_SQLITE_PATH) + active/current CSV manifest
       └─ backend=postgres → api.v_* views on disposable/synced Postgres mirror

Send/outreach truth (NOT the dashboard):
  SQLite pipeline + Gmail Sent ingest + DNR/outbound sidecars + operator scripts
```

**Hard rules**

- **Postgres `READY` / mirror_ok / dashboard verdict `READY` does not mean safe to send.**
- **Dashboard is read-only** — no send, draft, archive, or status-write buttons.
- **Mirror data** (`postgres_mirror`, Postgres chip in UI) is for faster list reads, not outbound approval.
- **Do not use production/scratch Postgres** for validation unless you explicitly accept that risk.
- **Do not mutate Gmail** or **production SQLite** during matrix smoke.

### Dashboard-2 — read-only contact drilldown (frozen)

| Item | Rule |
|------|------|
| **Route** | `GET /contacts/{email}` only (`apps/api` on **:8001**) |
| **UI** | Side panel on **Today** — click contact email in **Warm cases** or **Equipment opportunities** (when `contact_email` is present) |
| **Read-only** | No navigation away from Today; no send, draft, archive, mark-contacted, or status-edit controls |
| **Forbidden in UI** | Raw email bodies (`body`, `body_preview`, `email_body`), filesystem paths (`source_path`, `sqlite_path`), Gmail mutation beyond email-only `mailto:` |
| **Postgres backend** | Banner + panel must show **“Postgres mirror is not send/outreach truth.”** |

`scripts/smoke-v1.mjs` (also `npm run smoke:contacts`) calls `GET /contacts/{email}` using the first valid `contact_email` from warm cases or equipment rows. If none exist, it prints a **WARN** and skips (not a failure).

#### Dashboard-2 freeze validation (completed)

| Backend | Result | Notes |
|---------|--------|-------|
| **SQLite** | **Passed** | `npm run smoke:sqlite`, `npm run smoke:proxy`, API TestClient smoke; live UI panel from warm case |
| **Disposable Postgres** | **Passed** | Fresh DB `origenlab_dashboard2_test` on **`127.0.0.1:5433`** (Docker `postgres:16`); `EXPECT_BACKEND=postgres npm run smoke:postgres` / `smoke:contacts` / `smoke:proxy` |

**Safety during validation:** Gmail was not mutated. **Production SQLite** and **production/scratch Postgres** were not used. Mirror sync wrote only the disposable container. Legacy email-pipeline API on **:8000** was **removed in API-3 Phase 6**.

Example disposable Postgres bootstrap (Dashboard-2.2 reference):

```bash
docker run -d --name origenlab-dashboard2-pg \
  -e POSTGRES_USER=origenlab -e POSTGRES_PASSWORD=origenlab \
  -e POSTGRES_DB=origenlab_dashboard2_test \
  -p 127.0.0.1:5433:5432 postgres:16

export ORIGENLAB_TEST_POSTGRES_URL='postgresql+psycopg://origenlab:origenlab@127.0.0.1:5433/origenlab_dashboard2_test'
export ORIGENLAB_POSTGRES_URL="$ORIGENLAB_TEST_POSTGRES_URL"
```

Then Mode 2 sync + API on postgres backend (below). Remove container when done: `docker stop origenlab-dashboard2-pg && docker rm origenlab-dashboard2-pg`.

#### Contact drilldown smoke commands

```bash
cd apps/dashboard
# SQLite (API on :8001, backend=sqlite)
npm run smoke:sqlite
npm run smoke:contacts          # same script; includes GET /contacts/{email} when rows have email

# Via Vite proxy (:5173, requires npm run dev)
npm run smoke:proxy

# Postgres mirror (API must run with ORIGENLAB_API_BACKEND=postgres)
EXPECT_BACKEND=postgres npm run smoke:postgres
EXPECT_BACKEND=postgres npm run smoke:contacts
SMOKE_BASE_URL=http://127.0.0.1:5173 EXPECT_BACKEND=postgres npm run smoke:proxy
```

API TestClient (no HTTP server required for basic route check):

```bash
cd apps/api
uv run python scripts/dashboard_v1_http_smoke.py --expect-backend sqlite
ORIGENLAB_API_BACKEND=postgres ORIGENLAB_POSTGRES_URL="$ORIGENLAB_TEST_POSTGRES_URL" \
  uv run python scripts/dashboard_v1_http_smoke.py --expect-backend postgres
```

### Dashboard-2.3 — Today table polish (client-side only)

Warm cases and equipment tables on **Today** support **in-browser** search, filters, and sort — no extra API calls, no write actions.

| Table | Search covers | Filters | Sort options |
|-------|---------------|---------|--------------|
| Warm cases | contact, domain, org, subject, snippet | status, category | last seen, status, category, contact |
| Equipment | buyer, region, category, item, note | — | rank, close date, category, buyer |

Footer shows **Showing N of M loaded** with **client filters active** when narrowed. Empty states: API returned no rows vs no rows match filters. Contact drilldown unchanged (`ContactEmailButton` only when `contact_email` exists).

### Dashboard-2.5 — Read-only operator usability (client-side only)

All Dashboard-2.5 behavior is **in-browser only** on the frozen **Today** page. No new API routes, no write actions, no Gmail/SQLite/Postgres/CSV mutations from the dashboard.

| Feature | Behavior | Read-only rule |
|---------|----------|----------------|
| **Hide internal OrigenLab contacts** | Optional checkbox on **Warm cases** (default **off**). When enabled, hides rows whose contact email is `@origenlab.cl` or `@labdelivery.cl`. Does not delete API data — client filter only. | No pipeline writes |
| **Warning email drilldown** | **Operator status** warnings that contain an email address render the address as a **contact button** (same `GET /contacts/{email}` side panel as tables). | **No** mailto, send, draft, archive, mark-contacted, or status-edit from the warnings block |
| **Humanized labels** | Status/category/action tokens show Spanish operator labels (e.g. `waiting_client` → *Esperando cliente*, `needs_supplier_quote` → *Requiere cotización proveedor*). Raw token remains in `title`/tooltip where useful. | Display only |
| **OutreachTruthGuide** | Contact side panel explains when **Do not repeat** is set but **outreach state** is empty: DNR/suppression = safety memory flag; **Sent history** = Gmail Sent evidence; **outreach state** = manual sidecar when present. | No write controls in panel |

**Still forbidden (unchanged):** raw email bodies, `source_path`, `sqlite_path`, send/draft/archive/mark-contacted/status-edit buttons, direct DB/CSV/script access from the UI.

**Code map (2.5):** `src/lib/internalContactFilter.ts`, `src/lib/operatorLabels.ts`, `src/lib/warningEmailLinks.ts`, `src/components/operator/OperatorWarningsList.tsx`, `src/components/operator/TokenLabel.tsx`, updates to `WarmCasesTable`, `EquipmentOpportunitiesTable`, `ContactProfilePanel`, `TodayPage`.

**Legacy API:** email-pipeline FastAPI on **:8000** **removed** (Phase 6). See [API-3 Phase 6 completion](../../api/docs/API-3_PHASE6_LEGACY_REMOVAL_COMPLETE.md).

---

## Mode 1 — Normal local SQLite dashboard (default)

Daily operator loop: SQLite-backed API + Vite proxy + dashboard.

### API (`apps/api`)

```bash
cd apps/api
uv sync
export ORIGENLAB_SQLITE_PATH="$HOME/data/origenlab-email/sqlite/emails.sqlite"
# Do not set ORIGENLAB_API_BACKEND or ORIGENLAB_POSTGRES_URL

uv run uvicorn origenlab_api.main:app --host 127.0.0.1 --port 8001 --reload
```

### Dashboard (`apps/dashboard`)

```bash
cd apps/dashboard
# .env: leave VITE_ORIGENLAB_API_BASE_URL unset (uses Vite proxy → :8001)
npm run dev -- --host 127.0.0.1
```

Open [http://127.0.0.1:5173](http://127.0.0.1:5173). **Restart `npm run dev` after any `.env` change.**

### Expected signals

| Check | Expected |
|-------|----------|
| `GET /health` → `backend` | `sqlite` |
| `GET /health` → `mode` | `operator-sqlite-readonly` |
| Dashboard header | `API: (Vite proxy)` |
| Backend chip | **SQLite** |
| Warm `meta.data_source` | `sqlite` (not `postgres_mirror`) |

### Smoke

```bash
# API (TestClient, no server required)
cd apps/api
uv run python scripts/dashboard_v1_http_smoke.py --expect-backend sqlite

# Dashboard HTTP → API on :8001 (includes Dashboard-2 GET /contacts/{email})
cd apps/dashboard
npm run smoke:sqlite
npm run smoke:contacts

# Dashboard HTTP → Vite proxy on :5173 (requires npm run dev running)
npm run smoke:proxy
```

### Post-campaign mirror smoke (Postgres backend / Render)

After outreach send + bounce sync, run the [post-campaign refresh](../../../email-pipeline/docs/REFRESH_RENDER_DASHBOARD_ONCE.md#post-campaign-dashboard-refresh-order) from email-pipeline, then verify Today:

| Check | Expected |
|-------|----------|
| `GET /operator/status` → `verdict` | **`READY`** (UI: **LISTO**) |
| `outbound_readiness_json.verdict` | **`mirror_ok`** (not `mirror_stale`) |
| `/tmp/outbound_sidecar_mirror_verify.json` → `ok` | **`true`** |
| Suppression counts in verify JSON | SQLite = Postgres (`email_suppression_total`, `bounce_suppressions`) |
| Lead segments (if mirrored) | `lead_blocked`, `lead_net_new_safe` match SQLite |
| Browser | Hard refresh on https://dashboard.origenlab.cl |

```bash
# API smoke (requires postgres backend + fresh mirror)
cd apps/api
ORIGENLAB_API_BACKEND=postgres ORIGENLAB_POSTGRES_URL="$ORIGENLAB_CLOUD_POSTGRES_URL" \
  uv run python scripts/dashboard_v1_http_smoke.py --expect-backend postgres

curl -sS "http://127.0.0.1:8001/operator/status" | jq '.verdict, .outbound_readiness_json'
```

If **PRECAUCIÓN** persists with stale suppression counts, re-run refresh with default `RUN_OUTBOUND_SIDECAR_MIRROR=1` or `sqlite_outbound_sidecars_to_postgres.py --replace`.

---

## Mode 2 — Disposable Postgres mirror validation

Prove mirror-backed reads on **`apps/api`** without touching production/scratch Postgres.

### 1. Disposable Postgres (example port **5433**)

```bash
# Example only — use your own disposable instance
export ORIGENLAB_TEST_POSTGRES_URL='postgresql://origenlab:origenlab@127.0.0.1:5433/origenlab_matrix_test'
export ORIGENLAB_POSTGRES_URL="$ORIGENLAB_TEST_POSTGRES_URL"
```

Create the empty database, then **from email-pipeline** (writes mirror/read-model tables only):

```bash
cd apps/email-pipeline
uv sync --group postgres --group api
export ORIGENLAB_POSTGRES_URL="$ORIGENLAB_TEST_POSTGRES_URL"
export ORIGENLAB_SQLITE_PATH="$HOME/data/origenlab-email/sqlite/emails.sqlite"

uv run origenlab mirror-dashboard --alembic --live --apply \
  --operator v1-freeze-validation \
  --reason "dashboard v1 postgres matrix"
```

This sync **writes Postgres mirror tables** from SQLite — it does **not** send email and does **not** replace SQLite send truth. `--live` covers warm cases, commercial deals, and operator snapshots. **Equipment opportunities are not covered.** Scheduled ChileCompra auto-refresh publishes queue/CSV/W1 institution-prospect data only; direct Postgres publication of the legacy equipment read model is manual/backfill opt-in only, via `auto-refresh-chilecompra-equipment --once --apply --publish-read-model`. Use `mirror-dashboard --live -- --include-equipment-opportunities` only for an explicit legacy/backfill CSV reload. `/opportunities/equipment` and legacy Today equipment consumption are retired.

### 2. API on postgres backend

```bash
cd apps/api
uv sync --group postgres
export ORIGENLAB_API_BACKEND=postgres
export ORIGENLAB_POSTGRES_URL="$ORIGENLAB_TEST_POSTGRES_URL"
export ORIGENLAB_SQLITE_PATH="$HOME/data/origenlab-email/sqlite/emails.sqlite"

uv run uvicorn origenlab_api.main:app --host 127.0.0.1 --port 8001 --reload
```

### 3. Dashboard (same as Mode 1)

```bash
cd apps/dashboard
# Still leave VITE_ORIGENLAB_API_BASE_URL unset
npm run dev -- --host 127.0.0.1
```

### Expected signals

| Check | Expected |
|-------|----------|
| `GET /health` → `backend` | `postgres` |
| `GET /health` → `mode` | `operator-postgres-mirror-readonly` |
| Dashboard chip | **Postgres mirror** |
| Banner | includes **“Postgres mirror is not send/outreach truth.”** |
| Warm `meta.data_source` | `postgres_mirror` when mirror populated |
| Equipment `meta.data_source` | **Retired** — `/opportunities/equipment` no longer exists (see the 2026-08-30 correction note at the top of this document); this row is historical only |

### Smoke

```bash
cd apps/api
ORIGENLAB_API_BACKEND=postgres ORIGENLAB_POSTGRES_URL="$ORIGENLAB_TEST_POSTGRES_URL" \
  uv run python scripts/dashboard_v1_http_smoke.py --expect-backend postgres

cd apps/dashboard
EXPECT_BACKEND=postgres npm run smoke:postgres
EXPECT_BACKEND=postgres npm run smoke:contacts
SMOKE_BASE_URL=http://127.0.0.1:5173 EXPECT_BACKEND=postgres npm run smoke:proxy
```

Contact drilldown on postgres: expect `GET /contacts/{email}` **200**, `meta.data_source=postgres_mirror`, panel copy **“Postgres mirror is not send/outreach truth.”**

Optional integration tests (skip if `ORIGENLAB_TEST_POSTGRES_URL` unset):

```bash
cd apps/api
export ORIGENLAB_TEST_POSTGRES_URL="$ORIGENLAB_TEST_POSTGRES_URL"
uv run pytest tests/test_postgres_warm_cases.py tests/test_postgres_equipment.py -q -k integration

cd apps/email-pipeline
uv run pytest tests/test_sync_dashboard_postgres_mirror.py \
  tests/test_load_equipment_opportunity_mirror.py \
  tests/test_warm_case_promotion.py \
  tests/test_db1_preflight_static.py -q
```

---

## Mode 3 — Return to SQLite after Postgres validation

**Warning:** If you leave `apps/api` running with `ORIGENLAB_API_BACKEND=postgres` after stopping the disposable Docker Postgres container, the dashboard will fail health/list/contact requests. Always return to SQLite for daily operator work unless you intentionally keep a live mirror DB.

```bash
# 1. Stop the postgres-backed uvicorn on :8001 (Ctrl+C or kill the process)

# 2. Clear postgres backend env from your shell
unset ORIGENLAB_API_BACKEND
unset ORIGENLAB_POSTGRES_URL
unset ORIGENLAB_TEST_POSTGRES_URL
unset ALEMBIC_DATABASE_URL

# 3. Restart apps/api on SQLite (default)
cd apps/api
export ORIGENLAB_SQLITE_PATH="$HOME/data/origenlab-email/sqlite/emails.sqlite"
uv run uvicorn origenlab_api.main:app --host 127.0.0.1 --port 8001 --reload
```

Dashboard dev setup unchanged: leave `VITE_ORIGENLAB_API_BASE_URL` unset; refresh [http://127.0.0.1:5173](http://127.0.0.1:5173). Backend chip should show **SQLite** again; contact drilldown still works via `GET /contacts/{email}` against SQLite.

Verify:

```bash
curl -sS http://127.0.0.1:8001/health | jq '.backend, .mode'
# "sqlite" and "operator-sqlite-readonly"
```

Dashboard: keep `VITE_ORIGENLAB_API_BASE_URL` unset; refresh [http://127.0.0.1:5173](http://127.0.0.1:5173).

---

## Legacy email-pipeline API (removed — API-3 Phase 6)

The deprecated FastAPI app on port **8000** (`apps/email-pipeline/src/origenlab_api`) was **deleted**. Postgres mirror reporting uses **`apps/api`** `GET /mirror/*` on **:8001** only. The obsolete pre-v1 dashboard client has also been removed.

**Dev env:** leave `VITE_ORIGENLAB_API_BASE_URL` unset in `npm run dev` so Vite proxies to **:8001**. A wrong API base URL causes **Failed to fetch** for v1 routes.

---

## v1 freeze validation checklist (CI / agent)

### Default — SQLite-safe (recommended)

**`./scripts/run-v1-freeze-checklist.sh`** clears stale Postgres env from your shell (`ORIGENLAB_POSTGRES_URL`, `ALEMBIC_DATABASE_URL`, etc.) so tests do not accidentally connect to `127.0.0.1:5437` when no disposable DB is running. It keeps `ORIGENLAB_SQLITE_PATH` if already set.

```bash
cd apps/dashboard
./scripts/run-v1-freeze-checklist.sh
```

Runs: API pytest · dashboard `npm test` · production build · API sqlite smoke · email-pipeline mirror **unit** tests (`-m 'not integration'`).

| Step | Included in default script |
|------|----------------------------|
| API tests | yes |
| Dashboard tests + build | yes |
| API sqlite smoke | yes |
| Email-pipeline mirror unit tests | yes (no live Postgres) |
| Dashboard HTTP smoke / proxy / contacts | run manually if API/dev server up (`smoke:sqlite`, `smoke:contacts`, `smoke:proxy`) |
| Postgres mirror + contact drilldown | **no** — separate script below |

### Optional — Postgres mirror matrix

Requires a **live disposable** Postgres (e.g. `:5433`). **Not** production/scratch.

```bash
export ORIGENLAB_TEST_POSTGRES_URL='postgresql://user:pass@127.0.0.1:5433/origenlab_matrix_test'
cd apps/dashboard
./scripts/run-v1-postgres-matrix-check.sh
```

Fails early if URL is unset or unreachable. See **Mode 2** above for sync/migrate steps first.

| Step | Command (manual) |
|------|------------------|
| API tests | `cd apps/api && uv run pytest tests -q` |
| Dashboard tests | `cd apps/dashboard && npm test` |
| Dashboard production build | `VITE_ORIGENLAB_API_BASE_URL=https://dashboard.origenlab.cl/api npm run build` |
| Dashboard sqlite smoke | `npm run smoke:sqlite` (API on :8001) |
| Dashboard proxy smoke | `npm run smoke:proxy` (dev server on :5173) |

---

## Related docs

- [BACKEND_MATRIX_VALIDATION.md](./BACKEND_MATRIX_VALIDATION.md) — detailed matrix steps
- [../README.md](../README.md) — dashboard dev env
- [../../api/README.md](../../api/README.md) — `apps/api` package collision & endpoints
