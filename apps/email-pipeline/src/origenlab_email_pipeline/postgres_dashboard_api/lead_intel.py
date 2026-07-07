"""Read-only redacted lead research prospects (Postgres lead_intel.* mirror)."""

from __future__ import annotations

from typing import Any

from psycopg import Connection

from origenlab_email_pipeline.postgres_dashboard_api.db import fetch_all, fetch_one, table_exists
from origenlab_email_pipeline.postgres_dashboard_api.outbound_lists import DEFAULT_MAX_LIMIT
from origenlab_email_pipeline.lead_research.commercial_action_buckets import (
    enrich_prospect_for_dashboard,
    summarize_commercial_action_buckets,
)
from origenlab_email_pipeline.lead_research.lead_research_operational_overlay import (
    apply_operational_overlay_to_prospect,
    load_operational_indexes_from_postgres,
    summarize_prospects_for_dashboard,
)
from origenlab_email_pipeline.postgres_dashboard_api.schemas import (
    LEAD_RESEARCH_DISCLAIMER,
    LeadProspectBlockReasonRow,
    LeadProspectDetail,
    LeadProspectDetailResponse,
    LeadProspectEvidenceRow,
    LeadProspectListItem,
    LeadProspectRecommendationRow,
    LeadProspectsListResponse,
    LeadResearchSummaryResponse,
)

_PROSPECT_TABLE = ("lead_intel", "prospect")

CONTACT_SCOPES: frozenset[str] = frozenset(
    {"contacted", "followup", "active", "deepsearch", "net_new", "blocked"}
)

_CONTACTED_SOURCE_TYPES: tuple[str, ...] = (
    "gmail_historico",
    "followup_antiguo",
    "caso_activo",
    "same_domain_contacted_review",
)

_CONTACTED_OUTREACH_STATES: tuple[str, ...] = ("contacted", "replied")
_FOLLOWUP_OUTREACH_STATES: tuple[str, ...] = ("contacted",)
_ACTIVE_OUTREACH_STATES: tuple[str, ...] = ("replied",)

# Institution-domain matching only — exclude consumer webmail domains.
_PUBLIC_EMAIL_DOMAINS: tuple[str, ...] = (
    "gmail.com",
    "googlemail.com",
    "hotmail.com",
    "outlook.com",
    "yahoo.com",
    "yahoo.cl",
    "yahoo.es",
    "icloud.com",
    "live.com",
)

_PROSPECT = "lead_intel.prospect"

_LIST_SELECT = """
SELECT
  prospect_key, organization_name, contact_name, email, domain,
  sector, region, buyer_type, product_angle, final_score, classification,
  status, spanish_message_angle, recommended_next_action, risk_flags,
  evidence_url, is_blocked, campaign_bucket,
  source_type, dataset_label,
  gmail_first_contacted_at, gmail_last_contacted_at,
  gmail_sent_count, gmail_received_count, gmail_latest_subject_safe
FROM lead_intel.prospect
"""


def _clamp_limit(limit: int) -> int:
    return max(1, min(int(limit), DEFAULT_MAX_LIMIT))


def _table_available(conn: Connection) -> bool:
    schema, table = _PROSPECT_TABLE
    return table_exists(conn, schema=schema, table=table)


def _public_domain_sql_params() -> list[str]:
    return list(_PUBLIC_EMAIL_DOMAINS)


