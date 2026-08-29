# Current System Truth

Status: canonical
Last verified against code: 2026-08-29, `main` @ `a789b9b915407b223ddbd5592ca23a872e9a4359`

One page of what the system actually is today — the current authoritative source of truth
for this repository. If another doc contradicts this one (including
`COMMERCIAL_OPERATING_SYSTEM_AUDIT.md`, which is a historical/point-in-time audit and
evidence record, not current truth), this one wins; fix the other doc.

## Topology

```text
EXTERNAL SOURCES          Gmail · ChileCompra · Google Drive · supplier workbook ·
                          historical spreadsheets · manual annex uploads
        |
        v
apps/email-pipeline       ingestion, OCR/extraction, classification, semantic
(Python, SQLite-first)    suggestions, machine read models (PR2 identity,
                          PR3 opportunity, PR4 procurement/W1/T1), mirror jobs
        |
        |  auto-refresh-mail (Gmail -> SQLite, ~3 min)
        |  auto-mirror-dashboard (SQLite -> Postgres, ~1 min)
        |  local-tender-worker / chilecompra refresh
        v
PostgreSQL                commercial.* / catalog.* / lead_intel.* / api.* views
                          - REBUILDABLE mirrors: warm_case*, deal, purchase_event*,
                            equipment_opportunity*, opportunity* (PR3), catalog.*,
                            lead_intel.*
                          - DURABLE human CRM (survives any mirror rebuild):
                            sales_opportunity(+event), opportunity_operator_state,
                            activity, task, organization(+source), contact(+source),
                            command_idempotency, operator events
        ^
        |  durable writes (POST /operations/*) — the ONLY human write path
        v
apps/api (FastAPI :8001)  operator plane: reads (SQLite or Postgres backend),
                          /mirror/* read-only reporting, /operations/* durable
                          commands (trusted operator identity, Idempotency-Key,
                          optimistic concurrency), one file-backed annex import
        ^
        v
apps/dashboard-proxy      Cloudflare Worker at dashboard.origenlab.cl/api* —
                          strict method+path allowlist, injects upstream auth,
                          forwards operator identity; POST only for enumerated
                          /operations/* commands + annex preview/import
        ^
        v
apps/dashboard (React)    operator UI (hash-routed sections):
                          Hoy · Bandeja · Pipeline (durable, real writes) ·
                          Negocios · Prospectos · Clientes ·
                          Licitaciones/equipos · Pagos y logística · Proveedores ·
                          Catálogo · Sistema

apps/web (Astro)          public marketing site — no operator/CRM code
```

## Source-of-truth boundaries

| Concept | Owner | Nature |
|---|---|---|
| Gmail/outbound send safety, DNR, Sent memory | email-pipeline SQLite | operational truth (machine) |
| Machine-proposed opportunities (`o_*`) | PR3 read model → `commercial.opportunity*` | rebuildable projection |
| Human opportunity decisions | `commercial.sales_opportunity` (`sales_*`), `commercial.opportunity_operator_state` | **durable** |
| Human work (tasks, activities) | `commercial.task`, `commercial.activity` | **durable** |
| Organization / contact identity | `commercial.organization`, `commercial.contact` (+`*_source` provenance) | **durable**, reconciled by sales-opportunity promotion (no standalone CRUD routes) |
| Historical deal ledger | SQLite deal ledger → `commercial.deal` mirror | historical evidence |
| Catalog + supplier offers + price snapshots | `catalog.*` mirror | rebuildable projection |
| Lead research / prospects | `lead_intel.*` mirror | rebuildable projection |
| Tender/institution intelligence (W1/T1) | file-backed read models on API disk | rebuildable projection |

Rules:

- **Machine systems propose; the durable CRM records human commercial truth.**
- Every rebuildable layer above may be dropped and rebuilt from sources; no
  human decision may live only in a rebuildable layer or in the browser.
- Mirror/API data is never send approval; outbound remains human-reviewed
  batches in email-pipeline.

## Write paths

