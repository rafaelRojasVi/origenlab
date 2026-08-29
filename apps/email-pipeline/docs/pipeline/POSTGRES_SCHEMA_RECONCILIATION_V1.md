# PostgreSQL Schema Reconciliation V1

Status: **documentation / planning only**  
Owner: email-pipeline-maintainers  
Last reviewed: 2026-04-19  
Companion: [`POSTGRES_SCHEMA_TARGET_V1.md`](POSTGRES_SCHEMA_TARGET_V1.md)

> **Status: historical planning document.** Written before the Alembic migration chain
> existed. Current PostgreSQL/Alembic truth is defined by the migration chain in
> `apps/email-pipeline/alembic/versions/` and
> [`docs/architecture/CURRENT_SYSTEM_TRUTH.md`](../../../../docs/architecture/CURRENT_SYSTEM_TRUTH.md).
> The "no Alembic migrations exist yet" statements below describe the state at the time
> this document was written (2026-04-19), not the current state.

## 1. Purpose

This document **reconciles** the **current SQLite DDL** (as defined in Python source) with the **target PostgreSQL layout** in `POSTGRES_SCHEMA_TARGET_V1.md`. It is the column-accurate basis for a **future Alembic** revision chain.

- **No runtime code** was changed while producing this document.
- **No Alembic migrations** exist yet.
- **No SQLite schema files** were modified.

The goal is to surface **every column**, **constraint**, **index**, and **view dependency** so migration authors can avoid silent type drift and data loss.

---

## 2. Method

| Activity | Detail |
|----------|--------|
| Sources read | `db.py`, `business_mart_schema.py`, `pipeline_meta_schema.py`, `leads_schema.py`, `lead_master_keys.py`, `lead_accounts_schema.py`, `supplier_schema.py`, `commercial/commercial_intel_schema.py`, `bi_views.py`, `contact_email_suppression.py`, `contact_domain_suppression.py`, `outreach_contact_state.py`, `sqlite_migrate.py` |
| Extraction | DDL taken verbatim from `CREATE TABLE` / `CREATE INDEX` / `CREATE UNIQUE INDEX` strings; **ALTER TABLE** additions noted from `init_schema`, `ensure_leads_tables_ddl_base`, `ensure_lead_account_tables`, and migration helpers. |
| Views | `v_commercial_candidate_queue` from `commercial_intel_schema.py`; `v_lead_match_summary` from `bi_views.py` (core vs full variants). |
| Target names | Follow `POSTGRES_SCHEMA_TARGET_V1.md` (e.g. `lead_master` → `leads.lead`, `supplier_master` → `supplier.supplier`). |

---

## 3. Current SQLite table definitions

Below: **owner file**, columns with SQLite types, PK/UNIQUE/FK, indexes, and **ALTER-added** columns (not in original `CREATE TABLE`).

### 3.1 `emails`

| Item | Detail |
|------|--------|
| **Owner** | `db.py` (`SCHEMA_SQL` + `init_schema` ALTER loop) |
| **PK** | `id INTEGER PRIMARY KEY AUTOINCREMENT` |
| **Columns (CREATE)** | `source_file TEXT NOT NULL`, `folder`, `message_id`, `subject`, `sender`, `recipients`, `date_raw`, `date_iso`, `body` |
| **ALTER-added (db.init_schema)** | `body_html`, `body_text_raw`, `body_text_clean`, `body_source_type`, `body_has_plain INTEGER`, `body_has_html INTEGER`, `full_body_clean`, `top_reply_clean`, `attachment_count INTEGER`, `has_attachments INTEGER` — all `TEXT` or `INTEGER` per loop |
| **Indexes** | `idx_emails_message_id(message_id)`, `idx_emails_date_iso(date_iso)`, `idx_emails_body_source_type(body_source_type)` |

### 3.2 `attachments`

| Item | Detail |
|------|--------|
| **Owner** | `db.py` |
| **PK** | `id INTEGER PRIMARY KEY AUTOINCREMENT` |
| **Columns** | `email_id INTEGER NOT NULL`, `part_index INTEGER NOT NULL`, `filename`, `content_type`, `content_disposition`, `size_bytes`, `content_id`, `is_inline`, `sha256`, `saved_path`, `created_at` (all TEXT/INTEGER as in DDL) |
| **FK** | `FOREIGN KEY(email_id) REFERENCES emails(id) ON DELETE CASCADE` |
| **Indexes** | `idx_attachments_email_id(email_id)`, `idx_attachments_sha256(sha256)` |

### 3.3 `attachment_extracts`

| Item | Detail |
|------|--------|
| **Owner** | `db.py` |
| **PK** | `id INTEGER PRIMARY KEY AUTOINCREMENT` |
| **UNIQUE** | `attachment_id INTEGER NOT NULL UNIQUE` |
| **Columns** | `extract_status NOT NULL`, `extract_method NOT NULL`, `text_preview`, `text_truncated`, `char_count`, `page_count`, `sheet_count`, `detected_doc_type`, `has_quote_terms`, `has_invoice_terms`, `has_price_list_terms`, `has_purchase_terms`, `error_message`, `created_at` |
| **FK** | `FOREIGN KEY(attachment_id) REFERENCES attachments(id) ON DELETE CASCADE` |
| **Indexes** | `idx_attachment_extracts_attachment_id`, `idx_attachment_extracts_doc_type`, `idx_attachment_extracts_status_method` |

### 3.4 `pipeline_run`

| Item | Detail |
|------|--------|
| **Owner** | `pipeline_meta_schema.py` |
| **PK** | `id INTEGER PRIMARY KEY AUTOINCREMENT` |
| **Columns** | `started_at TEXT NOT NULL`, `finished_at`, `script_name TEXT NOT NULL`, `argv_json`, `git_describe`, `notes` |
| **Indexes** | `idx_pipeline_run_started(started_at DESC)`, `idx_pipeline_run_script(script_name)` |

### 3.5 `pipeline_kv`

| Item | Detail |
|------|--------|
| **Owner** | `pipeline_meta_schema.py` |
| **PK** | `k TEXT PRIMARY KEY` |
| **Columns** | `v TEXT`, `updated_at TEXT NOT NULL` |

### 3.6 `contact_master`

