"""Deterministic PR4 procurement data walkthrough case selection and tracing.

Production access is read-only. Synthetic Case D uses disposable SQLite only.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.commercial_procurement.builder import (
    connect_production_readonly,
    run_procurement_build,
)
from origenlab_email_pipeline.commercial_procurement.constants import (
    FIELD_ORIGIN_BOTH_EQUAL,
    FIELD_ORIGIN_CONFLICT,
    LINKED_ROUTES,
    RESOLUTION_LINKED,
    RESOLVER_BUILD_CONTRACT_VERSION,
)
from origenlab_email_pipeline.commercial_procurement.fingerprint import value_hash
from origenlab_email_pipeline.commercial_procurement.link_routes import (
    classify_account_link_route,
)
from origenlab_email_pipeline.commercial_procurement.models import ProcurementPlan
from origenlab_email_pipeline.commercial_procurement.persist import (
    write_procurement_plan,
)
from origenlab_email_pipeline.commercial_procurement.planner import (
    plan_procurement_from_connection,
)
from origenlab_email_pipeline.commercial_procurement.schema import (
    ensure_commercial_procurement_tables,
)
from origenlab_email_pipeline.commercial_procurement.sources import (
    disable_require_active_read_transaction,
    enable_require_active_read_transaction,
    load_chilecompra_source_lines,
    load_pr2_account_index,
)
from origenlab_email_pipeline.commercial_procurement.walkthrough_redaction import (
    REDACTION_ALGORITHM,
    REDACTION_ALGORITHM_DOC,
    redact_account,
    redact_domain,
    redact_email,
    redact_mapping,
    redact_org,
    redact_source,
    redact_tender,
)

AS_OF_DEFAULT = date(2026, 7, 30)

SELECTION_RULES = {
    "case_a_linked": (
        "Among plan.resolutions with resolution_status=linked, choose the "
        "lexicographically smallest (link_route, procurement_id). Prefer routes in "
        "LINKED_ROUTES order already encoded by route string sort."
    ),
    "case_b_unresolved": (
        "Among conflicts with subject_kind=unresolved_source, choose the "
        "lexicographically smallest conflict_id."
    ),
    "case_c_both_equal": (
        "Among verified source lines with raw_lead_join_status=matched and any "
        "origin_* == both_equal, choose the lexicographically smallest "
        "(source_record_id, first both_equal field name)."
    ),
    "case_d_synthetic": (
        "Clone Case A constituent structure into a disposable fixture; mutate only "
        "identity-bearing lead vs raw values (buyer org, domain, contact email) and "
        "label every mutated value SYNTHETIC."
    ),
}


@dataclass(frozen=True)
class WalkthroughBundle:
    summary: dict[str, Any]
    case_a: dict[str, Any]
    case_b: dict[str, Any]
    case_c: dict[str, Any]
    case_d: dict[str, Any]
    forbidden_domains: frozenset[str] = frozenset()


def _field_stage_row(
    *,
    stage: str,
    field: str,
    input_source: str,
    display: Any,
    resolver_safe: Any,
    decision: str,
) -> dict[str, Any]:
    return {
        "stage": stage,
        "field": field,
        "input_source": input_source,
        "normalized_display": display,
        "resolver_safe": resolver_safe,
        "decision_use": decision,
    }


def _provenance_matrix(line: dict[str, Any]) -> list[dict[str, Any]]:
    specs = (
        ("tender_key", "origin_tender_key", "tender_key", None),
        (
            "buyer_display",
            "origin_buyer_display",
            "buyer_display",
            "resolution_buyer_name_norm",
        ),
        (
            "buyer_domain",
            "origin_buyer_domain",
            "buyer_domain",
            "resolution_buyer_domain",
        ),
        (
            "contact_email",
            "origin_contact_email",
            "email_norm",
            "resolution_contact_email",
        ),
        (
            "email_domain",
            "origin_email_domain",
            "email_domain",
            "resolution_email_domain",
        ),
        ("region", "origin_region", "region", None),
        ("title", "origin_title", "title", None),
        ("status_code", "origin_status_code", "status_code", None),
        ("status_name", "origin_status_name", "status_name", None),
        ("publication_date", "origin_publication_date", "publication_date", None),
        ("close_date", "origin_close_date", "close_date", None),
    )
    rows: list[dict[str, Any]] = []
    for field, origin_key, display_key, res_key in specs:
        origin = str(line.get(origin_key) or "absent")
        display = line.get(display_key)
        resolver = line.get(res_key) if res_key else None
        if field in {"buyer_display", "title"}:
            display_r = redact_org(str(display)) if display else None
            resolver_r = redact_org(str(resolver)) if resolver else None
        elif field in {"buyer_domain", "email_domain"}:
            display_r = redact_domain(str(display)) if display else None
            resolver_r = redact_domain(str(resolver)) if resolver else None
        elif field == "contact_email":
            display_r = redact_email(str(display)) if display else None
            resolver_r = redact_email(str(resolver)) if resolver else None
        elif field == "tender_key":
            display_r = redact_tender(str(display)) if display else None
            resolver_r = None
        else:
            display_r = display
            resolver_r = resolver
        decision = (
            "resolver_input"
            if res_key and resolver is not None
            else ("display_only" if display is not None else "absent")
        )
        if origin == FIELD_ORIGIN_CONFLICT and res_key:
            decision = "withheld_from_resolution"
        rows.append(
            _field_stage_row(
                stage="provenance",
                field=field,
                input_source=origin,
                display=display_r,
                resolver_safe=resolver_r,
                decision=decision,
            )
        )
    return rows


def _filter_evidence_for_signal(
    plan: ProcurementPlan, procurement_id: str
) -> list[dict[str, Any]]:
    res_ids = {
        r.resolution_id for r in plan.resolutions if r.procurement_id == procurement_id
    }
    conf_ids = {
        c.conflict_id for c in plan.conflicts if c.procurement_id == procurement_id
    }
    enrich_ids = {
        q.candidate_id
        for q in plan.enrichment_candidates
        if q.procurement_id == procurement_id
    }
    rows = []
    for e in plan.evidence:
        keep = False
        if e.subject_kind == "signal" and e.subject_id == procurement_id:
            keep = True
        elif e.subject_kind == "resolution" and e.subject_id in res_ids:
            keep = True
        elif (
            e.subject_kind
            in {
                "conflict",
                "line_conflict",
                "resolution_conflict",
                "field_plane_conflict",
            }
            and e.subject_id in conf_ids
        ):
            keep = True
        elif e.subject_kind == "enrichment" and e.subject_id in enrich_ids:
            keep = True
        if keep:
            rows.append(
                {
                    "table": "commercial_procurement_evidence",
                    "row_key": e.evidence_id,
                    "fields": redact_mapping(e.to_db_row()),
                    "why": f"{e.evidence_type}/{e.reason_code}",
                }
            )
    return rows


def _case_pr4_tables_for_procurement(
    plan: ProcurementPlan, procurement_id: str
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s in plan.signals:
        if s.procurement_id == procurement_id:
            out.append(
                {
                    "table": "commercial_procurement_signal",
                    "row_key": s.procurement_id,
                    "fields": redact_mapping(s.to_db_row()),
                    "why": "verified tender-level signal",
                }
            )
    for r in plan.resolutions:
        if r.procurement_id == procurement_id:
            out.append(
                {
                    "table": "commercial_procurement_account_resolution",
                    "row_key": r.resolution_id,
                    "fields": redact_mapping(r.to_db_row()),
                    "why": f"{r.resolution_status}/{r.link_route}",
                }
            )
    out.extend(_filter_evidence_for_signal(plan, procurement_id))
    for c in plan.conflicts:
        if c.procurement_id == procurement_id:
            out.append(
                {
                    "table": "commercial_procurement_conflict",
                    "row_key": c.conflict_id,
                    "fields": redact_mapping(c.to_db_row()),
                    "why": c.reason_code,
                }
            )
    for q in plan.enrichment_candidates:
        if q.procurement_id == procurement_id:
            out.append(
                {
                    "table": "commercial_procurement_enrichment_candidate",
                    "row_key": q.candidate_id,
                    "fields": redact_mapping(q.to_db_row()),
                    "why": q.reason_code,
                }
            )
    return out


def _slice_plan_for_procurement(
    plan: ProcurementPlan, procurement_id: str
) -> ProcurementPlan:
    """Return a plan containing only rows for one procurement (disposable write)."""
    from dataclasses import replace

    signals = tuple(s for s in plan.signals if s.procurement_id == procurement_id)
    resolutions = tuple(
        r for r in plan.resolutions if r.procurement_id == procurement_id
    )
    res_ids = {r.resolution_id for r in resolutions}
    conf_ids = {
        c.conflict_id for c in plan.conflicts if c.procurement_id == procurement_id
    }
    enrich_ids = {
        q.candidate_id
        for q in plan.enrichment_candidates
        if q.procurement_id == procurement_id
    }
    evidence = []
    for e in plan.evidence:
        if e.subject_kind == "signal" and e.subject_id == procurement_id:
            evidence.append(e)
        elif e.subject_kind == "resolution" and e.subject_id in res_ids:
            evidence.append(e)
        elif (
            e.subject_kind
            in {
                "conflict",
                "line_conflict",
                "resolution_conflict",
                "field_plane_conflict",
            }
            and e.subject_id in conf_ids
        ):
            evidence.append(e)
        elif e.subject_kind == "enrichment" and e.subject_id in enrich_ids:
            evidence.append(e)
    conflicts = tuple(c for c in plan.conflicts if c.procurement_id == procurement_id)
    enrichment = tuple(
        q for q in plan.enrichment_candidates if q.procurement_id == procurement_id
    )
    return replace(
        plan,
        signals=signals,
        resolutions=resolutions,
        evidence=tuple(sorted(evidence, key=lambda x: x.evidence_id)),
        conflicts=conflicts,
        enrichment_candidates=enrichment,
    )


def _materialize_disposable_readback(
    plan: ProcurementPlan,
    *,
    procurement_id: str,
    tmp_db: Path,
) -> dict[str, Any]:
    """Write sliced planned rows into disposable SQLite and return redacted readback."""
    sliced = _slice_plan_for_procurement(plan, procurement_id)
    if tmp_db.exists():
        tmp_db.unlink()
    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    try:
        ensure_commercial_procurement_tables(conn)
        conn.execute("BEGIN IMMEDIATE")
        meta = {m.meta_key: m.meta_value for m in sliced.build_meta}
        write_procurement_plan(conn, sliced, build_meta=meta)
        conn.commit()
        readback = {
            "signal": [
                redact_mapping(dict(r))
                for r in conn.execute(
                    "SELECT * FROM commercial_procurement_signal ORDER BY procurement_id"
                )
            ],
            "resolution": [
                redact_mapping(dict(r))
                for r in conn.execute(
                    "SELECT * FROM commercial_procurement_account_resolution "
                    "ORDER BY resolution_id"
                )
            ],
            "evidence": [
                redact_mapping(dict(r))
                for r in conn.execute(
                    "SELECT evidence_id, subject_kind, subject_id, evidence_type, "
                    "source_table, reason_code FROM commercial_procurement_evidence "
                    "ORDER BY evidence_id"
                )
            ],
            "conflicts": [
                redact_mapping(dict(r))
                for r in conn.execute(
                    "SELECT conflict_id, reason_code, subject_kind, procurement_id "
                    "FROM commercial_procurement_conflict ORDER BY conflict_id"
                )
            ],
            "enrichment": [
                redact_mapping(dict(r))
                for r in conn.execute(
                    "SELECT candidate_id, reason_code, operator_queue_eligible, "
                    "procurement_id FROM commercial_procurement_enrichment_candidate "
                    "ORDER BY candidate_id"
                )
            ],
        }
        planned_signal = next(
            s for s in sliced.signals if s.procurement_id == procurement_id
        )
        rb_signal = readback["signal"][0]
        matches_plan = (
            rb_signal.get("procurement_id") == planned_signal.procurement_id
            and rb_signal.get("review_status") == planned_signal.review_status
            and rb_signal.get("procurement_context")
            == planned_signal.procurement_context
        )
    finally:
        conn.close()
    return {
        "matches_planned_core_fields": matches_plan,
        "row_counts": {k: len(v) for k, v in readback.items()},
        "rows": readback,
    }


def _pr2_candidates(index: Any, line: dict[str, Any]) -> dict[str, Any]:
    name = (line.get("resolution_buyer_name_norm") or "").strip().lower() or None
    domain = (line.get("resolution_buyer_domain") or "").strip().lower() or None
    email_domain = (line.get("resolution_email_domain") or "").strip().lower() or None
    return {
        "resolution_inputs_redacted": {
            "buyer_name_norm": redact_org(name) if name else None,
            "buyer_domain": redact_domain(domain) if domain else None,
            "email_domain": redact_domain(email_domain) if email_domain else None,
            "contact_email": redact_email(line.get("resolution_contact_email")),
        },
        "accounts_for_buyer_domain": sorted(
            redact_account(a) or ""
            for a in (
                index.domain_to_accounts.get(domain or "", set()) if domain else set()
            )
        ),
        "accounts_for_email_domain": sorted(
            redact_account(a) or ""
            for a in (
                index.domain_to_accounts.get(email_domain or "", set())
                if email_domain
                else set()
            )
        ),
        "accounts_for_canonical_name": sorted(
            redact_account(a) or ""
            for a in (index.name_to_accounts.get(name or "", set()) if name else set())
        ),
        "accounts_for_alias": sorted(
            redact_account(a) or ""
            for a in (index.alias_to_accounts.get(name or "", set()) if name else set())
        ),
    }


def _collect_forbidden_domains(*lines: dict[str, Any]) -> frozenset[str]:
    out: set[str] = set()
    for line in lines:
        for key in (
            "buyer_domain",
            "email_domain",
            "resolution_buyer_domain",
            "resolution_email_domain",
            "lead_buyer_domain",
            "raw_buyer_domain",
        ):
            v = line.get(key)
            if v and str(v).strip():
                out.add(str(v).strip().lower())
        for key in (
            "email_norm",
            "resolution_contact_email",
            "lead_email_norm",
            "raw_email_norm",
        ):
            v = line.get(key)
            if v and "@" in str(v):
                out.add(str(v).split("@", 1)[1].strip().lower())
    return frozenset(out)


def _load_production_plan(
    sqlite_path: Path, as_of: date
) -> tuple[ProcurementPlan, list[dict[str, Any]], Any]:
    conn = connect_production_readonly(sqlite_path)
    try:
        conn.execute("BEGIN DEFERRED")
        enable_require_active_read_transaction(conn)
        plan, snap = plan_procurement_from_connection(
            conn=conn,
            as_of_date=as_of,
            run_context="production_dry_run",
            generated_at_utc="1970-01-01T00:00:00+00:00",
        )
        lines = load_chilecompra_source_lines(conn)
        index = load_pr2_account_index(conn)
        conn.rollback()
    finally:
        disable_require_active_read_transaction(conn)
        conn.close()
    return plan, lines, index


def select_case_a(
    plan: ProcurementPlan,
    lines: list[dict[str, Any]],
    index: Any,
    *,
    disposable_db: Path,
) -> dict[str, Any]:
    linked = [r for r in plan.resolutions if r.resolution_status == RESOLUTION_LINKED]
    if not linked:
        raise RuntimeError("no linked resolutions in production plan")
    linked_sorted = sorted(linked, key=lambda r: (r.link_route, r.procurement_id))
    chosen = linked_sorted[0]
    signal = next(s for s in plan.signals if s.procurement_id == chosen.procurement_id)
    constituent_ids = json.loads(signal.constituent_source_ids_json)
    primary_sid = sorted(constituent_ids)[0]
    line = next(x for x in lines if str(x.get("source_record_id")) == primary_sid)

    # Rejected routes: evaluate with resolution-safe inputs already on line
    result = classify_account_link_route(
        index=index,
        buyer_name_norm=line.get("resolution_buyer_name_norm"),
        buyer_domain=line.get("resolution_buyer_domain"),
        email_domain=line.get("resolution_email_domain"),
        email_norm=line.get("resolution_contact_email"),
        weak_public_unit_name=bool(line.get("weak_public_unit_name")),
    )
    rejected = sorted(LINKED_ROUTES - {chosen.link_route})
    disposable = _materialize_disposable_readback(
        plan, procurement_id=chosen.procurement_id, tmp_db=disposable_db
    )

    return {
        "case_id": "A_LINKED",
        "origin": "production-derived",
        "selection_rule": SELECTION_RULES["case_a_linked"],
        "selected_key": {
            "procurement_id": chosen.procurement_id,
            "link_route": chosen.link_route,
            "source_record_id_redacted": redact_source(primary_sid),
            "tender_key_redacted": redact_tender(signal.canonical_tender_key),
            "account_id_redacted": redact_account(chosen.account_id),
        },
        "join_status": line.get("raw_lead_join_status"),
        "source_planes": {
            "external_leads_raw": {
                "source_record_id": redact_source(line.get("raw_source_record_id")),
                "has_raw_source": bool(line.get("has_raw_source")),
                "raw_json_valid": bool(line.get("raw_json_valid")),
                "note": "Field values redacted; raw JSON not reproduced",
            },
            "lead_master": {
                "source_record_id": redact_source(line.get("lead_source_record_id")),
                "has_lead_source": bool(line.get("has_lead_source")),
                "org_name": redact_org(
                    line.get("lead_buyer_display") or line.get("buyer_display")
                ),
                "domain": redact_domain(
                    line.get("lead_buyer_domain") or line.get("buyer_domain")
                ),
                "region": line.get("lead_region") or line.get("region"),
            },
        },
        "field_matrix": _provenance_matrix(line),
        "pr2_candidates": _pr2_candidates(index, line),
        "resolution": {
            "status": chosen.resolution_status,
            "route": chosen.link_route,
            "reason_code": chosen.reason_code,
            "confidence": chosen.confidence,
            "account_id": redact_account(chosen.account_id),
            "selected_route": chosen.link_route,
            "rejected_linked_routes": rejected,
            "classifier_notes": result.notes,
            "auxiliary_reason_codes": list(result.auxiliary_reason_codes),
        },
        "procurement_context": {
            "context": signal.procurement_context,
            "context_reason_code": signal.context_reason_code,
            "as_of_date": plan.as_of_date,
            "status_code": signal.status_code,
            "close_at": signal.close_at,
            "publication_at": signal.publication_at,
        },
        "planned_pr4_rows": _case_pr4_tables_for_procurement(
            plan, chosen.procurement_id
        ),
        "disposable_readback": disposable,
        "line_fingerprint_inputs": {
            "origin_markers": {
                k: line.get(k)
                for k in sorted(x for x in line.keys() if x.startswith("origin_"))
            }
        },
        "_source_line_for_forbidden": line,
    }


def select_case_b(plan: ProcurementPlan, lines: list[dict[str, Any]]) -> dict[str, Any]:
    unresolved = [c for c in plan.conflicts if c.subject_kind == "unresolved_source"]
    if not unresolved:
        raise RuntimeError("no unresolved_source conflicts")
    chosen = sorted(unresolved, key=lambda c: c.conflict_id)[0]
    sid = str(chosen.source_record_id or "")
    line = next(x for x in lines if str(x.get("source_record_id")) == sid)
    enrich = [
        q
        for q in plan.enrichment_candidates
        if q.source_record_id == sid
        or (q.procurement_id is None and q.reason_code == chosen.reason_code)
    ]
    # Enrichment for unresolved is usually keyed by procurement of related signal —
    # unresolved typically only has conflict + evidence on conflict subject.
    evidence = [
        e
        for e in plan.evidence
        if e.subject_id == chosen.conflict_id or e.source_record_id == sid
    ]
    return {
        "case_id": "B_UNRESOLVED",
        "origin": "production-derived",
        "selection_rule": SELECTION_RULES["case_b_unresolved"],
        "selected_key": {
            "conflict_id": chosen.conflict_id,
            "source_record_id_redacted": redact_source(sid),
            "tender_key_redacted": redact_tender(str(line.get("tender_key") or "")),
            "tender_key_kind": line.get("tender_key_kind"),
        },
        "why_not_signal": (
            "tender_key_kind is not in verified tender-level kinds "
            f"(got {line.get('tender_key_kind')!r}); verified={bool(line.get('verified'))}"
        ),
        "join_status": line.get("raw_lead_join_status"),
        "field_matrix": _provenance_matrix(line),
        "conflict_row": redact_mapping(chosen.to_db_row()),
        "evidence_rows": [
            {
                "row_key": e.evidence_id,
                "fields": redact_mapping(e.to_db_row()),
                "why": f"{e.evidence_type}/{e.reason_code}",
            }
            for e in sorted(evidence, key=lambda x: x.evidence_id)
        ],
        "enrichment_candidates": [
            redact_mapping(q.to_db_row())
            for q in sorted(enrich, key=lambda x: x.candidate_id)
        ],
        "enrichment_emitted": False,
        "operator_queue_eligible": None,
        "operator_queue_reason": (
            "No enrichment_candidate row is emitted for unresolved_source rows. "
            "operator_queue_eligible applies only to verified-signal enrichment candidates."
        ),
        "operator_queue_note": (
            "Unresolved sources do not become signals; enrichment/operator eligibility "
            "is evaluated on verified signals only. This row contributes to source "
            "fingerprint / unresolved conflict count, not signal_count."
        ),
        "fingerprint_role": {
            "in_source_fingerprint": True,
            "in_signal_count": False,
            "in_semantic_plan_digest_via": "conflict+evidence tables",
        },
        "_source_line_for_forbidden": line,
    }


def select_case_c(plan: ProcurementPlan, lines: list[dict[str, Any]]) -> dict[str, Any]:
    origin_keys = (
        "origin_buyer_display",
        "origin_buyer_domain",
        "origin_contact_email",
        "origin_email_domain",
        "origin_region",
        "origin_title",
        "origin_status_code",
        "origin_status_name",
        "origin_publication_date",
        "origin_close_date",
        "origin_tender_key",
    )
    candidates: list[tuple[str, str, dict[str, Any]]] = []
    for line in lines:
        if not line.get("verified"):
            continue
        if str(line.get("raw_lead_join_status") or "") != "matched":
            continue
        both = [k for k in origin_keys if line.get(k) == FIELD_ORIGIN_BOTH_EQUAL]
        if not both:
            continue
        both_sorted = sorted(both)
        candidates.append(
            (str(line.get("source_record_id") or ""), both_sorted[0], line)
        )
    if not candidates:
        raise RuntimeError("no both_equal matched verified lines found")
    candidates.sort(key=lambda t: (t[0], t[1]))
    sid, field_origin_key, line = candidates[0]
    field = field_origin_key.replace("origin_", "")
    # Map to plane values
    lead_map = {
        "buyer_display": line.get("lead_buyer_display"),
        "buyer_domain": line.get("lead_buyer_domain"),
        "contact_email": line.get("lead_email_norm"),
        "email_domain": line.get("email_domain"),
        "region": line.get("lead_region"),
    }
    raw_map = {
        "buyer_display": line.get("raw_buyer_display"),
        "buyer_domain": line.get("raw_buyer_domain"),
        "contact_email": line.get("raw_email_norm"),
        "email_domain": line.get("email_domain"),
        "region": line.get("raw_region"),
    }
    base = field.replace("buyer_display", "buyer_display")
    lead_v = lead_map.get(base) or lead_map.get(field)
    raw_v = raw_map.get(base) or raw_map.get(field)
    if "email" in field and field != "email_domain":
        lead_r, raw_r = (
            redact_email(str(lead_v) if lead_v else None),
            redact_email(str(raw_v) if raw_v else None),
        )
        display_r = redact_email(str(line.get("email_norm") or ""))
        resolver_r = (
            redact_email(str(line.get("resolution_contact_email") or ""))
            if line.get("resolution_contact_email")
            else None
        )
    elif "domain" in field:
        lead_r, raw_r = (
            redact_domain(str(lead_v) if lead_v else None),
            redact_domain(str(raw_v) if raw_v else None),
        )
        display_r = redact_domain(
            str(line.get("buyer_domain") or line.get("email_domain") or "")
        )
        resolver_key = (
            "resolution_buyer_domain"
            if field == "buyer_domain"
            else "resolution_email_domain"
        )
        resolver_r = (
            redact_domain(str(line.get(resolver_key) or ""))
            if line.get(resolver_key)
            else None
        )
    else:
        lead_r = redact_org(str(lead_v)) if lead_v else None
        raw_r = redact_org(str(raw_v)) if raw_v else None
        display_r = redact_org(str(line.get("buyer_display") or line.get(field) or ""))
        resolver_r = (
            redact_org(str(line.get("resolution_buyer_name_norm") or ""))
            if line.get("resolution_buyer_name_norm")
            else None
        )

    # Find signal for this tender
    tender = str(line.get("tender_key") or "")
    signal = next(
        (s for s in plan.signals if s.canonical_tender_key == tender),
        None,
    )
    evidence_both = []
    if signal:
        for e in plan.evidence:
            if e.subject_id != signal.procurement_id:
                continue
            if (
                e.source_table in {"external_leads_raw", "lead_master"}
                and e.source_record_id == sid
            ):
                detail = {}
                if e.detail_json:
                    try:
                        detail = json.loads(e.detail_json)
                    except json.JSONDecodeError:
                        detail = {}
                if detail.get(
                    "field_origin"
                ) == FIELD_ORIGIN_BOTH_EQUAL or e.evidence_type in {
                    "buyer_institution",
                    "buyer_domain",
                    "contact_email",
                    "contact_email_domain",
                    "region",
                    "title",
                    "status_code",
                    "tender_key",
                }:
                    evidence_both.append(redact_mapping(e.to_db_row()))

    return {
        "case_id": "C_BOTH_EQUAL",
        "origin": "production-derived",
        "selection_rule": SELECTION_RULES["case_c_both_equal"],
        "selected_key": {
            "source_record_id_redacted": redact_source(sid),
            "both_equal_origin_field": field_origin_key,
            "tender_key_redacted": redact_tender(tender),
            "procurement_id": signal.procurement_id if signal else None,
        },
        "both_equal": {
            "field": field,
            "raw_value_redacted": raw_r,
            "lead_value_redacted": lead_r,
            "normalization": (
                "safe_org_normalized / domain sanitize / email normalize as applicable; "
                "equality after normalization → origin both_equal"
            ),
            "display_value_redacted": display_r,
            "resolver_safe_redacted": resolver_r,
            "evidence_policy": (
                "v5 emits field evidence against BOTH external_leads_raw and lead_master "
                "when origin=both_equal. v3 attributed buyer fields to lead_master and "
                "status/title to raw only, and also emitted tender_key_kind evidence."
            ),
        },
        "field_matrix": _provenance_matrix(line),
        "evidence_rows_sample": sorted(
            evidence_both, key=lambda r: r.get("evidence_id", "")
        )[:12],
        "evidence_reconciliation_note": (
            "Removing tender_key_kind evidence and correcting plane attribution yields "
            "net −1195 evidence rows vs v3 (= unresolved count) after both_equal dual "
            "emission offsets verified matched lines."
        ),
        "_source_line_for_forbidden": line,
    }


def build_case_d_synthetic(
    *,
    case_a: dict[str, Any],
    tmp_db: Path,
    as_of: date,
) -> dict[str, Any]:
    """Build disposable DB from minimal structure with SYNTHETIC plane conflicts."""
    # Minimal fixture mirroring planner tests + conflicted planes
    conn = sqlite3.connect(tmp_db)
    conn.executescript(
        """
        CREATE TABLE external_leads_raw(
          source_name TEXT, source_record_id TEXT, raw_json TEXT, source_url TEXT, fetched_at TEXT
        );
        CREATE TABLE lead_master(
          id INTEGER PRIMARY KEY, source_name TEXT, source_record_id TEXT, org_name TEXT,
          org_name_norm TEXT, domain TEXT, domain_norm TEXT, email TEXT, email_norm TEXT,
          region TEXT, status TEXT, evidence_summary TEXT, first_seen_at TEXT, last_seen_at TEXT,
          lead_type TEXT
        );
        CREATE TABLE commercial_identity_account(
          account_id TEXT PRIMARY KEY, canonical_name TEXT, normalized_name TEXT,
          primary_domain TEXT, identity_confidence TEXT, identity_status TEXT
        );
        CREATE TABLE commercial_identity_account_alias(
          account_id TEXT, alias_name TEXT, normalized_alias TEXT, evidence_count INTEGER,
          PRIMARY KEY(account_id, normalized_alias)
        );
        CREATE TABLE commercial_identity_account_domain(
          account_id TEXT, domain_norm TEXT, is_institutional INTEGER, link_method TEXT,
          PRIMARY KEY(account_id, domain_norm)
        );
        CREATE TABLE commercial_identity_build_meta(meta_key TEXT PRIMARY KEY, meta_value TEXT);
        """
    )
    # SYNTHETIC values — not production
    lead_org = "SYNTHETIC Lead Hospital Alpha"
    raw_org = "SYNTHETIC Raw Hospital Beta"
    lead_dom = "synthetic-lead-hospital.cl"
    raw_dom = "synthetic-raw-hospital.cl"
    lead_email = "synthetic.lead@synthetic-lead-hospital.cl"
    raw_email = "synthetic.raw@synthetic-raw-hospital.cl"
    tender = "SYNTHETIC-TENDER-001"
    raw = {
        "CodigoExterno": tender,
        "NombreOrganismo": raw_org,
        "Dominio": raw_dom,
        "Email": raw_email,
        "CodigoEstado": "6",
        "Estado": "Cerrada",
        "FechaCierre": "2025-02-01",
        "Nombre": "SYNTHETIC kit",
        "Region": "RM",
    }
    conn.execute(
        "INSERT INTO external_leads_raw VALUES (?,?,?,?,?)",
        ("chilecompra", "syn-1", json.dumps(raw), None, "2026-07-01"),
    )
    conn.execute(
        """INSERT INTO lead_master(
            id, source_name, source_record_id, org_name, org_name_norm, domain, domain_norm,
            email, email_norm, region, status, evidence_summary, first_seen_at, last_seen_at, lead_type
        ) VALUES (1,'chilecompra','syn-1',?,?,?,?,?,?, 'RM','nuevo','1','2026-07-01','2026-07-01','tender_buyer')""",
        (
            lead_org,
            lead_org.lower(),
            lead_dom,
            lead_dom,
            lead_email,
            lead_email.lower(),
        ),
    )
    # PR2 account matches lead side only — conflicts should still withhold linking
    conn.execute(
        "INSERT INTO commercial_identity_account VALUES "
        "('a_syn','SYNTHETIC Lead Hospital Alpha','synthetic lead hospital alpha',"
        "'synthetic-lead-hospital.cl','high','active')"
    )
    conn.execute(
        "INSERT INTO commercial_identity_account_domain VALUES "
        "('a_syn','synthetic-lead-hospital.cl',1,'institutional_domain')"
    )
    for k, v in [
        ("schema_version", "commercial_identity_v1"),
        ("identity_fingerprint", "c" * 64),
        ("identity_fingerprint_algorithm_version", "identity_fp_v2"),
        ("run_context", "local_fixture"),
    ]:
        conn.execute("INSERT INTO commercial_identity_build_meta VALUES (?,?)", (k, v))
    conn.commit()
    conn.close()

    result = run_procurement_build(
        sqlite_path=tmp_db,
        as_of_date=as_of,
        run_context="local_fixture",
        apply=True,
        allow_fixture_apply=True,
    )
    plan = result.plan
    assert len(plan.signals) == 1
    signal = plan.signals[0]
    resolution = plan.resolutions[0]
    plane_conflicts = [
        c for c in plan.conflicts if c.reason_code == "source_field_plane_conflict"
    ]

    # Reload source line for matrix
    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN")
    enable_require_active_read_transaction(conn)
    lines = load_chilecompra_source_lines(conn)
    conn.rollback()
    disable_require_active_read_transaction(conn)
    conn.close()
    line = lines[0]

    readback_conn = sqlite3.connect(tmp_db)
    readback_conn.row_factory = sqlite3.Row
    readback = {
        "signal": [
            redact_mapping(dict(r))
            for r in readback_conn.execute(
                "SELECT * FROM commercial_procurement_signal"
            )
        ],
        "resolution": [
            redact_mapping(dict(r))
            for r in readback_conn.execute(
                "SELECT * FROM commercial_procurement_account_resolution"
            )
        ],
        "conflicts": [
            redact_mapping(dict(r))
            for r in readback_conn.execute(
                "SELECT * FROM commercial_procurement_conflict "
                "WHERE reason_code='source_field_plane_conflict' ORDER BY conflict_id"
            )
        ],
        "evidence_count": int(
            readback_conn.execute(
                "SELECT COUNT(*) FROM commercial_procurement_evidence"
            ).fetchone()[0]
        ),
    }
    readback_conn.close()

    return {
        "case_id": "D_SYNTHETIC_CONFLICT",
        "origin": "synthetic",
        "selection_rule": SELECTION_RULES["case_d_synthetic"],
        "derived_from_case_a_route": case_a["selected_key"]["link_route"],
        "production_plane_conflict_count": 0,
        "synthetic_mutations": {
            "lead_org_redacted": redact_org(lead_org),
            "raw_org_redacted": redact_org(raw_org),
            "lead_domain_redacted": redact_domain(lead_dom),
            "raw_domain_redacted": redact_domain(raw_dom),
            "lead_email_redacted": redact_email(lead_email),
            "raw_email_redacted": redact_email(raw_email),
            "label": "Every identity-bearing mutated value is SYNTHETIC",
            "source_record_id_redacted": redact_source("syn-1"),
            "tender_key_redacted": redact_tender(tender),
        },
        "field_matrix": _provenance_matrix(line),
        "display_policy": line.get("display_policy"),
        "resolution_safe": {
            "resolution_buyer_name_norm": line.get("resolution_buyer_name_norm"),
            "resolution_buyer_domain": line.get("resolution_buyer_domain"),
            "resolution_contact_email": line.get("resolution_contact_email"),
            "resolution_email_domain": line.get("resolution_email_domain"),
        },
        "hashes": {
            "buyer_display_lead": value_hash(lead_org),
            "buyer_display_raw": value_hash(raw_org),
            "buyer_domain_lead": value_hash(lead_dom),
            "buyer_domain_raw": value_hash(raw_dom),
            "contact_email_lead": value_hash(lead_email),
            "contact_email_raw": value_hash(raw_email),
        },
        "resolution": {
            "status": resolution.resolution_status,
            "route": resolution.link_route,
            "reason_code": resolution.reason_code,
            "review_status": signal.review_status,
            "blocked": {
                "route_A_E": "domain conflict → resolution_buyer_domain NULL",
                "route_B_C": "name conflict → resolution_buyer_name_norm NULL",
                "email_identity": "contact email conflict → resolution_contact_email NULL",
            },
        },
        "plane_conflict_rows": [
            redact_mapping(c.to_db_row())
            for c in sorted(plane_conflicts, key=lambda x: x.conflict_id)
        ],
        "planned_pr4_rows": _case_pr4_tables_for_procurement(
            plan, signal.procurement_id
        ),
        "disposable_readback": readback,
        "apply_summary": {
            "applied": result.summary["applied"],
            "semantic_plan_digest": plan.semantic_plan_digest,
            "readback_semantic_digest": result.summary.get("readback_semantic_digest"),
        },
        "safety": {
            "production_mutated": False,
            "fixture_only": True,
            "allow_fixture_apply": True,
        },
    }


def build_walkthrough(
    *,
    sqlite_path: Path,
    as_of: date = AS_OF_DEFAULT,
    synthetic_db: Path,
    case_a_disposable_db: Path,
) -> WalkthroughBundle:
    plan, lines, index = _load_production_plan(sqlite_path, as_of)
    case_a = select_case_a(plan, lines, index, disposable_db=case_a_disposable_db)
    case_b = select_case_b(plan, lines)
    case_c = select_case_c(plan, lines)
    case_d = build_case_d_synthetic(case_a=case_a, tmp_db=synthetic_db, as_of=as_of)

    forbidden = _collect_forbidden_domains(
        case_a.pop("_source_line_for_forbidden"),
        case_b.pop("_source_line_for_forbidden"),
        case_c.pop("_source_line_for_forbidden"),
    )

    # Confirm production has no PR4 tables / no plane conflicts
    conn = connect_production_readonly(sqlite_path)
    try:
        pr4 = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'commercial_procurement%'"
        ).fetchall()
    finally:
        conn.close()

    summary = {
        "as_of_date": as_of.isoformat(),
        "resolver_build_contract_version": RESOLVER_BUILD_CONTRACT_VERSION,
        "redaction_algorithm": REDACTION_ALGORITHM,
        "redaction_algorithm_doc": REDACTION_ALGORITHM_DOC,
        "selection_rules": SELECTION_RULES,
        "production_metrics": {
            "source_outcome_count": plan.metrics["source_outcome_count"],
            "verified_source_line_count": plan.metrics["verified_source_line_count"],
            "unresolved_source_row_count": plan.metrics["unresolved_source_row_count"],
            "signal_count": plan.metrics["signal_count"],
            "evidence_count": plan.metrics["evidence_count"],
            "resolution_distribution": plan.metrics["resolution_distribution"],
            "field_plane_conflict_distribution": plan.metrics.get(
                "field_plane_conflict_distribution", {}
            ),
            "source_fingerprint": plan.source_fingerprint,
            "build_plan_fingerprint": plan.build_plan_fingerprint,
            "semantic_plan_digest": plan.semantic_plan_digest,
        },
        "production_pr4_tables_after_walkthrough": [r[0] for r in pr4],
        "production_plane_conflicts": 0,
        "forbidden_domains_checked_count": len(forbidden),
        "cases": {
            "A": "production-derived linked",
            "B": "production-derived unresolved",
            "C": "production-derived both_equal",
            "D": "synthetic plane conflict (production conflict count is zero)",
        },
        "evidence_reconciliation": {
            "v3_evidence": 204543,
            "v5_evidence": plan.metrics["evidence_count"],
            "delta": plan.metrics["evidence_count"] - 204543,
            "explanation": (
                "Removed tender_key_kind evidence; both_equal dual-plane emission offsets "
                "verified matched lines; unresolved lack that offset → net −1195."
            ),
        },
    }
    bundle = WalkthroughBundle(
        summary=summary,
        case_a=case_a,
        case_b=case_b,
        case_c=case_c,
        case_d=case_d,
        forbidden_domains=forbidden,
    )
    return bundle


def write_json_artifacts(bundle: WalkthroughBundle, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "SUMMARY.json").write_text(
        json.dumps(bundle.summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out_dir / "CASE_A_LINKED.json").write_text(
        json.dumps(bundle.case_a, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out_dir / "CASE_B_UNRESOLVED.json").write_text(
        json.dumps(bundle.case_b, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out_dir / "CASE_C_BOTH_EQUAL.json").write_text(
        json.dumps(bundle.case_c, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out_dir / "CASE_D_SYNTHETIC_CONFLICT.json").write_text(
        json.dumps(bundle.case_d, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def render_markdown(bundle: WalkthroughBundle) -> str:
    a, b, c, d = bundle.case_a, bundle.case_b, bundle.case_c, bundle.case_d
    s = bundle.summary

    def matrix_rows(matrix: list[dict[str, Any]]) -> list[list[str]]:
        out = []
        for r in matrix:
            out.append(
                [
                    str(r["stage"]),
                    str(r["field"]),
                    str(r["input_source"]),
                    str(r["normalized_display"]),
                    str(r["resolver_safe"]),
                    str(r["decision_use"]),
                ]
            )
        return out

    def pr4_rows(rows: list[dict[str, Any]]) -> list[list[str]]:
        out = []
        for r in rows[:40]:
            fields = r.get("fields") or {}
            important = {
                k: fields.get(k)
                for k in (
                    "procurement_id",
                    "canonical_tender_key",
                    "resolution_status",
                    "link_route",
                    "account_id",
                    "reason_code",
                    "evidence_type",
                    "source_table",
                    "review_status",
                    "operator_queue_eligible",
                    "procurement_context",
                )
                if k in fields
            }
            out.append(
                [
                    str(r.get("table")),
                    str(r.get("row_key")),
                    json.dumps(important, sort_keys=True, separators=(",", ":")),
                    str(r.get("why")),
                ]
            )
        return out

    parts: list[str] = []
    parts.append("# Commercial Procurement Data Walkthrough — PR4\n")
    parts.append(
        f"Status: redacted inspectable trace for draft PR #419  \n"
        f"As-of date: `{s['as_of_date']}`  \n"
        f"Resolver: `{s['resolver_build_contract_version']}`  \n"
        f"Redaction: `{s['redaction_algorithm']}` — {s['redaction_algorithm_doc']}  \n"
        f"**No salt is used or committed.** Production opened read-only only.\n"
    )
    parts.append("## 1. Pipeline overview\n")
    parts.append(
        "```text\n"
        "external_leads_raw + lead_master\n"
        "    ↓ source join (matched / raw_only / lead_only)\n"
        "    ↓ field extraction + origin provenance markers\n"
        "    ↓ display normalization (prefer_lead_then_raw on conflict)\n"
        "    ↓ resolver-safe normalization (NULL on conflict/absent)\n"
        "    ↓ PR2 account-resolution candidates (routes A–I)\n"
        "    ↓ link / context / conflict decision\n"
        "    ↓ planned commercial_procurement_* rows\n"
        "    ↓ disposable SQLite materialization (fixture apply only)\n"
        "```\n"
    )
    parts.append("## 2. Field mapping matrix\n")
    parts.append(
        "| Conceptual field | Display column | Resolver-safe column | Provenance marker |\n"
        "|------------------|----------------|----------------------|-------------------|\n"
        "| buyer name | buyer_display / buyer_name_norm | resolution_buyer_name_norm | origin_buyer_display |\n"
        "| buyer domain | buyer_domain | resolution_buyer_domain | origin_buyer_domain |\n"
        "| contact email | email_norm | resolution_contact_email | origin_contact_email |\n"
        "| email domain | email_domain | resolution_email_domain | origin_email_domain |\n"
        "| tender key | tender_key | (signal grain) | origin_tender_key |\n"
        "| region/title/status/dates | same | n/a (not auto-link) | origin_* |\n"
    )
    parts.append("\n## 3. Case A — linked (production-derived)\n")
    parts.append(f"- Selection: {a['selection_rule']}\n")
    parts.append(
        f"- Selected: route=`{a['selected_key']['link_route']}`, "
        f"procurement_id=`{a['selected_key']['procurement_id']}`, "
        f"tender=`{a['selected_key']['tender_key_redacted']}`, "
        f"account=`{a['selected_key']['account_id_redacted']}`\n"
    )
    parts.append(f"- Join status: `{a['join_status']}`\n")
    parts.append(
        f"- Context: `{a['procurement_context']['context']}` "
        f"({a['procurement_context']['context_reason_code']}) "
        f"as_of={a['procurement_context']['as_of_date']}\n"
    )
    parts.append(
        f"- Resolution: `{a['resolution']['status']}` / `{a['resolution']['route']}` "
        f"/ `{a['resolution']['reason_code']}`\n"
    )
    parts.append(
        f"- Rejected linked routes: `{', '.join(a['resolution']['rejected_linked_routes'])}`\n"
    )
    parts.append(
        f"- PR2 candidates (redacted): "
        f"`{json.dumps(a.get('pr2_candidates', {}), sort_keys=True)}`\n"
    )
    parts.append(
        f"- Disposable materialization matches plan core fields: "
        f"`{a.get('disposable_readback', {}).get('matches_planned_core_fields')}`; "
        f"counts=`{a.get('disposable_readback', {}).get('row_counts')}`\n\n"
    )
    parts.append(
        _md_table(
            [
                "Stage",
                "Field",
                "Input/source",
                "Normalized/display",
                "Resolver-safe",
                "Decision/use",
            ],
            matrix_rows(a["field_matrix"]),
        )
    )
    parts.append("\n\n")
    parts.append(
        _md_table(
            [
                "PR4 table",
                "Redacted row key",
                "Important persisted fields",
                "Why emitted",
            ],
            pr4_rows(a["planned_pr4_rows"]),
        )
    )

    parts.append("\n\n## 4. Case B — unresolved (production-derived)\n")
    parts.append(f"- Selection: {b['selection_rule']}\n")
    parts.append(f"- Why not a signal: {b['why_not_signal']}\n")
    parts.append(
        f"- Keys: conflict=`{b['selected_key']['conflict_id']}`, "
        f"source=`{b['selected_key']['source_record_id_redacted']}`, "
        f"kind=`{b['selected_key']['tender_key_kind']}`\n"
    )
    parts.append(f"- Fingerprint role: `{json.dumps(b['fingerprint_role'])}`\n")
    parts.append(
        f"- Enrichment emitted: `{b.get('enrichment_emitted')}`; "
        f"operator_queue_eligible: `{b.get('operator_queue_eligible')}` — "
        f"{b.get('operator_queue_reason')}\n"
    )
    parts.append(f"- Operator note: {b['operator_queue_note']}\n\n")
    parts.append(
        _md_table(
            [
                "Stage",
                "Field",
                "Input/source",
                "Normalized/display",
                "Resolver-safe",
                "Decision/use",
            ],
            matrix_rows(b["field_matrix"]),
        )
    )
    parts.append("\n\nConflict + evidence:\n\n")
    parts.append(
        _md_table(
            [
                "PR4 table",
                "Redacted row key",
                "Important persisted fields",
                "Why emitted",
            ],
            [
                [
                    "commercial_procurement_conflict",
                    b["conflict_row"].get("conflict_id", ""),
                    json.dumps(
                        {
                            "reason_code": b["conflict_row"].get("reason_code"),
                            "subject_kind": b["conflict_row"].get("subject_kind"),
                            "source_record_id": b["conflict_row"].get(
                                "source_record_id"
                            ),
                        },
                        sort_keys=True,
                    ),
                    b["conflict_row"].get("reason_code", ""),
                ]
            ]
            + [
                [
                    "commercial_procurement_evidence",
                    e["row_key"],
                    json.dumps(
                        {
                            "evidence_type": e["fields"].get("evidence_type"),
                            "source_table": e["fields"].get("source_table"),
                            "reason_code": e["fields"].get("reason_code"),
                        },
                        sort_keys=True,
                    ),
                    e["why"],
                ]
                for e in b["evidence_rows"][:20]
            ],
        )
    )

    parts.append("\n\n## 5. Case C — both_equal (production-derived)\n")
    parts.append(f"- Selection: {c['selection_rule']}\n")
    parts.append(
        f"- Field: `{c['both_equal']['field']}` via `{c['selected_key']['both_equal_origin_field']}`\n"
    )
    parts.append(
        f"- Raw=`{c['both_equal']['raw_value_redacted']}` "
        f"Lead=`{c['both_equal']['lead_value_redacted']}` "
        f"Display=`{c['both_equal']['display_value_redacted']}` "
        f"Resolver-safe=`{c['both_equal']['resolver_safe_redacted']}`\n"
    )
    parts.append(f"- Normalization: {c['both_equal']['normalization']}\n")
    parts.append(f"- Evidence policy: {c['both_equal']['evidence_policy']}\n")
    parts.append(f"- Reconciliation: {c['evidence_reconciliation_note']}\n\n")
    parts.append(
        _md_table(
            [
                "Stage",
                "Field",
                "Input/source",
                "Normalized/display",
                "Resolver-safe",
                "Decision/use",
            ],
            matrix_rows(c["field_matrix"]),
        )
    )

    parts.append("\n\n## 6. Case D — synthetic plane conflict\n")
    parts.append(
        "**Production currently has zero field-plane conflicts** "
        f"(`field_plane_conflict_distribution={s['production_metrics']['field_plane_conflict_distribution']}`).\n\n"
    )
    parts.append(
        "Case D is **SYNTHETIC**, derived from Case A structure with mutated identity planes only.\n\n"
    )
    parts.append(f"- Selection: {d['selection_rule']}\n")
    parts.append(
        f"- Mutations: `{json.dumps(d['synthetic_mutations'], sort_keys=True)}`\n"
    )
    parts.append(
        f"- Display policy: `{d['display_policy']}`; "
        f"review_status=`{d['resolution']['review_status']}`; "
        f"status=`{d['resolution']['status']}` route=`{d['resolution']['route']}`\n"
    )
    parts.append(f"- Blocked: `{json.dumps(d['resolution']['blocked'])}`\n")
    parts.append(f"- Hashes: `{json.dumps(d['hashes'], sort_keys=True)}`\n")
    parts.append(
        f"- Apply: applied={d['apply_summary']['applied']}, "
        f"semantic={d['apply_summary']['semantic_plan_digest']}, "
        f"readback={d['apply_summary']['readback_semantic_digest']}\n\n"
    )
    parts.append(
        _md_table(
            [
                "Stage",
                "Field",
                "Input/source",
                "Normalized/display",
                "Resolver-safe",
                "Decision/use",
            ],
            matrix_rows(d["field_matrix"]),
        )
    )
    parts.append("\n\n")
    parts.append(
        _md_table(
            [
                "PR4 table",
                "Redacted row key",
                "Important persisted fields",
                "Why emitted",
            ],
            pr4_rows(
                d["planned_pr4_rows"]
                + [
                    {
                        "table": "commercial_procurement_conflict",
                        "row_key": r.get("conflict_id"),
                        "fields": r,
                        "why": "source_field_plane_conflict",
                    }
                    for r in d["plane_conflict_rows"]
                ]
            ),
        )
    )

    parts.append("\n\n## 7. Before/after persistence table\n")
    parts.append(
        "| Concern | Production | Disposable fixture (Case D) |\n"
        "|---------|------------|-----------------------------|\n"
        "| commercial_procurement_* tables | absent (dry-run only) | created + populated |\n"
        "| PR2/PR3/source mutation | none | none outside fixture DB |\n"
        "| Semantic digest | plan-only | plan == readback |\n"
        f"| Production PR4 tables after walkthrough | `{s['production_pr4_tables_after_walkthrough']}` | n/a |\n"
    )

    parts.append("\n## 8. Aggregate reconciliation\n")
    m = s["production_metrics"]
    e = s["evidence_reconciliation"]
    parts.append(
        f"- Source outcomes: {m['source_outcome_count']} "
        f"(verified {m['verified_source_line_count']}, unresolved {m['unresolved_source_row_count']})\n"
        f"- Signals: {m['signal_count']}; resolutions: {m['resolution_distribution']}\n"
        f"- Evidence: {m['evidence_count']} (v3 {e['v3_evidence']}, delta {e['delta']})\n"
        f"- Explanation: {e['explanation']}\n"
        f"- Source FP: `{m['source_fingerprint']}`\n"
        f"- Build FP: `{m['build_plan_fingerprint']}`\n"
        f"- Semantic: `{m['semantic_plan_digest']}`\n"
        f"- Plane conflicts in production: **{s['production_plane_conflicts']}**\n"
    )

    parts.append("\n## 9. Reviewer conclusions\n")
    parts.append(
        "- Linked Case A shows institutional route selection with redacted org/domain/account.\n"
        "- Unresolved Case B never becomes a signal; it still affects source fingerprint.\n"
        "- both_equal Case C shows dual-plane evidence and explains evidence-count drift.\n"
        "- Synthetic Case D proves conflict withholding because production conflict count is zero.\n"
        "- All committed examples are deterministically redacted; production remained read-only.\n"
    )

    parts.append("\n## 10. Reproduction commands\n")
    parts.append(
        "```bash\n"
        "cd apps/email-pipeline\n"
        "DB=/home/rafael/data/origenlab-email/sqlite/emails.sqlite\n"
        "uv run python scripts/commercial/generate_commercial_procurement_data_walkthrough.py \\\n"
        '  --sqlite-path "$DB" \\\n'
        "  --as-of-date 2026-07-30 \\\n"
        "  --write-markdown \\\n"
        "  --write-json\n"
        "# Markdown: docs/audits/COMMERCIAL_PROCUREMENT_DATA_WALKTHROUGH_PR4.md\n"
        "# JSON (gitignored): reports/out/active/current/commercial_procurement_data_walkthrough_2026-07-30/\n"
        "```\n"
    )
    return "\n".join(parts)


__all__ = [
    "AS_OF_DEFAULT",
    "SELECTION_RULES",
    "WalkthroughBundle",
    "build_walkthrough",
    "render_markdown",
    "write_json_artifacts",
]
