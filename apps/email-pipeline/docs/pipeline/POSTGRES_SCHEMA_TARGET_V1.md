# PostgreSQL Schema Target V1

Status: **design only** (no migrations implemented)  
Owner: email-pipeline-maintainers  
Last reviewed: 2026-04-19

> **Status: historical planning document.** Written before the Alembic migration chain
> existed. Current PostgreSQL/Alembic truth is defined by the migration chain in
> `apps/email-pipeline/alembic/versions/` and
> [`docs/architecture/CURRENT_SYSTEM_TRUTH.md`](../../../../docs/architecture/CURRENT_SYSTEM_TRUTH.md).
> The "design only / no migrations implemented" status above describes the state at the
> time this document was written (2026-04-19), not the current state.

## 1. Purpose

The OrigenLab email pipeline today uses a **single SQLite file** as the operational database. DDL is spread across Python modules (`db.py`, `business_mart_schema.py`, `leads_schema.py`, `commercial/commercial_intel_schema.py`, etc.); see the internal schema inventory derived from those modules.

**This document does not change runtime behavior.** SQLite remains the operational store until an explicit cutover.

**Goal:** define a **target PostgreSQL model** that is:

- **API-ready** (clear schema boundaries, stable names, typed columns where it matters),
- **Alembic-friendly** (schemas as namespaces, explicit tables vs views vs materialized views),
- **Aligned with current table names** where possible, with **explicit rename notes** where we intentionally improve clarity (e.g. `lead_master` → `leads.lead`).

Use this document to guide a **future** Alembic migration series—not a lift-and-shift of SQLite files.

---

## 2. Design principles

| Principle | Implication |
|-----------|-------------|
| **Raw archive is immutable evidence** | Ingest appends to `archive.*`; corrections are rare and audit-logged. No “rebuild” job may delete or rewrite archive mail rows except dedicated admin tooling. |
| **Mart tables are rebuildable** | `mart.*` projections can be truncated and rebuilt from `archive` + rules. Operators treat them as **heuristic**, not CRM truth. |
| **Durable / operator state must never be wiped by rebuild jobs** | Suppression, outreach state, lead research notes, commercial review decisions, supplier review, pipeline KV—**separate jobs**, retention policies, and backups. |
| **Exports must be auditable** | Cold-outreach batches should be recorded in **`outbound.outbound_batch`** / **`outbound.outbound_batch_recipient`** (new), not only as CSV/JSON on disk. Files remain useful but are **not** the sole system of record once productized. |
| **Review actions are durable workflow state** | `candidate_review_event`, `candidate_manual_override`, Streamlit-driven updates to suppression/outreach—persist as first-class rows with actor and timestamp. |
| **External CSV/report artifacts are not the canonical database** | `reports/out/...` paths are outputs; the DB holds what was decided, to whom, and when. |
| **Avoid merging contact / lead / candidate concepts unless lifecycle is identical** | `mart.contact_master` (mail-graph rollup) ≠ `leads.lead` (prospecting record) ≠ `commercial.contact_candidate` (review queue). Keep boundaries explicit in schema names and FKs. |

---

## 3. Proposed PostgreSQL schemas

Below: **purpose**, **target objects**, **SQLite → target name map**, and **classification** (base = persistent row storage; derived = computed by jobs from upstream data; MV = materialized view; view = read-only query; durable = operator/workflow state; job output = written by batch jobs but safe to recompute from inputs in principle).

### 3.1 `archive`

**Purpose:** Immutable (append-oriented) **raw mailbox evidence**: messages, binary attachments, extracted text.

| Target object | SQLite source | Classification | Notes |
|---------------|-----------------|----------------|-------|
| `archive.emails` | `emails` | Base table | Core message row; ingest-owned. |
| `archive.attachments` | `attachments` | Base table | FK to emails; CASCADE on delete from parent policy TBD. |
| `archive.attachment_extracts` | `attachment_extracts` | Base table | One row per attachment extract. |