| Item | Detail |
|------|--------|
| **Owner** | `business_mart_schema.py` |
| **PK** | `email TEXT PRIMARY KEY` |
| **Columns** | `contact_name_best`, `domain`, `organization_name_guess`, `organization_type_guess`, `first_seen_at`, `last_seen_at`, `total_emails`, `inbound_emails`, `outbound_emails`, `quote_email_count`, `invoice_email_count`, `purchase_email_count`, `business_doc_email_count`, `quote_doc_count`, `invoice_doc_count`, `top_equipment_tags`, `confidence_score REAL` |
| **Indexes** | `idx_contact_master_domain(domain)`, `idx_contact_master_last_seen(last_seen_at)` |

### 3.7 `organization_master`

| Item | Detail |
|------|--------|
| **Owner** | `business_mart_schema.py` |
| **PK** | `domain TEXT PRIMARY KEY` |
| **Columns** | `organization_name_guess`, `organization_type_guess`, `first_seen_at`, `last_seen_at`, `total_emails`, `total_contacts`, quote/invoice/purchase/business_doc counts, `quote_doc_count`, `invoice_doc_count`, `top_equipment_tags`, `key_contacts` |
| **Indexes** | `idx_org_master_last_seen(last_seen_at)` |

### 3.8 `document_master`

| Item | Detail |
|------|--------|
| **Owner** | `business_mart_schema.py` + `db.init_schema` ALTER |
| **PK** | `attachment_id INTEGER PRIMARY KEY` |
| **Columns (CREATE)** | `email_id`, `filename`, `extension`, `sender_email`, `sender_domain`, `recipient_domain`, `sent_at`, `doc_type`, `has_quote_terms`, `has_invoice_terms`, `has_purchase_terms`, `has_price_list_terms`, `equipment_tags` |
| **ALTER-added (db.init_schema)** | `extracted_preview_raw`, `extracted_preview_clean`, `preview_quality_score REAL` |
| **FK** | `FOREIGN KEY(email_id) REFERENCES emails(id) ON DELETE CASCADE` |
| **Indexes** | sender_domain, recipient_domain, sent_at, doc_type |

### 3.9 `opportunity_signals`

| Item | Detail |
|------|--------|
| **Owner** | `business_mart_schema.py` |
| **PK** | `id INTEGER PRIMARY KEY AUTOINCREMENT` |
| **Columns** | `signal_type NOT NULL`, `entity_kind NOT NULL`, `entity_key NOT NULL`, `email_id`, `attachment_id`, `score REAL`, `details_json`, `created_at` |
| **Indexes** | `(entity_kind, entity_key)`, `signal_type` |

### 3.10 `external_leads_raw`

| Item | Detail |
|------|--------|
| **Owner** | `leads_schema.py` |
| **PK** | `id INTEGER PRIMARY KEY AUTOINCREMENT` |
| **UNIQUE** | `(source_name, source_record_id)` |
| **Columns** | `source_name NOT NULL`, `source_record_id NOT NULL`, `fetched_at NOT NULL`, `raw_json`, `source_url` |
| **Indexes** | `(source_name, source_record_id)`, `(source_name, fetched_at)` |

### 3.11 `lead_master`

| Item | Detail |
|------|--------|
| **Owner** | `leads_schema.py` + ALTER migrations + `lead_master_keys.py` (unique index) |
| **PK** | `id INTEGER PRIMARY KEY AUTOINCREMENT` |
| **Columns (CREATE)** | `source_name`, `source_type`, `source_record_id`, `source_url`, `org_name`, `contact_name`, `email`, `phone`, `website`, `domain`, `region`, `city`, `lead_type`, `organization_type_guess`, `evidence_summary`, `first_seen_at`, `last_seen_at`, `priority_score`, `priority_reason`, `status DEFAULT 'nuevo'`, `review_owner`, `last_reviewed_at`, `next_action`, `notes` |
| **ALTER-added (ensure_leads_tables_ddl_base)** | `email_norm`, `domain_norm`, `org_name_norm`; `buyer_kind`, `lab_context_score`, `lab_context_tags`, `fit_bucket`, `upstream_sync_state DEFAULT 'active'`, `upstream_retired_at`, `upstream_retired_reason` |
| **UNIQUE index (runtime)** | `uidx_lead_master_source_name_record ON (source_name, source_record_id)` — created by `ensure_lead_master_source_unique_index` after canonical backfill |
| **Indexes** | source, domain, status, priority DESC, last_seen; post-migration: `email_norm`, `domain_norm`, `org_name_norm` |

### 3.12 `lead_matches_existing_orgs`

| Item | Detail |
|------|--------|
| **Owner** | `leads_schema.py` + `_migrate_lead_matches_org_columns` |
| **PK** | `id INTEGER PRIMARY KEY AUTOINCREMENT` |
| **Columns (CREATE)** | `lead_id NOT NULL`, `matched_domain NOT NULL`, `matched_org_name`, `match_type NOT NULL`, `confidence_score NOT NULL`, `already_in_archive_flag NOT NULL DEFAULT 1` |
| **ALTER-added** | `pipeline_run_id`, `evidence_json` |
| **FK** | `lead_id → lead_master(id) ON DELETE CASCADE`, `pipeline_run_id → pipeline_run(id)` |
| **Indexes** | `lead_id`, `matched_domain`, `pipeline_run_id` |

### 3.13 `lead_matches_existing_contacts`

| Item | Detail |
|------|--------|
| **Owner** | `leads_schema.py` |
| **PK** | `id INTEGER PRIMARY KEY AUTOINCREMENT` |
| **Columns** | `lead_id NOT NULL`, `matched_contact_email NOT NULL`, `matched_contact_name`, `matched_domain`, `match_type NOT NULL`, `confidence_score NOT NULL`, `already_in_archive_flag NOT NULL DEFAULT 1`, `evidence_json`, `pipeline_run_id`, `created_at NOT NULL` |
| **FK** | `lead_id → lead_master`, `pipeline_run_id → pipeline_run` |
| **Indexes** | lead_id, matched_contact_email, matched_domain, pipeline_run_id |

### 3.14 `lead_outreach_enrichment`

| Item | Detail |
|------|--------|
| **Owner** | `leads_schema.py` |
| **PK** | `lead_id INTEGER PRIMARY KEY` |
| **Columns** | `enrichment_json NOT NULL`, `source_file`, `updated_at NOT NULL` |
| **FK** | `lead_id → lead_master(id) ON DELETE CASCADE` |

### 3.15 `lead_contact_research`

| Item | Detail |
|------|--------|
| **Owner** | `leads_schema.py` |
| **PK** | `lead_id INTEGER PRIMARY KEY` |
| **Columns** | `contact_research_status NOT NULL DEFAULT 'nuevo'`, `resolved_domain`, `resolved_contact_name`, `resolved_contact_email`, `contact_source`, `contact_research_notes`, `updated_at NOT NULL`, `updated_by` |
| **FK** | `lead_id → lead_master(id) ON DELETE CASCADE` |
| **Indexes** | `contact_research_status` |

