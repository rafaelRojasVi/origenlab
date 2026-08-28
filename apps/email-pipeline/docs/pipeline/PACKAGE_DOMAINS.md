# Python package domains (`origenlab_email_pipeline`)

Status: canonical  
Owner: email-pipeline-maintainers  
Last reviewed: 2026-08-07

This document is **Phase 0+**: it clarifies **domain ownership** and **import boundaries** for code under [`src/origenlab_email_pipeline/`](../../src/origenlab_email_pipeline/). **Phase 1 (2026-04):** the **commercial intel** cluster lives under [`commercial/`](../../src/origenlab_email_pipeline/commercial/) (root `commercial_intel_*` shims removed Phase 5I). The **operational trust** cluster lives only under [`operational_trust/`](../../src/origenlab_email_pipeline/operational_trust/); import the public API from that package (facade in [`__init__.py`](../../src/origenlab_email_pipeline/operational_trust/__init__.py)) or from submodules such as `operational_trust.operational_trust_csv` — **root `operational_trust_*.py` shims were removed** after verification of zero in-repo callers. Further physical grouping is still optional for other domains.

<a id="m-pkg-purpose"></a>
## Why this exists

The package root lists **many modules at one namespace level**. That is workable, but **cognitive load** is high. Use this map to decide where a change belongs and which dependencies are allowed.

<a id="m-pkg-domains"></a>
## Domain map (logical, not physical paths)

