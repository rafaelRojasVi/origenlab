"""Proposed PR4 schema contract (design only — not applied to production)."""

from __future__ import annotations

from typing import Any

from origenlab_email_pipeline.qa.commercial_procurement_link_audit.constants import (
    BUILD_CONTRACT_PROPOSED,
    PROCUREMENT_BUILD_PLAN_FP_ALGORITHM,
    PROCUREMENT_SOURCE_FP_ALGORITHM,
    RESOLVER_BUILD_CONTRACT_VERSION,
    SCHEMA_VERSION_PROPOSED,
    TRANSACTION_CONTRACT,
)

PROPOSED_TABLES: dict[str, dict[str, Any]] = {
    "commercial_procurement_signal": {
        "purpose": "Canonical verified procurement/tender observation (one row per verified tender key).",
        "pk": "procurement_id",
        "columns": [
            ("procurement_id", "TEXT NOT NULL", "stable id v1|procurement|{source}|{tender_key}"),
            ("source_system", "TEXT NOT NULL", "e.g. chilecompra"),
            ("canonical_tender_key", "TEXT NOT NULL", "verified tender-level key only"),
            ("tender_key_kind", "TEXT NOT NULL", "codigo_externo|codigo_licitacion|numero_adquisicion"),
            ("buyer_name_raw", "TEXT", "safe institution display name (not redacted tokens)"),
            ("buyer_name_norm", "TEXT", "normalized buyer name"),
            ("buyer_domain_norm", "TEXT", "institutional only; never marketplace"),
            ("buyer_email_norm", "TEXT", "optional contact email"),
            ("region", "TEXT", "optional; never identity alone"),
            ("title", "TEXT", "tender title"),
            ("status_code", "TEXT", "structured ChileCompra code"),
            ("status_name", "TEXT", "structured status label"),
            ("publication_at", "TEXT", "ISO date if known"),
            ("close_at", "TEXT", "ISO date if known"),
            ("procurement_context", "TEXT NOT NULL", "none|tender_watch|tender_active|historical_tender|unknown"),
            ("context_reason_code", "TEXT NOT NULL", "why context was assigned"),
            ("confidence", "TEXT NOT NULL", "high|medium|low|none for signal completeness"),
            ("line_item_count", "INTEGER NOT NULL", "coalesced source lines"),
            ("constituent_source_ids_json", "TEXT NOT NULL", "JSON list of source_record_id"),
            ("first_seen_at", "TEXT", "min source observation"),
            ("last_seen_at", "TEXT", "max source observation"),
            ("review_status", "TEXT NOT NULL", "ok|needs_review (rebuildable flag, not operator lifecycle)"),
        ],
        "indexes": [
            "idx_cps_tender_key (source_system, canonical_tender_key)",
            "idx_cps_buyer_domain (buyer_domain_norm)",
            "idx_cps_context (procurement_context)",
        ],
        "fk": [],
        "notes": "Separate from account-link. Never mutates PR2 accounts. Never creates PR3 opportunities. Unresolved line keys are conflicts, not signals.",
    },
    "commercial_procurement_account_link": {
        "purpose": "Optional link from a procurement signal to a PR2 commercial_identity_account.",
        "pk": "link_id",
        "columns": [
            ("link_id", "TEXT NOT NULL", "hash(signal|account|route|reason)"),
            ("procurement_id", "TEXT NOT NULL", "FK signal"),
            ("account_id", "TEXT", "nullable when unresolved; real PR2 id, not redacted"),
            ("link_route", "TEXT NOT NULL", "A,B,C,E,F,G,H,I (D removed)"),
            ("confidence", "TEXT NOT NULL", "high|medium|low|none"),
            ("reason_code", "TEXT NOT NULL", "policy reason"),
            ("auto_link_allowed", "INTEGER NOT NULL", "0/1"),
            ("review_status", "TEXT NOT NULL", "ok|needs_review|rejected"),
        ],
        "indexes": [
            "idx_cpal_procurement (procurement_id)",
            "idx_cpal_account (account_id)",
            "idx_cpal_route (link_route)",
        ],
        "fk": [
            "procurement_id → commercial_procurement_signal(procurement_id)",
            "account_id → commercial_identity_account(account_id) (soft; no cascade mutate)",
        ],
        "notes": "Prefer separate table vs embedding account_id on the signal.",
    },
    "commercial_procurement_evidence": {
        "purpose": "Exact source pointers explaining every signal field, link, or refusal.",
        "pk": "evidence_id",
        "columns": [
            ("evidence_id", "TEXT NOT NULL", "hash(subject|source|pointer|reason)"),
            ("subject_kind", "TEXT NOT NULL", "signal|link|conflict|enrichment"),
            ("subject_id", "TEXT NOT NULL", "procurement_id / link_id / …"),
            ("source_table", "TEXT NOT NULL", "external_leads_raw|lead_master|…"),
            ("source_record_id", "TEXT NOT NULL", "stable pointer"),
            ("evidence_type", "TEXT NOT NULL", "tender_key|buyer_name|status|domain|…"),
            ("evidence_at", "TEXT", "source event/date when known — never build time"),
            ("reason_code", "TEXT NOT NULL", ""),
            ("detail_json", "TEXT", "non-PII structured detail"),
        ],
        "indexes": ["idx_cpe_subject (subject_kind, subject_id)", "idx_cpe_source (source_table)"],
        "fk": [],
        "notes": "No email bodies / attachment text. Multiple rows per signal allowed.",
    },
    "commercial_procurement_conflict": {
        "purpose": "Ambiguity / policy refusals / line-field conflicts (not silent merges).",
        "pk": "conflict_id",
        "columns": [
            ("conflict_id", "TEXT NOT NULL", ""),
            ("procurement_id", "TEXT", "nullable for unresolved-key rows"),
            ("account_id", "TEXT", "real account id when known"),
            ("reason_code", "TEXT NOT NULL", ""),
            ("confidence", "TEXT NOT NULL", ""),
            ("detail_json", "TEXT", "candidate account ids / field names"),
            ("created_at", "TEXT NOT NULL", "build stamp metadata only"),
        ],
        "indexes": ["idx_cpc_reason (reason_code)", "idx_cpc_procurement (procurement_id)"],
        "fk": [],
        "notes": "Does not copy suppression, outreach, classification, or deal state. Multiple reasons per signal allowed.",
    },
    "commercial_procurement_enrichment_candidate": {
        "purpose": (
            "Rebuildable human-research candidates (Option B). No mutable "
            "open/in_progress/resolved/dismissed lifecycle in PR4 — durable operator "
            "review events deferred to a later PR if needed."
        ),
        "pk": "candidate_id",
        "columns": [
            ("candidate_id", "TEXT NOT NULL", ""),
            ("procurement_id", "TEXT", "real procurement id when known"),
            ("buyer_name_raw", "TEXT", "safe institution name — not redacted audit tokens"),
            ("account_id", "TEXT", "proposed candidate account id when known"),
            ("reason_code", "TEXT NOT NULL", ""),
            ("confidence", "TEXT NOT NULL", ""),
            ("recommended_research_field", "TEXT NOT NULL", "domain|contact|status|tender_id|…"),
            ("priority", "INTEGER NOT NULL", "evidence completeness only"),
            ("operator_queue_eligible", "INTEGER NOT NULL", "0/1 conservative eligibility"),
            ("candidate_account_ids_json", "TEXT", "JSON list of real account ids"),
        ],
        "indexes": [
            "idx_cpec_reason (reason_code)",
            "idx_cpec_eligible (operator_queue_eligible)",
            "idx_cpec_procurement (procurement_id)",
        ],
        "fk": ["procurement_id → commercial_procurement_signal (soft)"],
        "notes": (
            "Multiple enrichment reasons per signal allowed. Historical unmatched "
            "signals remain market history (eligible=0), not immediate tasks. "
            "Redacted tokens are for audit CSV/JSON only."
        ),
    },
    "commercial_procurement_build_meta": {
        "purpose": "Schema version, source/build-plan fingerprints, as_of_date, run context, metrics JSON.",
        "pk": "meta_key",
        "columns": [
            ("meta_key", "TEXT NOT NULL", ""),
            ("meta_value", "TEXT NOT NULL", ""),
        ],
        "indexes": [],
        "fk": [],
        "notes": (
            f"schema_version={SCHEMA_VERSION_PROPOSED}; "
            f"build_contract={BUILD_CONTRACT_PROPOSED}; "
            f"source_fp_algorithm={PROCUREMENT_SOURCE_FP_ALGORITHM}; "
            f"build_plan_fp_algorithm={PROCUREMENT_BUILD_PLAN_FP_ALGORITHM}; "
            f"resolver={RESOLVER_BUILD_CONTRACT_VERSION}; "
            f"transaction={TRANSACTION_CONTRACT}; "
            "requires matching persisted PR2 identity_fp_v2 on apply; "
            "as_of_date recorded separately from source fingerprint."
        ),
    },
}