### 3.16 `lead_upstream_reconcile_log`

| Item | Detail |
|------|--------|
| **Owner** | `leads_schema.py` |
| **PK** | `id INTEGER PRIMARY KEY AUTOINCREMENT` |
| **Columns** | `run_at NOT NULL`, `dry_run NOT NULL`, `lead_id NOT NULL`, `source_name NOT NULL`, `canonical_source_record_id NOT NULL`, `action NOT NULL`, `detail` |
| **FK** | `lead_id → lead_master(id)` |
| **Indexes** | `run_at`, `lead_id` |

### 3.17 `lead_account_master`

| Item | Detail |
|------|--------|
| **Owner** | `lead_accounts_schema.py` |
| **PK** | `id INTEGER PRIMARY KEY AUTOINCREMENT` |
| **UNIQUE** | `account_dedupe_key NOT NULL` |
| **Columns** | `canonical_name NOT NULL`, `normalized_name NOT NULL`, `primary_domain`, `official_website`, `org_type`, `region`, `city`, `country NOT NULL DEFAULT 'CL'`, `source_count`, `lead_count`, `first_seen_at`, `last_seen_at`, `quality_status`, `created_at NOT NULL`, `updated_at NOT NULL` |
| **Indexes** | normalized_name, primary_domain, quality_status, lead_count DESC |

### 3.18 `lead_account_aliases`

| Item | Detail |
|------|--------|
| **Owner** | `lead_accounts_schema.py` |
| **PK** | `id INTEGER PRIMARY KEY AUTOINCREMENT` |
| **UNIQUE** | `(lead_account_id, normalized_alias)` |
| **Columns** | `lead_account_id NOT NULL`, `alias_name NOT NULL`, `normalized_alias NOT NULL`, `alias_type`, `source_name`, `confidence REAL`, `created_at NOT NULL` |
| **FK** | `lead_account_id → lead_account_master(id) ON DELETE CASCADE` |

### 3.19 `lead_account_membership`

| Item | Detail |
|------|--------|
| **Owner** | `lead_accounts_schema.py` |
| **PK** | `id INTEGER PRIMARY KEY AUTOINCREMENT` |
| **UNIQUE** | `(lead_id, lead_account_id)` |
| **Columns** | `lead_id NOT NULL`, `lead_account_id NOT NULL`, `membership_method NOT NULL`, `confidence NOT NULL`, `is_primary NOT NULL DEFAULT 1`, `evidence_json`, `created_at NOT NULL` |
| **FK** | `lead_id → lead_master`, `lead_account_id → lead_account_master` |

### 3.20 `lead_account_matches_existing_orgs`

| Item | Detail |
|------|--------|
| **Owner** | `lead_accounts_schema.py` + ALTER |
| **PK** | `id INTEGER PRIMARY KEY AUTOINCREMENT` |
| **UNIQUE** | `(lead_account_id, organization_domain)` |
| **Columns (CREATE)** | `lead_account_id NOT NULL`, `organization_domain NOT NULL`, `match_method NOT NULL`, `confidence NOT NULL`, `evidence_json`, `review_status NOT NULL DEFAULT 'auto'`, `created_at NOT NULL` |
| **ALTER-added** | `pipeline_run_id` |
| **FK** | `lead_account_id → lead_account_master`, `pipeline_run_id → pipeline_run` |
| **Indexes** | lead_account_id, organization_domain, pipeline_run_id |

### 3.21 `lead_account_overrides`

| Item | Detail |
|------|--------|
| **Owner** | `lead_accounts_schema.py` |
| **PK** | `id INTEGER PRIMARY KEY AUTOINCREMENT` |
| **Columns** | `override_type NOT NULL`, `source_value`, `normalized_source_value`, `target_account_name`, `target_account_id`, `notes`, `is_active NOT NULL DEFAULT 1`, `created_at NOT NULL`, `updated_at NOT NULL` |
| **Indexes** | partial on `normalized_source_value WHERE is_active = 1`; `(override_type, is_active)` |

### 3.22 Commercial rebuildable tables

| Table | Owner | PK / notes |
|-------|-------|------------|
| `commercial_email_signal_fact` | `commercial_intel_schema.py` | PK `id`; UNIQUE `(email_id, signal_code, reason_code, contact_email, org_domain)`; FK `email_id → emails(id)`; columns include `source_file`, `sent_at`, sender/contact/org fields, `signal_kind`, `reason_code`, `reason_text`, scores, `rationale_json`, `run_id`, `created_at` |
| `commercial_org_signal_rollup` | same | PK `org_domain`; rollup counters + `is_suppressed`, `updated_at`, etc. |
| `commercial_contact_signal_rollup` | same | PK `contact_email` |
| `commercial_opportunity_fact` | same | PK `opportunity_key` |

### 3.23 Commercial durable tables

| Table | Owner | PK / UNIQUE |
|-------|-------|-------------|
| `organization_candidate` | `commercial_intel_schema.py` | PK `id`; UNIQUE `org_domain` |
| `contact_candidate` | same | PK `id`; UNIQUE `contact_email` |
| `opportunity_candidate` | same | PK `id`; UNIQUE `opportunity_key` |
| `candidate_review_event` | same | PK `id` |
| `candidate_manual_override` | same | PK `id`; UNIQUE `(entity_kind, entity_key, override_code, is_active)` |

### 3.24 Supplier tables

| Table | Owner | Notes |
|-------|-------|-------|
| `supplier_import_batch` | `supplier_schema.py` | PK `id`; columns `source_filename`, `file_sha256`, `imported_at`, JSON text fields, `resumen_note` |
| `supplier_master` | same | PK `id`; UNIQUE `domain_norm`; `is_exclusion`, timestamps |
| `supplier_evidence` | same | PK `id`; UNIQUE `(supplier_id, url)`; FKs to supplier + batch |
| `supplier_contact_channel` | same | UNIQUE `(supplier_id, channel_type, value_normalized)` |
| `supplier_priority_snapshot` | same | UNIQUE `(supplier_id, batch_id)` |
| `supplier_review_state` | same | PK `supplier_id` → `supplier_master` |

### 3.25 `contact_email_suppression`

