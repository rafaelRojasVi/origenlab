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
            ("procurement_context", "TEXT NOT NULL", "derived via as_of_date — build-plan gated"),
            ("context_reason_code", "TEXT NOT NULL", "why context was assigned"),
            ("confidence", "TEXT NOT NULL", "high|medium|low|none for signal completeness"),
            ("line_item_count", "INTEGER NOT NULL", "coalesced source lines"),
            ("constituent_source_ids_json", "TEXT NOT NULL", "JSON list of source_record_id"),
            ("constituent_lines_fp", "TEXT NOT NULL", "component FP of constituent semantic line payloads"),
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
        "checks": [],
        "notes": (
            "Separate from account_resolution. Never mutates PR2 accounts. Never creates PR3 "
            "opportunities. Unresolved line keys are conflicts, not signals."
        ),
    },
    "commercial_procurement_account_resolution": {
        "purpose": "One deterministic account-resolution row per verified procurement signal.",
        "pk": "resolution_id",
        "columns": [
            ("resolution_id", "TEXT NOT NULL", "hash(procurement|route|reason|status)"),
            ("procurement_id", "TEXT NOT NULL", "physical FK → signal"),
            ("resolution_status", "TEXT NOT NULL", "linked|unlinked|ambiguous|refused"),
            ("account_id", "TEXT", "NULL unless linked; logical PR2 reference (not physical FK)"),
            ("link_route", "TEXT NOT NULL", "A,B,C,E,F,G,H,I"),
            ("confidence", "TEXT NOT NULL", "high|medium|low|none"),
            ("reason_code", "TEXT NOT NULL", "policy reason"),
            ("auto_link_allowed", "INTEGER NOT NULL", "0/1"),
            ("review_status", "TEXT NOT NULL", "ok|needs_review|rejected"),
            ("candidate_account_ids_json", "TEXT", "JSON list for ambiguity — never as selected account_id"),
        ],
        "indexes": [
            "idx_cpar_procurement (procurement_id)",
            "idx_cpar_account (account_id)",
            "idx_cpar_status (resolution_status)",
            "idx_cpar_route (link_route)",
        ],
        "fk": [
            "procurement_id → commercial_procurement_signal(procurement_id) [physical]",
        ],
        "checks": [
            "linked ⇒ account_id IS NOT NULL AND auto_link_allowed=1 AND link_route IN (A,B,C,E)",
            "unlinked|ambiguous|refused ⇒ account_id IS NULL AND auto_link_allowed=0",
        ],
        "notes": (
            "Logical PR2 account reference (not physical SQLite FK). Independent PR2 "
            "DELETE+INSERT rebuildability would break/interfere with a physical cross-model FK. "
            "Apply-time validation: identity schema exists, identity_fp_v2 matches, every linked "
            "account_id exists in commercial_identity_account, rechecked inside write txn."
        ),
    },
    "commercial_procurement_evidence": {
        "purpose": "Exact source pointers explaining every signal field, resolution, or refusal.",
        "pk": "evidence_id",
        "columns": [
            ("evidence_id", "TEXT NOT NULL", "hash(subject|source|pointer|reason)"),
            ("subject_kind", "TEXT NOT NULL", "signal|resolution|conflict|enrichment|unresolved_source"),
            ("subject_id", "TEXT NOT NULL", "procurement_id / resolution_id / conflict_id / subject_key"),
            ("source_system", "TEXT", "required when procurement_id absent"),
            ("source_table", "TEXT NOT NULL", "external_leads_raw|lead_master|…"),
            ("source_record_id", "TEXT NOT NULL", "stable pointer"),
            ("subject_key", "TEXT", "v1|procurement-source|{source_system}|{source_record_id}"),
            ("evidence_type", "TEXT NOT NULL", "tender_key|buyer_name|status|domain|…"),
            ("evidence_at", "TEXT", "source event/date when known — never build time"),
            ("reason_code", "TEXT NOT NULL", ""),
            ("detail_json", "TEXT", "non-PII structured detail"),
        ],
        "indexes": [
            "idx_cpe_subject (subject_kind, subject_id)",
            "idx_cpe_source (source_table, source_record_id)",
            "idx_cpe_subject_key (subject_key)",
        ],
        "fk": [],
        "checks": [],
        "notes": "No email bodies / attachment text. Supports rows with no procurement_id.",
    },
    "commercial_procurement_conflict": {
        "purpose": "Ambiguity / policy refusals / line-field / unresolved-key conflicts.",
        "pk": "conflict_id",
        "columns": [
            (
                "conflict_id",
                "TEXT NOT NULL",
                "for unresolved: hash(v1|procurement-conflict|{source}|{record}|{reason})",
            ),
            ("procurement_id", "TEXT", "nullable when unresolved-key / raw-only"),
            ("source_system", "TEXT", "required when procurement_id absent"),
            ("source_record_id", "TEXT", "required when procurement_id absent"),
            ("subject_kind", "TEXT NOT NULL", "signal|unresolved_source|line_conflict|…"),
            ("subject_key", "TEXT", "stable subject identity without procurement_id"),
            ("account_id", "TEXT", "never a falsely selected link target"),
            ("reason_code", "TEXT NOT NULL", ""),
            ("confidence", "TEXT NOT NULL", ""),
            ("detail_json", "TEXT", "candidate account ids / field hashes — no raw emails"),
            ("created_at", "TEXT NOT NULL", "build stamp metadata only"),
        ],
        "indexes": [
            "idx_cpc_reason (reason_code)",
            "idx_cpc_procurement (procurement_id)",
            "idx_cpc_subject_key (subject_key)",
        ],
        "fk": [],
        "checks": [
            "procurement_id IS NOT NULL OR (source_system IS NOT NULL AND source_record_id IS NOT NULL)",
        ],
        "notes": (
            "Unresolved rows use direct provenance via source_system+source_record_id+subject_key; "
            "do not rely on absent procurement_id alone."
        ),
    },
    "commercial_procurement_enrichment_candidate": {
        "purpose": (
            "Rebuildable human-research candidates (Option B). No mutable "
            "open/in_progress/resolved/dismissed lifecycle in PR4."
        ),
        "pk": "candidate_id",
        "columns": [
            ("candidate_id", "TEXT NOT NULL", ""),
            ("procurement_id", "TEXT", "real procurement id when known"),
            ("source_system", "TEXT", "for unresolved provenance"),
            ("source_record_id", "TEXT", "for unresolved provenance"),
            ("buyer_name_raw", "TEXT", "safe institution name — not redacted audit tokens"),
            ("account_id", "TEXT", "proposed candidate account id when known — not selected link"),
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
        "fk": [],
        "checks": [],
        "notes": (
            "Multiple enrichment reasons per signal allowed. Historical unmatched "
            "signals remain market history (eligible=0)."
        ),
    },
    "commercial_procurement_build_meta": {
        "purpose": "Schema version, source/build-plan fingerprints, as_of_date, identity FP, metrics.",
        "pk": "meta_key",
        "columns": [
            ("meta_key", "TEXT NOT NULL", ""),
            ("meta_value", "TEXT NOT NULL", ""),
        ],
        "indexes": [],
        "fk": [],
        "checks": [],
        "notes": (
            f"schema_version={SCHEMA_VERSION_PROPOSED}; "
            f"build_contract={BUILD_CONTRACT_PROPOSED}; "
            f"source_fp_algorithm={PROCUREMENT_SOURCE_FP_ALGORITHM}; "
            f"build_plan_fp_algorithm={PROCUREMENT_BUILD_PLAN_FP_ALGORITHM}; "
            f"resolver={RESOLVER_BUILD_CONTRACT_VERSION}; "
            f"transaction={TRANSACTION_CONTRACT}; "
            "records matching persisted PR2 identity_fp_v2 on apply; "
            "as_of_date is build-plan input only."
        ),
    },
}