def _outreach_exists_sql(
    *,
    states: tuple[str, ...],
    allow_exact: bool = True,
    allow_same_domain: bool = True,
) -> str:
    state_ph = ", ".join("%s" for _ in states)
    match_parts: list[str] = []
    if allow_exact:
        match_parts.append(
            f"LOWER(TRIM(ocs.contact_email_norm)) = LOWER(TRIM({_PROSPECT}.email))"
        )
    if allow_same_domain:
        public_ph = ", ".join("%s" for _ in _PUBLIC_EMAIL_DOMAINS)
        match_parts.append(
            f"""(
              {_PROSPECT}.domain IS NOT NULL
              AND TRIM({_PROSPECT}.domain) <> ''
              AND LOWER(TRIM({_PROSPECT}.domain)) NOT IN ({public_ph})
              AND SPLIT_PART(LOWER(TRIM(ocs.contact_email_norm)), '@', 2) = LOWER(TRIM({_PROSPECT}.domain))
            )"""
        )
    match_sql = " OR ".join(match_parts)
    return f"""EXISTS (
  SELECT 1
  FROM outbound.outreach_contact_state ocs
  WHERE ocs.state IN ({state_ph})
    AND ({match_sql})
)"""


def _outreach_exists_params(states: tuple[str, ...], *, allow_same_domain: bool = True) -> list[Any]:
    params: list[Any] = list(states)
    if allow_same_domain:
        params.extend(_public_domain_sql_params())
    return params


def contact_scope_sql_clause(
    contact_scope: str | None,
    *,
    include_outreach: bool = True,
) -> str | None:
    """Return a WHERE fragment for mirror prospect scope filtering."""
    if not contact_scope:
        return None
    scope = contact_scope.strip().lower()
    if scope not in CONTACT_SCOPES:
        return None
    if scope == "contacted":
        placeholders = ", ".join("%s" for _ in _CONTACTED_SOURCE_TYPES)
        parts = [
            "COALESCE(gmail_sent_count, 0) > 0",
            "COALESCE(gmail_received_count, 0) > 0",
            f"source_type IN ({placeholders})",
        ]
        if include_outreach:
            parts.append(_outreach_exists_sql(states=_CONTACTED_OUTREACH_STATES))
        return f"({' OR '.join(parts)})"
    if scope == "followup":
        gmail_part = (
            "(COALESCE(gmail_sent_count, 0) > 0 "
            "AND COALESCE(gmail_received_count, 0) = 0 "
            "AND is_blocked = FALSE)"
        )
        if not include_outreach:
            return gmail_part
        outreach_part = (
            f"(COALESCE(gmail_received_count, 0) = 0 "
            f"AND is_blocked = FALSE "
            f"AND {_outreach_exists_sql(states=_FOLLOWUP_OUTREACH_STATES)})"
        )
        return f"({gmail_part} OR {outreach_part})"
    if scope == "active":
        parts = ["COALESCE(gmail_received_count, 0) > 0", "source_type = %s"]
        if include_outreach:
            parts.append(_outreach_exists_sql(states=_ACTIVE_OUTREACH_STATES))
        return f"({' OR '.join(parts)})"
    if scope == "deepsearch":
        return "source_type = %s"
    if scope == "net_new":
        base = (
            "(COALESCE(gmail_sent_count, 0) = 0 "
            "AND COALESCE(gmail_received_count, 0) = 0 "
            "AND is_blocked = FALSE"
        )
        if include_outreach:
            return f"{base} AND NOT {_outreach_exists_sql(states=_CONTACTED_OUTREACH_STATES)})"
        return f"{base})"
    if scope == "blocked":
        return "is_blocked = TRUE"
    return None


def contact_scope_sql_params(
    contact_scope: str | None,
    *,
    include_outreach: bool = True,
) -> list[Any]:
    if not contact_scope:
        return []
    scope = contact_scope.strip().lower()
    if scope == "active":
        params: list[Any] = ["caso_activo"]
        if include_outreach:
            params.extend(_outreach_exists_params(_ACTIVE_OUTREACH_STATES))
        return params
    if scope == "deepsearch":
        return ["deepsearch"]
    if scope == "contacted":
        params = list(_CONTACTED_SOURCE_TYPES)
        if include_outreach:
            params.extend(_outreach_exists_params(_CONTACTED_OUTREACH_STATES))
        return params
    if scope == "followup" and include_outreach:
        return _outreach_exists_params(_FOLLOWUP_OUTREACH_STATES)
    if scope == "net_new" and include_outreach:
        return _outreach_exists_params(_CONTACTED_OUTREACH_STATES)
    return []


