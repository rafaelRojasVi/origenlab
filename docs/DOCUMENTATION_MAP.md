# Documentation Map

Status: canonical  
Owner: project-maintainers  
Last reviewed: 2026-09-03 (added re-foundation docs; see changelog below)

This file is the source of truth for documentation placement, intent, and lifecycle.

<a id="m-docmap-entry"></a>
## Canonical Entry Points

- Claude Code entrypoint: [CLAUDE.md](../CLAUDE.md)
- Monorepo: [README.md](../README.md)
- Canonical system truth: [architecture/CURRENT_SYSTEM_TRUTH.md](./architecture/CURRENT_SYSTEM_TRUTH.md)
- Target commercial architecture: [architecture/TARGET_COMMERCIAL_ARCHITECTURE.md](./architecture/TARGET_COMMERCIAL_ARCHITECTURE.md)
- Monorepo agent context: [PROJECT_CONTEXT.md](./PROJECT_CONTEXT.md)
- Release process: [RELEASE_PROCESS.md](./RELEASE_PROCESS.md)
- Web app: [apps/web/README.md](../apps/web/README.md)
- Web app agent context: [apps/web/docs/APP_CONTEXT.md](../apps/web/docs/APP_CONTEXT.md)
- Web agent policy: [apps/web/AGENTS.md](../apps/web/AGENTS.md)
- Email pipeline app: [apps/email-pipeline/README.md](../apps/email-pipeline/README.md)
- Email pipeline agent context: [apps/email-pipeline/docs/APP_CONTEXT.md](../apps/email-pipeline/docs/APP_CONTEXT.md)
- Email pipeline docs index: [apps/email-pipeline/docs/README.md](../apps/email-pipeline/docs/README.md)
- Email pipeline operator script map: [apps/email-pipeline/docs/SCRIPT_MAP.md](../apps/email-pipeline/docs/SCRIPT_MAP.md)
- Operator API: [apps/api/README.md](../apps/api/README.md) · [apps/api/docs/README.md](../apps/api/docs/README.md)
- Dashboard (Today UI): [apps/dashboard/README.md](../apps/dashboard/README.md) · [apps/dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md](../apps/dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md)
- Data authority map: [data/DATA_AUTHORITY_MAP.md](./data/DATA_AUTHORITY_MAP.md)
- SQLite database register: [data/SQLITE_REGISTER.md](./data/SQLITE_REGISTER.md)
- Repository system inventory: [refoundation/SYSTEM_INVENTORY.md](./refoundation/SYSTEM_INVENTORY.md)
- Re-foundation plan and decision register: [refoundation/REFOUNDATION_PLAN.md](./refoundation/REFOUNDATION_PLAN.md)
- Business workflow docs: [workflows/README.md](./workflows/README.md)

**Architecture entrypoint (monorepo):** [architecture/CURRENT_SYSTEM_TRUTH.md](./architecture/CURRENT_SYSTEM_TRUTH.md) (durable CRM vs machine mirrors, write paths, ownership) with [PROJECT_CONTEXT.md](./PROJECT_CONTEXT.md) for per-app context. [architecture/COMMERCIAL_RESET_LEDGER.md](./architecture/COMMERCIAL_RESET_LEDGER.md) is the completed 2026-08 reset migration record.

### Operator stack (HTTP + UI)

| App | Canonical docs |
|-----|----------------|
| **Operator API** (`apps/api`, :8001) | [apps/api/README.md](../apps/api/README.md) · [apps/api/docs/README.md](../apps/api/docs/README.md) |
| **Dashboard** (`apps/dashboard`, :5173) | [apps/dashboard/README.md](../apps/dashboard/README.md) (historical freeze record: [V1_FREEZE_OPERATOR_HANDOFF.md](../apps/dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md)) |
| **Dashboard proxy** (`apps/dashboard-proxy`) | [apps/dashboard-proxy/README.md](../apps/dashboard-proxy/README.md) |
| **Email pipeline** (SQLite OLTP, ingest, outbound) | [apps/email-pipeline/docs/OUTBOUND_SOURCE_OF_TRUTH.md](../apps/email-pipeline/docs/OUTBOUND_SOURCE_OF_TRUTH.md) · [RUNBOOK.md](../apps/email-pipeline/docs/RUNBOOK.md) |

Historical API-3 migration notes (legacy `:8000` removal): [apps/api/docs/archive/api3/](../apps/api/docs/archive/api3/README.md) — not current operator runbooks.