1. **Durable CRM (human):** dashboard → proxy (allowlisted POST) → `apps/api`
   `/operations/*` → `CommercialOperationsService` → Postgres repository →
   transaction + append-only event. Requires trusted operator identity and
   `Idempotency-Key`; stage/state transitions use `expected_version`.
   Current commands: PR3 operator state (confirm/reject + manual_stage),
   sales-opportunity promote + stage, activity create, task create/complete/cancel.
   Promote also conservatively reconciles durable CRM identity in the same
   transaction: it resolves an existing `commercial.organization`/
   `commercial.contact` via the `*_source` provenance tables' `(source_kind,
   source_id)` key (never by matching raw domain/email strings), or creates
   one when the machine evidence is sufficient (a contact is only linked once
   an organization is resolved). Before this, `organization_id`/
   `primary_crm_contact_id` were always left `NULL`. Insufficient or
   malformed evidence still leaves them `NULL` rather than fabricate
   identity — promotion never blocks on it.
2. **Operator annex import (file evidence):** dashboard → proxy → POST
   `/operator/procurement/tenders/{code}/annex-bundle/{preview|import}`.
3. **Machine writes:** email-pipeline CLIs and cron loops only (SQLite +
   mirror publish + Alembic migrations). The API never ingests or sends.

Anything else is read-only. `apps/api` write policy is enforced by
`apps/api/tests/test_no_write_policy.py`; the proxy enforces method+path.

## Dashboard sections and their data

| Section | Reads | Writes | Nature |
|---|---|---|---|
| Hoy | operator status, warm counts, `/operations/work-queue` | — | durable work queue + machine counts |
| Bandeja de revisión | `/cases/warm` | local-only view labels (browser) | machine evidence |
| Pipeline | `GET /operations/sales-opportunities` (+ task/activity routes) | `POST /operations/*` stage transitions, activity/task CRUD | **durable** — the post-promotion sales-opportunity board (Kanban, drag/drop, workspace drawer); real writes, not read-only |
| Negocios | `/opportunities/commercial` (PR3 intake cockpit), `/mirror/commercial/deals` (historical ledger) | `/operations/*` via detail panel (confirm/reject + promote into Pipeline) | machine intake + durable commands + historical evidence |
| Prospectos | `/mirror/leads/prospects` | — | machine evidence |
| Clientes / instituciones | `/cases/warm` grouping, `/contacts/{email}`, gmail audit | — | machine evidence |
| Licitaciones / equipos | W1 queues + T1 tender detail | annex preview/import | machine evidence + evidence import |
| Pagos y logística | `/cases/warm` filtered | — | machine evidence |
| Proveedores | `/cases/warm` filtered + gmail audit (domain-derived grouping) | — | machine evidence |
| Catálogo | `/mirror/catalog/products` | — | machine evidence |
| Sistema | health/status | — | diagnostics |

The dashboard's `/cases/warm` category set (`WarmCaseCategory` in
`apps/dashboard/src/api/commercialTypes.ts`) matches the API's canonical contract
(`apps/api/src/origenlab_api/schemas/cases.py`) exactly.

## Migrations

Alembic lives in `apps/email-pipeline/alembic`; head `20260828_0039`.
Durable CRM tables were introduced through 0032–0038; 0039 adds the CRM-4A
writer grants and organization/contact API read views (no table/column/
constraint or data changes). Shipped migrations are never rewritten;
corrections are new migrations. Downgrades that would drop human data are
fail-closed.

## Deployment

- `render.yaml`: `origenlab-api` (Docker, persistent disk for W1/T1 read
  models) + `origenlab-dashboard` (static build) + managed Postgres.
- `apps/dashboard-proxy/wrangler.toml`: Worker on `dashboard.origenlab.cl/api*`
  → `https://api.origenlab.cl`.
- Local operator: systemd user units under `deploy/systemd/user/` + cron
  loops documented in `apps/email-pipeline/docs/pipeline/OPERATOR_CRON.md`.

## Known stale claims this doc supersedes

Older docs describing the API/dashboard as entirely read-only/GET-only
(pre-ARCH-3B) are historical. The durable `/operations/*` command surface
exists and is the only human write path. The legacy email-pipeline FastAPI
app on port 8000 (`apps/email-pipeline/src/origenlab_api`) was fully
removed in API-3 Phase 6 — `apps/api` on :8001 is the only HTTP app; no
FastAPI code remains under `apps/email-pipeline`.
