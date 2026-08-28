# Commercial Platform Reset — Cleanup Ledger

Operational evidence log for `refactor/commercial-platform-reset-v1`.
Temporary during the reset; compressed into permanent docs at the end.

## Checkpoint 0 — Baseline (2026-08-28)

- **Branch:** `refactor/commercial-platform-reset-v1`
- **HEAD / base SHA:** `efa3ce356d6c905dca3319e07a323ec6dc4ef051` (== `origin/main`, merge-base identical)
- **git status:** clean at start
- **Alembic head:** `20260827_0038` (`20260827_0038_crm_organization_contact_v1.py`), 39 migration files, script location `apps/email-pipeline/alembic`
- **Worktrees:** this repo + `/home/rafael/dev/origenlab-main` (main) — do not touch
- **Stashes:** 5 rescue/audit stashes — do not touch
- **Apps:** `apps/{api, dashboard, dashboard-proxy, email-pipeline, web}`
- **CI:** `.github/workflows/{api,dashboard,dashboard-proxy,email-pipeline,web,secret-scan}.yml`, each app runs `./scripts/validate.sh` (dashboard: `npm run validate` = vitest + tsc build; proxy: vitest)
- **Deploy:** `render.yaml` (origenlab-api docker + origenlab-dashboard static), `deploy/systemd/user/*` (API health/recover units), `apps/dashboard-proxy/wrangler.toml` (Cloudflare Worker)
- **Runtime entry points:**
  - API: `uvicorn origenlab_api.main:app` :8001 (Dockerfile → Render)
  - Dashboard: Vite build → static on Render behind `dashboard.origenlab.cl`
  - Proxy: Cloudflare Worker (`apps/dashboard-proxy/src/index.ts`)
  - email-pipeline: `origenlab` CLI (`cli.py`), cron loops (auto-refresh-mail, auto-mirror-dashboard)
  - web: Astro site (separate)

## Checkpoint 1 — Dependency / reachability audit (2026-08-28)

### Architectural generations found

1. **Streamlit era (retired 2026-06):** neutral read modules (`read/` package,
   `operator_copy_es`) kept "for future UI" — no runtime consumer remains.
2. **Read-only mirror era (API-3, 2026-05/06):** SQLite → Postgres mirror →
   `/mirror/*` reporting routes + read-only dashboard v1. Still active as the
   machine-evidence read path; the *docs* from this era still claim the whole
   stack is GET-only, which is now false.
3. **`core/` reorg attempt (Phase 5, 2026-06):** 40 facade modules re-exporting
   root implementations "so future code can import from core" — the migration
   phase never happened; 37 facades have zero runtime importers.
4. **PR2/PR3/PR4 machine read models (2026-08):** `commercial_identity` (PR2),
   `commercial_opportunity` (PR3, `o_*` ids, mirrored to `commercial.opportunity`),
   `commercial_procurement*` (PR4 tender workflow, W1/T1 read models). Active
   machine intelligence.
5. **Durable commercial operations (ARCH-3B, 2026-08, migrations 0032–0038):**
   `commercial.opportunity_operator_state`, `commercial.activity`, `commercial.task`,
   `commercial.sales_opportunity(+event)`, `commercial.organization(+source)`,
   `commercial.contact(+source)` + `/operations/*` command routes with trusted
   operator identity, idempotency, optimistic concurrency, append-only events.
   This is the durable human CRM. Partially connected to the dashboard
   (work-queue + PR3 operator-state panel; sales-opportunity promote/stage has
   **no UI consumer yet**).

### Classification map (major components)