| Item | Detail |
|------|--------|
| **Owner** | `contact_email_suppression.py` |
| **PK** | `email TEXT PRIMARY KEY` |
| **Columns** | `suppression_reason_code NOT NULL`, `suppression_reason_text`, `suppression_source`, `last_bounced_at`, `updated_at NOT NULL`, `updated_by` |
| **Indexes** | `suppression_reason_code` |

### 3.26 `contact_domain_suppression`

| Item | Detail |
|------|--------|
| **Owner** | `contact_domain_suppression.py` |
| **PK** | `domain_norm TEXT PRIMARY KEY` |
| **Columns** | `suppression_reason_text`, `updated_at NOT NULL`, `updated_by` |

### 3.27 `outreach_contact_state`

| Item | Detail |
|------|--------|
| **Owner** | `outreach_contact_state.py` |
| **PK** | `contact_email_norm TEXT PRIMARY KEY` |
| **Columns** | `state NOT NULL`, `first_contacted_at`, `last_contacted_at`, `source`, `notes`, `updated_at NOT NULL`, `updated_by`, `lead_id` (**no FK** in SQLite) |
| **Indexes** | `state`; partial `lead_id WHERE lead_id IS NOT NULL` |

---

## 4. Current SQLite views

### 4.1 `v_commercial_candidate_queue`

| Item | Detail |
|------|--------|
| **Source** | `commercial/commercial_intel_schema.py` (`VIEW_SQL`) |
| **Definition** | `UNION ALL` of three `SELECT`s from `organization_candidate`, `contact_candidate`, `opportunity_candidate` with shared column list including computed `reason_summary`. |
| **Dependencies** | Three candidate tables only. |
| **Postgres** | Recreate as **`CREATE OR REPLACE VIEW commercial.v_commercial_candidate_queue`** (or target name from §5). SQL is portable; verify string concat `||` and `TRIM` match Postgres. |

### 4.2 `v_lead_match_summary`

| Item | Detail |
|------|--------|
| **Source** | `bi_views.py` — two bodies: `VIEW_LEAD_MATCH_SUMMARY_CORE` vs `VIEW_LEAD_MATCH_SUMMARY_FULL` |
| **Dependencies** | `lead_master`, `lead_matches_existing_orgs`, `lead_matches_existing_contacts`; full variant adds `lead_account_membership`, `lead_account_master`. |
| **WHERE** | Dynamic `sql_upstream_active("LM")` from `lead_upstream_reconcile` — **must be ported** to equivalent Postgres predicate. |
| **Postgres** | Recreate after tables migrated; choose one view body matching whether account tables exist. **Redesign optional:** e.g. parameterized view vs two views if account optional — **needs-decision**. |

---

## 5. Target PostgreSQL column mapping

**Legend — Action:** `keep` | `rename` | `type-upgrade` | `convert-to-jsonb` | `split` | `derive` | `drop-later` | `needs-decision`

**Schema prefixes:** `archive.*`, `ops.*`, `mart.*`, `leads.*`, `commercial.*`, `outbound.*`, `supplier.*`, `reporting.*` per `POSTGRES_SCHEMA_TARGET_V1.md`.

### 5.1 `emails` → `archive.emails`

| SQLite column | SQLite type | Target column | Target type | Action | Notes |
|---------------|---------------|---------------|-------------|--------|-------|
| id | INTEGER PK | id | BIGSERIAL | type-upgrade | Surrogate |
| source_file | TEXT NOT NULL | source_file | TEXT NOT NULL | keep | |
| folder | TEXT | folder | TEXT | keep | |
| message_id | TEXT | message_id | TEXT | keep | |
| subject | TEXT | subject | TEXT | keep | |
| sender | TEXT | sender | TEXT | keep | |
| recipients | TEXT | recipients | TEXT | keep | |
| date_raw | TEXT | date_raw | TEXT | keep | Opaque |
| date_iso | TEXT | date_iso | TIMESTAMPTZ | type-upgrade | **Validate** parse; else TEXT + needs-decision |
| body | TEXT | body | TEXT | keep | Large objects: needs-decision (TOAST ok) |
| body_html … has_attachments | TEXT/INTEGER | same names | BOOLEAN for *_has_* ; TEXT otherwise | type-upgrade | Map INTEGER 0/1 → BOOLEAN |
| attachment_count | INTEGER | attachment_count | INTEGER | keep | |

### 5.2 `attachments` → `archive.attachments`

| SQLite column | Target | Action |
|---------------|--------|--------|
| id | BIGSERIAL PK | type-upgrade |
| email_id | BIGINT FK → archive.emails(id) | type-upgrade |
| part_index, filename, … | keep names; is_inline → BOOLEAN | type-upgrade |
| created_at TEXT | TIMESTAMPTZ | type-upgrade if ISO-safe |

### 5.3 `attachment_extracts` → `archive.attachment_extracts`

| SQLite column | Target | Action |
|---------------|--------|--------|
| id | BIGSERIAL | type-upgrade |
| attachment_id | BIGINT UNIQUE FK | type-upgrade |
| has_* INTEGER | BOOLEAN | type-upgrade |
| created_at | TIMESTAMPTZ | type-upgrade |

### 5.4 `pipeline_run` → `ops.pipeline_run`

| SQLite column | Target | Action |
|---------------|--------|--------|
| id | BIGSERIAL | type-upgrade |
| started_at, finished_at | TIMESTAMPTZ | type-upgrade |
| argv_json | JSONB | convert-to-jsonb | Validate JSON |
| script_name, git_describe, notes | TEXT | keep |

### 5.5 `pipeline_kv` → `ops.pipeline_kv`

| SQLite column | Target | Action |
|---------------|--------|--------|
| k | key TEXT or k TEXT | rename | Target doc used `key`; SQLite is `k` — **rename k → key** |
| v | value_json JSONB | convert-to-jsonb | Only if always JSON; else TEXT **needs-decision** |
| updated_at | TIMESTAMPTZ | type-upgrade |

### 5.6 Mart tables → `mart.*`

| SQLite table | Target object | Action summary |
|--------------|---------------|----------------|
| contact_master | mart.contact_master | keep columns; first_seen_at / last_seen_at → TIMESTAMPTZ if parseable |
| organization_master | mart.organization_master | same |
| document_master | mart.document_master | attachment_id PK → BIGINT; FK emails; ALTER columns preserved |
| opportunity_signals | mart.opportunity_signals | details_json → JSONB; created_at → TIMESTAMPTZ |

### 5.7 Leads tables → `leads.*`