### 3.2 `ops`

**Purpose:** **Pipeline/system metadata**: run records, argv, KV flags—shared across rebuilds and exports.

| Target object | SQLite source | Classification | Notes |
|---------------|-----------------|----------------|-------|
| `ops.pipeline_run` | `pipeline_run` | Durable state | Extend with optional `status`, `error_message`, `metadata_json` in target. |
| `ops.pipeline_kv` | `pipeline_kv` | Durable state | Value may become `JSONB`. |

### 3.3 `mart`

**Purpose:** **Rebuildable** business-mart projections from archive (contacts, orgs, documents, opportunity signals).

| Target object | SQLite source | Classification | Notes |
|---------------|-----------------|----------------|-------|
| `mart.contact_master` | `contact_master` | Derived table (or MV — open) | Mail-graph rollup; rebuild job. |
| `mart.organization_master` | `organization_master` | Derived table (or MV — open) | Domain-level rollup. |
| `mart.document_master` | `document_master` | Derived table | Tied to `archive.attachments` / emails. |
| `mart.opportunity_signals` | `opportunity_signals` | Derived table | Signals rebuilt from mart + rules. |

### 3.4 `leads`

**Purpose:** **External prospecting** pipeline: raw fetches, normalized leads, matches to archive, account rollups, enrichment.

| Target object | SQLite source | Classification | Notes |
|---------------|-----------------|----------------|-------|
| `leads.external_leads_raw` | `external_leads_raw` | Base table | Raw JSON payloads. |
| `leads.lead` | `lead_master` | Base table | **Rename** from `lead_master`; canonical normalized lead. |
| `leads.lead_match_existing_org` | `lead_matches_existing_orgs` | Derived / job output | Match rows; can be rebuilt from rules + mart snapshots. |
| `leads.lead_match_existing_contact` | `lead_matches_existing_contacts` | Derived / job output | Same. |
| `leads.lead_outreach_enrichment` | `lead_outreach_enrichment` | Durable / operator-adjacent | JSON enrichment; treat as durable unless policy says rebuild. |
| `leads.lead_contact_research` | `lead_contact_research` | Durable state | Operator research UI. |
| `leads.lead_upstream_reconcile_log` | `lead_upstream_reconcile_log` | Durable audit | Reconciliation audit trail. |
| `leads.lead_account` | `lead_account_master` | Base table | CRM-style account. |
| `leads.lead_account_alias` | `lead_account_aliases` | Base table | |
| `leads.lead_account_membership` | `lead_account_membership` | Base table | Links lead ↔ account. |
| `leads.lead_account_match_existing_org` | `lead_account_matches_existing_orgs` | Derived / job output | |
| `leads.lead_account_override` | `lead_account_overrides` | Durable state | Operator merge/split overrides. |

### 3.5 `commercial`

**Purpose:** **Commercial intelligence**: rebuildable signal facts/rollups + **durable** candidate queues and review/overrides.

| Target object | SQLite source | Classification | Notes |
|---------------|-----------------|----------------|-------|
| `commercial.email_signal_fact` | `commercial_email_signal_fact` | Derived table | Rebuildable from archive; FK to `archive.emails`. |
| `commercial.org_signal_rollup` | `commercial_org_signal_rollup` | Derived table | |
| `commercial.contact_signal_rollup` | `commercial_contact_signal_rollup` | Derived table | |
| `commercial.opportunity_fact` | `commercial_opportunity_fact` | Derived table | |
| `commercial.organization_candidate` | `organization_candidate` | Durable state | Review workflow. |
| `commercial.contact_candidate` | `contact_candidate` | Durable state | **Not** `mart.contact_master`. |
| `commercial.opportunity_candidate` | `opportunity_candidate` | Durable state | |
| `commercial.candidate_review_event` | `candidate_review_event` | Durable audit | |
| `commercial.candidate_manual_override` | `candidate_manual_override` | Durable state | |
| `commercial.v_candidate_queue` | `v_commercial_candidate_queue` | **View** | Union of candidates; recreate as SQL view. |