| Domain | Owns (typical modules) | Core vs optional |
|--------|----------------------|------------------|
| **Shared core / root** | `db.py`, `config.py`, `sqlite_migrate.py`, `pipeline_meta_schema.py`, `pipeline_run_recorder.py`, `timeutil.py`, `progress.py`, `business_mart.py`, `business_mart_schema.py`, `bi_views.py`, `freshness_dates.py`, `email_business_filters.py`, `business_filter_rules.py`, `attachment_extract.py`, `parse_mbox.py`, `export_jsonl.py`, `contacto_gmail_source.py`, `gmail_workspace_oauth.py`, `cases_review_queue.py`, `contact_email_suppression.py`, `outreach_contact_state.py`, `reported_non_delivery_signals.py`, `ndr_bounce_extraction.py`, `org_normalize.py`, `outbound_readiness_check.py` | **Core** for DB/mart/ingest and cross-cutting SQLite utilities. |
| **Stable outbound anchors** | `candidate_export_gate.py`, `marketing_export_context.py`, `outbound_core.py` (shared Gmail/Sent resolution + lane `GateContext` helpers + `outbound_run` summary envelope), `marketing_contact_noise.py`, `next_marketing_queue.py`, `contact_export_queries.py` | **Core** for cold-export **policy** and **shared GateContext**. **Do not relocate** without a dedicated migration plan (high fan-in). |
| **Archive / warm outreach** | `archive_send_batch_builder.py`, `archive_outreach_queue.py`, `archive_shortlist_commercial_precheck.py`, `outreach_queue_compare.py` | **Core** for archive batch + commercial precheck **integration**; builds on gate + mart. |
| **Commercial intel / read models** | [`commercial/`](../../src/origenlab_email_pipeline/commercial/) — `commercial_intel_schema.py`, `commercial_intel_queries.py`, `commercial_intel_review.py`, `commercial_intel_rules.py`; audit/break-glass read-model packages: [`commercial_identity/`](../../src/origenlab_email_pipeline/commercial_identity/), [`commercial_opportunity/`](../../src/origenlab_email_pipeline/commercial_opportunity/), [`commercial_procurement/`](../../src/origenlab_email_pipeline/commercial_procurement/), [`commercial_procurement_acquisition/`](../../src/origenlab_email_pipeline/commercial_procurement_acquisition/), [`commercial_procurement_live_relevance/`](../../src/origenlab_email_pipeline/commercial_procurement_live_relevance/), [`commercial_procurement_live_feed_bridge/`](../../src/origenlab_email_pipeline/commercial_procurement_live_feed_bridge/), [`commercial_procurement_candidate_planner/`](../../src/origenlab_email_pipeline/commercial_procurement_candidate_planner/), [`commercial_procurement_product_relevance/`](../../src/origenlab_email_pipeline/commercial_procurement_product_relevance/), [`commercial_procurement_contact_resolution/`](../../src/origenlab_email_pipeline/commercial_procurement_contact_resolution/), [`commercial_procurement_institution_prospects/`](../../src/origenlab_email_pipeline/commercial_procurement_institution_prospects/) | **Core** for Engine-B review/suppression semantics on top of SQLite; read-model / recognition packages are separate audit/break-glass layers and **not** daily outbound send truth, outreach authorization, or PR5F persistence. |
| **Leads (pipeline + master)** | `leads_*.py`, `lead_*.py`, `lead_accounts_schema.py`, `hunt_csv_alignment.py`, `dr50_payload_loader.py` | **Core** for Chile external-lead ingest → `lead_master`; **lead accounts** are additive. See [naming](#m-pkg-naming) below. |
| **Client reports** | `client_report_*.py`, `attachment_report_sql.py` | **Secondary** (client-facing narrative/metrics); not on the outbound send path. |
| **Operational trust** | [`operational_trust/`](../../src/origenlab_email_pipeline/operational_trust/) — facade [`__init__.py`](../../src/origenlab_email_pipeline/operational_trust/__init__.py) + `operational_trust_*.py` submodules (**no** root-level `operational_trust_*.py`) | **Core** for publish-safe QA **consistency** checks; not eligibility for send lists. |
| **Suppliers** | `supplier_schema.py`, `supplier_workbook.py`, `marketing_supplier_domains.py` | **Core** for supplier domain exclusion in the gate; workbook tooling is **operator-facing** but not send logic. |
| **Tatiana / drafting** | `tatiana_copilot/` (subpackage), `tatiana_review_cohort.py`, `tatiana_voice_cohort.py` | **Optional** copilot; **no send path**; must not own export eligibility. |

<a id="m-pkg-naming"></a>
## Naming: `lead_*` vs `leads_*`

Convention today (historical but consistent):

| Prefix | Meaning | Examples |
|--------|---------|----------|
| **`leads_*`** | **Pipeline and table suite** for external Chile leads: schema, ingest, normalize, score, match, equipment helpers. | `leads_schema.py`, `leads_normalize.py`, `leads_ingest.py`, `leads_match.py` |
| **`lead_*`** | **Single-lead / master-row semantics**: identity keys, dedupe, upstream lifecycle, account rollup hooks, provenance, hunt alignment helpers tied to `id_lead`. | `lead_master_keys.py`, `lead_provenance.py`, `lead_upstream_reconcile.py`, `lead_accounts_schema.py` |

**Rule of thumb:** if it defines **`lead_master`** lifecycle or **one row’s** identity → `lead_*`. If it defines **pipeline stages** or the **`leads_` DDL bundle** → `leads_*`. Merging these families is **out of scope** for Phase 0.

<a id="m-pkg-import-rules"></a>
## Import and dependency rules

1. **Export eligibility is centralized** — Archive and lead marketing exports must use **`candidate_export_gate.evaluate_export_eligibility`** (and **`marketing_export_context.build_marketing_export_gate_context`** for DB-backed sets). Do not duplicate gate rules in Tatiana, dashboard read paths, or one-off scripts.
2. **Dashboard/API are not send truth** — Mirror and React panels are read-only; sidecar writes stay on **documented CLIs** (and opt-in `ORIGENLAB_OPERATOR_*_RW` env gates; legacy `ORIGENLAB_STREAMLIT_*` names still accepted). **Canonical batch selection** remains CLIs and reproducible CSVs ([`OUTBOUND_SOURCE_OF_TRUTH.md`](../OUTBOUND_SOURCE_OF_TRUTH.md)).
3. **Tatiana does not own eligibility** — Drafting and retrieval must **not** implement or bypass marketing/archive export gates. Copilot code stays in `tatiana_copilot/` + small cohort entry modules.
4. **Operational trust ≠ export gate** — `operational_trust*` checks **publish consistency** (pack, CSVs, cohorts, URLs). It does not replace `candidate_export_gate` for “who may be emailed.”
5. **Stable root anchors (for now)** — **`candidate_export_gate.py`**, **`marketing_export_context.py`**, and **`outbound_core.py`** remain at package root intentionally. Future package restructures should **move other domains first** and treat these as last-mile moves.
6. **Commercial intel imports** — Use **`origenlab_email_pipeline.commercial.commercial_intel_*`** for schema/queries/rules/review; do not duplicate SQL or review semantics outside this cluster.
7. **Operational trust imports** — Use **`origenlab_email_pipeline.operational_trust`** (package facade) for the published QA API; implementation modules are **`origenlab_email_pipeline.operational_trust.operational_trust_*`**. Do not bypass the facade for scripts unless you are testing a single submodule.

<a id="m-pkg-guards"></a>
## Automated guard (lightweight)

[`tests/test_package_import_boundaries.py`](../../tests/test_package_import_boundaries.py) encodes a **minimal** subset of the rules above (forbidden imports under `tatiana_copilot/`). Extend only when the check stays simple and high-signal.

## See also

- Data flow and gates: [`ARCHITECTURE.md`](../ARCHITECTURE.md)
- App-level context: [`APP_CONTEXT.md`](../APP_CONTEXT.md)
- Outbound lanes: [`OUTBOUND_SOURCE_OF_TRUTH.md`](../OUTBOUND_SOURCE_OF_TRUTH.md)