| SQLite table | Target | Rename / notes |
|--------------|--------|----------------|
| lead_master | **leads.lead** | **rename table**; all columns keep names unless target uses snake_case only (already snake_case) |
| external_leads_raw | leads.external_leads_raw | raw_json → JSONB |
| lead_matches_* | leads.lead_match_existing_org / contact | rename per target doc |
| lead_outreach_enrichment | leads.lead_outreach_enrichment | enrichment_json → JSONB |
| lead_contact_research | leads.lead_contact_research | keep |
| lead_upstream_reconcile_log | leads.lead_upstream_reconcile_log | keep |
| lead_account_* | leads.lead_account, etc. | rename per POSTGRES_SCHEMA_TARGET_V1 |

### 5.8 Commercial tables → `commercial.*`

| SQLite table | Target | Action |
|--------------|--------|--------|
| commercial_email_signal_fact | commercial.email_signal_fact | rename prefix; rationale_json → JSONB |
| commercial_org_signal_rollup | commercial.org_signal_rollup | rename |
| commercial_contact_signal_rollup | commercial.contact_signal_rollup | rename |
| commercial_opportunity_fact | commercial.opportunity_fact | rename |
| organization_candidate | commercial.organization_candidate | provenance_json → JSONB |
| contact_candidate | commercial.contact_candidate | same |
| opportunity_candidate | commercial.opportunity_candidate | same |
| candidate_review_event | commercial.candidate_review_event | keep |
| candidate_manual_override | commercial.candidate_manual_override | UNIQUE constraint port as-is |

### 5.9 Supplier tables → `supplier.*`

| SQLite table | Target | Action |
|--------------|--------|--------|
| supplier_import_batch | supplier.import_batch | sheet JSON → JSONB |
| supplier_master | supplier.supplier | rename table; id BIGSERIAL |
| supplier_evidence | supplier.evidence | rename |
| supplier_contact_channel | supplier.contact_channel | rename |
| supplier_priority_snapshot | supplier.priority_snapshot | rename |
| supplier_review_state | supplier.review_state | PK supplier_id → BIGINT FK |

### 5.10 Outbound sidecars → `outbound.*`

| SQLite table | Target | Action |
|--------------|--------|--------|
| contact_email_suppression | outbound.contact_email_suppression | PK email → could be CITEXT **needs-decision** |
| contact_domain_suppression | outbound.contact_domain_suppression | keep |
| outreach_contact_state | outbound.outreach_contact_state | lead_id → optional FK BIGINT to leads.lead(id) **needs-decision** |

### 5.11 Full row-level mapping: `lead_master` → `leads.lead`

Every SQLite column from `LEAD_SCHEMA_SQL` + ALTER migrations:

| SQLite column | SQLite type | Target column | Target type | Action | Notes |
|---------------|-------------|---------------|-------------|--------|-------|
| id | INTEGER PK | id | BIGSERIAL PK | type-upgrade | |
| source_name | TEXT NOT NULL | source_name | TEXT NOT NULL | keep | |
| source_type | TEXT | source_type | TEXT | keep | |
| source_record_id | TEXT | source_record_id | TEXT | keep | UNIQUE with source_name |
| source_url | TEXT | source_url | TEXT | keep | |
| org_name | TEXT | org_name | TEXT | keep | |
| contact_name | TEXT | contact_name | TEXT | keep | |
| email | TEXT | email | TEXT | keep | |
| phone | TEXT | phone | TEXT | keep | |
| website | TEXT | website | TEXT | keep | |
| domain | TEXT | domain | TEXT | keep | |
| region | TEXT | region | TEXT | keep | |
| city | TEXT | city | TEXT | keep | |
| lead_type | TEXT | lead_type | TEXT | keep | |
| organization_type_guess | TEXT | organization_type_guess | TEXT | keep | |
| buyer_kind | TEXT | buyer_kind | TEXT | keep | ALTER-added |
| equipment_match_tags | TEXT | equipment_match_tags | TEXT | keep | |
| lab_context_score | REAL | lab_context_score | DOUBLE PRECISION | type-upgrade | |
| lab_context_tags | TEXT | lab_context_tags | TEXT | keep | |
| evidence_summary | TEXT | evidence_summary | TEXT | keep | |
| first_seen_at | TEXT | first_seen_at | TIMESTAMPTZ | type-upgrade | validate |
| last_seen_at | TEXT | last_seen_at | TIMESTAMPTZ | type-upgrade | validate |
| priority_score | REAL | priority_score | DOUBLE PRECISION | type-upgrade | |
| priority_reason | TEXT | priority_reason | TEXT | keep | |
| fit_bucket | TEXT | fit_bucket | TEXT | keep | |
| status | TEXT | status | TEXT | keep | default nuevo |
| review_owner | TEXT | review_owner | TEXT | keep | |
| last_reviewed_at | TEXT | last_reviewed_at | TIMESTAMPTZ | type-upgrade | |
| next_action | TEXT | next_action | TEXT | keep | |
| notes | TEXT | notes | TEXT | keep | |
| email_norm | TEXT | email_norm | TEXT | keep | ALTER |
| domain_norm | TEXT | domain_norm | TEXT | keep | |
| org_name_norm | TEXT | org_name_norm | TEXT | keep | |
| upstream_sync_state | TEXT | upstream_sync_state | TEXT | keep | |
| upstream_retired_at | TEXT | upstream_retired_at | TIMESTAMPTZ | type-upgrade | |
| upstream_retired_reason | TEXT | upstream_retired_reason | TEXT | keep | |

Unique constraint: `UNIQUE (source_name, source_record_id)` replacing SQLite `uidx_lead_master_source_name_record`.

### 5.12 Full row-level mapping: `commercial_email_signal_fact` → `commercial.email_signal_fact`

| SQLite column | Target column | Target type | Action |
|---------------|---------------|-------------|--------|
| id | id | BIGSERIAL | type-upgrade |
| email_id | email_id | BIGINT FK | type-upgrade |
| source_file | source_file | TEXT | keep |
| sent_at | sent_at | TIMESTAMPTZ | type-upgrade |
| sender_email | sender_email | TEXT | keep |
| sender_domain | sender_domain | TEXT | keep |
| contact_email | contact_email | TEXT | keep |
| contact_domain | contact_domain | TEXT | keep |
| org_domain | org_domain | TEXT | keep |
| signal_code | signal_code | TEXT | keep |
| signal_kind | signal_kind | TEXT | keep |
| reason_code | reason_code | TEXT | keep |
| reason_text | reason_text | TEXT | keep |
| confidence_score | confidence_score | DOUBLE PRECISION | type-upgrade |
| strength_score | strength_score | DOUBLE PRECISION | type-upgrade |
| rationale_json | rationale_json | JSONB | convert-to-jsonb |
| run_id | run_id | BIGINT | type-upgrade |
| created_at | created_at | TIMESTAMPTZ | type-upgrade |

