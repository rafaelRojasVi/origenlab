# OrigenLab — Dashboard (React)

> **Operator handoff (v1–v2 freeze):** [docs/V1_FREEZE_OPERATOR_HANDOFF.md](docs/V1_FREEZE_OPERATOR_HANDOFF.md) — three run modes (SQLite / disposable Postgres / return to SQLite), Dashboard-2 contact drilldown, smoke commands, send-truth rules.

**Dashboard v1** is the active read-only operator UI. It talks only to **`apps/api`** on port **8001** (legacy email-pipeline API removed in API-3 Phase 6):

| Route | Use |
|-------|-----|
| `GET /health` | Backend / service health |
| `GET /operator/status` | Operator verdict panel |
| `GET /operator/automation-status` | Read-only automation health (Gmail→SQLite + mirror publish loops) |
| `GET /cases/warm` | Warm cases table |
| `GET /opportunities/equipment` | Equipment opportunities table |
| `GET /contacts/{email}` | Read-only contact profile drilldown (Dashboard-2) |

The browser does not open SQLite/Postgres, CSV files, or `apps/email-pipeline` modules.

**Parked legacy:** the pre-v1 multi-tab panel lives under [`src/legacy/`](src/legacy/README.md) (mirror paths on :8001 if revived). It is **not mounted**, **not tested** in CI, and **must not** be imported from active code.

## Run locally

**Terminal 1 — API** (`apps/api`):

```bash
cd apps/api
uv sync
export ORIGENLAB_SQLITE_PATH="$HOME/data/origenlab-email/sqlite/emails.sqlite"

uv run uvicorn origenlab_api.main:app --host 127.0.0.1 --port 8001 --reload
```

**Terminal 2 — Dashboard**:

```bash
cd apps/dashboard
npm install
npm run dev -- --host 127.0.0.1
```

