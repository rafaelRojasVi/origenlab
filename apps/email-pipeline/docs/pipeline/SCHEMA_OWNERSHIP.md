# SQLite schema ownership

Canonical map of **who defines DDL**, **who ALTERs**, **who rebuilds row data**, and **whether ownership is clean or split**. For ordering when applying multiple layers, see `migrate_sqlite_schema` in [`src/origenlab_email_pipeline/sqlite_migrate.py`](../../src/origenlab_email_pipeline/sqlite_migrate.py).

Outbound lane/source-of-truth usage guidance (operator model, not DDL ownership) lives in [`../OUTBOUND_SOURCE_OF_TRUTH.md`](../OUTBOUND_SOURCE_OF_TRUTH.md). **Classification layers and send gates:** [`SCHEMA_CLASSIFICATION_MODEL.md`](SCHEMA_CLASSIFICATION_MODEL.md).

<a id="m-schema-orchestrated"></a>
## Orchestrated order (optional entrypoint)

`migrate_sqlite_schema(conn)` runs, in order:

1. `init_schema` — archive + mart DDL/migrations + pipeline meta (see [`db.py`](../../src/origenlab_email_pipeline/db.py)).
2. `ensure_leads_tables(..., refresh_view=False)` — leads DDL/migrations (+ optional backfill via `leads_backfill_norms`).
3. `ensure_lead_account_tables(..., refresh_view=False)` — account DDL/migrations.
4. `refresh_lead_match_summary_view` **once** (avoids double view rebuild).

Callers may still use `ensure_leads_tables()` / `ensure_lead_account_tables()` alone; defaults preserve prior behavior (backfill + view refresh).

**Phase 2 adoption:** [`build_business_mart.py`](../../scripts/mart/build_business_mart.py), [`match_leads_to_mart.py`](../../scripts/leads/match_leads_to_mart.py), [`build_lead_account_rollup.py`](../../scripts/leads/advanced/build_lead_account_rollup.py), and [`match_lead_accounts_to_existing_orgs.py`](../../scripts/leads/advanced/match_lead_accounts_to_existing_orgs.py) call `migrate_sqlite_schema` with the appropriate `SchemaLayer` set. Operational one-shot: [`scripts/pipeline/run_aligned_stack.sh`](../../scripts/pipeline/run_aligned_stack.sh).

---

<a id="m-schema-archive"></a>
## Archive layer

| Object | DDL owner | ALTER / extra DDL | Indexes | Data rebuild | Ownership |
|--------|-----------|-------------------|---------|----------------|-----------|
| `emails` | [`db.py`](../../src/origenlab_email_pipeline/db.py) `SCHEMA_SQL` + `init_schema` | `init_schema` loop (body/attachment columns) | `SCHEMA_SQL` + `idx_emails_body_source_type` in `init_schema` (perf; requires `body_source_type` column) | [`backfill_phase2_2_text_fields.py`](../../scripts/validation/backfill_phase2_2_text_fields.py) | **Clean** |
| `attachments` | `db.py` `SCHEMA_SQL` / `init_schema` | — | `db.py` | ingest | **Clean** |
| `attachment_extracts` | `db.py` `SCHEMA_SQL` / `init_schema` | — | `db.py` | extraction scripts | **Clean** |

---

<a id="m-schema-mart"></a>
## Mart layer (derived, rebuildable)

| Object | DDL owner | ALTER | Indexes | Data rebuild | Ownership |
|--------|-----------|-------|---------|--------------|-----------|
| `contact_master` | [`business_mart_schema.py`](../../src/origenlab_email_pipeline/business_mart_schema.py) | — | same string | [`build_business_mart.py`](../../scripts/mart/build_business_mart.py) DELETE+INSERT | **Clean DDL**; **data** = mart job |
| `organization_master` | `business_mart_schema.py` | — | same | mart job | **Clean** |
| `document_master` | `business_mart_schema.py` (includes preview columns in CREATE) | [`db.py`](../../src/origenlab_email_pipeline/db.py) `init_schema` (`extracted_preview_*`, `preview_quality_score`) | `business_mart_schema.py` | mart job | **Split** (CREATE vs ALTER for same columns on old DBs) |
| `opportunity_signals` | `business_mart_schema.py` | — | same | mart job | **Clean** |

Mart **DDL** is applied inside `init_schema` via `BUSINESS_MART_SCHEMA_SQL`. Mart **rows** are not owned by schema modules.