<a id="m-docmap-mapping"></a>
## Canonical vs Archive Mapping

### Monorepo

- [RELEASE_PROCESS.md](./RELEASE_PROCESS.md) → **canonical** GitHub Release / tag workflow (changelog snapshots; not package distribution).
- [business/BUSINESS_RULES_QUOTES_AND_SUPPLIERS.md](./business/BUSINESS_RULES_QUOTES_AND_SUPPLIERS.md) → **canonical** quote/supplier **truth rules** (cotizaciones, proveedores, research runs vs master data; schema design lives in [architecture/TARGET_COMMERCIAL_ARCHITECTURE.md](./architecture/TARGET_COMMERCIAL_ARCHITECTURE.md)).
- [commercial/COMMERCIAL_DEAL_LEDGER_SCHEMA_V1.md](./commercial/COMMERCIAL_DEAL_LEDGER_SCHEMA_V1.md) → **historical** design-only doc, not current truth; canonical replacement is [architecture/CURRENT_SYSTEM_TRUTH.md](./architecture/CURRENT_SYSTEM_TRUTH.md) (`commercial.deal`).
- [catalog/PRODUCT_CATALOG_SCHEMA_AUDIT_V1.md](./catalog/PRODUCT_CATALOG_SCHEMA_AUDIT_V1.md) → **historical** design/audit-only doc, not current truth; canonical replacement is [architecture/CURRENT_SYSTEM_TRUTH.md](./architecture/CURRENT_SYSTEM_TRUTH.md) (`catalog.*` mirror).

### Operator API (`apps/api`)

- [apps/api/README.md](../apps/api/README.md) → **canonical** API (:8001; operator reads, `GET /mirror/*` reporting, durable `POST /operations/*` CRM commands).
- [apps/api/docs/README.md](../apps/api/docs/README.md) → **canonical** API docs index.
- [apps/api/docs/API-3_PHASE6_LEGACY_REMOVAL_COMPLETE.md](../apps/api/docs/API-3_PHASE6_LEGACY_REMOVAL_COMPLETE.md) → **canonical** removal record (legacy :8000); test-gated regression fixture.
- [apps/api/docs/archive/api3/](../apps/api/docs/archive/api3/README.md) → **historical** API-3 migration context, retained because several tests assert on it directly (regression protection against legacy-path resurrection).

### Dashboard (`apps/dashboard`)

- [apps/dashboard/README.md](../apps/dashboard/README.md) → **canonical** active operator UI (:5173).
- [apps/dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md](../apps/dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md) → **historical** freeze record for the pre-CRM read-only era.

### Web docs

- [deployment.md](../apps/web/docs/deployment.md) → canonical runbook.
- [deployment-status.md](../apps/web/docs/deployment-status.md) → canonical snapshot of **external** hosting/DNS state; must include last external verification date (not implied by git).
- [email-setup.md](../apps/web/docs/email-setup.md) → canonical email operations.
- [security-audit-v1.md](../apps/web/docs/security-audit-v1.md) → canonical baseline audit.
- [company-scope.md](../apps/web/docs/company-scope.md) → canonical human-facing business brief; **should match** [`apps/web/src/data/`](../apps/web/src/data/) (manually maintained; not an automated sync).
- Live chat (Tidio in `Layout.astro`; legacy FloatingChat removed) → [stage-4 web audit](../apps/web/docs/audits/stage-4-cleanup-hardening-2026-05-16.md) (historical; `floating-chat-widget-notes.md` not retained in-repo).

### Re-foundation docs (2026-09, added this pass)