| Component | Class | Evidence / decision |
|---|---|---|
| `apps/api` `/operations/*` (commands + reads) | **CANONICAL** | The single durable CRM write path; proxy-allowlisted; write-policy test enforces surface |
| `commercial.sales_opportunity` + events (0035–0038) | **CANONICAL** | Durable human opportunity lifecycle |
| `commercial.organization` / `commercial.contact` (0038) | **CANONICAL** | Durable identity; no API routes yet (next slice) |
| `commercial.activity` / `commercial.task` (0032) | **CANONICAL** | Durable work records |
| `commercial.opportunity_operator_state` (0032) | **TRANSITIONAL** | Human overlay keyed by PR3 `o_*` ids; needed until confirmed machine opportunities are promoted to `sales_opportunity` at confirm time; see Special Questions |
| PR3 `commercial.opportunity*` mirror (0031) + email-pipeline `commercial_opportunity` pkg | **CANONICAL (machine)** | Rebuildable machine proposal projection; feeds review intake |
| PR2 `commercial_identity` pkg | **CANONICAL (machine)** | Machine identity read model; future reconcile source into `commercial.organization` |
| PR4 `commercial_procurement*` pkgs, W1/T1 read models | **CANONICAL (machine)** | Active tender intelligence; file-backed read models on Render disk |
| `/mirror/*` routes + `postgres_dashboard_api` library | **CANONICAL (machine read)** | Documented reporting surface (RUNBOOK curl + dashboard deals/catalog/leads/audits); read-only |
| Warm cases (`commercial.warm_case*`, `/cases/warm`, Bandeja/Proveedores/Pagos pages) | **CANONICAL (machine)** | Intake/triage projection from Gmail; read-only evidence |
| `commercial.deal` mirror + Deals table ("Registro financiero") | **HISTORICAL (evidence)** | Redacted mirror of the historical SQLite deal ledger; context, not workflow |
| Equipment-first feed (`/opportunities/equipment`, `commercial.equipment_opportunity*`) | **TRANSITIONAL** | Pre-eligibility legacy feed; deliberately unrendered on Tenders page; still feeds Today counts + reduced-mode signal |
| Dashboard equipment table client (EquipmentOpportunitiesTable + drawer + watchlist) | **LEGACY** | Unrendered since Tenders W1 master/detail replaced it; localStorage watchlist is local-only workflow state |
| `TodayPage.tsx` shim | **LEGACY** | Deprecated re-export; zero importers (its test imports DashboardApp directly) |
| `IntelPreviewPage` + `institutionIntel` display components | **LEGACY** | Dev-only page outside nav; Licitación tab mock-backed; live tabs duplicate Tenders/Contacts surfaces |
| Hard-coded `SUPPLIER_GROUP_DEFINITIONS` (6 companies) | **LEGACY** | UI-owned supplier universe; grouping already falls back to email domain — derive labels instead |
| `warmCaseReviewLabels` (localStorage) | **TRANSITIONAL** | Local-only triage labels; no durable replacement yet; remove when warm-case triage promotes into durable CRM |
| email-pipeline `read/` package (leads/suppliers/today browse) | **LEGACY** | Streamlit-era read modules; only self-tests import them |
| `operator_copy_es.py` | **LEGACY** | Spanish copy for UI retired 2026-06-04; only QA inventory + shim-parity tests reference it |
| `canonical_contacto_source.py` | **LEGACY** | Unadopted re-export hub; impl is `contacto_gmail_source` (everyone imports that directly) |
| `core/*` facade modules (40) | **LEGACY** | Stalled reorg; 37 have zero runtime importers; 3 reachable only via `core/__init__` re-export or 2 migratable callers |
| `core/` real modules (mart builders, step_runner, safety, reports_out, research_automation, broad_marketing_contacts, do_not_repeat_master) | **ACTIVE** | Real implementations with runtime consumers — keep in place |
| Root-level email-pipeline modules (~108 of 112) | **ACTIVE** | Script/CLI-reachable; verified via import scan + wrapper scripts |
| `/emails/recent` route family | **TRANSITIONAL** | No UI consumer, proxy-blocked in prod; documented in demo/contract docs; retire when inbox surface reads durable path |
| Outbound/campaign/Tatiana/leads pipelines | **ACTIVE (machine)** | Outside commercial-core scope; safety-gated; untouched |
| Docs: root README, AGENTS.md, PROJECT_CONTEXT, api README, dashboard README, V1_FREEZE handoff | **STALE** | All still describe a GET-only/read-only stack and a `src/legacy` dashboard dir that no longer exists |

### Proposed deletion list (executed in Checkpoint 3)

Each item was verified against imports, callers, CLI entrypoints, package
scripts, CI, Docker/Render config, dashboard imports, proxy allowlists,
tests, and docs:

1. `apps/dashboard/src/pages/TodayPage.tsx` — zero importers (criterion 4);
   update `apps/api/tests/mirror/test_mirror_phase3_consumer_docs.py`
   `_DASHBOARD_ACTIVE` tuple; rename `TodayPage.test.tsx` (a second
   DashboardApp suite, not a duplicate) to a DashboardApp-named file.
2. `apps/dashboard` legacy equipment client: `EquipmentOpportunitiesTable(.test)`,
   `EquipmentOpportunityDetailDrawer`, `EquipmentWatchlistButton`,
   `lib/equipmentWatchlist(.test)`, `lib/equipmentTableView(.test)` and other
   libs used only by these (criteria 2+5: replaced by W1 ActionableOpportunitiesTable;
   deliberately unrendered; localStorage-only workflow state). Keep
   `/opportunities/equipment` API + Today counts + feed-status signal.