**Plausible-date policy for mart timelines** (raw `emails` unchanged): see [`MART_FRESHNESS.md`](./MART_FRESHNESS.md).

---

<a id="m-schema-pipeline-meta"></a>
## Pipeline metadata

| Object | DDL owner | ALTER | Indexes | Data | Ownership |
|--------|-----------|-------|---------|------|-----------|
| `pipeline_run` | [`pipeline_meta_schema.py`](../../src/origenlab_email_pipeline/pipeline_meta_schema.py) | — | same | [`pipeline_run_recorder.py`](../../src/origenlab_email_pipeline/pipeline_run_recorder.py) | **Clean** |
| `pipeline_kv` | `pipeline_meta_schema.py` | — | — | `pipeline_run_recorder` | **Clean** |

Also invoked from `init_schema`, `ensure_leads_tables`, `ensure_lead_account_tables`, and recorder helpers.

---

<a id="m-schema-leads"></a>
## Leads layer

| Object | DDL owner | ALTER / deferred indexes | Backfill | View refresh | Ownership |
|--------|-----------|--------------------------|----------|--------------|-----------|
| `external_leads_raw` | [`leads_schema.py`](../../src/origenlab_email_pipeline/leads_schema.py) `LEAD_SCHEMA_SQL` | — | — | — | **Clean** |
| `lead_master` | `leads_schema.py` | `_migrate_lead_master_norm_columns`; legacy buyer/fit/lab columns loop; norm indexes after ALTER; **upstream lifecycle** columns `upstream_sync_state` (default `active`), `upstream_retired_at`, `upstream_retired_reason`; **UNIQUE** `(source_name, source_record_id)` index `uidx_lead_master_source_name_record` via `finalize_lead_master_source_keys` ([`lead_master_keys.py`](../../src/origenlab_email_pipeline/lead_master_keys.py)) | `backfill_canonical_source_record_ids` + `backfill_lead_master_norm_columns`; conflict upsert in [`lead_normalize_upsert.py`](../../src/origenlab_email_pipeline/lead_normalize_upsert.py); reactivation of `upstream_sync_state` on upsert; soft retire via [`lead_upstream_reconcile.py`](../../src/origenlab_email_pipeline/lead_upstream_reconcile.py) + [`scripts/leads/reconcile_lead_upstream.py`](../../scripts/leads/reconcile_lead_upstream.py) | via `ensure_leads_tables` / `bi_views` | **Clean module**; use `ensure_leads_tables_ddl_base` + audit/dedupe when duplicates block index creation |
| `lead_upstream_reconcile_log` | `leads_schema.py` | `CREATE TABLE` in `LEAD_SCHEMA_SQL` | append-only on `reconcile_lead_upstream.py --apply` | — | **Audit** for retire events |
| `lead_matches_existing_orgs` | `leads_schema.py` | `_migrate_lead_matches_org_columns`; deferred `pipeline_run_id` index | cleared by matching code | — | **Clean**; read-side “best match per lead” for exports is centralized in [`lead_export_queries.py`](../../src/origenlab_email_pipeline/lead_export_queries.py) (deterministic `MIN(id)` per `lead_id`). |
| `lead_matches_existing_contacts` | `leads_schema.py` | — (new table) | cleared by matching | — | **Clean** |
| `lead_outreach_enrichment` | `leads_schema.py` | — | import/merge scripts | — | **Clean** |

`ensure_leads_tables_ddl_base` holds DDL+migrations+secondary indexes without the lead source-key finalize step. `ensure_leads_tables_ddl` calls base then `finalize_lead_master_source_keys` (canonical `source_record_id` + UNIQUE index). `ensure_leads_tables` wraps full DDL, norm backfill, and view (defaults unchanged). Read-only duplicate audit: [`scripts/leads/audit_lead_master_duplicates.py`](../../scripts/leads/audit_lead_master_duplicates.py); merge: [`scripts/leads/dedupe_lead_master.py`](../../scripts/leads/dedupe_lead_master.py). Raw-vs-master soft retire: [`scripts/leads/reconcile_lead_upstream.py`](../../scripts/leads/reconcile_lead_upstream.py). Operational stack manifest (not DB): [`run_leads_operational_stack.sh`](../../scripts/leads/run_leads_operational_stack.sh) writes [`reports/out/active/operational_stack_last_run.json`](../../reports/out/README.md) plus a per-run copy under `reports/out/active/operational_run_manifests/` (see [`lead_provenance.py`](../../src/origenlab_email_pipeline/lead_provenance.py)).