PR2_LOGICAL_REFERENCE_NOTE = """
## PR2 account-reference integrity (logical, not physical FK)

PR2 identity tables are independently rebuildable via DELETE+INSERT (transaction
contract B). A physical SQLite FOREIGN KEY from
`commercial_procurement_account_resolution.account_id` to
`commercial_identity_account.account_id` would:

1. block or cascade-interfere with independent PR2 rebuilds;
2. couple PR4 apply ordering to PR2 physical table lifecycle;
3. risk orphaning or constraint failures during legitimate identity refreshes.

Therefore PR4 v1 uses a **logical** PR2 account reference with apply-time
validation:

- persisted identity schema exists;
- `identity_fp_v2` matches the planned fingerprint;
- every `resolution_status=linked` row has `account_id` present in
  `commercial_identity_account`;
- unlinked / ambiguous / refused rows never store `account_id`;
- account existence is rechecked inside the write transaction;
- identity fingerprint is recorded in `commercial_procurement_build_meta`.

Physical FKs remain required **within** the `commercial_procurement_*`
namespace (e.g. resolution → signal).
""".strip()


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
        "Signal and **account_resolution** are separate tables (one resolution row per signal).",
        "Enrichment uses rebuildable `commercial_procurement_enrichment_candidate` (Option B).",
        "No mutable operator lifecycle state in PR4.",
        "PR4 does not mutate PR2 accounts or PR3 opportunity stages.",
        "",
        PR2_LOGICAL_REFERENCE_NOTE,
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
        if spec.get("checks"):
            lines.append("- CHECK: " + "; ".join(f"`{x}`" for x in spec["checks"]))
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


__all__ = ["PR2_LOGICAL_REFERENCE_NOTE", "PROPOSED_TABLES", "schema_contract_markdown"]