def schema_contract_markdown() -> str:
    lines = [
        "# Proposed PR4 schema — `commercial_procurement_*`",
        "",
        f"- schema_version: `{SCHEMA_VERSION_PROPOSED}`",
        f"- build_contract: `{BUILD_CONTRACT_PROPOSED}`",
        f"- source fingerprint: `{PROCUREMENT_SOURCE_FP_ALGORITHM}`",
        f"- build-plan fingerprint: `{PROCUREMENT_BUILD_PLAN_FP_ALGORITHM}`",
        f"- resolver: `{RESOLVER_BUILD_CONTRACT_VERSION}`",
        f"- transaction: `{TRANSACTION_CONTRACT}`",
        "",
        "Signal and account-link are **separate tables**.",
        "Enrichment uses rebuildable `commercial_procurement_enrichment_candidate` (Option B).",
        "No mutable operator lifecycle state in PR4.",
        "PR4 does not mutate PR2 accounts or PR3 opportunity stages.",
        "",
    ]
    for table, spec in PROPOSED_TABLES.items():
        lines.append(f"## `{table}`")
        lines.append("")
        lines.append(spec["purpose"])
        lines.append("")
        lines.append(f"- PK: `{spec['pk']}`")
        if spec["fk"]:
            lines.append("- FK: " + "; ".join(f"`{x}`" for x in spec["fk"]))
        if spec["indexes"]:
            lines.append("- Indexes: " + ", ".join(f"`{x}`" for x in spec["indexes"]))
        lines.append("")
        lines.append("| Column | Type | Notes |")
        lines.append("|--------|------|-------|")
        for col, typ, note in spec["columns"]:
            lines.append(f"| `{col}` | `{typ}` | {note} |")
        lines.append("")
        lines.append(f"_Notes:_ {spec['notes']}")
        lines.append("")
    return "\n".join(lines)


__all__ = ["PROPOSED_TABLES", "schema_contract_markdown"]