UNIQUE `(email_id, signal_code, reason_code, contact_email, org_domain)` — port exactly; NULL handling in Postgres may differ **needs-decision**.

### 5.13 Other tables

All remaining tables in §3 map **1:1 column name** to the target object in `POSTGRES_SCHEMA_TARGET_V1.md` unless listed above: use **TEXT → TEXT**, **REAL → DOUBLE PRECISION**, **INTEGER** counts → **INTEGER** or **BIGINT** for FKs, **JSON-suffixed** columns → **JSONB** with validation. Omitting per-column tables here avoids a 15-page doc; generate the final Alembic autogenerate **diff** from SQLite `PRAGMA table_info` vs Postgres `information_schema` at implementation time.

---

## 6. Target DDL draft (PostgreSQL)

**Conventions:** `BIGSERIAL` for surrogate keys; `TIMESTAMPTZ` only where noted; JSON columns documented; **new tables only** as approved in `POSTGRES_SCHEMA_TARGET_V1.md`.

```sql
-- Schemas
CREATE SCHEMA IF NOT EXISTS archive;
CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS mart;
CREATE SCHEMA IF NOT EXISTS leads;
CREATE SCHEMA IF NOT EXISTS commercial;
CREATE SCHEMA IF NOT EXISTS outbound;
CREATE SCHEMA IF NOT EXISTS supplier;
CREATE SCHEMA IF NOT EXISTS reporting;
```

### 6.1 `archive`

```sql
CREATE TABLE archive.emails (
  id BIGSERIAL PRIMARY KEY,
  source_file TEXT NOT NULL,
  folder TEXT,
  message_id TEXT,
  subject TEXT,
  sender TEXT,
  recipients TEXT,
  date_raw TEXT,
  date_iso TIMESTAMPTZ,  -- validate migration; NULL ok
  body TEXT,
  body_html TEXT,
  body_text_raw TEXT,
  body_text_clean TEXT,
  body_source_type TEXT,
  body_has_plain BOOLEAN,
  body_has_html BOOLEAN,
  full_body_clean TEXT,
  top_reply_clean TEXT,
  attachment_count INTEGER NOT NULL DEFAULT 0,
  has_attachments BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX idx_emails_message_id ON archive.emails(message_id);
CREATE INDEX idx_emails_date_iso ON archive.emails(date_iso);
CREATE INDEX idx_emails_body_source_type ON archive.emails(body_source_type);

CREATE TABLE archive.attachments (
  id BIGSERIAL PRIMARY KEY,
  email_id BIGINT NOT NULL REFERENCES archive.emails(id) ON DELETE CASCADE,
  part_index INTEGER NOT NULL,
  filename TEXT,
  content_type TEXT,
  content_disposition TEXT,
  size_bytes BIGINT,
  content_id TEXT,
  is_inline BOOLEAN,
  sha256 TEXT,
  saved_path TEXT,
  created_at TIMESTAMPTZ
);
CREATE INDEX idx_attachments_email_id ON archive.attachments(email_id);
CREATE INDEX idx_attachments_sha256 ON archive.attachments(sha256);

CREATE TABLE archive.attachment_extracts (
  id BIGSERIAL PRIMARY KEY,
  attachment_id BIGINT NOT NULL UNIQUE REFERENCES archive.attachments(id) ON DELETE CASCADE,
  extract_status TEXT NOT NULL,
  extract_method TEXT NOT NULL,
  text_preview TEXT,
  text_truncated TEXT,
  char_count INTEGER,
  page_count INTEGER,
  sheet_count INTEGER,
  detected_doc_type TEXT,
  has_quote_terms BOOLEAN,
  has_invoice_terms BOOLEAN,
  has_price_list_terms BOOLEAN,
  has_purchase_terms BOOLEAN,
  error_message TEXT,
  created_at TIMESTAMPTZ
);
CREATE INDEX idx_attachment_extracts_attachment_id ON archive.attachment_extracts(attachment_id);
CREATE INDEX idx_attachment_extracts_doc_type ON archive.attachment_extracts(detected_doc_type);
CREATE INDEX idx_attachment_extracts_status_method ON archive.attachment_extracts(extract_status, extract_method);
```

### 6.2 `ops`

```sql
CREATE TABLE ops.pipeline_run (
  id BIGSERIAL PRIMARY KEY,
  started_at TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ,
  script_name TEXT NOT NULL,
  argv_json JSONB,
  git_describe TEXT,
  notes TEXT
);
CREATE INDEX idx_pipeline_run_started ON ops.pipeline_run(started_at DESC);
CREATE INDEX idx_pipeline_run_script ON ops.pipeline_run(script_name);

CREATE TABLE ops.pipeline_kv (
  key TEXT PRIMARY KEY,
  value_json JSONB,
  updated_at TIMESTAMPTZ NOT NULL
);
```

### 6.3 `mart`