def list_lead_prospects(
    conn: Connection,
    *,
    q: str | None = None,
    classification: str | None = None,
    source_type: str | None = None,
    blocked_only: bool = False,
    sector: str | None = None,
    region: str | None = None,
    buyer_type: str | None = None,
    campaign_bucket: str | None = None,
    min_score: int | None = None,
    include_blocked: bool = False,
    contact_scope: str | None = None,
    limit: int = 50,
) -> LeadProspectsListResponse:
    if not _table_available(conn):
        return LeadProspectsListResponse(
            table_available=False,
            disclaimer=LEAD_RESEARCH_DISCLAIMER,
        )

    clauses: list[str] = []
    params: list[Any] = []
    scope = (contact_scope or "").strip().lower() or None

    include_outreach = table_exists(conn, schema="outbound", table="outreach_contact_state")
    scope_clause = contact_scope_sql_clause(scope, include_outreach=include_outreach)
    if scope_clause:
        clauses.append(scope_clause)
        params.extend(
            contact_scope_sql_params(scope, include_outreach=include_outreach),
        )

    if scope == "blocked":
        pass
    elif blocked_only:
        clauses.append("is_blocked = TRUE")
    elif not include_blocked:
        clauses.append("is_blocked = FALSE")
    if source_type:
        clauses.append("source_type = %s")
        params.append(source_type)
    if classification:
        clauses.append("classification = %s")
        params.append(classification)
    if sector:
        clauses.append("sector ILIKE %s")
        params.append(f"%{sector}%")
    if region:
        clauses.append("region ILIKE %s")
        params.append(f"%{region}%")
    if buyer_type:
        clauses.append("buyer_type = %s")
        params.append(buyer_type)
    if campaign_bucket:
        clauses.append("campaign_bucket = %s")
        params.append(campaign_bucket)
    if min_score is not None:
        clauses.append("final_score >= %s")
        params.append(int(min_score))
    if q:
        clauses.append(
            "(organization_name ILIKE %s OR contact_name ILIKE %s OR email ILIKE %s OR domain ILIKE %s)"
        )
        like = f"%{q}%"
        params.extend([like, like, like, like])

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    lim = _clamp_limit(limit)

    count_row = fetch_one(conn, f"SELECT COUNT(*) AS c FROM lead_intel.prospect {where}", params)
    total = int(count_row["c"]) if count_row else 0

    rows = fetch_all(
        conn,
        f"{_LIST_SELECT} {where} ORDER BY final_score DESC, organization_name LIMIT %s",
        [*params, lim],
    )
    emails = [str(r.get("email") or "") for r in rows]
    indexes = load_operational_indexes_from_postgres(conn, emails=emails)
    items = [
        LeadProspectListItem.model_validate(
            enrich_prospect_for_dashboard(
                apply_operational_overlay_to_prospect(dict(r), indexes),
            ),
        )
        for r in rows
    ]
    return LeadProspectsListResponse(
        table_available=True,
        items=items,
        total=total,
        disclaimer=LEAD_RESEARCH_DISCLAIMER,
    )


