# System inventory

Status: canonical
Owner: project-maintainers
Last reviewed: 2026-09-03, verified against `main` @ `774cc36cf4dee2b90ff043e4307544573787b229`
Part of: [`REFOUNDATION_PLAN.md`](REFOUNDATION_PLAN.md)

Per-app and per-internal-module business responsibility, data owned vs.
read-only, side effects, and lifecycle status. This complements
[`../DOCUMENTATION_MAP.md`](../DOCUMENTATION_MAP.md) (which app owns which
doc) and [`../data/DATA_AUTHORITY_MAP.md`](../data/DATA_AUTHORITY_MAP.md)
(which system owns which business fact) with a module-level "what does this
package actually do" view. Status vocabulary: **active**, **transitional**,
**experimental**, **obsolete-candidate**.

## Applications

| App | Business responsibility | Data owned | Data only read | Side effects | Runtime | Status |
|---|---|---|---|---|---|---|
| `apps/web` | Public B2B marketing site | Static TS data (`src/data/`) | Nothing operator/CRM | None — static build | HostGator static hosting | active |
| `apps/email-pipeline` | Ingestion, extraction, machine intelligence, rebuildable projections | SQLite (all mart/lead/supplier/outbound/procurement tables), Postgres rebuildable schemas (`commercial.opportunity*`, `catalog.*`, `lead_intel.*`, `outbound.*`) | Nothing durable-CRM | Gmail IMAP read (never write), file writes to `reports/out`, Postgres mirror publish, Alembic migrations | Local-first Python/uv, cron loops | active |
| `apps/api` | Application/business command + read boundary | `commercial.command_idempotency` (writer-only, no reader route) | SQLite (via backend switch), Postgres durable + mirror | Durable CRM writes via `/operations/*` only; no send/ingest/migrate | FastAPI, Render (Docker) | active |
| `apps/dashboard-proxy` | Authenticated browser/API trust boundary | Nothing | Nothing (pure proxy) | Strict method+path allowlist, injects upstream auth + operator identity | Cloudflare Worker | active — **no CI deploy step**; `wrangler deploy` is a documented manual command (per `COMMERCIAL_OPERATING_SYSTEM_AUDIT.md`'s Phase 9 finding) |
| `apps/dashboard` | Operator UI — presentation only | Nothing (browser never opens SQLite/Postgres/CSV) | Everything via `apps/api` | None directly; writes only through allowlisted `/operations/*` calls | React/Vite, static build | active |

## Email-pipeline internal module families

Not exhaustive (the package tree is large and mostly flat by design — see
`apps/email-pipeline/docs/pipeline/PACKAGE_DOMAINS.md` for the full map).
This covers the families most load-bearing for the commercial/marketing/
communications questions this re-foundation is scoping.

| Module family | Business responsibility | Data owned | Status |
|---|---|---|---|
| `business_mart_schema` / `build_business_mart.py` | Rebuilds `contact_master`/`organization_master`/`document_master`/`opportunity_signals` wholesale from `emails`/`attachments` | `contact_master`, `organization_master` (SQLite) | active |
| `leads_schema` / `leads_match.py` | Lead ingestion, normalized-string matching to mart identity (not real fuzzy matching) | `external_leads_raw`, `lead_master`, `lead_matches_existing_*` | active |
| `lead_research/*` (DeepSearch, Phase 10B) | Batch prospect research, evidence, recommendations | `lead_research_batch/prospect/evidence/recommendation` (SQLite) → `lead_intel.*` (Postgres, one-way mirror) | active |
| `supplier_schema` | Supplier master + evidence + contact channels from workbook imports | `supplier_master`, `supplier_evidence`, `supplier_contact_channel` | active — **no bridge to `commercial.organization`** despite `TARGET_COMMERCIAL_ARCHITECTURE.md`'s stated direction |
| `outbound_campaign_schema` / `outbound_campaign_sender.py` / `outbound_campaign_reconcile.py` | Campaign creation, recipient lifecycle, two-phase Gmail send safety, reconciliation | `outbound_campaign`, `outbound_campaign_recipient`, `outbound_send_attempt`, `manual_contact_status` | active — CLI-only, zero dashboard surface (confirmed by repo-wide grep) |
| `commercial_procurement*` (multiple sub-packages: `_anexo_tender_terms`, `_institution_prospects`, `_candidate_planner`, `_acquisition`, `_contact_resolution`, `_link_audit`, `_product_relevance`) | ChileCompra tender acquisition, deterministic institution identity, candidate planning, evidence bundling | SQLite (`commercial_procurement_signal/evidence/conflict/enrichment_candidate/account_resolution`), file-backed W1/T1 read models | active |
| `commercial/` (canonical commercial-intel package) | Deal-ledger design/promotion, purchase events, warm-case classification support | `commercial_deal*` (partially implemented per design doc), `commercial_purchase_events` | active/transitional (deal ledger is design-complete, only partially built per its own Phase 1 checklist) |
| `warm_case_promotion.py` / `warm_case_classification.py` | Promotes/classifies Gmail-derived warm cases | `commercial.warm_case*` (Postgres) | **transitional** — writer is opt-in CLI, never scheduled; the live-computed SQLite `/cases/warm` queue is the fresher, actually-used path. Per `COMMERCIAL_OPERATING_SYSTEM_AUDIT.md`: "half-wired/ownership question," not simply orphaned — the Postgres read path is live under the Postgres API backend, but production runs the SQLite backend by default |
| `catalog` mirror build scripts | Product/category/alias/spec/offer/price-snapshot catalogue | `catalog.*` (SQLite → Postgres) | active, thin |
| `Tatiana` (`dataset/TATIANA_*`) | Commercial-drafting copilot: retrieval + guarded LLM draft + mandatory human review | Reads Gmail archive + `apps/web/src/data`; no send path | active, explicitly no autonomous reply |
| `operational_trust/` | Publication-gate consistency checks before treating lead/client outputs as publish-safe | Reads reports/CSVs; writes gate manifests | active |

## apps/api internal layout

| Layer | Business responsibility | Status |
|---|---|---|
| `routes/operations.py` | Durable CRM command surface (`/operations/*`) | active |
| `mirror/routes/*` | Rebuildable-projection read surface (`/mirror/*`) | active |
| `routes/opportunities.py` | PR3 rebuildable-opportunity reads — **lives outside `/mirror/*` despite being the same rebuildable-projection nature**, with a backend-factory switch between Postgres/SQLite repositories | active — naming/placement inconsistency, not a safety problem (see decision register) |
| `routes/institutions.py` | W1/T1 tender queues + file-backed annex import (a write path, but not `/operations/*`) | active |
| `services/commercial_operations_service.py`, `customer_quote_service.py`, `customer_quote_intake_resolution_service.py` | Command-side business logic, validation, idempotency/version handling | active |
| `repositories/postgres/*`, `repositories/sqlite/*` | Durable transaction + optimistic-concurrency SQL, parallel backend implementations of the same repository protocols | active |
| `drive/*` | Google Drive provider boundary — factory, protocol, provider impl, read-only preflight | active |

## Legacy / removed (context only — do not restore)

| Item | Status |
|---|---|
| Legacy email-pipeline FastAPI app (port 8000) | **removed** (API-3 Phase 6) — do not reintroduce |
| Streamlit operator UI | **removed** (2026-06-04) — do not reintroduce |
| Pre-v1 multi-tab dashboard, `TodayPage.tsx` single-mount | **removed** — extend the current multi-section `DashboardApp` instead |
| `commercial.equipment_opportunity*` (DB-1 era) | **obsolete-candidate**, pending full-repo grep before any deletion — route and read model are still wired end to end; only the primary UI content was removed |