### 3.6 `outbound`

**Purpose:** **Operator outbound memory** and **export audit**: suppression, domain blocks, outreach state, and **future** batch records.

| Target object | SQLite source | Classification | Notes |
|---------------|-----------------|----------------|-------|
| `outbound.contact_email_suppression` | `contact_email_suppression` | Durable state | |
| `outbound.contact_domain_suppression` | `contact_domain_suppression` | Durable state | |
| `outbound.outreach_contact_state` | `outreach_contact_state` | Durable state | Optional FK to `leads.lead` when present. |
| `outbound.outbound_batch` | *(new)* | Durable audit | See §5. |
| `outbound.outbound_batch_recipient` | *(new)* | Durable audit | Per-recipient eligibility snapshot. |

### 3.7 `supplier`

**Purpose:** **Supplier/sourcing** domain (DeepSearch imports, evidence, channels, review)—separate from buyer leads.

| Target object | SQLite source | Classification | Notes |
|---------------|-----------------|----------------|-------|
| `supplier.import_batch` | `supplier_import_batch` | Base / job metadata | |
| `supplier.supplier` | `supplier_master` | Base table | Rename for clarity optional (`supplier.supplier` vs `supplier_master`). |
| `supplier.evidence` | `supplier_evidence` | Base table | |
| `supplier.contact_channel` | `supplier_contact_channel` | Base table | |
| `supplier.priority_snapshot` | `supplier_priority_snapshot` | Job output | Per-batch tiering. |
| `supplier.review_state` | `supplier_review_state` | Durable state | |

### 3.8 `reporting`

**Purpose:** **Report generation metadata**—what was generated, where artifacts live, for dashboards and compliance. **Not** a replacement for `archive` or `mart` data.

| Target object | SQLite source | Classification | Notes |
|---------------|-----------------|----------------|-------|
| `reporting.report_run` | *(new)* | Durable audit | See §5. |
| `reporting.report_artifact` | *(new)* | Durable audit | Pointer to file/object storage. |

### 3.9 `leads` reporting view (optional schema placement)

| Target object | SQLite source | Classification | Notes |
|---------------|-----------------|----------------|-------|
| `leads.v_lead_match_summary` | `v_lead_match_summary` | **View** | Today implemented in `bi_views.py`; port SQL into Postgres view definition. |

---

## 4. Current-to-target mapping

Full inventory of **current SQLite** objects from the operational codebase and their **target** counterparts. **Action** describes the migration intent at a high level.