Open [http://127.0.0.1:5173](http://127.0.0.1:5173).

Copy [`.env.example`](.env.example) to `.env` if needed — **do not** copy a `:8000` URL from older setups.

### Local dev vs production env

| Mode | `VITE_ORIGENLAB_API_BASE_URL` | Behavior |
|------|-------------------------------|----------|
| **`npm run dev`** | **Leave unset** (recommended) | Browser uses same-origin requests; Vite proxies `/health`, `/operator`, `/cases`, `/opportunities`, `/contacts` to `http://127.0.0.1:8001` |
| **`npm run dev`** | Set to a **wrong** API port (e.g. old legacy port) | **Wrong** — bypasses proxy → “Failed to fetch”. UI may show a dev warning; unset `VITE_ORIGENLAB_API_BASE_URL` and **restart** `npm run dev`. |
| **`npm run dev`** | Set to `http://127.0.0.1:8001` | Works but unnecessary; prefer unset + proxy. |
| **`npm run build`** / production | **Required** | Set to same-origin proxy base, e.g. `https://dashboard.origenlab.cl/api` (Cloudflare Worker — see [`apps/dashboard-proxy/README.md`](../dashboard-proxy/README.md)), or direct API host if not using proxy |

Production builds **throw at runtime** if `VITE_ORIGENLAB_API_BASE_URL` is missing (no silent localhost fallback).

**Production auth:** Private API routes require origin token auth. The browser must **not** embed `ORIGENLAB_API_AUTH_TOKEN` in `VITE_*`. Production uses the same-origin Worker proxy at `/api` — set `VITE_ORIGENLAB_API_BASE_URL=https://dashboard.origenlab.cl/api` after deploying [`apps/dashboard-proxy`](../dashboard-proxy/README.md). See [docs/PRODUCTION_API_AUTH.md](docs/PRODUCTION_API_AUTH.md).

**After changing `.env`, restart `npm run dev`** — Vite only reads env at startup.

## Read-only scope

- **GET only** — no write/send/draft/archive actions.
- **Today** includes an **Automatización operador** card (`AutomationHealthCard`) fed by `GET /operator/automation-status` — local automation state only, no trigger buttons. The card surfaces freshness for Gmail/SQLite, the dashboard mirror, and the API snapshot using existing read-only automation status fields. It distinguishes actual Postgres mirror sync freshness from the auto-mirror loop state, so manual mirror refreshes are reflected without implying the dashboard can trigger writes.
- **Send/outreach truth** remains in the SQLite pipeline and operator scripts; Postgres mirror reads are not send approval.
- **No raw email bodies** or filesystem paths in the UI (API snippet/subject previews only).

## Tests and build

```bash
cd apps/dashboard
npm run validate  # full local validation: tests + build
npm test          # active src only (excludes src/legacy)
npm run build
```

Use **`npm run validate`** before opening or merging dashboard PRs. Today parser contract tests lock the dashboard boundary for `/cases/warm`, `/opportunities/equipment`, `/operator/status`, `/operator/automation-status`, and `/contacts/{email}`. Equipment opportunities also show compact triage badges derived client-side from current API fields, can be filtered client-side by triage badge key, show triage-specific empty-state guidance when a filter has no matches, and support a local browser watchlist stored in localStorage (not written back to the API or shared across users). Targeted Vitest runs (`vitest run path/to/file.test.tsx`) are useful while developing, but full validation should pass before review. This matters especially for Today / operator-status changes because fixtures span multiple test files (`TodaySummaryPage.test.tsx`, `TodayPage.test.tsx`, `DashboardApp.test.tsx`, component tests, etc.).

GitHub Actions workflow [`.github/workflows/dashboard.yml`](../../.github/workflows/dashboard.yml) runs `npm ci`, `npm test`, and `npm run build` for dashboard changes.

```bash
npm run smoke          # HTTP smoke → :8001 (same as smoke:sqlite)
npm run smoke:sqlite   # assert health.backend=sqlite
npm run smoke:postgres # assert postgres mirror labels (API must use postgres backend)
npm run smoke:proxy    # smoke via Vite dev server :5173 (requires npm run dev)
npm run smoke:contacts # same as smoke; includes GET /contacts/{email} when rows have email
./scripts/run-v1-freeze-checklist.sh      # SQLite-safe CI bundle (clears stale Postgres env)
./scripts/run-v1-postgres-matrix-check.sh  # optional; live disposable Postgres only
```

Safety tests enforce: `App.tsx` → `TodayPage` only, Dashboard v1 GET routes (including `/contacts/{email}`), no legacy client, no mutating HTTP, no DB/pipeline imports.

### Dashboard-2 — contact drilldown (frozen & validated)

Click a contact email in **Warm cases** or **Equipment opportunities** (when `contact_email` is present) to open a read-only **side panel** on Today (`GET /contacts/{email}` only).

**Dashboard-2.3 (Today UI polish):** client-side search, status/category filters, and sort on warm cases; search and sort on equipment opportunities; row counts (`Showing N of M loaded`) and distinct empty vs no-match-filter states. All filtering is in-browser only — no extra API calls.

**Dashboard-2.5 (operator usability):** optional **Hide internal OrigenLab contacts** on warm cases (`@origenlab.cl`, `@labdelivery.cl`, default off); warning emails open read-only **contact drilldown** (no mailto from warnings); humanized status/category/action labels; **OutreachTruthGuide** in the contact panel (DNR vs Sent history vs outreach state). All client-side — no write/send/draft/archive/mark-contacted/status-edit.

| Allowed | Forbidden |
|---------|-----------|
| Contact summary, outreach state, sent-history **counts/subjects**, DNR/suppression warnings (read-only) | Raw email bodies, `source_path`, `sqlite_path`, send/draft/archive/mark-contacted/status-edit |

**Validation (2026-05):** SQLite contact smoke **passed**; disposable Postgres mirror on **`127.0.0.1:5433`** (`origenlab_dashboard2_test`) **passed**. Gmail and production/scratch Postgres were not touched.

```bash
npm run smoke:contacts                    # :8001, includes GET /contacts/{email}
EXPECT_BACKEND=postgres npm run smoke:contacts
SMOKE_BASE_URL=http://127.0.0.1:5173 npm run smoke:proxy
```

After postgres matrix testing, **return to SQLite** — see handoff **Mode 3** (stop postgres `uvicorn`, unset `ORIGENLAB_API_BACKEND` / postgres URLs, restart API on :8001).

## Gmail → React operator refresh chain

Full chain (Gmail ingest → mirror sync → API checks → React Today): email-pipeline RUNBOOK anchor [`m-eprun-dashboard-gmail-to-react`](../email-pipeline/docs/RUNBOOK.md#m-eprun-dashboard-gmail-to-react).

After ingest, run `sync_dashboard_postgres_mirror.py`, then verify mirror freshness:

- Preferred: `GET /mirror/meta/dashboard-sync` and `GET /mirror/classification/summary` on **`apps/api` :8001**
- Mirror reporting uses **`GET /mirror/*`** on **:8001** only (not used by Dashboard v1 Today).

Use unset `VITE_ORIGENLAB_API_BASE_URL` + Vite proxy to **:8001** for local dev.

## Backend matrix validation

Prove Dashboard v1 against **`apps/api`** sqlite and postgres mirror backends: [`docs/BACKEND_MATRIX_VALIDATION.md`](docs/BACKEND_MATRIX_VALIDATION.md).

- **Active API:** `apps/api` on port **8001** (Dashboard v1 routes).
- **Mirror smoke:** `npm run smoke:mirror` — GET `/mirror/*` on **:8001** (`apps/api`). Legacy email-pipeline HTTP API removed (API-3 Phase 6).

## Mounted code map

```
App.tsx → TodayPage.tsx
  → api/operatorClient.ts (+ commercialParse, contactParse, operatorTypes, commercialTypes, contactTypes)
  → components/commercial/* (tables + ContactProfilePanel), components/operator/ReadOnlyBanner.tsx
```
