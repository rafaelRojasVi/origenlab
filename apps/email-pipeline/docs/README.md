# Email Pipeline Documentation Index

Status: canonical  
Owner: email-pipeline-maintainers  
Last reviewed: 2026-05-14

Use this page as the navigation and truth hierarchy for [`apps/email-pipeline/docs/`](./).

## Cross-cutting monorepo rules

- Commercial quotes & supplier research (policy + proposed DB entities): [`docs/business/BUSINESS_RULES_QUOTES_AND_SUPPLIERS.md`](../../../docs/business/BUSINESS_RULES_QUOTES_AND_SUPPLIERS.md)

## HTTP API (not in this package)

Postgres mirror reporting and Dashboard operator routes are served by **[`apps/api`](../../api/README.md)** on port **8001** only. Email-pipeline provides sync (`sync_dashboard_postgres_mirror.py`), CLIs, scripts, and SQLite jobs; it has no active product UI and does **not** expose `origenlab_api` FastAPI (removed API-3 Phase 6).

## Agent-first start

- **[`pipeline/CURRENT_SAFETY_CHECKPOINT.md`](pipeline/CURRENT_SAFETY_CHECKPOINT.md)** — **pause marker**: safe loop, golden rules, what not to do next (read before restarting work)
- **[`pipeline/POST_SEND_SAFE_LOOP.md`](pipeline/POST_SEND_SAFE_LOOP.md)** — **canonical post-send procedure** after new Sent mail / NDRs (ingest → dry-run → targeted apply → safety → mirror)
- **[`SCRIPT_MAP.md`](SCRIPT_MAP.md)** — **canonical operator map** (daily outbound lanes, core / ops / lab / break-glass, workspace prep stories)
- **[`OPERATOR_CHEAT_SHEET.md`](OPERATOR_CHEAT_SHEET.md)** — short **“which script should I run?”** aid; not a substitute for **`SCRIPT_MAP.md`** / **`RUNBOOK.md`**
- Deep Research automation (review-ready only, **daily cadence**): [`DEEP_RESEARCH_AUTOMATION_PLAN.md`](DEEP_RESEARCH_AUTOMATION_PLAN.md) and [`../scripts/research/run_deep_research_prospecting.py`](../scripts/research/run_deep_research_prospecting.py) (supports `--sector`, `--day-rotation`, and optional read-only `--run-contacted-coverage-check`; still stops before send)
- Reproducibility, CRUD policy, refactor strategy, and script groupings: [`REPRODUCIBILITY.md`](REPRODUCIBILITY.md) · [`CRUD_SAFETY.md`](CRUD_SAFETY.md) · [`QUALITY_AND_REFACTOR_STRATEGY.md`](QUALITY_AND_REFACTOR_STRATEGY.md) · [`SCRIPT_INVENTORY.md`](SCRIPT_INVENTORY.md) · **Tatiana/lab (not daily outbound):** [`TATIANA_LAB_BOUNDARY.md`](TATIANA_LAB_BOUNDARY.md) · read-only: [`check_reproducibility.py`](../scripts/qa/check_reproducibility.py) · [`plan_reports_out_cleanup.py`](../scripts/qa/plan_reports_out_cleanup.py) · [`plan_script_consolidation.py`](../scripts/qa/plan_script_consolidation.py) · [`plan_source_quality.py`](../scripts/qa/plan_source_quality.py) (heuristic source/scan, planning only; `tatiana_lab` bucket)
- **Audits / cleanup planning (not runbooks):** [`audits/POSTGRES_API_PIPELINE_MESS_AUDIT.md`](audits/POSTGRES_API_PIPELINE_MESS_AUDIT.md) (monorepo **audit**: SQLite as operational OLTP, **optional** Postgres/API notes, risks) · [`audits/SCRIPT_CONSOLIDATION_NEXT_STEPS.md`](audits/SCRIPT_CONSOLIDATION_NEXT_STEPS.md) (conservative **plan** beside `plan_script_consolidation.py` — no moves/deletes implied). These are **planning and audit** documents, not procedures. **`SCRIPT_MAP.md` and `RUNBOOK.md` remain the canonical operator truth** for how to run the pipeline day to day.
- [`APP_CONTEXT.md`](APP_CONTEXT.md#m-epapp-start)
- [`BUSINESS_CONTEXT.md`](BUSINESS_CONTEXT.md#m-epbiz-objective)
- [`ARCHITECTURE.md`](ARCHITECTURE.md#m-eparch-flow) · Python package domains / import rules: [`pipeline/PACKAGE_DOMAINS.md`](pipeline/PACKAGE_DOMAINS.md)
- [`RUNBOOK.md`](RUNBOOK.md#m-eprun-path) — incl. [**publicación / gate QA**](RUNBOOK.md#m-eprun-publish-qa) ([`publish_gate.py`](../scripts/qa/publish_gate.py)) y [**elegibilidad export frío / gate compartido**](RUNBOOK.md#m-eprun-cold-export-gate)
- [`DATA_LOCATIONS.md`](DATA_LOCATIONS.md#m-epdata-root)
- [`REPORTING.md`](REPORTING.md#m-eprep-mail) (+ [`REPORT_SCOPE_CLIENT.md`](REPORT_SCOPE_CLIENT.md) para el texto de alcance que se adjunta al informe de correo); **validación pack + CSVs operativos:** [§ QA leads](REPORTING.md#m-eprep-leads-qa)
- SQLite storage / offline backup & compaction: [`SQLITE_STORAGE_MAINTENANCE.md`](SQLITE_STORAGE_MAINTENANCE.md) · body-column inventory: [`SQLITE_BODY_STORAGE_ASSESSMENT.md`](SQLITE_BODY_STORAGE_ASSESSMENT.md) · writable restore / cutover design: [`SQLITE_WRITABLE_RESTORE_AND_CUTOVER.md`](SQLITE_WRITABLE_RESTORE_AND_CUTOVER.md) · production cutover orchestrator: [`SQLITE_PRODUCTION_CUTOVER_ORCHESTRATOR.md`](SQLITE_PRODUCTION_CUTOVER_ORCHESTRATOR.md)

## Setup and navigation

- Monorepo context: [`PROJECT_CONTEXT.md`](../../../docs/PROJECT_CONTEXT.md#m-proj-start) · map: [`DOCUMENTATION_MAP.md`](../../../docs/DOCUMENTATION_MAP.md#m-docmap-entry)
- Project run/setup entrypoint: [`../README.md`](../README.md)
- **Operator script map:** [`SCRIPT_MAP.md`](SCRIPT_MAP.md)
- Script folder index (thin): [`../scripts/README.md`](../scripts/README.md)
- Reporting output paths: [`../reports/README.md`](../reports/README.md) and [`../reports/out/README.md`](../reports/out/README.md)

**Core package (library):** [`../src/origenlab_email_pipeline/core/`](../src/origenlab_email_pipeline/core/) holds shared infrastructure implementations (`core.safety`, `core.step_runner`, `core.reports_out`, `core.research_automation`, real `core.mart` / `core.outbound` builders). The former Stage 2A+ re-export facade layer (`core.config`, `core.db`, `core.gmail.*`, `core.leads.*`, `core.suppliers.*`, facade twins of root modules) was removed in the 2026-08 commercial platform reset: import implementations from the top-level `origenlab_email_pipeline` modules (e.g. `from origenlab_email_pipeline.candidate_export_gate import …`). Smoke tests: [`../tests/test_core_import_surface.py`](../tests/test_core_import_surface.py).

## Deep references by domain

- Operations: [`RUNBOOK.md`](RUNBOOK.md#m-eprun-path), [publish-safe QA / gate](RUNBOOK.md#m-eprun-publish-qa), [cold outreach shared export gate](RUNBOOK.md#m-eprun-cold-export-gate)
- Reporting: [`REPORTING.md`](REPORTING.md#m-eprep-mail), [QA vs artefactos leads](REPORTING.md#m-eprep-leads-qa), [`REPORT_SCOPE_CLIENT.md`](REPORT_SCOPE_CLIENT.md), [`reporting/OUTPUTS_OVERVIEW.md`](reporting/OUTPUTS_OVERVIEW.md)
- Pipeline architecture:
  - [`pipeline/PACKAGE_DOMAINS.md`](pipeline/PACKAGE_DOMAINS.md) — `origenlab_email_pipeline` logical map (Phase 0)
  - [`pipeline/BUSINESS_MART.md`](pipeline/BUSINESS_MART.md)
  - [`pipeline/BUSINESS_FILTERING.md`](pipeline/BUSINESS_FILTERING.md)
  - [`pipeline/SCHEMA_OWNERSHIP.md`](pipeline/SCHEMA_OWNERSHIP.md#m-schema-orchestrated)
  - [`pipeline/SCHEMA_CLASSIFICATION_MODEL.md`](pipeline/SCHEMA_CLASSIFICATION_MODEL.md) — evidence / safety / workflow layers; send-gate rule
  - [`pipeline/CURRENT_SAFETY_CHECKPOINT.md`](pipeline/CURRENT_SAFETY_CHECKPOINT.md) — pause marker; safe outreach loop; do-not-next list
  - [`pipeline/POST_SEND_SAFE_LOOP.md`](pipeline/POST_SEND_SAFE_LOOP.md) — post-send / NDR safe loop (operator commands)
  - [`pipeline/INSTITUTION_ALIAS_POLICY.md`](pipeline/INSTITUTION_ALIAS_POLICY.md) — alias decision checkpoint (no production table yet; explorer-only)
  - [`pipeline/INSTITUTION_EXPLORER_SPEC.md`](pipeline/INSTITUTION_EXPLORER_SPEC.md) — read-only institution explorer product spec (not implemented)
  - Read-only QA: [`../scripts/qa/audit_institution_grouping.py`](../scripts/qa/audit_institution_grouping.py) — domain/org institution grouping audit (reports under `reports/out/active/current/`)
  - [`pipeline/PHASE2_EMAIL_PIPELINE.md`](pipeline/PHASE2_EMAIL_PIPELINE.md)
- Leads:
  - [`leads/LEAD_PIPELINE.md`](leads/LEAD_PIPELINE.md)
  - [`leads/LEAD_ACCOUNT_LAYER.md`](leads/LEAD_ACCOUNT_LAYER.md)
  - [`leads/CHILE_LEAD_SOURCES.md`](leads/CHILE_LEAD_SOURCES.md)
- ML: [`ml/AI_ML_IMPLEMENTED_SUMMARY.md`](ml/AI_ML_IMPLEMENTED_SUMMARY.md) (includes ML options reference + LLM prompt appendix)
- **Tatiana commercial drafting (OrigenLab scope):** human-in-the-loop draft suggestions from archive-derived examples — not a sender, not CRM automation. **Boundary (vs production outbound):** [`TATIANA_LAB_BOUNDARY.md`](TATIANA_LAB_BOUNDARY.md).
  - Overview + eval: [`dataset/TATIANA_DRAFTING_COPILOT.md`](dataset/TATIANA_DRAFTING_COPILOT.md)
  - **Pilot batches** (current operational path): [`dataset/TATIANA_PILOT_WORKFLOW.md`](dataset/TATIANA_PILOT_WORKFLOW.md)
  - **OrigenLab drafting context** (`--origenlab`, web data as facts): [`dataset/ORIGENLAB_COMMERCIAL_DRAFTING_CONTEXT.md`](dataset/ORIGENLAB_COMMERCIAL_DRAFTING_CONTEXT.md)
  - Manual eval rubric: [`dataset/TATIANA_EVAL_REVIEW.md`](dataset/TATIANA_EVAL_REVIEW.md)

## Generated / Snapshot Docs

Do not hand-edit these unless the process changes:

- [`generated/CONTACT_READINESS_AUDIT.md`](generated/CONTACT_READINESS_AUDIT.md) (generated by [`scripts/leads/advanced/audit_contact_readiness.py`](../scripts/leads/advanced/audit_contact_readiness.py))
- [`generated/operational_trust_scorecard.md`](generated/operational_trust_scorecard.md) (generated by [`scripts/qa/audit_operational_trust.py`](../scripts/qa/audit_operational_trust.py) or [`publish_gate.py`](../scripts/qa/publish_gate.py); JSON hermano en [`reports/out/active/operational_trust_scorecard.json`](../reports/out/README.md))
- [`generated/DEEP_RESEARCH_RECONCILIATION.md`](generated/DEEP_RESEARCH_RECONCILIATION.md) (generated by [`scripts/leads/campaigns/reconcile_deepresearch_50_with_current_cohort.py`](../scripts/leads/campaigns/reconcile_deepresearch_50_with_current_cohort.py); payload DR50 versionado: [`scripts/leads/README.md`](../scripts/leads/README.md#m-leads-dr50-payload))
- [`generated/READY8_AND_TOP20_REPORTING_PLAN.md`](generated/READY8_AND_TOP20_REPORTING_PLAN.md) (cohort-specific generated planning artifact; [`apply_ready8_contact_patch.py`](../scripts/leads/campaigns/apply_ready8_contact_patch.py) — **`--apply`** required to regenerate)
- [`generated/AI_READINESS_AUDIT.md`](generated/AI_READINESS_AUDIT.md) (machine-specific environment snapshot)
- [`generated/PHASE2_1_VALIDATION.md`](generated/PHASE2_1_VALIDATION.md) and [`generated/PHASE2_2_VALIDATION.md`](generated/PHASE2_2_VALIDATION.md) (dated validation snapshots)

## Historical / Archived Context

These documents are kept for traceability, not as operational truth:

- Archive index + policy: [`ARCHIVE/README.md`](ARCHIVE/README.md).

## Policy

- [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)
- [`SECURITY.md`](SECURITY.md)

## Documentation Rules

- Every maintained doc should include: `Status`, `Owner`, and `Last reviewed`.
- If a doc is historical, include `Canonical replacement: <path>`.
- Prefer relative paths and env vars over machine-specific absolute paths.