| Current SQLite table | Target PostgreSQL object | Action | Lifecycle | Notes |
|---------------------|----------------------------|--------|-----------|-------|
| `emails` | `archive.emails` | Migrate + type upgrade | Append / read-heavy | Use `TIMESTAMPTZ` for parsed dates where applicable; preserve `source_file` semantics. |
| `attachments` | `archive.attachments` | Migrate | Immutable | FK `email_id` → `archive.emails`. |
| `attachment_extracts` | `archive.attachment_extracts` | Migrate | Immutable | FK to attachments. |
| `pipeline_run` | `ops.pipeline_run` | Migrate + extend | Durable | Optional: `status`, `error_message`, `metadata_json`. |
| `pipeline_kv` | `ops.pipeline_kv` | Migrate | Durable | `v` → `JSONB` optional. |
| `contact_master` | `mart.contact_master` | Migrate | Rebuildable | May become **materialized view** later (open question). |
| `organization_master` | `mart.organization_master` | Migrate | Rebuildable | |
| `document_master` | `mart.document_master` | Migrate | Rebuildable | FK to archive attachment/email ids. |
| `opportunity_signals` | `mart.opportunity_signals` | Migrate | Rebuildable | |
| `external_leads_raw` | `leads.external_leads_raw` | Migrate | Append / replace by source policy | `raw_json` → `JSONB`. |
| `lead_master` | `leads.lead` | Migrate + rename | Durable operational | **Canonical rename** in target model; unique `(source_name, source_record_id)`. |
| `lead_matches_existing_orgs` | `leads.lead_match_existing_org` | Migrate | Regenerable | FK to `leads.lead`, optional `ops.pipeline_run`. |
| `lead_matches_existing_contacts` | `leads.lead_match_existing_contact` | Migrate | Regenerable | |
| `lead_outreach_enrichment` | `leads.lead_outreach_enrichment` | Migrate | Durable | |
| `lead_contact_research` | `leads.lead_contact_research` | Migrate | Durable | |
| `lead_upstream_reconcile_log` | `leads.lead_upstream_reconcile_log` | Migrate | Audit | |
| `lead_account_master` | `leads.lead_account` | Migrate + rename | Durable | |
| `lead_account_aliases` | `leads.lead_account_alias` | Migrate | Durable | |
| `lead_account_membership` | `leads.lead_account_membership` | Migrate | Durable | |
| `lead_account_matches_existing_orgs` | `leads.lead_account_match_existing_org` | Migrate | Regenerable | |
| `lead_account_overrides` | `leads.lead_account_override` | Migrate | Durable | |
| `commercial_email_signal_fact` | `commercial.email_signal_fact` | Migrate | Rebuildable | Shortened prefix in schema. |
| `commercial_org_signal_rollup` | `commercial.org_signal_rollup` | Migrate | Rebuildable | |
| `commercial_contact_signal_rollup` | `commercial.contact_signal_rollup` | Migrate | Rebuildable | |
| `commercial_opportunity_fact` | `commercial.opportunity_fact` | Migrate | Rebuildable | |
| `organization_candidate` | `commercial.organization_candidate` | Migrate | Durable workflow | Distinct from `mart.organization_master`. |
| `contact_candidate` | `commercial.contact_candidate` | Migrate | Durable workflow | Distinct from `mart.contact_master`. |
| `opportunity_candidate` | `commercial.opportunity_candidate` | Migrate | Durable workflow | |
| `candidate_review_event` | `commercial.candidate_review_event` | Migrate | Audit | |
| `candidate_manual_override` | `commercial.candidate_manual_override` | Migrate | Durable | |
| `supplier_import_batch` | `supplier.import_batch` | Migrate | Metadata | |
| `supplier_master` | `supplier.supplier` | Migrate | Durable + sourcing | `domain_norm` unique. |
| `supplier_evidence` | `supplier.evidence` | Migrate | Append | |
| `supplier_contact_channel` | `supplier.contact_channel` | Migrate | Durable | |
| `supplier_priority_snapshot` | `supplier.priority_snapshot` | Migrate | Job output | |
| `supplier_review_state` | `supplier.review_state` | Migrate | Durable | |
| `contact_email_suppression` | `outbound.contact_email_suppression` | Migrate | Durable | |
| `contact_domain_suppression` | `outbound.contact_domain_suppression` | Migrate | Durable | |
| `outreach_contact_state` | `outbound.outreach_contact_state` | Migrate | Durable | Optional FK `lead_id` → `leads.lead`. |
| `v_commercial_candidate_queue` | `commercial.v_candidate_queue` | Recreate as view | Read-only | Same union semantics as SQLite. |
| `v_lead_match_summary` | `leads.v_lead_match_summary` | Recreate as view | Read-only | Depends on lead + match + optional account tables. |

---

## 5. Proposed new tables

These **do not exist** in current SQLite as first-class tables; they close gaps for **API/productization** and **auditability**.

### 5.1 `outbound.outbound_batch`

**Purpose:** One row per **canonical export or batch generation** (lead CSV export, archive send batch, etc.), replacing “CSV-only” as the only proof of what ran.

**Key columns (illustrative):**