```sql
CREATE TABLE mart.contact_master (
  email TEXT PRIMARY KEY,
  contact_name_best TEXT,
  domain TEXT,
  organization_name_guess TEXT,
  organization_type_guess TEXT,
  first_seen_at TIMESTAMPTZ,
  last_seen_at TIMESTAMPTZ,
  total_emails INTEGER,
  inbound_emails INTEGER,
  outbound_emails INTEGER,
  quote_email_count INTEGER,
  invoice_email_count INTEGER,
  purchase_email_count INTEGER,
  business_doc_email_count INTEGER,
  quote_doc_count INTEGER,
  invoice_doc_count INTEGER,
  top_equipment_tags TEXT,
  confidence_score DOUBLE PRECISION
);
CREATE INDEX idx_contact_master_domain ON mart.contact_master(domain);
CREATE INDEX idx_contact_master_last_seen ON mart.contact_master(last_seen_at);

CREATE TABLE mart.organization_master (
  domain TEXT PRIMARY KEY,
  organization_name_guess TEXT,
  organization_type_guess TEXT,
  first_seen_at TIMESTAMPTZ,
  last_seen_at TIMESTAMPTZ,
  total_emails INTEGER,
  total_contacts INTEGER,
  quote_email_count INTEGER,
  invoice_email_count INTEGER,
  purchase_email_count INTEGER,
  business_doc_email_count INTEGER,
  quote_doc_count INTEGER,
  invoice_doc_count INTEGER,
  top_equipment_tags TEXT,
  key_contacts TEXT
);
CREATE INDEX idx_org_master_last_seen ON mart.organization_master(last_seen_at);

CREATE TABLE mart.document_master (
  attachment_id BIGINT PRIMARY KEY REFERENCES archive.attachments(id) ON DELETE CASCADE,
  email_id BIGINT REFERENCES archive.emails(id) ON DELETE CASCADE,
  filename TEXT,
  extension TEXT,
  sender_email TEXT,
  sender_domain TEXT,
  recipient_domain TEXT,
  sent_at TIMESTAMPTZ,
  doc_type TEXT,
  extracted_preview_raw TEXT,
  extracted_preview_clean TEXT,
  preview_quality_score DOUBLE PRECISION,
  has_quote_terms BOOLEAN,
  has_invoice_terms BOOLEAN,
  has_purchase_terms BOOLEAN,
  has_price_list_terms BOOLEAN,
  equipment_tags TEXT
);
CREATE INDEX idx_document_master_sender_domain ON mart.document_master(sender_domain);
CREATE INDEX idx_document_master_recipient_domain ON mart.document_master(recipient_domain);
CREATE INDEX idx_document_master_sent_at ON mart.document_master(sent_at);
CREATE INDEX idx_document_master_doc_type ON mart.document_master(doc_type);

CREATE TABLE mart.opportunity_signals (
  id BIGSERIAL PRIMARY KEY,
  signal_type TEXT NOT NULL,
  entity_kind TEXT NOT NULL,
  entity_key TEXT NOT NULL,
  email_id BIGINT,
  attachment_id BIGINT,
  score DOUBLE PRECISION,
  details_json JSONB,
  created_at TIMESTAMPTZ
);
CREATE INDEX idx_opportunity_signals_entity ON mart.opportunity_signals(entity_kind, entity_key);
CREATE INDEX idx_opportunity_signals_type ON mart.opportunity_signals(signal_type);
```

### 6.4 `leads` (core — `lead` table mirrors `lead_master` columns)

```sql
CREATE TABLE leads.external_leads_raw (
  id BIGSERIAL PRIMARY KEY,
  source_name TEXT NOT NULL,
  source_record_id TEXT NOT NULL,
  fetched_at TIMESTAMPTZ NOT NULL,
  raw_json JSONB,
  source_url TEXT,
  UNIQUE (source_name, source_record_id)
);

CREATE TABLE leads.lead (
  id BIGSERIAL PRIMARY KEY,
  source_name TEXT NOT NULL,
  source_type TEXT,
  source_record_id TEXT,
  source_url TEXT,
  org_name TEXT,
  contact_name TEXT,
  email TEXT,
  phone TEXT,
  website TEXT,
  domain TEXT,
  region TEXT,
  city TEXT,
  lead_type TEXT,
  organization_type_guess TEXT,
  buyer_kind TEXT,
  equipment_match_tags TEXT,
  lab_context_score DOUBLE PRECISION,
  lab_context_tags TEXT,
  evidence_summary TEXT,
  first_seen_at TIMESTAMPTZ,
  last_seen_at TIMESTAMPTZ,
  priority_score DOUBLE PRECISION,
  priority_reason TEXT,
  fit_bucket TEXT,
  status TEXT NOT NULL DEFAULT 'nuevo',
  review_owner TEXT,
  last_reviewed_at TIMESTAMPTZ,
  next_action TEXT,
  notes TEXT,
  email_norm TEXT,
  domain_norm TEXT,
  org_name_norm TEXT,
  upstream_sync_state TEXT NOT NULL DEFAULT 'active',
  upstream_retired_at TIMESTAMPTZ,
  upstream_retired_reason TEXT,
  UNIQUE (source_name, source_record_id)
);
-- Indexes matching SQLite: add idx_lead_master_* equivalents on leads.lead
```

*(Remaining `leads.*` tables: mirror §3 column-for-column with BIGINT FKs and JSONB for `evidence_json` / `enrichment_json`.)*

### 6.5 `commercial` (abbreviated — full parity with §3.22–3.23)

Create `commercial.email_signal_fact`, `org_signal_rollup`, `contact_signal_rollup`, `opportunity_fact`, `organization_candidate`, `contact_candidate`, `opportunity_candidate`, `candidate_review_event`, `candidate_manual_override` with columns as in SQLite; use `JSONB` for `rationale_json`, `provenance_json`, `evidence_json` where present; `DOUBLE PRECISION` for REAL.

### 6.6 `outbound` (sidecars + **new** batch tables)

```sql
CREATE TABLE outbound.contact_email_suppression (
  email TEXT PRIMARY KEY,
  suppression_reason_code TEXT NOT NULL,
  suppression_reason_text TEXT,
  suppression_source TEXT,
  last_bounced_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL,
  updated_by TEXT
);
CREATE INDEX idx_contact_email_suppression_reason ON outbound.contact_email_suppression(suppression_reason_code);

CREATE TABLE outbound.contact_domain_suppression (
  domain_norm TEXT PRIMARY KEY,
  suppression_reason_text TEXT,
  updated_at TIMESTAMPTZ NOT NULL,
  updated_by TEXT
);

CREATE TABLE outbound.outreach_contact_state (
  contact_email_norm TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  first_contacted_at TIMESTAMPTZ,
  last_contacted_at TIMESTAMPTZ,
  source TEXT,
  notes TEXT,
  updated_at TIMESTAMPTZ NOT NULL,
  updated_by TEXT,
  lead_id BIGINT REFERENCES leads.lead(id) ON DELETE SET NULL
);
CREATE INDEX idx_outreach_contact_state_state ON outbound.outreach_contact_state(state);
CREATE INDEX idx_outreach_contact_state_lead_id ON outbound.outreach_contact_state(lead_id) WHERE lead_id IS NOT NULL;

-- New (no SQLite equivalent)
CREATE TABLE outbound.outbound_batch (
  id BIGSERIAL PRIMARY KEY,
  lane TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by TEXT,
  gmail_user TEXT NOT NULL,
  sent_folders TEXT[] NOT NULL,
  sent_preflight_json JSONB NOT NULL,
  gate_version TEXT,
  output_artifact_path TEXT,
  notes TEXT
);

CREATE TABLE outbound.outbound_batch_recipient (
  id BIGSERIAL PRIMARY KEY,
  batch_id BIGINT NOT NULL REFERENCES outbound.outbound_batch(id) ON DELETE CASCADE,
  email_norm TEXT NOT NULL,
  lead_id BIGINT REFERENCES leads.lead(id) ON DELETE SET NULL,
  source_kind TEXT,
  source_key TEXT,
  organization_name TEXT,
  organization_domain TEXT,
  eligibility_result TEXT NOT NULL,
  exclusion_reason TEXT,
  exported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  metadata_json JSONB DEFAULT '{}'::jsonb,
  UNIQUE (batch_id, email_norm)
);
```