def get_lead_prospect(conn: Connection, *, prospect_key: str) -> LeadProspectDetailResponse:
    if not _table_available(conn):
        return LeadProspectDetailResponse(table_available=False, disclaimer=LEAD_RESEARCH_DISCLAIMER)

    row = fetch_one(
        conn,
        """
        SELECT prospect_key, organization_name, contact_name, email, domain,
               sector, region, buyer_type, likely_need, product_angle,
               evidence_url, evidence_note, source, final_score, confidence,
               classification, spanish_message_angle, risk_flags,
               block_or_review_reason, recommended_next_action, status,
               campaign_bucket, is_blocked,
               source_type, dataset_label,
               gmail_first_contacted_at, gmail_last_contacted_at,
               gmail_sent_count, gmail_received_count, gmail_latest_subject_safe
        FROM lead_intel.prospect
        WHERE prospect_key = %s
        LIMIT 1
        """,
        [prospect_key],
    )
    if not row:
        return LeadProspectDetailResponse(table_available=True, disclaimer=LEAD_RESEARCH_DISCLAIMER)

    indexes = load_operational_indexes_from_postgres(
        conn,
        emails=[str(row.get("email") or "")],
    )
    row = enrich_prospect_for_dashboard(
        apply_operational_overlay_to_prospect(dict(row), indexes),
    )

    evidence_rows = fetch_all(
        conn,
        """
        SELECT evidence_kind, evidence_url, evidence_note, source, confidence
        FROM lead_intel.evidence
        WHERE prospect_key = %s
        ORDER BY id
        """,
        [prospect_key],
    )
    rec_row = fetch_one(
        conn,
        """
        SELECT campaign_bucket, recommended_message_angle, recommended_next_action,
               why_this_lead, suggested_subject, suggested_body_preview, safety_note
        FROM lead_intel.recommendation
        WHERE prospect_key = %s
        LIMIT 1
        """,
        [prospect_key],
    )
    block_rows = fetch_all(
        conn,
        """
        SELECT reason_code, reason_label
        FROM lead_intel.block_reason
        WHERE prospect_key = %s
        ORDER BY id
        """,
        [prospect_key],
    )

    return LeadProspectDetailResponse(
        table_available=True,
        prospect=LeadProspectDetail.model_validate(dict(row)),
        evidence=[LeadProspectEvidenceRow.model_validate(dict(r)) for r in evidence_rows],
        recommendation=(
            LeadProspectRecommendationRow.model_validate(dict(rec_row)) if rec_row else None
        ),
        block_reasons=[LeadProspectBlockReasonRow.model_validate(dict(r)) for r in block_rows],
        disclaimer=LEAD_RESEARCH_DISCLAIMER,
    )


def get_lead_research_summary(conn: Connection) -> LeadResearchSummaryResponse:
    if not _table_available(conn):
        return LeadResearchSummaryResponse(table_available=False, disclaimer=LEAD_RESEARCH_DISCLAIMER)

    rows = fetch_all(
        conn,
        """
        SELECT prospect_key, classification, status, is_blocked, source_type, email, domain,
               gmail_sent_count, gmail_received_count, gmail_last_contacted_at
        FROM lead_intel.prospect
        """,
        [],
    )
    if not rows:
        return LeadResearchSummaryResponse(table_available=True, disclaimer=LEAD_RESEARCH_DISCLAIMER)

    indexes = load_operational_indexes_from_postgres(conn)
    overlaid = [apply_operational_overlay_to_prospect(dict(r), indexes) for r in rows]
    agg = summarize_prospects_for_dashboard(overlaid)
    commercial = summarize_commercial_action_buckets(overlaid)

    return LeadResearchSummaryResponse(
        table_available=True,
        total=int(agg["total"]),
        review_count=int(agg["review_count"]),
        blocked_count=int(agg["blocked_count"]),
        net_new_safe=int(agg["net_new_safe"]),
        gmail_historico=int(agg["gmail_historico"]),
        followup_antiguo=int(agg["followup_antiguo"]),
        caso_activo=int(agg["caso_activo"]),
        public_tender_review=int(agg["public_tender_review"]),
        same_domain_review=int(agg["same_domain_review"]),
        research_needed=int(agg["research_needed"]),
        ready_to_contact=int(commercial["ready_to_contact"]),
        already_contacted=int(commercial["already_contacted"]),
        needs_email_enrichment=int(commercial["needs_email_enrichment"]),
        tender_opportunity=int(commercial["tender_opportunity"]),
        review_history=int(commercial["review_history"]),
        followup_eligible=int(commercial["followup_eligible"]),
        disclaimer=LEAD_RESEARCH_DISCLAIMER,
    )