| Column | Type | Notes |
|--------|------|-------|
| `id` | `BIGSERIAL` | Primary key. |
| `lane` | `TEXT` | e.g. `lead` \| `archive`. |
| `created_at` | `TIMESTAMPTZ` | |
| `created_by` | `TEXT` | Operator or service principal. |
| `gmail_user` | `TEXT` | Mailbox context for Sent-history / gate. |
| `sent_folders` | `TEXT[]` | Resolved Sent folder labels. |
| `sent_preflight_json` | `JSONB` | Same structure as today’s `sent_preflight` summary. |
| `gate_version` / `policy_ref` | `TEXT` | Optional pointer to gate/policy semver or git ref. |
| `output_artifact_path` | `TEXT` | Primary CSV/JSON path (nullable if multi-artifact). |
| `notes` | `TEXT` | |

**Why it matters:** Satisfies **exports must be auditable**; enables idempotent APIs (“show me batch 42”) without parsing filesystem layout alone.

### 5.2 `outbound.outbound_batch_recipient`

**Purpose:** **Per-email** (or per-key) snapshot of what the gate decided for that batch—eligibility, exclusion reason, optional link to `leads.lead`.

**Key columns (illustrative):**

| Column | Type | Notes |
|--------|------|-------|
| `id` | `BIGSERIAL` | |
| `batch_id` | `BIGINT` | FK → `outbound.outbound_batch(id)` ON DELETE CASCADE. |
| `email_norm` | `TEXT` | Normalized recipient. |
| `lead_id` | `BIGINT` | Nullable FK → `leads.lead`. |
| `source_kind` / `source_key` | `TEXT` | Trace to archive vs lead row. |
| `eligibility_result` | `TEXT` | Pass/fail/skip. |
| `exclusion_reason` | `TEXT` | Machine-readable reason code. |
| `metadata_json` | `JSONB` | Extra audit context. |
| `exported_at` | `TIMESTAMPTZ` | |

**Unique:** `(batch_id, email_norm)`.

**Why it matters:** Supports **regression tests against DB**, customer support (“why was X excluded?”), and future UI without re-running the gate.

### 5.3 `reporting.report_run`

**Purpose:** Record **report jobs** (pack builds, QA scorecards, hunt exports) distinct from outbound batches.

**Key columns:** `id`, `started_at`, `finished_at`, `report_kind`, `triggered_by`, `parameters_json`, `status`, `error_message`.

**Why it matters:** Separates **reporting** from **outbound** while keeping **ops.pipeline_run** for low-level script runs if both coexist.

### 5.4 `reporting.report_artifact`

**Purpose:** **Pointer** to generated files (path, checksum, mime, size) linked to `report_run` or optionally to `outbound_batch`.

**Key columns:** `id`, `report_run_id` FK, `storage_uri`, `sha256`, `bytes`, `created_at`, `artifact_role` (e.g. `csv`, `json`, `pdf`).

**Why it matters:** **External CSV/report artifacts are not the canonical database**—but we still **index** them for discoverability and integrity.

---

## 6. Relationship model (text ERD)

```
archive.emails
  -> archive.attachments
  -> archive.attachment_extracts

archive.emails
  -> mart.document_master          (rebuildable; via email/attachment ids)
  -> commercial.email_signal_fact  (rebuildable; ON DELETE CASCADE from emails)

mart.contact_master
mart.organization_master
mart.opportunity_signals            (rebuildable cluster)

leads.external_leads_raw
  -> leads.lead

leads.lead
  -> leads.lead_contact_research
  -> leads.lead_match_existing_org
  -> leads.lead_match_existing_contact
  -> leads.lead_outreach_enrichment
  -> leads.lead_upstream_reconcile_log

leads.lead_account
  -> leads.lead_account_alias
  -> leads.lead_account_membership   -> leads.lead
  -> leads.lead_account_match_existing_org

outbound.outreach_contact_state
  ~> leads.lead                     (optional FK; nullable for email-only DBs)

outbound.outbound_batch
  -> outbound.outbound_batch_recipient
       ~> leads.lead               (optional FK)

commercial.organization_candidate
commercial.contact_candidate
commercial.opportunity_candidate
  -> commercial.candidate_review_event
  -> commercial.candidate_manual_override

supplier.supplier
  -> supplier.evidence
  -> supplier.contact_channel
  -> supplier.priority_snapshot
  -> supplier.review_state
```