- [data/DATA_AUTHORITY_MAP.md](./data/DATA_AUTHORITY_MAP.md) → **canonical**, per-business-concept authority (canonical store, evidence source, writers/readers, durability, desired future authority). Companion to [architecture/CURRENT_SYSTEM_TRUTH.md](./architecture/CURRENT_SYSTEM_TRUTH.md)'s durable/rebuildable split — this file is the wider concept-by-concept detail; `CURRENT_SYSTEM_TRUTH.md` remains authoritative if the two ever disagree. Supersedes no existing file; builds on and credits [architecture/COMMERCIAL_OPERATING_SYSTEM_AUDIT.md](./architecture/COMMERCIAL_OPERATING_SYSTEM_AUDIT.md)'s domain matrix (historical, point-in-time) with a current, maintained equivalent.
- [data/SQLITE_REGISTER.md](./data/SQLITE_REGISTER.md) → **canonical**, read-only forensic inventory of every SQLite file found at the configured runtime path and its immediate directory. Cross-references the existing `apps/email-pipeline/docs/SQLITE_*.md` operational docs rather than duplicating their maintenance/cutover procedures.
- [refoundation/SYSTEM_INVENTORY.md](./refoundation/SYSTEM_INVENTORY.md) → **canonical**, per-app and per-internal-module business responsibility, data owned vs. read-only, and lifecycle status (active/transitional/experimental/obsolete-candidate).
- [refoundation/REFOUNDATION_PLAN.md](./refoundation/REFOUNDATION_PLAN.md) → **canonical**, the re-foundation effort's own scope, decision register, and unresolved-questions log. Not an architecture-truth document itself — points back to `CURRENT_SYSTEM_TRUTH.md`/`TARGET_COMMERCIAL_ARCHITECTURE.md` for that.
- [workflows/README.md](./workflows/README.md) and [workflows/*.md](./workflows/) → **canonical**, non-technical business-workflow descriptions filled from [templates/WORKFLOW_TEMPLATE.md](./templates/WORKFLOW_TEMPLATE.md). `CAMPAIGN_TO_REPLY.md` is deliberately left with open design questions rather than a designed answer — see `REFOUNDATION_PLAN.md`.
- **Drift found and fixed in this pass:** [architecture/CURRENT_SYSTEM_TRUTH.md](./architecture/CURRENT_SYSTEM_TRUTH.md) stated Alembic head `20260830_0040`; actual head at the time of this pass was `20260902_0046` (6 migrations of drift — the customer-quote workflow, closure, and Drive-intake-resolution shape had all shipped without a doc update). Fixed in place; see that file's "Partially refreshed" note for exact scope.
- **Drift flagged, not yet fixed (needs an operator/maintainer decision, not a doc fix):** root [`AGENTS.md`](../AGENTS.md) directs agents to read `apps/email-pipeline/docs/EXPERIMENTAL_PARKED.md` "before Postgres/API... work," but that doc (last reviewed 2026-05-19) predates every durable-CRM migration (`sales_opportunity` 0035 onward) and, read literally, only covers email-pipeline's own mirror-sync/migrate/break-glass scripts — not `apps/api`'s durable command layer, which it does not mention and which ships/writes independently. A reader can reasonably over-generalize "Postgres/API is parked" from a doc whose actual claims are narrower. See `refoundation/REFOUNDATION_PLAN.md`'s decision register.

### Email pipeline docs

Paths below are under [`apps/email-pipeline/docs/`](../apps/email-pipeline/docs/).

- Canonical operations:
  - [SCRIPT_MAP.md](../apps/email-pipeline/docs/SCRIPT_MAP.md) (daily outbound lanes; core / ops / lab / break-glass)
  - [SQLITE_STORAGE_MAINTENANCE.md](../apps/email-pipeline/docs/SQLITE_STORAGE_MAINTENANCE.md) (offline backup/compaction; never live VACUUM)
  - [SQLITE_WRITABLE_RESTORE_AND_CUTOVER.md](../apps/email-pipeline/docs/SQLITE_WRITABLE_RESTORE_AND_CUTOVER.md) (synthetic writable rehearsal + RPO=0 cutover design; not an authorization to cut over)
  - [SQLITE_PRODUCTION_CUTOVER_ORCHESTRATOR.md](../apps/email-pipeline/docs/SQLITE_PRODUCTION_CUTOVER_ORCHESTRATOR.md) (staged fail-closed cutover CLI; draft tooling — not a live cutover authorization)
  - [RUNBOOK.md](../apps/email-pipeline/docs/RUNBOOK.md#m-eprun-path) (incl. [cold outreach / shared export gate](../apps/email-pipeline/docs/RUNBOOK.md#m-eprun-cold-export-gate))
  - [REPORTING.md](../apps/email-pipeline/docs/REPORTING.md#m-eprep-mail) (informe correo + paquete leads)
  - [REPORT_SCOPE_CLIENT.md](../apps/email-pipeline/docs/REPORT_SCOPE_CLIENT.md) (alcance del informe de correo; copiado por [`generate_client_report.py`](../apps/email-pipeline/scripts/reports/generate_client_report.py) a `ALCANCE_INFORME.md`)
  - [reporting/OUTPUTS_OVERVIEW.md](../apps/email-pipeline/docs/reporting/OUTPUTS_OVERVIEW.md) (includes derived-insights backlog)
- Canonical architecture:
  - [ARCHITECTURE.md](../apps/email-pipeline/docs/ARCHITECTURE.md#m-eparch-flow) (incl. [shared cold-outreach export gate](../apps/email-pipeline/docs/ARCHITECTURE.md#m-eparch-export-gate))
  - [pipeline/BUSINESS_MART.md](../apps/email-pipeline/docs/pipeline/BUSINESS_MART.md)
  - [pipeline/BUSINESS_FILTERING.md](../apps/email-pipeline/docs/pipeline/BUSINESS_FILTERING.md)
  - [pipeline/SCHEMA_OWNERSHIP.md](../apps/email-pipeline/docs/pipeline/SCHEMA_OWNERSHIP.md#m-schema-orchestrated)
  - [pipeline/PHASE2_EMAIL_PIPELINE.md](../apps/email-pipeline/docs/pipeline/PHASE2_EMAIL_PIPELINE.md)
  - [leads/LEAD_PIPELINE.md](../apps/email-pipeline/docs/leads/LEAD_PIPELINE.md)
  - [leads/LEAD_ACCOUNT_LAYER.md](../apps/email-pipeline/docs/leads/LEAD_ACCOUNT_LAYER.md)
  - [leads/CHILE_LEAD_SOURCES.md](../apps/email-pipeline/docs/leads/CHILE_LEAD_SOURCES.md)
- Canonical ML/AI:
  - [ml/AI_ML_IMPLEMENTED_SUMMARY.md](../apps/email-pipeline/docs/ml/AI_ML_IMPLEMENTED_SUMMARY.md) (includes former ML options + LLM prompt appendix)
- Tatiana commercial drafting (OrigenLab / Labdelivery voice; human-reviewed; no send integration):
  - [dataset/TATIANA_DRAFTING_COPILOT.md](../apps/email-pipeline/docs/dataset/TATIANA_DRAFTING_COPILOT.md)
  - [dataset/TATIANA_PILOT_WORKFLOW.md](../apps/email-pipeline/docs/dataset/TATIANA_PILOT_WORKFLOW.md) (operational pilot batches + `pilot_review.csv`)
  - [dataset/TATIANA_EVAL_REVIEW.md](../apps/email-pipeline/docs/dataset/TATIANA_EVAL_REVIEW.md)
- Generated or snapshot docs (regenerated by the scripts named in each; not hand-edited):
  - [generated/CONTACT_READINESS_AUDIT.md](../apps/email-pipeline/docs/generated/CONTACT_READINESS_AUDIT.md)
  - [generated/DEEP_RESEARCH_RECONCILIATION.md](../apps/email-pipeline/docs/generated/DEEP_RESEARCH_RECONCILIATION.md)
  - [generated/READY8_AND_TOP20_REPORTING_PLAN.md](../apps/email-pipeline/docs/generated/READY8_AND_TOP20_REPORTING_PLAN.md)
  - [generated/operational_trust_scorecard.md](../apps/email-pipeline/docs/generated/operational_trust_scorecard.md)

<a id="m-docmap-lifecycle"></a>
## Lifecycle Labels

Use this metadata block at the top of maintained docs:

- `Status: canonical | generated | historical`
- `Owner: team-or-person`
- `Last reviewed: YYYY-MM-DD`
- `Canonical replacement: <path>` (for historical docs)

<a id="m-docmap-link-check"></a>
## Link checking

From the monorepo root:

```bash
python3 docs/check_doc_links.py
```

<a id="m-docmap-linking-conventions"></a>
## Documentation linking conventions

- **First mention** of another maintained doc in prose: use a markdown link. **Later mentions** in the same doc may stay as `` `path` `` / plain text.
- **Tables, views, and schema objects**: link to schema/source docs (e.g. [`SCHEMA_OWNERSHIP.md`](../apps/email-pipeline/docs/pipeline/SCHEMA_OWNERSHIP.md#m-schema-orchestrated)), not to raw DB files or ad-hoc paths unless the topic is literally “where the file lives”.
- **External operational facts** (hosting, DNS, live URLs): label **externally verified** with a date when the repo cannot prove them from code alone; see deployment snapshot docs for the pattern.
- **Stable deep links**: prefer explicit anchors `m-*` defined in this repo’s markdown (see `<a id="m-..."></a>` before major sections) over relying on auto-generated heading slugs, which can change when headings are reworded.