3. `apps/dashboard` IntelPreview: `IntelPreviewPage`, `LicitacionIntelCard`
   (mock), `InstitutionProfileCard`, `ProspectQueueList` (criterion 5: dev-only,
   outside nav, live equivalents exist). Keep `api/institutionIntel` client
   (used by tenders components). Update `dashboardHashRoute`/nav types + tests.
4. Hard-coded `SUPPLIER_GROUP_DEFINITIONS` — replace with pure domain-derived
   grouping in `supplierEntityGrouping.ts` (criterion 5; fallback grouping
   already exists for unknown domains).
5. email-pipeline `read/` package + tests `test_leads_browse_read`,
   `test_suppliers_browse_read`, `test_today_workspace_read`,
   `test_canonical_operational_read` (verify scope), `test_read_module_shim_parity`
   (criteria 3+6: Streamlit-only support; UI retired).
6. email-pipeline `operator_copy_es.py` + `test_operator_copy_es.py` (criterion 3);
   update `test_package_import_boundaries`, QA inventory scripts' allowlists.
7. email-pipeline `canonical_contacto_source.py` + its test (criterion 4).
8. email-pipeline `core/` facade layer: 40 re-export modules + facade-only
   `__init__` re-exports (criterion 4). Migrate the 3 remaining internal
   callers (`core/outbound/do_not_repeat_master.py`,
   `scripts/qa/export_do_not_repeat_master.py`, `core/leads` consumers +
   `scripts/leads/process_broad_marketing_contacts.py`,
   `scripts/mart/*` facade imports) to root imports; delete
   `core/gmail`, `core/suppliers`, `core/leads` subpackages if nothing but
   facades remain; keep real core modules in place; update
   `test_core_import_surface` and facade-audit expectations.

### Explicitly NOT deleted (stop conditions / evidence of use)

- `/emails/recent` — documented contract + demo surface (TRANSITIONAL).
- `/mirror/classification|organizations|outbound|meta|dashboard|health` —
  documented RUNBOOK reporting surface with doc-enforcement tests.
- `postgres_dashboard_api` — library behind `/mirror/*`.
- Warm-case local review labels — no durable replacement yet.
- Equipment-first pipeline + API — Today still consumes counts/feed status.
- All Alembic migrations, all durable schemas, all safety/outbound machinery.
- PR3 cockpit (`/opportunities/commercial`) — production-blocked by the proxy
  today but it is the machine-intake review surface; fix is reconnection
  (allowlist), not deletion. See Checkpoint 4.

## Checkpoint 3 — Executed deletions/consolidations (2026-08-28)

Each group committed separately with focused validation:

1. **`9389601` Streamlit-era remnants (email-pipeline):** deleted `read/`
   package (leads/suppliers/today browse), `operator_copy_es.py`,
   `canonical_contacto_source.py` + 6 test files; updated
   `test_package_import_boundaries`, `plan_source_quality` taxonomy,
   `test_canonical_operational_read` (kept the active
   `canonical_operational_sql` assertions), RUNBOOK, PACKAGE_DOMAINS.
   Validation: focused suites green.
2. **`ef3f52e` core facade layer (email-pipeline):** deleted all 40 facade
   modules + facade-only subpackages (`core/gmail`, `core/leads`,
   `core/suppliers`); migrated 3 remaining callers (incl. a relative import
   inside `broad_marketing_contacts`) to root imports; rewrote
   `test_core_import_surface` as a stay-removed guard; facade audit gained a
   reviewed-distinct allowlist for `db.py`/`postgres_dashboard_api/db.py`.
   Validation: full email-pipeline suite, 5630 passed.
3. **`7066a14` legacy equipment client + TodayPage shim (dashboard):**
   deleted the unrendered equipment_first client closure (table, drawer,
   triage badges, watchlist button, equipmentTableView/Watchlist/EmptyState/
   Triage libs — including the localStorage watchlist) and the TodayPage
   re-export shim; renamed its test to `DashboardApp.today.test.tsx`;
   updated the apps/api consumer-docs test + phase6 grep-gate allowlist.
   `/opportunities/equipment` API + Today counts kept.
   Validation: dashboard validate (657 tests + build), api mirror tests.
