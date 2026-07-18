# Documentation Map

Status: canonical  
Owner: project-maintainers  
Last reviewed: 2026-06-17

This file is the source of truth for documentation placement, intent, and lifecycle.

<a id="m-docmap-entry"></a>
## Canonical Entry Points

- Monorepo: [README.md](../README.md)
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

**Architecture entrypoint (monorepo):** [PROJECT_CONTEXT.md](./PROJECT_CONTEXT.md) — canonical for what each app owns; do not duplicate a separate architecture index in this phase.

### Operator stack (read-only HTTP + UI)

| App | Canonical docs |
|-----|----------------|
| **Operator API** (`apps/api`, :8001) | [apps/api/README.md](../apps/api/README.md) · [apps/api/docs/README.md](../apps/api/docs/README.md) |
| **Dashboard** (`apps/dashboard`, :5173) | [apps/dashboard/README.md](../apps/dashboard/README.md) · [apps/dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md](../apps/dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md) |
| **Email pipeline** (SQLite OLTP, ingest, outbound) | [apps/email-pipeline/docs/OUTBOUND_SOURCE_OF_TRUTH.md](../apps/email-pipeline/docs/OUTBOUND_SOURCE_OF_TRUTH.md) · [RUNBOOK.md](../apps/email-pipeline/docs/RUNBOOK.md) |

Historical API-3 migration notes (legacy `:8000` removal): [apps/api/docs/archive/api3/](../apps/api/docs/archive/api3/README.md) — not current operator runbooks.

<a id="m-docmap-mapping"></a>
## Canonical vs Archive Mapping

### Monorepo

- [MONOREPO.md](./MONOREPO.md) → historical context, keep as archive reference.
- [RELEASE_PROCESS.md](./RELEASE_PROCESS.md) → **canonical** GitHub Release / tag workflow (changelog snapshots; not package distribution).
- [business/BUSINESS_RULES_QUOTES_AND_SUPPLIERS.md](./business/BUSINESS_RULES_QUOTES_AND_SUPPLIERS.md) → **canonical** quote/supplier **truth rules** and proposed entities (cotizaciones, proveedores, research runs vs master data).

### Operator API (`apps/api`)

- [apps/api/README.md](../apps/api/README.md) → **canonical** active read-only API (:8001; operator routes + `GET /mirror/*`).
- [apps/api/docs/README.md](../apps/api/docs/README.md) → **canonical** API docs index.
- [apps/api/docs/API-3_PHASE6_LEGACY_REMOVAL_COMPLETE.md](../apps/api/docs/API-3_PHASE6_LEGACY_REMOVAL_COMPLETE.md) → **canonical** removal record (legacy :8000).
- [apps/api/docs/archive/api3/](../apps/api/docs/archive/api3/README.md) → **historical** API-3 migration context (not current runbooks).

### Dashboard (`apps/dashboard`)

- [apps/dashboard/README.md](../apps/dashboard/README.md) → **canonical** active read-only operator UI (:5173).
- [apps/dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md](../apps/dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md) → **canonical** Today operator handoff.
- [apps/dashboard/src/legacy/README.md](../apps/dashboard/src/legacy/README.md) → **historical** parked multi-tab UI (not active dashboard).

### Web docs

- [deployment.md](../apps/web/docs/deployment.md) → canonical runbook.
- [deployment-status.md](../apps/web/docs/deployment-status.md) → canonical snapshot of **external** hosting/DNS state; must include last external verification date (not implied by git).
- [email-setup.md](../apps/web/docs/email-setup.md) → canonical email operations.
- [security-audit-v1.md](../apps/web/docs/security-audit-v1.md) → canonical baseline audit.
- [company-scope.md](../apps/web/docs/company-scope.md) → canonical human-facing business brief; **should match** [`apps/web/src/data/`](../apps/web/src/data/) (manually maintained; not an automated sync).
- Live chat (Tidio in `Layout.astro`; legacy FloatingChat removed) → [stage-4 web audit](../apps/web/docs/audits/stage-4-cleanup-hardening-2026-05-16.md) (historical; `floating-chat-widget-notes.md` not retained in-repo).
- [compat/EMAIL_BUSINESS_SIGNAL_PROMPT.md](../apps/web/docs/compat/EMAIL_BUSINESS_SIGNAL_PROMPT.md) → archive/stub in web; canonical prompt in [AI_ML_IMPLEMENTED_SUMMARY.md](../apps/email-pipeline/docs/ml/AI_ML_IMPLEMENTED_SUMMARY.md) (appendix).
- [compat/email-archive-locations.md](../apps/web/docs/compat/email-archive-locations.md) → archive/stub in web; canonical copy in [DATA_LOCATIONS.md](../apps/email-pipeline/docs/DATA_LOCATIONS.md#m-epdata-root).
- [compat/legacy-mail-migration-notes.md](../apps/web/docs/compat/legacy-mail-migration-notes.md) → archive/stub in web; canonical copy in [LEGACY_MAIL_MIGRATION.md](../apps/email-pipeline/docs/ARCHIVE/research/LEGACY_MAIL_MIGRATION.md).

### Email pipeline docs

Paths below are under [`apps/email-pipeline/docs/`](../apps/email-pipeline/docs/).

- Canonical operations:
  - [SCRIPT_MAP.md](../apps/email-pipeline/docs/SCRIPT_MAP.md) (daily outbound lanes; core / ops / lab / break-glass)
  - [SQLITE_STORAGE_MAINTENANCE.md](../apps/email-pipeline/docs/SQLITE_STORAGE_MAINTENANCE.md) (offline backup/compaction; never live VACUUM)
  - [SQLITE_WRITABLE_RESTORE_AND_CUTOVER.md](../apps/email-pipeline/docs/SQLITE_WRITABLE_RESTORE_AND_CUTOVER.md) (synthetic writable rehearsal + RPO=0 cutover design; not an authorization to cut over)
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
- Generated or snapshot docs:
  - [generated/CONTACT_READINESS_AUDIT.md](../apps/email-pipeline/docs/generated/CONTACT_READINESS_AUDIT.md)
  - [generated/DEEP_RESEARCH_RECONCILIATION.md](../apps/email-pipeline/docs/generated/DEEP_RESEARCH_RECONCILIATION.md)
  - [generated/READY8_AND_TOP20_REPORTING_PLAN.md](../apps/email-pipeline/docs/generated/READY8_AND_TOP20_REPORTING_PLAN.md)
  - [generated/AI_READINESS_AUDIT.md](../apps/email-pipeline/docs/generated/AI_READINESS_AUDIT.md)
  - [generated/PHASE2_1_VALIDATION.md](../apps/email-pipeline/docs/generated/PHASE2_1_VALIDATION.md) and [generated/PHASE2_2_VALIDATION.md](../apps/email-pipeline/docs/generated/PHASE2_2_VALIDATION.md)
- Archived or superseded context:
  - primary storage under [ARCHIVE/](../apps/email-pipeline/docs/ARCHIVE/)

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
