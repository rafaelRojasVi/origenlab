# OrigenLab — Dashboard (React)

> **Operator handoff (v1–v2 freeze):** [docs/V1_FREEZE_OPERATOR_HANDOFF.md](docs/V1_FREEZE_OPERATOR_HANDOFF.md) — three run modes (SQLite / disposable Postgres / return to SQLite), Dashboard-2 contact drilldown, smoke commands, send-truth rules.

The active operator UI. It talks only to **`apps/api`** on port **8001** (legacy email-pipeline API removed in API-3 Phase 6). Main routes:

| Route | Use |
|-------|-----|
| `GET /health` · `GET /operator/status` · `GET /operator/automation-status` | Health, operator verdict, automation loops |
| `GET /cases/warm` | Warm cases (Bandeja / Proveedores / Pagos, machine evidence) |
| `GET /opportunities/commercial` | PR3 machine-proposed opportunity intake (Negocios cockpit) |
| `GET /operations/work-queue` + `POST /operations/*` | Durable CRM: work queue, PR3 operator state, activities, tasks, customer quotes + Drive workspace (trusted operator identity + Idempotency-Key) |
| `GET /operator/procurement/*` + annex `preview`/`import` POSTs | Licitaciones W1/T1 + explicit annex import |
| `GET /mirror/commercial/deals` · `/mirror/catalog/products` · `/mirror/leads/*` · `/mirror/audits/*` | Mirror lists (Negocios ledger, Catálogo, Prospectos, Gmail audit) |
| `GET /contacts/{email}` | Contact profile drilldown |

The browser does not open SQLite/Postgres, CSV files, or `apps/email-pipeline` modules; production writes pass the `apps/dashboard-proxy` method+path allowlist. Machine surfaces propose; durable CRM state changes only through `/operations/*`. Canonical architecture: [`../../docs/architecture/CURRENT_SYSTEM_TRUTH.md`](../../docs/architecture/CURRENT_SYSTEM_TRUTH.md).

**Legacy UI:** the obsolete pre-v1 multi-tab dashboard and the unrendered equipment_first client were removed. Extend the active Dashboard/API contracts instead of restoring old clients.

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

### Local development operator identity

Durable-write routes (`POST /operations/*`) require a trusted
`X-OriginLab-Operator-Email` header. In production, `apps/dashboard-proxy`
injects it after its own auth — the browser can never set it. For local dev
against a disposable Postgres, set a plain (non-`VITE_`-prefixed) env var
before starting the dev server:

```bash
ORIGENLAB_DEV_OPERATOR_EMAIL="dev-operator@origenlab.cl" npm run dev -- --host 127.0.0.1
```

`vite.config.ts` reads this in the Node process and injects the header on
proxied requests (`vite.devOperatorProxy.ts` — unit tested), the same way the
production Worker does. **Disabled by default** (unset var → no header, so
writes correctly 401 with no identity present); only active for `vite dev`,
never `vite build` — the logic is gated on Vite's `command`, and
`server.proxy` is not part of the production build output at all. Because
the var is not `VITE_`-prefixed, Vite never exposes it to client code via
`import.meta.env`, so it cannot leak into a built bundle. Do not commit a
real personal email as a default anywhere — always pass it via your own
shell environment.

**`VITE_ORIGENLAB_API_BASE_URL` must stay unset** while relying on this
mechanism. When it's set, client code builds absolute request URLs straight
to that API host instead of relative paths, so the browser bypasses Vite's
dev proxy entirely — and with it, the proxy's server-side identity
injection (and the strip-inbound-header step that always runs alongside it).
Durable-write requests then arrive with no trusted operator header and are
correctly rejected (401), or — if you also happen to be proxying elsewhere —
could carry whatever `X-OriginLab-Operator-Email` the client sent, unstripped.

## Write scope and safety boundaries

- **Durable CRM writes are explicitly allowlisted**, not general write
  access: `POST /operations/*` (PR3 operator state, sales-opportunity
  promote/stage, activity/task create, customer-quote create + Drive
  workspace retry) requires a trusted operator identity
  and `Idempotency-Key`, and is the only path that changes durable
  `commercial.*` state. Quote Drive links render only from parse-validated
  https Google URLs; the browser never invents quote numbers or Drive
  references.
- **Annex import** (`POST /operator/procurement/tenders/{code}/annex-bundle/{preview|import}`)
  is a separate, similarly sanctioned write path for tender evidence — not
  part of `/operations/*`.
- The dashboard still does **not** send email, create Gmail drafts, archive
  Gmail, or otherwise mutate outbound/send truth — that remains exclusively
  in the SQLite pipeline and operator scripts; Postgres mirror reads are not
  send approval.