4. **`35fc5f6` IntelPreview (dashboard):** deleted the dev-only page +
   preview-only InstitutionProfileCard/ProspectQueueList/AxisScoreCard and
   the `intel-preview` section id. Live tender intel path untouched.
   Validation: dashboard validate (651 tests + build).
5. **`5231bef` hard-coded supplier universe (dashboard):** deleted
   `SUPPLIER_GROUP_DEFINITIONS`; grouping now derives id from the evidence
   domain and label from the most frequent account name. Validation:
   dashboard validate (652 tests + build).
6. **`e963475` stale docs:** README, AGENTS, PROJECT_CONTEXT, api/dashboard
   READMEs, DOCUMENTATION_MAP corrected; V1 freeze handoff marked
   historical; broken links fixed. Doc link checker: 177 files OK.

## Checkpoint 4 — Reconnection (2026-08-28)

- **`3a6457f`**: proxy GET allowlist now carries `/opportunities/commercial`
  and `/opportunities/commercial/o_<32hex>` — the PR3 intake cockpit had
  been silently 403-blocked in production. Cockpit relabeled as
  system-proposed intake whose human decisions land in the durable CRM;
  deals table relabeled as historical evidence ledger.
- Durable CRM already reaches the dashboard via `/operations/work-queue`
  (Hoy) and the operator-state/tasks/activities panel (Negocios detail).
  Sales-opportunity promote/stage UI is deliberately left to the next
  branch (feature work, not consolidation).

## Special legacy questions — resolutions

1. **`opportunity_operator_state.manual_stage`:** TRANSITIONAL. It remains
   the pre-promotion triage overlay on machine (`o_*`) opportunities.
   Human lifecycle for promoted records lives in
   `commercial.sales_opportunity.stage`. Removal condition: when the
   promote-at-confirm flow ships in the UI, stop writing `manual_stage`
   for promoted records (column stays; shipped migrations unchanged).
2. **PR3 vs durable CRM lifecycle:** PR3 keeps dedupe, evidence,
   conflicts, and canonical-stage *suggestions* (rebuildable). Human truth
   is operator_state (confirm/reject) + sales_opportunity (stage). PR3
   never owns human decisions.
3. **Deals:** historical/evidence context (redacted mirror of the SQLite
   deal ledger). Stays a read-only table on Negocios labeled as historical
   ledger; long-term it attaches as provenance to organizations/
   opportunities. Not a separate operational workflow.
4. **Warm Cases:** remain the machine intake/triage queue (Bandeja +
   role-filtered views). Local-only review labels are TRANSITIONAL until
   triage promotes into durable CRM records.
5. **Proveedores page:** kept as a supplier *evidence* view, but the
   hard-coded company/domain universe is deleted; grouping is now derived
   from evidence. Canonical supplier identity: `commercial.organization`
   (role=supplier) once organization routes ship.
6. **Supplier modules:** canonical evidence utilities = supplier workbook/
   schema, `marketing_supplier_domains`, warm-case supplier categories,
   `catalog.supplier_offer`, Gmail interaction audit, PR2 identity.
   Obsolete duplicates removed = `read/suppliers_browse`, dashboard
   hard-coded groups, `core/suppliers` facades.
7. **`lead_*`/`leads_*`:** ACTIVE machine/outbound layer (lead_master
   SQLite + `lead_intel` mirror + Prospectos page + export gate). Only the
   `core/leads` facade layer was dead — removed.
8. **Streamlit remnants:** `read/` package, `operator_copy_es`, shim-parity
   tests — all removed this pass. Remaining mentions are historical
   audits/reports only.
9. **Old API/read layers:** legacy `:8000` API was already removed
   (API-3); `/mirror/*` + `postgres_dashboard_api` survive as the
   documented read-only reporting surface; `read/` package removed.
   `/emails/recent` is TRANSITIONAL (no UI consumer, proxy-blocked;
   retire when an inbox surface reads a durable path).
10. **Materially false docs:** root README, AGENTS.md, PROJECT_CONTEXT,
    api README, dashboard README — corrected. V1 freeze handoff — marked
    historical. Architecture truth now lives in
    `CURRENT_SYSTEM_TRUTH.md` / `TARGET_COMMERCIAL_ARCHITECTURE.md`.

## Status

This ledger is the completed migration record of the 2026-08 commercial
platform reset. Permanent documentation:
`docs/architecture/CURRENT_SYSTEM_TRUTH.md` (what is) and
`docs/architecture/TARGET_COMMERCIAL_ARCHITECTURE.md` (direction + rules).
