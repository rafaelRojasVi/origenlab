"""Deterministic PR5A walkthrough case selection and redacted bundles."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from origenlab_email_pipeline.commercial_procurement.walkthrough_redaction import (
    REDACTION_ALGORITHM,
    assert_no_pii_leaks,
    redact_mapping,
)
from origenlab_email_pipeline.commercial_procurement_live_relevance.artifact_open import (
    classify_artifact_row_open,
    inspect_artifact_provenance,
    pick_best_open_row,
    summarize_open_classes,
)
from origenlab_email_pipeline.commercial_procurement_live_relevance.constants import (
    ACTIVE_CLASSIFIER_VERSION,
    AS_OF_TIMEZONE,
    CONTACT_RESOLUTION_DEFERRED,
    CONTACT_RESOLVER_VERSION,
    RELEVANCE_CLASSIFIER_VERSION,
)
from origenlab_email_pipeline.commercial_procurement_live_relevance.production_cases import (
    assert_no_forbidden_identifier_leaks,
)

SANTIAGO = ZoneInfo(AS_OF_TIMEZONE)

SELECTION_RULES: dict[str, str] = {
    "case_a_artifact_declared_open": (
        "Select recent_artifact_declared_open rows only after strict status, "
        "America/Santiago close_at, provenance, and freshness checks. "
        "Never label artifact-only evidence as live_verified_open without an "
        "authenticated API revalidation at the audit instant."
    ),
    "case_b_historical_equipment": (
        "PR4 historical_tender with equipment keyword; must remain not_eligible."
    ),
    "case_c_excluded": (
        "Real source text hitting consumable/service/rental exclusion keywords; "
        "emit relevance evidence + not_eligible_reason, not a conflict."
    ),
    "case_d_contact_research": (
        "Historical linked account with no suitable contact; final outcome "
        "not_eligible; hypothetical_contact_path may explain contact research."
    ),
    "case_e_existing_contact": (
        "Historical linked account with PR2 contacts; final outcome not_eligible; "
        "hypothetical_contact_path may explain outreach-review."
    ),
}


@dataclass(frozen=True)
class Pr5WalkthroughBundle:
    summary: dict[str, Any]
    case_a: dict[str, Any]
    case_b: dict[str, Any]
    case_c: dict[str, Any]
    case_d: dict[str, Any]
    case_e: dict[str, Any]
    forbidden_domains: frozenset[str]
    forbidden_values: frozenset[str]


def select_case_ids(seeds: dict[str, Any]) -> dict[str, str | None]:
    """Deterministic IDs from a seeds/fixture document."""
    a = seeds.get("case_a_open_row") or seeds.get("case_a")
    b = seeds.get("case_b_equipment_keyword_overlay") or seeds.get(
        "case_b_linked_historical"
    ) or seeds.get("case_b")
    c = seeds.get("case_c_exclusion_keyword_hit") or seeds.get("case_c")
    d = seeds.get("case_d_linked_contact_n") or seeds.get("case_d")
    e = seeds.get("case_e_linked_with_contact") or seeds.get("case_e")

    def _id(obj: Any, *keys: str) -> str | None:
        if not isinstance(obj, dict):
            return None
        for k in keys:
            if obj.get(k):
                return str(obj[k])
        return None

    return {
        "case_a": _id(a, "selection_id", "procurement_id_redacted", "codigo_licitacion_redacted"),
        "case_b": _id(b, "procurement_id_redacted", "procurement_id"),
        "case_c": _id(c, "procurement_id_redacted", "procurement_id"),
        "case_d": _id(d, "procurement_id_redacted", "procurement_id"),
        "case_e": _id(e, "procurement_id_redacted", "procurement_id"),
    }


def _stage(
    stage: str,
    source_value: Any,
    normalized_value: Any,
    rule: str,
    result: str,
) -> dict[str, Any]:
    return {
        "stage": stage,
        "source_value": source_value,
        "normalized_value": normalized_value,
        "rule_reason": rule,
        "result": result,
    }


def _planned_row(table: str, row: dict[str, Any], why: str) -> dict[str, Any]:
    return {"proposed_table": table, "planned_redacted_row": row, "why_emitted": why}


def build_case_a_from_open_queue_row(
    row: dict[str, Any],
    *,
    artifact_path: Path,
    as_of: datetime,
    classification: Any,
    provenance: Any,
) -> dict[str, Any]:
    """Case A from a strictly classified artifact-declared-open row."""
    from origenlab_email_pipeline.commercial_procurement.walkthrough_redaction import (
        redact_org,
        redact_tender,
        redact_token,
    )

    redacted = {
        "codigo_licitacion": redact_tender(row.get("codigo_licitacion")),
        "buyer": redact_org(row.get("buyer")),
        "title": redact_token("title", row.get("title") or "") if row.get("title") else None,
        "item_description": redact_token(
            "title", row.get("item_description") or row.get("line_description") or ""
        )
        if (row.get("item_description") or row.get("line_description"))
        else None,
        "descripcion": redact_token("title", row.get("descripcion") or "")
        if row.get("descripcion")
        else None,
        "region": redact_token("title", row.get("region") or "") if row.get("region") else None,
        "mercado_publico_url": redact_token("source", row.get("mercado_publico_url") or "")
        if row.get("mercado_publico_url")
        else None,
    }
    redacted = redact_mapping(redacted)

    open_class = classification.open_class
    is_recent_declared = open_class == "recent_artifact_declared_open"
    equipment_category = row.get("equipment_category")
    relevance = (
        "strong_equipment_class" if equipment_category else "laboratory_context_only"
    )
    outcome = (
        "account_resolution_required"
        if is_recent_declared
        else "not_eligible"
    )

    stages = [
        _stage(
            "open_classification",
            row.get("validity_status"),
            open_class,
            SELECTION_RULES["case_a_artifact_declared_open"],
            open_class,
        ),
        _stage(
            "status",
            f"{row.get('chilecompra_status_code')}/{row.get('chilecompra_status')}",
            "publicada" if str(row.get("chilecompra_status_code")) == "5" else None,
            f"{ACTIVE_CLASSIFIER_VERSION}: code 5 + Publicada + close>as_of",
            classification.close_at_america_santiago,
        ),
        _stage(
            "live_api_revalidation",
            False,
            "not_performed",
            "PR5A does not make authenticated ChileCompra requests",
            "current_status_not_independently_revalidated",
        ),
        _stage(
            "relevance",
            equipment_category,
            equipment_category,
            f"{RELEVANCE_CLASSIFIER_VERSION}: equipment_first category → equipment_class",
            relevance,
        ),
        _stage(
            "product_resolution",
            None,
            "equipment_class_only",
            "No SKU/model/alias evidence in queue row",
            "equipment_class_only",
        ),
        _stage(
            "candidate_source_kind",
            "live_snapshot_artifact",
            "live_snapshot",
            "Absent from PR4 file-backed corpus → live-only coalescence",
            "live_only",
        ),
        _stage(
            "account_resolution",
            "not_in_pr4",
            "unresolved",
            "Contact search blocked until account resolved",
            "account_resolution_required",
        ),
        _stage(
            "candidate_outcome",
            None,
            outcome,
            "Live-only relevant tender without resolved account",
            outcome,
        ),
    ]

    planned = [
        _planned_row(
            "commercial_procurement_candidate",
            {
                "tender_key_redacted": redacted.get("codigo_licitacion"),
                "candidate_source_kind": "live_snapshot",
                "pr4_procurement_id": None,
                "coalescence_status": "live_only",
                "active_status_class": "active_open"
                if is_recent_declared
                else "status_unknown",
                "closing_soon_bucket": "not_applicable",
                "relevance_class": relevance,
                "equipment_class": equipment_category,
                "product_resolution_status": "equipment_class_only",
                "candidate_outcome_state": outcome,
            },
            "Artifact-declared open live-only candidate (not live-verified)",
        ),
        _planned_row(
            "commercial_procurement_line_relevance",
            {
                "equipment_class": equipment_category,
                "relevance_class": relevance,
                "item_description_redacted": redacted.get("item_description"),
            },
            "Line-level equipment evidence",
        ),
        _planned_row(
            "commercial_procurement_contact_resolution",
            {
                "final_contact_status": CONTACT_RESOLUTION_DEFERRED,
                "search_stages_completed": [],
                "considered_contact_count": 0,
                "next_action": "resolve_account",
                "reason_code": "account_unresolved",
            },
            "Contact search deferred — prerequisite account unresolved",
        ),
    ]

    prov_dict = provenance.to_dict() if provenance else None
    return {
        "case_id": "A_artifact_declared_open_relevant",
        "live_verified_open": False,
        "open_classification": open_class,
        "current_status_independently_revalidated": False,
        "selection_rule": SELECTION_RULES["case_a_artifact_declared_open"],
        "selection_id": redacted.get("codigo_licitacion"),
        "source_artifact": artifact_path.name,
        "artifact_provenance": prov_dict,
        "effective_artifact_timestamp_utc": (
            (prov_dict or {}).get("effective_artifact_timestamp_utc")
        ),
        "effective_timestamp_source": (prov_dict or {}).get("effective_timestamp_source"),
        "artifact_age_seconds": (prov_dict or {}).get("artifact_age_seconds"),
        "contact_resolution_status": CONTACT_RESOLUTION_DEFERRED,
        "candidate_outcome_state": outcome,
        "classification": classification.to_dict(),
        "as_of_america_santiago": as_of.isoformat(),
        "redacted_fields": redacted,
        "equipment_category": equipment_category,
        "stages": stages,
        "planned_pr5_rows": planned,
        "field_flow": [
            "source artifact / PR4 row",
            "provenance normalization",
            "active classification",
            "relevance classification",
            "account resolution",
            "contact resolution state",
            "candidate outcome",
        ],
    }


def build_case_b_historical(seed: dict[str, Any]) -> dict[str, Any]:
    stages = [
        _stage(
            "pr4_context",
            seed.get("procurement_context") or "historical_tender",
            "historical_tender",
            "PR4 persisted procurement_context",
            "historical_tender",
        ),
        _stage(
            "equipment_keyword",
            seed.get("equipment_keyword_hit"),
            seed.get("equipment_keyword_hit"),
            f"{RELEVANCE_CLASSIFIER_VERSION}: keyword → equipment_class (not SKU)",
            "strong_equipment_class",
        ),
        _stage(
            "active_status_class",
            seed.get("close_at"),
            "closed",
            f"{ACTIVE_CLASSIFIER_VERSION}: close_at < America/Santiago as_of",
            "closed",
        ),
        _stage(
            "candidate_outcome",
            "would_look_relevant",
            "not_eligible",
            "Historical tenders must not enter the current operator queue",
            "not_eligible",
        ),
    ]
    planned = [
        _planned_row(
            "commercial_procurement_candidate",
            {
                "procurement_id_redacted": seed.get("procurement_id_redacted")
                or seed.get("procurement_id"),
                "candidate_source_kind": "pr4",
                "active_status_class": "closed",
                "closing_soon_bucket": "not_applicable",
                "relevance_class": "strong_equipment_class",
                "candidate_outcome_state": "not_eligible",
                "not_eligible_reason": "historical_tender",
            },
            "Document historical relevance without queue admission",
        )
    ]
    return {
        "case_id": "B_historical_equipment",
        "selection_rule": SELECTION_RULES["case_b_historical_equipment"],
        "seed_redacted": seed,
        "stages": stages,
        "planned_pr5_rows": planned,
    }


def build_case_c_excluded(seed: dict[str, Any]) -> dict[str, Any]:
    keyword = seed.get("exclusion_keyword")
    relevance = {
        "reactivo": "consumable_or_reagent",
        "insumo": "consumable_or_reagent",
        "arriendo": "rental_or_comodato",
        "comodato": "rental_or_comodato",
        "mantenimiento": "service_or_maintenance_only",
    }.get(str(keyword), "non_laboratory_false_positive")
    stages = [
        _stage(
            "exclusion_keyword",
            keyword,
            keyword,
            "Reuse equipment-first / leads exclusion patterns",
            relevance,
        ),
        _stage(
            "candidate_outcome",
            "equipment_hit_possible",
            "not_eligible",
            "Negative relevance class wins; not a conflict",
            "not_eligible",
        ),
    ]
    planned = [
        _planned_row(
            "commercial_procurement_candidate",
            {
                "procurement_id_redacted": seed.get("procurement_id_redacted")
                or seed.get("procurement_id"),
                "relevance_class": relevance,
                "candidate_outcome_state": "not_eligible",
                "not_eligible_reason": relevance,
            },
            "Excluded by negative relevance rule",
        ),
        _planned_row(
            "commercial_procurement_candidate_evidence",
            {
                "subject_kind": "candidate",
                "reason_code": f"negative_relevance:{keyword}",
                "evidence_type": "exclusion_keyword",
            },
            "Routine exclusion emits evidence, not conflict",
        ),
        _planned_row(
            "commercial_procurement_line_relevance",
            {
                "relevance_class": relevance,
                "negative_rules_json": [f"exclusion:{keyword}"],
            },
            "Line-level negative rule retention",
        ),
    ]
    return {
        "case_id": "C_excluded",
        "selection_rule": SELECTION_RULES["case_c_excluded"],
        "seed_redacted": seed,
        "stages": stages,
        "planned_pr5_rows": planned,
    }


def build_case_d_contact_research(seed: dict[str, Any]) -> dict[str, Any]:
    if seed.get("unavailable"):
        return {
            "case_id": "D_contact_research_historical",
            "selection_rule": SELECTION_RULES["case_d_contact_research"],
            "unavailable": True,
            "missing_reason": seed.get("missing_reason")
            or "no_linked_account_with_zero_pr2_contacts",
            "seed_redacted": seed,
            "stages": [],
            "planned_pr5_rows": [],
            "note": (
                "No qualifying zero-contact linked account in production; "
                "audit continues without inventing a synthetic Case D."
            ),
        }
    contact_n = int(seed.get("contact_n") or 0)
    if contact_n != 0:
        raise ValueError(
            "Case D requires exactly zero PR2 contacts; "
            f"got contact_n={contact_n}"
        )
    hypo_status = "contact_research_required"
    stages = [
        _stage(
            "active_status_class",
            seed.get("close_at"),
            "closed",
            "Historical PR4 tender",
            "closed",
        ),
        _stage(
            "candidate_outcome",
            seed.get("link_route"),
            "not_eligible",
            "Historical gate — final outcome is not_eligible",
            "not_eligible",
        ),
    ]
    planned = [
        _planned_row(
            "commercial_procurement_candidate",
            {
                "procurement_id_redacted": seed.get("procurement_id_redacted")
                or seed.get("procurement_id"),
                "candidate_source_kind": "pr4",
                "candidate_outcome_state": "not_eligible",
                "not_eligible_reason": "historical_tender",
            },
            "Final state remains not_eligible",
        ),
        _planned_row(
            "commercial_procurement_contact_resolution",
            {
                "final_contact_status": hypo_status,
                "considered_contact_count": contact_n,
                "next_action": "research_contact_if_active",
            },
            "Hypothetical path only — not an active outcome",
        ),
    ]
    return {
        "case_id": "D_contact_research_historical",
        "selection_rule": SELECTION_RULES["case_d_contact_research"],
        "seed_redacted": seed,
        "stages": stages,
        "planned_pr5_rows": planned,
        "hypothetical_contact_path": {
            "if_active_and_relevant": "contact_research_candidate",
            "contact_status": hypo_status,
            "note": "Would require active_open + relevance before contact research queue",
        },
    }


def build_case_e_existing_contact(seed: dict[str, Any]) -> dict[str, Any]:
    contacts = seed.get("contacts_redacted") or []
    suppressed = int(seed.get("suppressed_email_count") or 0)
    has_email = any(c.get("has_email") for c in contacts)
    if suppressed and suppressed >= sum(1 for c in contacts if c.get("has_email")):
        hypo_status = "contact_blocked"
        hypo_outcome = "relevant_tender"
    elif has_email:
        hypo_status = "existing_contact_needs_role_review"
        hypo_outcome = "outreach_review_candidate"
    else:
        hypo_status = "role_known_email_missing"
        hypo_outcome = "contact_research_candidate"
    stages = [
        _stage(
            "active_status_class",
            seed.get("close_at"),
            "closed",
            "Historical PR4 tender",
            "closed",
        ),
        _stage(
            "candidate_outcome",
            seed.get("contact_n"),
            "not_eligible",
            "Historical gate — final outcome is not_eligible",
            "not_eligible",
        ),
    ]
    planned = [
        _planned_row(
            "commercial_procurement_candidate",
            {
                "procurement_id_redacted": seed.get("procurement_id_redacted")
                or seed.get("procurement_id"),
                "candidate_outcome_state": "not_eligible",
                "not_eligible_reason": "historical_tender",
            },
            "Final state remains not_eligible",
        ),
        _planned_row(
            "commercial_procurement_contact_resolution",
            {
                "final_contact_status": hypo_status,
                "considered_contact_count": len(contacts),
            },
            "Summary contact decision (hypothetical if active)",
        ),
        _planned_row(
            "commercial_procurement_contact_candidate",
            {"contacts_redacted": contacts, "suppressed_email_count": suppressed},
            "Zero-or-more considered contacts",
        ),
    ]
    return {
        "case_id": "E_existing_contact_historical",
        "selection_rule": SELECTION_RULES["case_e_existing_contact"],
        "seed_redacted": seed,
        "stages": stages,
        "planned_pr5_rows": planned,
        "hypothetical_contact_path": {
            "if_active_and_relevant": hypo_outcome,
            "contact_status": hypo_status,
            "note": "Human-reviewed outreach-review only; never auto-send",
        },
    }


def load_open_queue_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open(newline="", encoding="utf-8", errors="replace") as fh:
        return list(csv.DictReader(fh))


def build_pr5_walkthrough_bundle(
    *,
    seeds: dict[str, Any],
    open_queue_csv: Path | None,
    as_of: datetime | None = None,
    manifest: dict[str, Any] | None = None,
    forbidden_values: set[str] | frozenset[str] | None = None,
    fixture_mode: bool = False,
) -> Pr5WalkthroughBundle:
    as_of = as_of or datetime.now(SANTIAGO)
    as_of = as_of if as_of.tzinfo else as_of.replace(tzinfo=SANTIAGO)

    case_a: dict[str, Any]
    open_summary: dict[str, int] = {}
    provenance_dict: dict[str, Any] | None = None
    selected_open_class: str | None = None

    if open_queue_csv and open_queue_csv.is_file():
        rows = load_open_queue_rows(open_queue_csv)
        provenance = inspect_artifact_provenance(
            open_queue_csv, as_of=as_of, manifest=manifest
        )
        provenance_dict = provenance.to_dict()
        best, best_oc, all_oc = pick_best_open_row(
            rows, as_of=as_of, provenance=provenance
        )
        open_summary = summarize_open_classes(all_oc)
        if best is not None and best_oc is not None:
            selected_open_class = best_oc.open_class
            case_a = build_case_a_from_open_queue_row(
                best,
                artifact_path=open_queue_csv,
                as_of=as_of,
                classification=best_oc,
                provenance=provenance,
            )
        else:
            # Still classify a sample for reporting when none selectable
            sample_oc = (
                classify_artifact_row_open(
                    rows[0], as_of=as_of, provenance=provenance
                )
                if rows
                else None
            )
            case_a = {
                "case_id": "A_artifact_declared_open_relevant",
                "live_verified_open": False,
                "open_classification": None,
                "current_status_independently_revalidated": False,
                "missing_reason": (
                    "No recent_artifact_declared_open rows after strict classifier"
                ),
                "open_class_counts": open_summary,
                "sample_classification": sample_oc.to_dict() if sample_oc else None,
                "artifact_provenance": provenance_dict,
                "stages": [],
                "planned_pr5_rows": [],
            }
    else:
        case_a = {
            "case_id": "A_artifact_declared_open_relevant",
            "live_verified_open": False,
            "open_classification": None,
            "current_status_independently_revalidated": False,
            "missing_reason": "No equipment-first operator queue artifact provided",
            "stages": [],
            "planned_pr5_rows": [],
        }

    case_b = build_case_b_historical(
        seeds.get("case_b")
        or seeds.get("case_b_equipment_keyword_overlay")
        or seeds.get("case_b_linked_historical")
        or {}
    )
    case_c = build_case_c_excluded(
        seeds.get("case_c") or seeds.get("case_c_exclusion_keyword_hit") or {}
    )
    case_d = build_case_d_contact_research(
        seeds.get("case_d") or seeds.get("case_d_linked_contact_n") or {}
    )
    case_e = build_case_e_existing_contact(
        seeds.get("case_e") or seeds.get("case_e_linked_with_contact") or {}
    )

    summary = {
        "as_of_america_santiago": as_of.isoformat(),
        "redaction_algorithm": REDACTION_ALGORITHM,
        "fixture_mode": fixture_mode,
        "live_verified_open_count": 0,
        "recent_artifact_declared_open_count": open_summary.get(
            "recent_artifact_declared_open", 0
        ),
        "stale_artifact_declared_open_count": open_summary.get(
            "stale_artifact_declared_open", 0
        ),
        "artifact_declared_open_unverified_provenance_count": open_summary.get(
            "artifact_declared_open_unverified_provenance", 0
        ),
        "open_class_counts": open_summary,
        "case_a_open_classification": selected_open_class
        or case_a.get("open_classification"),
        "case_a_live_verified_open": False,
        "current_status_independently_revalidated": False,
        "artifact_provenance": provenance_dict,
        "selection_rules": SELECTION_RULES,
        "classifier_versions": {
            "active": ACTIVE_CLASSIFIER_VERSION,
            "relevance": RELEVANCE_CLASSIFIER_VERSION,
            "contact": CONTACT_RESOLVER_VERSION,
        },
        "case_ids": select_case_ids(
            {
                **seeds,
                "case_a_open_row": {
                    "codigo_licitacion_redacted": case_a.get("selection_id")
                },
            }
        ),
    }

    forbidden_vals = frozenset(forbidden_values or ())
    # Derive domain-like forbidden tokens for leak checks
    forbidden_domains = frozenset(
        v for v in forbidden_vals if "." in v and "@" not in v and " " not in v
    )

    bundle = Pr5WalkthroughBundle(
        summary=summary,
        case_a=case_a,
        case_b=case_b,
        case_c=case_c,
        case_d=case_d,
        case_e=case_e,
        forbidden_domains=forbidden_domains,
        forbidden_values=forbidden_vals,
    )
    for payload in (
        bundle.summary,
        bundle.case_a,
        bundle.case_b,
        bundle.case_c,
        bundle.case_d,
        bundle.case_e,
    ):
        blob = json.dumps(payload, default=str)
        assert_no_pii_leaks(blob, forbidden_domains=bundle.forbidden_domains)
        assert_no_forbidden_identifier_leaks(blob, set(bundle.forbidden_values))
    return bundle


def render_walkthrough_markdown(bundle: Pr5WalkthroughBundle) -> str:
    lines: list[str] = [
        "# Commercial procurement live relevance — PR5A data walkthrough",
        "",
        f"As-of (America/Santiago): `{bundle.summary['as_of_america_santiago']}`",
        f"live_verified_open_count: **{bundle.summary['live_verified_open_count']}**",
        f"recent_artifact_declared_open_count: **{bundle.summary['recent_artifact_declared_open_count']}**",
        f"Case A open_classification: `{bundle.summary.get('case_a_open_classification')}`",
        "Current status independently revalidated: **false** (no authenticated API call in PR5A)",
        "",
        "## Field flow",
        "",
        "```",
        "source artifact / PR4 row",
        "    ↓",
        "provenance normalization",
        "    ↓",
        "active classification",
        "    ↓",
        "relevance classification",
        "    ↓",
        "account resolution",
        "    ↓",
        "contact resolution state",
        "    ↓",
        "candidate outcome",
        "```",
        "",
    ]

    def emit_case(title: str, case: dict[str, Any]) -> None:
        lines.append(f"## {title}")
        lines.append("")
        lines.append(f"Selection rule: {case.get('selection_rule', '')}")
        if case.get("unavailable"):
            lines.append("")
            lines.append(f"Unavailable: `{case.get('missing_reason')}`")
            if case.get("note"):
                lines.append(case["note"])
            lines.append("")
            return
        if case.get("live_verified_open") is False and case.get("open_classification"):
            lines.append("")
            lines.append(
                f"- effective_timestamp_source: `{case.get('effective_timestamp_source')}`"
            )
            lines.append(
                f"- effective_artifact_timestamp_utc: `{case.get('effective_artifact_timestamp_utc')}`"
            )
            lines.append(f"- artifact_age_seconds: `{case.get('artifact_age_seconds')}`")
            lines.append(f"- open_classification: `{case.get('open_classification')}`")
            lines.append("- live_verified_open: **false**")
            lines.append(
                f"- candidate_outcome_state: `{case.get('candidate_outcome_state')}`"
            )
            lines.append(
                f"- contact_resolution_status: `{case.get('contact_resolution_status')}`"
            )
        if case.get("hypothetical_contact_path"):
            lines.append("")
            lines.append(
                f"Hypothetical contact path (not final outcome): "
                f"`{json.dumps(case['hypothetical_contact_path'], sort_keys=True)}`"
            )
        lines.append("")
        lines.append("| Stage | Source value | Normalized value | Rule/reason | Result |")
        lines.append("|-------|--------------|------------------|-------------|--------|")
        for st in case.get("stages") or []:
            lines.append(
                f"| `{st['stage']}` | `{st['source_value']}` | `{st['normalized_value']}` | "
                f"{st['rule_reason']} | `{st['result']}` |"
            )
        lines.append("")
        lines.append("| Proposed PR5 table | Planned redacted row | Why emitted |")
        lines.append("|--------------------|----------------------|-------------|")
        for row in case.get("planned_pr5_rows") or []:
            lines.append(
                f"| `{row['proposed_table']}` | `{json.dumps(row['planned_redacted_row'], sort_keys=True)}` | "
                f"{row['why_emitted']} |"
            )
        lines.append("")

    emit_case(
        "Case A — strongest recent artifact-declared open + relevant",
        bundle.case_a,
    )
    emit_case("Case B — historical equipment match", bundle.case_b)
    emit_case("Case C — false positive / excluded", bundle.case_c)
    emit_case("Case D — historical linked path without suitable contact", bundle.case_d)
    emit_case("Case E — historical linked path with existing contacts", bundle.case_e)
    return "\n".join(lines) + "\n"
