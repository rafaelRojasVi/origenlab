"""Next cold-outreach recipients from ``lead_master`` (ranked SQL + shared export gate).

**Canonical lead lane:** ``export_next_marketing_recipients.py`` and the marketing queue read path use
``compute_next_marketing_recipients``; keep this module aligned with ``outbound_core`` and
``candidate_export_gate`` — do not remove or bypass without a migrated, tested replacement.

**This module:** ``compute_next_marketing_recipients`` — ranked candidate selection and
per-row gate evaluation.

**Gate context:** ``compute_next_marketing_recipients`` uses ``outbound_core.gate_context_for_lead_master_export``
(same defaults as canonical lead CLI). ``marketing_export_context`` still owns ``GateContext`` assembly;
the symbols below are re-exported so existing ``from next_marketing_queue import …`` keeps working.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from origenlab_email_pipeline.candidate_export_gate import evaluate_export_eligibility
from origenlab_email_pipeline.lead_export_queries import (
    sql_left_join_best_org_match,
    sql_upstream_active_lead_master,
)
from origenlab_email_pipeline.marketing_export_context import (
    DEFAULT_EXCLUDE_DOMAINS,
    DEFAULT_SENT_FOLDERS,
    build_marketing_export_gate_context,
    load_outreach_state_map,
    load_sent_recipient_norms,
    load_suppressed_contact_domains,
    load_suppressed_norms,
    norm_lead_email,
)
from origenlab_email_pipeline.outbound_core import gate_context_for_lead_master_export
from origenlab_email_pipeline.tatiana_copilot.marketing_outreach import (
    MARKETING_VARIANT_GENERAL,
    MARKETING_VARIANT_TYPES,
)

_LM_UPSTREAM_ACTIVE = sql_upstream_active_lead_master("lm")
_JOIN_BEST_ORG = sql_left_join_best_org_match(variant="org_and_archive")
_RESEARCH_CONTACTABLE_STATUSES = ("contacto_encontrado", "listo_para_contacto")


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return bool(row)


@dataclass(frozen=True)
class NextMarketingQueueStats:
    n_scanned: int
    n_kept: int
    n_sent_folder_recipients: int
    n_suppressed: int
    n_outreach_state: int
    gmail_user: str


def compute_next_marketing_recipients(
    conn: sqlite3.Connection,
    *,
    gmail_user: str,
    sent_folders: tuple[str, ...] = DEFAULT_SENT_FOLDERS,
    limit: int = 40,
    fetch_cap: int = 4000,
    include_low_fit: bool = False,
    min_priority: float | None = None,
    extra_exclude_domains: tuple[str, ...] = (),
    variant_type: str = MARKETING_VARIANT_GENERAL,
    allow_prior_outreach_history: bool = False,
) -> tuple[list[dict[str, object]], NextMarketingQueueStats]:
    """Return outreach-ready rows and exclusion stats. Read-only on ``conn``.

    The default preserves the legacy one-shot lead-export semantics. Durable
    repeat campaigns may set ``allow_prior_outreach_history=True`` so previous
    Sent/contacted/replied history is informational while hard suppression,
    snoozed state, active-commercial holds, supplier/internal/noise rules still
    apply through the shared gate.
    """
    sent_norms = load_sent_recipient_norms(conn, gmail_user=gmail_user, sent_folders=sent_folders)
    suppressed = load_suppressed_norms(conn)
    outreach_map = load_outreach_state_map(conn)
    gate_ctx = gate_context_for_lead_master_export(
        conn,
        gmail_user=gmail_user,
        sent_folders=sent_folders,
        extra_exclude_domains=extra_exclude_domains,
        allow_prior_outreach_history=bool(allow_prior_outreach_history),
    )

    variant = str(variant_type).strip()
    if variant not in MARKETING_VARIANT_TYPES:
        variant = MARKETING_VARIANT_GENERAL

    min_pri_sql = ""
    params_tail: list[object] = []
    if min_priority is not None:
        min_pri_sql = " AND COALESCE(lm.priority_score, 0) >= ? "
        params_tail.append(min_priority)

    low_fit_sql = ""
    if not include_low_fit:
        low_fit_sql = " AND COALESCE(lm.fit_bucket, 'low_fit') != 'low_fit' "

    has_research = _table_exists(conn, "lead_contact_research")
    research_join_sql = "LEFT JOIN lead_contact_research r ON r.lead_id = lm.id" if has_research else ""
    research_email_sql = "r.resolved_contact_email" if has_research else "NULL"
    research_status_sql = "r.contact_research_status" if has_research else "NULL"
    research_filter_sql = (
        "OR ("
        "  lower(trim(COALESCE(r.contact_research_status, ''))) IN ('contacto_encontrado', 'listo_para_contacto')"
        "  AND NULLIF(TRIM(COALESCE(r.resolved_contact_email, '')), '') IS NOT NULL"
        ")"
        if has_research
        else ""
    )

    cur = conn.execute(
        f"""
        SELECT
          lm.id AS id_lead,
          lm.source_name,
          lm.org_name,
          lm.contact_name,
          lm.email,
          lm.email_norm,
          {research_email_sql} AS resolved_contact_email,
          {research_status_sql} AS contact_research_status,
          lm.region,
          lm.city,
          lm.lead_type,
          lm.priority_score,
          COALESCE(lm.fit_bucket, 'low_fit') AS fit_bucket,
          lm.evidence_summary,
          lm.website,
          m.matched_org_name,
          COALESCE(m.already_in_archive_flag, 0) AS already_in_archive_flag
        FROM lead_master lm
        {research_join_sql}
        {_JOIN_BEST_ORG}
        WHERE
          {_LM_UPSTREAM_ACTIVE}
          {low_fit_sql}
          {min_pri_sql}
          AND (
            NULLIF(TRIM(COALESCE(lm.email_norm, lm.email)), '') IS NOT NULL
            {research_filter_sql}
          )
        ORDER BY
          CASE COALESCE(lm.fit_bucket, 'low_fit')
            WHEN 'high_fit' THEN 0
            WHEN 'medium_fit' THEN 1
            ELSE 2
          END,
          COALESCE(m.already_in_archive_flag, 0) ASC,
          COALESCE(lm.priority_score, 0) DESC,
          CASE WHEN lm.equipment_match_tags IS NOT NULL AND length(trim(lm.equipment_match_tags)) > 0 THEN 0 ELSE 1 END,
          COALESCE(lm.lab_context_score, 0) DESC,
          lm.last_seen_at DESC
        LIMIT ?
        """,
        (*params_tail, int(fetch_cap)),
    )
    cols = [d[0] for d in cur.description]
    export_rows: list[dict[str, object]] = []
    seen_email: set[str] = set()
    n_scanned = 0

    for row in cur:
        n_scanned += 1
        d = dict(zip(cols, row))
        em_master = norm_lead_email(
            str(d["email_norm"]) if d["email_norm"] else None,
            str(d["email"]) if d["email"] else None,
        )
        research_status = str(d.get("contact_research_status") or "").strip().lower()
        em_research = norm_lead_email(
            None,
            str(d.get("resolved_contact_email") or "").strip(),
        )
        use_research = (
            not em_master
            and research_status in _RESEARCH_CONTACTABLE_STATUSES
            and bool(em_research)
        )
        em = em_master or (em_research if use_research else None)
        email_source = "lead_master" if em_master else ("lead_contact_research" if use_research else "")
        if not em or em in seen_email:
            continue
        inst_name = (str(d["org_name"] or "").strip() or str(d["matched_org_name"] or "").strip())
        gres = evaluate_export_eligibility(
            contact_email=em,
            institution_name=inst_name or None,
            ctx=gate_ctx,
        )
        if not gres.eligible:
            continue
        seen_email.add(em)
        sector = next(
            (str(d[k] or "").strip() for k in ("region", "city", "lead_type") if str(d.get(k) or "").strip()),
            "",
        )
        export_rows.append(
            {
                "case_id": f"lead_{d['id_lead']}",
                "id_lead": d["id_lead"],
                "contact_email": em,
                "email_source": email_source,
                "recipient_name": (str(d["contact_name"] or "").strip()),
                "institution_name": inst_name,
                "sector": sector,
                "fit_bucket": d["fit_bucket"],
                "priority_score": d["priority_score"],
                "already_in_archive_flag": d["already_in_archive_flag"],
                "source_name": d["source_name"],
                "website": str(d["website"] or "").strip(),
                "evidence_summary": str(d["evidence_summary"] or "").strip(),
                "variant_type": variant,
            }
        )
        if len(export_rows) >= int(limit):
            break

    stats = NextMarketingQueueStats(
        n_scanned=n_scanned,
        n_kept=len(export_rows),
        n_sent_folder_recipients=len(sent_norms),
        n_suppressed=len(suppressed),
        n_outreach_state=len(outreach_map),
        gmail_user=gmail_user.strip(),
    )
    return export_rows, stats