**Nullable / weak links:**

- **`outreach_contact_state.lead_id`:** Intentionally nullable so outbound memory works before leads exist.
- **Rebuildable vs durable:** Anything under `mart.*` and rebuildable `commercial.*` facts/rollups may be **truncated and rebuilt**; `outbound.*`, `leads.lead_contact_research`, `commercial.*_candidate`, `supplier.review_state`, and **`ops.pipeline_*`** must follow **retention and backup** rules, not mart rebuild semantics.

---

## 7. API ownership implications

| Category | Objects | Access pattern |
|----------|---------|----------------|
| **Read-only API (default)** | `archive.emails` (filtered), `mart.*` read, `leads.lead` read, `commercial.v_candidate_queue`, `leads.v_lead_match_summary`, `reporting.report_artifact` metadata | GET; no destructive writes from public clients. |
| **Mutation API (operator)** | `outbound.contact_*_suppression`, `outbound.outreach_contact_state`, `leads.lead_contact_research`, `commercial.candidate_*` status transitions, `supplier.review_state` | PATCH/POST with auth; audit fields required. |
| **Admin-only** | `ops.pipeline_kv`, bulk deletes, rebuild triggers for `mart` / commercial facts, ingest impersonation | Restricted role; may map to internal tools only. |
| **Background job outputs** | Mart rebuild, commercial rollup rebuild, `supplier.priority_snapshot`, match table regeneration | Written by workers; **not** hand-edited in UI. |
| **Never manually edited (except break-glass)** | `archive.*` body content, raw `leads.external_leads_raw` payloads | Ingest pipelines own writes; human edits only via controlled tools. |

**New tables** `outbound_batch` / `outbound_batch_recipient`: written by **export pipeline** or API service after gate evaluation—**not** arbitrary client inserts.

---

## 8. Migration strategy

1. **Do not lift-and-shift** the SQLite file as the Postgres data plane. Use **target DDL** derived from this document.
2. **Introduce Alembic** in a dedicated changeset when implementation starts; revision chain lives beside `apps/email-pipeline`.
3. **Order of data migration (suggested):**
   - **Archive first:** `emails` → `attachments` → `attachment_extracts` (largest, least contested FKs).
   - **Durable sidecars:** `outbound.*` suppression/state, `ops.pipeline_*`.
   - **Leads:** raw → `lead` → dependent tables → account tables.
   - **Rebuildable mart + commercial facts:** migrate **or** rebuild from archive post-cutover (rebuild may be simpler than byte-perfect port).
   - **Views:** recreate `v_commercial_candidate_queue`, `v_lead_match_summary` last (depend on tables).
4. **Validation:** row counts per table, checksum samples on `archive.emails`, replay key **pytest** suites against Postgres test instance (future CI job).

---

## 9. Open questions

| # | Question | Options / notes |
|---|----------|-----------------|
| 1 | Should `contact_master` remain a **table** or become a **materialized view** over rules? | Table = simpler migration; MV = stricter separation of “derived only.” |
| 2 | Rename `lead_master` → **`leads.lead`** everywhere in app code vs keep `lead_master` in Postgres? | Target doc favors `leads.lead`; renaming app layer is a separate effort. |
| 3 | How much of **reporting** stays **file-based** vs rows in `reporting.*`? | Hybrid is likely: DB stores metadata + paths; blobs in object storage. |
| 4 | Does **Streamlit** stay a **direct DB client** or move behind an **API**? | Affects connection pooling, RLS, and mutation auditing. |
| 5 | Expose **Sent folder config** in Streamlit vs config-only? | Tied to outbound preflight UX and `outbound_batch.sent_folders`. |
| 6 | Single Postgres database vs **multiple DBs** per domain? | One DB + schemas is the default assumption here; split for scale is future work. |
| 7 | **RLS** (row-level security) for multi-tenant future? | Not required for v1; note for API hardening. |

