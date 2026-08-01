"""Duplication matrix across overlapping ChileCompra / lead / equipment paths."""

from __future__ import annotations

from typing import Any


def existing_paths_document() -> dict[str, Any]:
    return {
        "lanes": {
            "pr4_procurement_truth": {
                "tables": [
                    "commercial_procurement_signal",
                    "commercial_procurement_account_resolution",
                    "commercial_procurement_evidence",
                    "commercial_procurement_conflict",
                    "commercial_procurement_enrichment_candidate",
                    "commercial_procurement_build_meta",
                ],
                "modules": [
                    "src/origenlab_email_pipeline/commercial_procurement/",
                    "scripts/commercial/build_commercial_procurement_read_model.py",
                ],
                "role": "Rebuildable tender↔account evidence truth over file-backed leads + PR2",
            },
            "generic_lead_pipeline": {
                "scripts": [
                    "scripts/leads/fetch_chilecompra.py",
                    "scripts/leads/normalize_leads.py",
                    "scripts/leads/leads_score.py",
                    "scripts/leads/match_leads_to_mart.py",
                    "scripts/leads/run_weekly_focus.py",
                    "scripts/leads/export_leads_shortlist.py",
                    "scripts/leads/advanced/export_contact_hunt_sheet.py",
                    "scripts/leads/advanced/merge_contact_hunt_enrichment.py",
                    "scripts/leads/advanced/import_contact_hunt_to_sqlite.py",
                ],
                "tables": [
                    "external_leads_raw",
                    "lead_master",
                    "lead_matches_existing_orgs",
                    "lead_outreach_enrichment",
                ],
                "role": "File ingest + CRM-ish lead workflow; status!=tender validity",
            },
            "equipment_first_queue": {
                "modules": [
                    "equipment_first_licitacion_queue.py",
                    "equipment_first_operator_queue.py",
                    "equipment_first_chilecompra_queue.py",
                    "chilecompra_api.py",
                    "equipment_opportunity_mirror.py",
                ],
                "artifacts": "reports/out/active/current/equipment_first_operator_queue_*.csv",
                "role": "Live/API + Licitacion_Publicada equipment ICP operator queue",
            },
            "product_sources": {
                "web": [
                    "apps/web/src/data/products.ts",
                    "brands.ts",
                    "categories.ts",
                    "productFamilies.ts",
                    "scripts/validate-catalog.mjs",
                ],
                "docs": ["docs/catalog/PRODUCT_CATALOG_SCHEMA_AUDIT_V1.md"],
            },
            "contact_sources": {
                "pr2": "commercial_identity_contact (+ account links)",
                "leads": "lead_master contact fields; lead_outreach_enrichment",
                "safety": [
                    "contact_email_suppression",
                    "contact_domain_suppression",
                    "outreach_contact_state",
                ],
            },
        },
        "duplication_matrix": [
            {
                "concept": "ChileCompra acquisition",
                "existing_implementations": [
                    "fetch_chilecompra.py (file→SQLite)",
                    "chilecompra_api.py + equipment_first_chilecompra_queue.py (API→CSV)",
                    "Licitacion_Publicada parser in equipment_first_licitacion_queue.py",
                ],
                "canonical_candidate": "Official API for live published/open; file bulk as fallback/backfill",
                "reuse_refactor_retire": "Reuse API client; do not scrape HTML; keep file ingest for corpus rebuild",
            },
            {
                "concept": "Tender identity grain",
                "existing_implementations": [
                    "PR4 canonical_tender_key (verified tender-level)",
                    "lead source_record_id often line-level",
                    "equipment queue groups by codigo",
                ],
                "canonical_candidate": "PR4 verified tender key",
                "reuse_refactor_retire": "PR5 candidate grain = one per tender; lines as separate evidence",
            },
            {
                "concept": "Active / close semantics",
                "existing_implementations": [
                    "PR4 status.classify_procurement_context (UTC calendar AS_OF)",
                    "API classify_chilecompra_validity_status",
                    "equipment next_action vs naive now",
                    "mirror America/Santiago close_at",
                ],
                "canonical_candidate": "PR5 active classifier America/Santiago + positive Publicada evidence",
                "reuse_refactor_retire": "Reuse ChileCompra code maps; do not treat lead_master.status as open",
            },
            {
                "concept": "Equipment relevance tags",
                "existing_implementations": [
                    "business_mart / leads_equipment Spanish tags",
                    "equipment_first English EQUIPMENT_RULES",
                    "web filterTags / productFamilies",
                ],
                "canonical_candidate": "Canonical equipment_class + alias map (PR5 taxonomy)",
                "reuse_refactor_retire": "Reuse exclusion regexes; map aliases; no silent rename of historical fields",
            },
            {
                "concept": "Buyer → account link",
                "existing_implementations": [
                    "PR4 → PR2 link_routes",
                    "leads match_leads_to_mart → organization_master",
                ],
                "canonical_candidate": "PR4→PR2 for procurement candidates",
                "reuse_refactor_retire": "Do not merge mart match into PR2 automatically",
            },
            {
                "concept": "Operator queue eligibility",
                "existing_implementations": [
                    "PR4 operator_queue_eligible (enrichment)",
                    "equipment next_action / safe_channel",
                    "weekly focus USAR/REFERENCIA",
                ],
                "canonical_candidate": "PR5 candidate_outcome_state + human review",
                "reuse_refactor_retire": "Compose PR4 context + PR5 relevance; keep lanes separate in UI",
            },
            {
                "concept": "Contact enrichment",
                "existing_implementations": [
                    "PR2 contacts",
                    "lead_master fields",
                    "lead_outreach_enrichment / hunt sheets",
                ],
                "canonical_candidate": "Ordered search: PR2 → lead_master → enrichment → participants",
                "reuse_refactor_retire": "Hunt remains human research; never invent emails",
            },
            {
                "concept": "Suppression / outreach",
                "existing_implementations": [
                    "contact_email_suppression",
                    "contact_domain_suppression",
                    "outreach_contact_state",
                ],
                "canonical_candidate": "Read-only gates on outreach_review_candidate",
                "reuse_refactor_retire": "Out of scope for relevance scoring; never auto-send",
            },
            {
                "concept": "fit_bucket / priority_score",
                "existing_implementations": ["leads_score.py"],
                "canonical_candidate": "Not PR5 truth",
                "reuse_refactor_retire": "May inform analytics only; relevance_class is authoritative",
            },
        ],
    }