### 6.7 `supplier`

Mirror `supplier_schema.py`: `import_batch`, `supplier` (ex-`supplier_master`), `evidence`, `contact_channel`, `priority_snapshot`, `review_state` with BIGINT PKs/FKs and JSONB for `sheet_row_counts_json`, `category_priorities_json` after validation.

### 6.8 `reporting` (new only)

```sql
CREATE TABLE reporting.report_run (
  id BIGSERIAL PRIMARY KEY,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  report_kind TEXT NOT NULL,
  triggered_by TEXT,
  parameters_json JSONB DEFAULT '{}'::jsonb,
  status TEXT NOT NULL DEFAULT 'running',
  error_message TEXT
);

CREATE TABLE reporting.report_artifact (
  id BIGSERIAL PRIMARY KEY,
  report_run_id BIGINT NOT NULL REFERENCES reporting.report_run(id) ON DELETE CASCADE,
  storage_uri TEXT NOT NULL,
  sha256 TEXT,
  bytes BIGINT,
  mime_type TEXT,
  artifact_role TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 6.9 Views

- `CREATE VIEW commercial.v_commercial_candidate_queue AS …` — port from SQLite verbatim (§4.1).
- `CREATE VIEW leads.v_lead_match_summary AS …` — port from `bi_views.py` after `sql_upstream_active` is translated.

---

## 7. Risky conversions

| Risk | Detail |
|------|--------|
| **TEXT → TIMESTAMPTZ** | `date_iso`, `*_at`, `created_at`, `updated_at`, `sent_at`, `fetched_at` — many are ISO-8601 but **not guaranteed**; failed rows need NULL or quarantine. |
| **TEXT → JSONB** | `raw_json`, `argv_json`, `rationale_json`, `provenance_json`, `evidence_json`, enrichment JSON, pipeline_kv `v` — **invalid JSON** in legacy rows breaks migration unless `TEXT` fallback or cleansing step. |
| **INTEGER 0/1 → BOOLEAN** | Semantic match for `has_*`, `is_*`, `already_in_archive_flag`, `dry_run`, `is_active` — verify no `2` or NULL surprises. |
| **Renamed tables** | App code still uses `lead_master`; cutover requires views or dual-write period. |
| **Nullable → NOT NULL** | Prefer keep nullable until backfill (e.g. `date_iso`). |
| **New FKs** | Postgres `outreach_contact_state.lead_id` FK vs SQLite no FK — **orphan rows** must be cleaned before enforcing. |
| **`document_master.attachment_id`** | SQLite PK without FK to `attachments`; Postgres adds `REFERENCES archive.attachments` — **orphans** must be resolved. |
| **UNIQUE on candidate_manual_override** | SQLite `UNIQUE(entity_kind, entity_key, override_code, is_active)` — unusual; confirm application intent before port. |
| **PK type change** | INTEGER → BIGSERIAL changes driver/client expectations only if IDs exported to external systems — document ID stability requirement. |
| **Materialized view vs table** | `mart.contact_master` — if MV, migration loads differently than table copy. |

---

## 8. Open decisions before Alembic

- [ ] **`mart.contact_master`:** table vs **materialized view**?
- [ ] **App rename:** immediate `lead_master` → `leads.lead` in code vs **compatibility view** `CREATE VIEW lead_master AS SELECT * FROM leads.lead`?
- [ ] **Postgres compatibility views** for all renamed SQLite tables during transition?
- [ ] **JSON columns:** which are **guaranteed** valid JSON (enforce CHECK or migration cleanse)?
- [ ] **Timestamp columns:** which have **non-ISO** legacy values (keep TEXT)?
- [ ] **`outreach_contact_state.lead_id`:** enforce **FK** to `leads.lead` in Postgres?
- [ ] **`outbound_batch` / recipients:** implement **only in Postgres** vs add SQLite mirror for dual-run?
- [ ] **`pipeline_kv.k` → `key`:** reserved word in SQL — quote or rename?
- [ ] **Full vs core `v_lead_match_summary`:** single view with LEFT JOINs or two views?

---

## 9. Recommended first implementation slice

| Option | Description |
|--------|-------------|
| **A** | Alembic scaffold only, no tables |
| **B** | Create empty schemas + `ops` tables only |
| **C** | Create `archive` schema + core tables first |
| **D** | Add `outbound_batch` to SQLite first |
| **E** | Stand up Postgres test DB parallel to SQLite |

**Recommendation: E + B (incremental).**

1. **Scaffold Alembic (A)** with env pointing at a **disposable Postgres** — no impact on production SQLite.
2. **Apply (B):** create **schemas** + **`ops.pipeline_run` / `ops.pipeline_kv`** — small surface, validates connectivity, JSONB, and conventions.
3. **Then (C) archive** in dedicated revisions once timestamp/JSON validation rules are written.
4. Keep **SQLite as SoT** until archive + ops + outbound sidecars are proven migrated (**E** parallel validation with row counts).

**Avoid D early** unless product explicitly needs SQLite export audit before Postgres; the design favors **Postgres-only** new tables.

---

## Return summary

| Deliverable | Location |
|-------------|----------|
| **File created** | `apps/email-pipeline/docs/pipeline/POSTGRES_SCHEMA_RECONCILIATION_V1.md` |
| **Risky conversions** | §7 — timestamps, JSONB, booleans, new FKs, attachment_id, PK widening |
| **First slice** | §9 — Alembic scaffold + Postgres test instance; schemas + `ops` first; then `archive` |
| **Assumptions** | ISO timestamps for most `*_at` fields; JSON columns are intended JSON; no silent data fixes in this doc — validation scripts are a prerequisite migration sub-step |

---

## Assumptions (explicit)

- **SQLite `PRAGMA foreign_keys`** behavior differs from Postgres enforced FKs — migration must **detect orphans** before enabling constraints.
- **`leads.lead`** table name is valid in PostgreSQL; if tooling conflicts, use `"lead"` quoted or rename to `leads.lead_record` (**needs-decision**).
- **Commercial and supplier** DDL in §6 is abbreviated; full CREATE statements must be **generated mechanically** from §3 to avoid column omission.