---

## Appendix A — Illustrative target DDL (reference)

The following fragments are **starting points** for Alembic-generated DDL. Column lists are **not** guaranteed to match every SQLite column today; a migration pass must **diff** against `src/origenlab_email_pipeline/*_schema*.py`.

### A.1 `archive` schema

Raw evidence. Mostly append / read-only via ingest.

```sql
CREATE SCHEMA IF NOT EXISTS archive;

CREATE TABLE archive.emails (
    id BIGSERIAL PRIMARY KEY,
    message_id TEXT,
    source_file TEXT NOT NULL,
    folder TEXT,
    sender TEXT,
    recipients TEXT,
    subject TEXT,
    date_iso TIMESTAMPTZ,
    date_raw TEXT,
    body TEXT,
    body_html TEXT,
    body_source_type TEXT,
    has_attachments BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT now()
    -- Additional columns from SQLite (body_text_*, attachment_count, …) to be merged during migration design.
);

CREATE TABLE archive.attachments (
    id BIGSERIAL PRIMARY KEY,
    email_id BIGINT NOT NULL REFERENCES archive.emails(id) ON DELETE CASCADE,
    filename TEXT,
    content_type TEXT,
    size_bytes BIGINT,
    sha256 TEXT,
    storage_path TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE archive.attachment_extracts (
    id BIGSERIAL PRIMARY KEY,
    attachment_id BIGINT NOT NULL UNIQUE REFERENCES archive.attachments(id) ON DELETE CASCADE,
    extract_status TEXT NOT NULL,
    text_content TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
```

**Rule:** Public API should not mutate archive rows except through **ingest/admin** tooling.

### A.2 `ops` schema

```sql
CREATE SCHEMA IF NOT EXISTS ops;

CREATE TABLE ops.pipeline_run (
    id BIGSERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    script_name TEXT NOT NULL,
    argv_json JSONB,
    status TEXT NOT NULL DEFAULT 'running',
    error_message TEXT,
    metadata_json JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE ops.pipeline_kv (
    key TEXT PRIMARY KEY,
    value_json JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### A.3 `mart` schema (rebuildable)

```sql
CREATE SCHEMA IF NOT EXISTS mart;

CREATE TABLE mart.contact_master (
    email TEXT PRIMARY KEY,
    domain TEXT,
    display_name TEXT,
    first_seen_at TIMESTAMPTZ,
    last_seen_at TIMESTAMPTZ,
    email_count INTEGER DEFAULT 0,
    metadata_json JSONB DEFAULT '{}'::jsonb,
    rebuilt_at TIMESTAMPTZ NOT NULL DEFAULT now()
    -- Merge numeric rollup columns from current SQLite contact_master in migration.
);