---

<a id="m-schema-lead-accounts"></a>
## Lead accounts layer

| Object | DDL owner | ALTER / deferred index | Data rebuild | View | Ownership |
|--------|-----------|------------------------|--------------|------|-----------|
| `lead_account_master` | [`lead_accounts_schema.py`](../../src/origenlab_email_pipeline/lead_accounts_schema.py) | — | [`build_lead_account_rollup.py`](../../scripts/leads/advanced/build_lead_account_rollup.py) | optional via `refresh_view` | **Clean** |
| `lead_account_aliases` | same | — | rollup | — | **Clean** |
| `lead_account_membership` | same | — | rollup | — | **Clean** |
| `lead_account_matches_existing_orgs` | same | `ADD pipeline_run_id` + index after (legacy DBs) | [`match_lead_accounts_to_existing_orgs.py`](../../scripts/leads/advanced/match_lead_accounts_to_existing_orgs.py) | — | **Clean** (deferred index pattern) |
| `lead_account_overrides` | same | — | manual | — | **Clean** |

---

<a id="m-schema-views"></a>
## Views

| Object | Owner | Behavior |
|--------|-------|------------|
| `v_lead_match_summary` | [`bi_views.py`](../../src/origenlab_email_pipeline/bi_views.py) `refresh_lead_match_summary_view` | Prerequisites checked before DROP; transactional replace; returns status string. Core vs full SQL depends on account tables. Excludes `lead_master` rows with `upstream_sync_state = 'retired_no_raw'` (soft-retired missing raw). |
| `v_commercial_candidate_queue` | [`commercial/commercial_intel_schema.py`](../../src/origenlab_email_pipeline/commercial/commercial_intel_schema.py) `ensure_commercial_intel_tables` | Recreated idempotently; unions durable candidate tables for operational review queues. |

---

<a id="m-schema-commercial-intel"></a>
## Commercial intelligence layer (v1)

| Object | DDL owner | Data rebuild/ownership | Notes |
|--------|-----------|------------------------|-------|
| `commercial_email_signal_fact` | [`commercial/commercial_intel_schema.py`](../../src/origenlab_email_pipeline/commercial/commercial_intel_schema.py) | [`build_commercial_intel_v1.py`](../../scripts/commercial/build_commercial_intel_v1.py) rewrites selected email ids idempotently | Rebuildable evidence facts, linked to `emails.id`. |
| `commercial_org_signal_rollup` | `commercial/commercial_intel_schema.py` | `build_commercial_intel_v1.py` recomputes from facts | Rebuildable org-level evidence/suppression rollup. |
| `commercial_contact_signal_rollup` | `commercial/commercial_intel_schema.py` | `build_commercial_intel_v1.py` recomputes from facts | Rebuildable contact-level rollup. |
| `commercial_opportunity_fact` | `commercial/commercial_intel_schema.py` | `build_commercial_intel_v1.py` recomputes from org rollups | Rebuildable opportunity facts for candidate promotion. |
| `organization_candidate` | `commercial/commercial_intel_schema.py` | Durable UPSERT target from builder + manual review | Human-facing durable state. |
| `contact_candidate` | `commercial/commercial_intel_schema.py` | Durable UPSERT target from builder + manual review | Human-facing durable state. |
| `opportunity_candidate` | `commercial/commercial_intel_schema.py` | Durable UPSERT target from builder + manual review | Human-facing durable state. |
| `candidate_review_event` | `commercial/commercial_intel_schema.py` | Append audit events from builder/manual ops | Durable review audit trail. |
| `candidate_manual_override` | `commercial/commercial_intel_schema.py` | Manual operational input | Durable overrides applied by builder sync. |

Commercial schema is added through `migrate_sqlite_schema(..., layers={SchemaLayer.COMMERCIAL_INTEL})`.

### Commercial read models (PR2–PR4, audit / break-glass)

These table families are separate from commercial intel v1 and are **not** part of the default `migrate_sqlite_schema` stack. Builders default to dry-run; production `--apply` requires explicit approval plus the fingerprint/stale-plan gates documented in the linked audit docs.

