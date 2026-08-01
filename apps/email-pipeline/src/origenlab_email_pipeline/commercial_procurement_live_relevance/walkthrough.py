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
from origenlab_email_pipeline.commercial_procurement_live_relevance.constants import (
    ACTIVE_CLASSIFIER_VERSION,
    AS_OF_TIMEZONE,
    CONTACT_RESOLVER_VERSION,
    RELEVANCE_CLASSIFIER_VERSION,
)

SANTIAGO = ZoneInfo(AS_OF_TIMEZONE)

SELECTION_RULES: dict[str, str] = {
    "case_a_active_relevant": (
        "Prefer a repository artifact row with positive active evidence "
        "(validity_status=open OR status Publicada/code 5 AND close_at > America/Santiago now). "
        "If none in PR4 SQLite, use equipment-first ChileCompra API operator queue. "
        "Never invent an open tender."
    ),
    "case_b_historical_equipment": (
        "PR4 historical_tender with equipment keyword or linked multi-line signal; "
        "must remain ineligible for current operator queue."
    ),
    "case_c_excluded": (
        "Real source text hitting consumable/service/rental/non-lab exclusion keywords."
    ),
    "case_d_contact_research": (
        "Relevant-shaped historical or active tender with clear buyer / PR2 link but "
        "no verified suitable contact (contact_n=0 or no role-suitable email)."
    ),
    "case_e_existing_contact": (
        "PR4 linked tender whose PR2 account has at least one contact row; "
        "evaluate suppression/outreach conceptually without mutating state."
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


def select_case_ids(seeds: dict[str, Any]) -> dict[str, str | None]:
    """Deterministic IDs from a seeds document (fixture or audit output)."""
    a = seeds.get("case_a_open_row") or seeds.get("case_a")
    b = seeds.get("case_b_equipment_keyword_overlay") or seeds.get("case_b_linked_historical")
    c = seeds.get("case_c_exclusion_keyword_hit")
    d = seeds.get("case_d_linked_contact_n")
    e = seeds.get("case_e_linked_with_contact")

    def _id(obj: Any, *keys: str) -> str | None:
        if not isinstance(obj, dict):
            return None
        for k in keys:
            if obj.get(k):
                return str(obj[k])
        return None

    return {
        "case_a": _id(a, "selection_id", "procurement_id", "codigo_licitacion_redacted"),
        "case_b": _id(b, "procurement_id"),
        "case_c": _id(c, "procurement_id"),
        "case_d": _id(d, "procurement_id"),
        "case_e": _id(e, "procurement_id"),
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
    artifact_name: str,
    as_of: datetime,
) -> dict[str, Any]:
    """Case A from a genuinely open equipment-first queue row (already may be raw)."""
    from origenlab_email_pipeline.commercial_procurement.walkthrough_redaction import (
        redact_org,
        redact_tender,
        redact_token,
    )

    # Explicit redaction — queue CSV column names are not all covered by redact_mapping.
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
    validity = row.get("validity_status")
    status_name = row.get("chilecompra_status")
    status_code = row.get("chilecompra_status_code")
    close_date = row.get("close_date")
    equipment_category = row.get("equipment_category")
    is_open = validity == "open" and str(status_code) == "5"
    active_class = "active_open" if is_open else "status_unknown"
    relevance = (
        "strong_equipment_class"
        if equipment_category
        else "laboratory_context_only"
    )
    # No PR4 account resolution for API-only rows unless separately linked
    contact_status = "contact_research_required"
    outcome = "contact_research_candidate" if is_open and relevance.startswith(
        ("strong", "compatible", "exact")
    ) else "not_eligible"

    stages = [
        _stage(
            "source_artifact",
            artifact_name,
            artifact_name,
            SELECTION_RULES["case_a_active_relevant"],
            "selected",
        ),
        _stage(
            "status",
            f"{status_code}/{status_name}",
            "publicada" if str(status_code) == "5" else status_name,
            f"{ACTIVE_CLASSIFIER_VERSION}: code 5 + validity_status",
            active_class,
        ),
        _stage(
            "close_date_vs_santiago",
            close_date,
            close_date,
            f"America/Santiago as_of={as_of.isoformat()}",
            "future_close" if is_open else "not_open",
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
            "pr4_account_resolution",
            "not_in_pr4_sqlite_corpus",
            "unlinked_pending_pr4_refresh",
            "Live API observation not yet in PR4 file-backed corpus",
            "account_resolution_deferred",
        ),
        _stage(
            "contact_search",
            "skipped_until_account_clear",
            contact_status,
            CONTACT_RESOLVER_VERSION,
            contact_status,
        ),
        _stage(
            "candidate_outcome",
            None,
            outcome,
            "Active+relevant without verified contact → contact research",
            outcome,
        ),
    ]

    planned = [
        _planned_row(
            "commercial_procurement_candidate",
            {
                "tender_key_redacted": redacted.get("codigo_licitacion"),
                "active_status_class": active_class,
                "relevance_class": relevance,
                "equipment_class": equipment_category,
                "product_resolution_status": "equipment_class_only",
                "candidate_outcome_state": outcome,
            },
            "Live open relevant tender candidate",
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
            {"contact_status": contact_status},
            "No verified contact yet",
        ),
    ]

    return {
        "case_id": "A_active_relevant",
        "genuine_live_active": bool(is_open),
        "synthetic_active_overlay": False,
        "selection_rule": SELECTION_RULES["case_a_active_relevant"],
        "selection_id": redacted.get("codigo_licitacion"),
        "source_artifact": artifact_name,
        "as_of_america_santiago": as_of.isoformat(),
        "redacted_fields": redacted,
        "equipment_category": equipment_category,
        "validity_status": validity,
        "stages": stages,
        "planned_pr5_rows": planned,
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
            "strong_equipment_class_candidate",
        ),
        _stage(
            "active_eligibility",
            seed.get("close_at"),
            "close_in_past",
            f"{ACTIVE_CLASSIFIER_VERSION}: close_at < America/Santiago today",
            "closed",
        ),
        _stage(
            "operator_queue",
            "would_look_relevant",
            "ineligible",
            "Historical tenders must not enter current operator queue",
            "not_eligible",
        ),
    ]
    planned = [
        _planned_row(
            "commercial_procurement_candidate",
            {
                "procurement_id": seed.get("procurement_id"),
                "active_status_class": "closed",
                "relevance_class": "strong_equipment_class",
                "candidate_outcome_state": "not_eligible",
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
            "relevance_gate",
            "equipment_hit_possible",
            "blocked",
            "Negative class wins over weak lab context",
            "not_eligible",
        ),
    ]
    planned = [
        _planned_row(
            "commercial_procurement_candidate",
            {
                "procurement_id": seed.get("procurement_id"),
                "relevance_class": relevance,
                "candidate_outcome_state": "not_eligible",
            },
            "Excluded by negative relevance rule",
        ),
        _planned_row(
            "commercial_procurement_candidate_conflict",
            {"conflict_kind": "negative_relevance", "reason_code": f"exclusion:{keyword}"},
            "Explain exclusion",
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
    contact_n = int(seed.get("contact_n") or 0)
    status = "contact_research_required" if contact_n == 0 else "existing_contact_needs_role_review"
    stages = [
        _stage(
            "account_resolution",
            seed.get("resolution_status"),
            seed.get("link_route"),
            "PR4 linked account",
            "linked",
        ),
        _stage(
            "pr2_contacts",
            contact_n,
            contact_n,
            "COUNT commercial_identity_contact for account",
            "none" if contact_n == 0 else "present_needs_review",
        ),
        _stage(
            "lead_master_contact",
            "checked_conceptually",
            "not_promoted_automatically",
            "Search order step 2 — no invention",
            "no_verified_suitable_contact",
        ),
        _stage(
            "outcome",
            None,
            "contact_research_candidate",
            "Relevant buyer clear; contact missing/unverified",
            "contact_research_candidate",
        ),
    ]
    # Historical note
    if (seed.get("procurement_context") or "historical_tender") == "historical_tender":
        stages.append(
            _stage(
                "active_gate",
                seed.get("close_at"),
                "historical",
                "Case demonstrates contact funnel on real linked account; "
                "not admitted to live queue while historical",
                "not_live_eligible",
            )
        )
    planned = [
        _planned_row(
            "commercial_procurement_contact_resolution",
            {
                "account_id_redacted": seed.get("account_id_redacted"),
                "contact_status": status,
            },
            "Contact research required",
        )
    ]
    return {
        "case_id": "D_contact_research",
        "selection_rule": SELECTION_RULES["case_d_contact_research"],
        "seed_redacted": seed,
        "stages": stages,
        "planned_pr5_rows": planned,
        "live_active": False,
        "note": "Real PR4 linked account path; active admission requires live open tender.",
    }


def build_case_e_existing_contact(seed: dict[str, Any]) -> dict[str, Any]:
    contacts = seed.get("contacts_redacted") or []
    suppressed = int(seed.get("suppressed_email_count") or 0)
    has_email = any(c.get("has_email") for c in contacts)
    if suppressed and suppressed >= sum(1 for c in contacts if c.get("has_email")):
        contact_status = "contact_blocked"
        outcome = "relevant_tender"
    elif has_email:
        contact_status = "existing_contact_needs_role_review"
        outcome = "outreach_review_candidate"
    else:
        contact_status = "role_known_email_missing"
        outcome = "contact_research_candidate"
    stages = [
        _stage(
            "account_resolution",
            seed.get("link_route"),
            "linked",
            "PR4 link_route",
            "linked",
        ),
        _stage(
            "pr2_contacts",
            seed.get("contact_n"),
            f"n={seed.get('contact_n')}",
            "PR2 contact rows exist",
            "existing_contact",
        ),
        _stage(
            "role_suitability",
            "unknown_until_review",
            "needs_role_review",
            "Do not treat generic mailbox as named person",
            contact_status,
        ),
        _stage(
            "suppression_outreach",
            suppressed,
            suppressed,
            "Read-only check against suppression tables",
            "blocked" if contact_status == "contact_blocked" else "not_auto_send",
        ),
        _stage(
            "outcome",
            None,
            outcome,
            "Human-reviewed outreach-review only; never auto-send",
            outcome,
        ),
    ]
    if (seed.get("procurement_context") or "historical_tender") == "historical_tender":
        stages.append(
            _stage(
                "active_gate",
                seed.get("close_at"),
                "historical",
                "Existing contact path demonstrated; live queue requires active_open",
                "not_live_eligible",
            )
        )
    planned = [
        _planned_row(
            "commercial_procurement_contact_resolution",
            {
                "contact_status": contact_status,
                "contacts_redacted": contacts,
            },
            "Existing contact with human review required",
        ),
        _planned_row(
            "commercial_procurement_candidate",
            {
                "candidate_outcome_state": outcome
                if seed.get("procurement_context") != "historical_tender"
                else "not_eligible",
            },
            "Outreach-review only when also active+relevant",
        ),
    ]
    return {
        "case_id": "E_existing_contact",
        "selection_rule": SELECTION_RULES["case_e_existing_contact"],
        "seed_redacted": seed,
        "stages": stages,
        "planned_pr5_rows": planned,
        "live_active": False,
    }


def load_open_queue_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open(newline="", encoding="utf-8", errors="replace") as fh:
        return list(csv.DictReader(fh))


def pick_best_open_row(rows: list[dict[str, str]]) -> dict[str, str] | None:
    open_rows = [r for r in rows if r.get("validity_status") == "open"]
    if not open_rows:
        return None
    prefer = {
        "centrifuge",
        "balance",
        "sonicator",
        "incubator",
        "homogenizer",
        "osmometer",
        "lab_ultrasonic_processor",
    }

    def key(r: dict[str, str]) -> tuple[int, float]:
        cat = (r.get("equipment_category") or "").lower()
        try:
            score = -float(r.get("fit_score") or 0)
        except ValueError:
            score = 0.0
        return (0 if cat in prefer else 1, score)

    return sorted(open_rows, key=key)[0]


def build_pr5_walkthrough_bundle(
    *,
    seeds: dict[str, Any],
    open_queue_csv: Path | None,
    as_of: datetime | None = None,
) -> Pr5WalkthroughBundle:
    as_of = as_of or datetime.now(SANTIAGO)
    case_a: dict[str, Any]
    genuine_active = False
    if open_queue_csv and open_queue_csv.is_file():
        rows = load_open_queue_rows(open_queue_csv)
        best = pick_best_open_row(rows)
        if best is not None:
            genuine_active = True
            case_a = build_case_a_from_open_queue_row(
                best, artifact_name=open_queue_csv.name, as_of=as_of
            )
        else:
            case_a = {
                "case_id": "A_active_relevant",
                "genuine_live_active": False,
                "synthetic_active_overlay": False,
                "missing_reason": "No validity_status=open rows in provided artifact",
                "stages": [],
                "planned_pr5_rows": [],
            }
    else:
        case_a = {
            "case_id": "A_active_relevant",
            "genuine_live_active": False,
            "missing_reason": "No equipment-first open queue artifact provided",
            "stages": [],
            "planned_pr5_rows": [],
        }

    case_b = build_case_b_historical(
        seeds.get("case_b_equipment_keyword_overlay")
        or seeds.get("case_b_linked_historical")
        or {}
    )
    case_c = build_case_c_excluded(seeds.get("case_c_exclusion_keyword_hit") or {})
    case_d = build_case_d_contact_research(seeds.get("case_d_linked_contact_n") or {})
    case_e = build_case_e_existing_contact(seeds.get("case_e_linked_with_contact") or {})

    summary = {
        "as_of_america_santiago": as_of.isoformat(),
        "redaction_algorithm": REDACTION_ALGORITHM,
        "genuine_active_tenders_in_walkthrough": genuine_active,
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
    forbidden: set[str] = set()
    bundle = Pr5WalkthroughBundle(
        summary=summary,
        case_a=case_a,
        case_b=case_b,
        case_c=case_c,
        case_d=case_d,
        case_e=case_e,
        forbidden_domains=frozenset(forbidden),
    )
    # Leak check on serialized forms
    for payload in (
        bundle.summary,
        bundle.case_a,
        bundle.case_b,
        bundle.case_c,
        bundle.case_d,
        bundle.case_e,
    ):
        assert_no_pii_leaks(json.dumps(payload, default=str), forbidden_domains=bundle.forbidden_domains)
    return bundle


def render_walkthrough_markdown(bundle: Pr5WalkthroughBundle) -> str:
    lines: list[str] = [
        "# Commercial procurement live relevance — PR5A data walkthrough",
        "",
        f"As-of (America/Santiago): `{bundle.summary['as_of_america_santiago']}`",
        f"Genuine active tender in walkthrough: **{bundle.summary['genuine_active_tenders_in_walkthrough']}**",
        "",
    ]

    def emit_case(title: str, case: dict[str, Any]) -> None:
        lines.append(f"## {title}")
        lines.append("")
        lines.append(f"Selection rule: {case.get('selection_rule', '')}")
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

    emit_case("Case A — strongest currently active and relevant", bundle.case_a)
    emit_case("Case B — historical equipment match", bundle.case_b)
    emit_case("Case C — false positive / excluded", bundle.case_c)
    emit_case("Case D — relevant path without verified contact", bundle.case_d)
    emit_case("Case E — existing contact path", bundle.case_e)
    return "\n".join(lines) + "\n"