- **Machine evidence is distinct from durable human CRM truth.** Read-only
  machine surfaces (PR3 opportunities, warm cases, contact/lead intel) only
  ever *propose*; a human operator decision only becomes durable through the
  allowlisted `/operations/*` commands above.
- **Today** includes an **Automatización operador** card (`AutomationHealthCard`) fed by `GET /operator/automation-status` — local automation state only, no trigger buttons. The card surfaces freshness for Gmail/SQLite, the dashboard mirror, and the API snapshot using existing read-only automation status fields. It distinguishes actual Postgres mirror sync freshness from the auto-mirror loop state, so manual mirror refreshes are reflected without implying the dashboard can trigger writes.
- **No raw email bodies** or filesystem paths in the UI (API snippet/subject previews only).

## Tests and build

```bash
cd apps/dashboard
npm run validate  # full local validation: tests + build
npm test          # dashboard test suite
npm run build
```

Use **`npm run validate`** before opening or merging dashboard PRs. Today parser contract tests lock the dashboard boundary for `/cases/warm`, `/opportunities/commercial`, `/operator/status`, `/operator/automation-status`, and `/contacts/{email}`. Targeted Vitest runs (`vitest run path/to/file.test.tsx`) are useful while developing, but full validation should pass before review. This matters especially for Today / operator-status changes because fixtures span multiple test files (`TodaySummaryPage.test.tsx`, `DashboardApp.test.tsx`, `DashboardApp.today.test.tsx`, component tests, etc.).

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

Safety tests enforce: `App.tsx` → `DashboardApp` (multi-section, hash-routed; `TodaySummaryPage` is the default/`today` section), Dashboard v1 GET routes (including `/contacts/{email}`), no legacy client, no DB/pipeline imports, and that POST/PUT/PATCH/DELETE HTTP is confined to the three narrowly allowlisted mutation modules described below (`noWritePolicy.test.ts`) — no other dashboard source file may issue a mutating request.

### Dashboard-2 — contact drilldown (frozen & validated)

Click a contact email in **Warm cases** (when `contact_email` is present) to open a read-only **side panel** on Today (`GET /contacts/{email}` only).

**Dashboard-2.3 (Today UI polish):** client-side search, status/category filters, and sort on warm cases; row counts (`Showing N of M loaded`) and distinct empty vs no-match-filter states. All filtering is in-browser only — no extra API calls.

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
- Active dashboard tabs also use **`GET /mirror/*`** for Catálogo, Negocio, leads, audit, and commercial views (via Worker in production).

Use unset `VITE_ORIGENLAB_API_BASE_URL` + Vite proxy to **:8001** for local dev.

## Backend matrix validation

Prove Dashboard v1 against **`apps/api`** sqlite and postgres mirror backends: [`docs/BACKEND_MATRIX_VALIDATION.md`](docs/BACKEND_MATRIX_VALIDATION.md).

- **Active API:** `apps/api` on port **8001** (Dashboard v1 routes).
- **Mirror smoke:** `npm run smoke:mirror` — GET `/mirror/*` on **:8001** (`apps/api`). Legacy email-pipeline HTTP API removed (API-3 Phase 6).

## Mounted code map

Current (hash-routed, multi-section):

```
App.tsx → pages/DashboardApp.tsx
  → context/DashboardDataContext.tsx, components/layout/DashboardShell.tsx
  → section pages (lib/dashboardHashRoute.ts picks one; "today" is default):
      TodaySummaryPage.tsx      (Inicio)
      InboxTriagePage.tsx       (Bandeja de revisión)
      VentasPage.tsx            (Ventas)
      CotizacionesPage.tsx      (Cotizaciones)
      DealsPage.tsx             (Negocios)
      ProspectosPage.tsx        (Prospectos)
      CatalogPage.tsx           (Catálogo)
      SuppliersPage.tsx         (Proveedores)
      TendersPage.tsx           (Licitaciones)
      PaymentsLogisticsPage.tsx (Pagos y logística)
      ContactsPage.tsx          (Clientes)
      SystemPage.tsx            (Sistema)
  → components/commercial/*, components/pipeline/*, components/operator/*,
    components/tenders/*, components/institutionIntel/*, components/catalog/*
```

`InboxTriagePage.tsx`, `DealsPage.tsx`, and `ProspectosPage.tsx` are still mounted
and reachable by direct hash (`#/inbox`, `#/deals`, `#/prospectos`), but are
intentionally no longer in the sidebar nav under the flat V2 IA — don't "fix"
them back into the nav.

**Historical (removed):** `TodayPage.tsx` was the original single-page mount
(`App.tsx → TodayPage.tsx` directly). It no longer exists — do not reference
or restore it; extend the current multi-section `DashboardApp` structure
above instead.