| Object family | DDL / data owner | Rebuild command | Notes |
|---------------|------------------|-----------------|-------|
| `commercial_identity_*` | [`commercial_identity/`](../../src/origenlab_email_pipeline/commercial_identity/) | [`build_commercial_identity_read_model.py`](../../scripts/commercial/build_commercial_identity_read_model.py) | PR2 deterministic account/contact identity read model; transaction contract B. |
| `commercial_opportunity_*` | [`commercial_opportunity/`](../../src/origenlab_email_pipeline/commercial_opportunity/) | [`build_commercial_opportunity_read_model.py`](../../scripts/commercial/build_commercial_opportunity_read_model.py) | PR3 opportunity-stage read model; gates on PR2 identity fingerprint when applying. |
| `commercial_procurement_*` | [`commercial_procurement/`](../../src/origenlab_email_pipeline/commercial_procurement/) | [`build_commercial_procurement_read_model.py`](../../scripts/commercial/build_commercial_procurement_read_model.py) | PR4 procurement planner/persistence; dry-run uses read-only SQLite, apply uses stale-plan checks. |

Design audits: [`COMMERCIAL_IDENTITY_READ_MODEL_PR2.md`](../audits/COMMERCIAL_IDENTITY_READ_MODEL_PR2.md), [`COMMERCIAL_OPPORTUNITY_STAGE_READ_MODEL_PR3.md`](../audits/COMMERCIAL_OPPORTUNITY_STAGE_READ_MODEL_PR3.md), [`COMMERCIAL_PROCUREMENT_LINK_READ_MODEL_PR4.md`](../audits/COMMERCIAL_PROCUREMENT_LINK_READ_MODEL_PR4.md).

---

<a id="m-schema-operator-sidecar"></a>
## Operator sidecar (durable, lazy DDL)

Small tables for **human / operator state** that must not live on raw `emails`. DDL is applied via `CREATE TABLE IF NOT EXISTS` when callers invoke `ensure_*`; they are **not** part of the default `migrate_sqlite_schema` stack (same pattern as `contact_email_suppression`).

| Object | DDL / helpers | Data | Notes |
|--------|----------------|------|--------|
| `outreach_contact_state` | [`outreach_contact_state.py`](../../src/origenlab_email_pipeline/outreach_contact_state.py) `ensure_outreach_contact_state_table` | Upsert/fetch helpers | Contact-level **operator** outreach status (`not_contacted`, `contacted`, `replied`, `snoozed`). **`contacted`**, **`replied`**, and **`snoozed`** all **block** cold-outreach export eligibility via [`candidate_export_gate.py`](../../src/origenlab_email_pipeline/candidate_export_gate.py) (shared with the **`contact_master`** export script). Optional `lead_id` is a plain INTEGER (no FK) so the table can exist before `lead_master`. Hard excludes remain [`contact_email_suppression.py`](../../src/origenlab_email_pipeline/contact_email_suppression.py). **Inferred “we contacted them” from raw mail should use the OrigenLab mailbox / `@origenlab.cl`, not LabDelivery (`labdelivery.cl`)—see module docstring. |

**Export eligibility (code policy, not a table):** [`candidate_export_gate.py`](../../src/origenlab_email_pipeline/candidate_export_gate.py) centralizes rules for **candidate** lists (`compute_next_marketing_recipients` / Streamlit Cola, archive-outreach queue audit/export path, and `export_marketing_from_contact_master`). The **`contact_master`** path enables **stricter marketing-email noise** than **`lead_master`** (same module, different flag). Sent-history blocking and current outreach memory are anchored to the current sender mailbox (`contacto@origenlab.cl`) plus operator sidecars; this policy surface complements operator tables above but is not DDL-owned schema. Read-only QA: [`scripts/qa/export_candidate_audit.py`](../../scripts/qa/export_candidate_audit.py). See [ARCHITECTURE § Cold outreach export eligibility](../ARCHITECTURE.md#m-eparch-export-gate), [RUNBOOK § Cold outreach export eligibility](../RUNBOOK.md#m-eprun-cold-export-gate), and [`../OUTBOUND_SOURCE_OF_TRUTH.md`](../OUTBOUND_SOURCE_OF_TRUTH.md).

---

<a id="m-schema-summary"></a>
## Clean vs split (summary)

- **Split:** `document_master` (CREATE in mart schema + ALTER in `db.py` for preview columns on old DBs).
- **Split by lifecycle:** commercial v1 separates rebuildable evidence tables from durable review/candidate tables.
- **Otherwise:** ownership aligns with one primary module per layer; operational DELETE/INSERT lives in named scripts, not schema modules.