CREATE TABLE mart.organization_master (
    domain TEXT PRIMARY KEY,
    name_guess TEXT,
    first_seen_at TIMESTAMPTZ,
    last_seen_at TIMESTAMPTZ,
    metadata_json JSONB DEFAULT '{}'::jsonb,
    rebuilt_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE mart.document_master (
    attachment_id BIGINT PRIMARY KEY REFERENCES archive.attachments(id) ON DELETE CASCADE,
    email_id BIGINT REFERENCES archive.emails(id) ON DELETE CASCADE,
    doc_type TEXT,
    filename TEXT,
    sender_domain TEXT,
    organization_domain TEXT,
    sent_at TIMESTAMPTZ,
    preview_text TEXT,
    metadata_json JSONB DEFAULT '{}'::jsonb,
    rebuilt_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE mart.opportunity_signals (
    id BIGSERIAL PRIMARY KEY,
    entity_kind TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    strength NUMERIC,
    source_email_id BIGINT REFERENCES archive.emails(id) ON DELETE SET NULL,
    source_attachment_id BIGINT REFERENCES archive.attachments(id) ON DELETE SET NULL,
    evidence_json JSONB DEFAULT '{}'::jsonb,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    rebuilt_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### A.4 `leads` schema (excerpt)

```sql
CREATE SCHEMA IF NOT EXISTS leads;

CREATE TABLE leads.external_leads_raw (
    id BIGSERIAL PRIMARY KEY,
    source_name TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    raw_json JSONB NOT NULL,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_name, source_record_id)
);

CREATE TABLE leads.lead (
    id BIGSERIAL PRIMARY KEY,
    source_name TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    -- … all lead_master columns …
    UNIQUE (source_name, source_record_id)
);

CREATE TABLE leads.lead_contact_research (
    lead_id BIGINT PRIMARY KEY REFERENCES leads.lead(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'nuevo',
    resolved_domain TEXT,
    resolved_contact_name TEXT,
    resolved_contact_email TEXT,
    notes TEXT,
    updated_by TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

*(Full account + match tables follow the mapping in §3.4 / §4.)*

### A.5 `commercial` schema (excerpt)

Facts rebuildable; candidates/events durable. **Port full column sets** from `commercial_intel_schema.py`.

### A.6 `outbound` schema (including new audit tables)

```sql
CREATE SCHEMA IF NOT EXISTS outbound;

CREATE TABLE outbound.contact_email_suppression (
    email_norm TEXT PRIMARY KEY,
    reason TEXT,
    source TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE outbound.contact_domain_suppression (
    domain_norm TEXT PRIMARY KEY,
    reason TEXT,
    source TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE outbound.outreach_contact_state (
    contact_email_norm TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    lead_id BIGINT REFERENCES leads.lead(id) ON DELETE SET NULL,
    first_contacted_at TIMESTAMPTZ,
    last_contacted_at TIMESTAMPTZ,
    source TEXT,
    notes TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

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

### A.7 `supplier` schema (excerpt)

Aligns with `supplier_schema.py`; use `supplier.supplier` as the renamed `supplier_master` target or keep table name `supplier_master` inside schema `supplier` if rename churn is undesirable.

### A.8 `reporting` schema (new)

```sql
CREATE SCHEMA IF NOT EXISTS reporting;

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

---

## Summary of decisions (v1 design)

| Decision | Choice |
|----------|--------|
| Namespace model | **PostgreSQL schemas** (`archive`, `ops`, `mart`, `leads`, `commercial`, `outbound`, `supplier`, `reporting`). |
| Primary rename | `lead_master` → **`leads.lead`** in target; SQLite name unchanged until migration. |
| Export audit | New **`outbound.outbound_batch`** + **`outbound.outbound_batch_recipient`**. |
| Reporting audit | New **`reporting.report_run`** + **`reporting.report_artifact`**. |
| Views | **`commercial.v_candidate_queue`**, **`leads.v_lead_match_summary`** recreated in Postgres. |
| Commercial rollups | Kept as **tables** in `commercial` schema (rebuildable); names shortened from `commercial_*` prefix where clarity allows. |

## Assumptions

- **Single Postgres database** with multiple schemas (not separate databases) unless scale review says otherwise.
- **Timestamps** move to **`TIMESTAMPTZ`** where values are ISO datetimes today; raw opaque strings may stay `TEXT` where parsing is lossy.
- **JSON** columns (`details_json`, `raw_json`, …) become **`JSONB`** where queried.
- **Alembic** will generate final DDL; appendix SQL is **illustrative** and must be reconciled column-by-column with current SQLite DDL in code.
