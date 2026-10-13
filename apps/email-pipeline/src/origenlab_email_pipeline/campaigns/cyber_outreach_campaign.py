"""Read-only Cyber B2B outreach segmentation for laboratory equipment (Chile).

No Gmail sends, no outreach-state writes. Uses ``candidate_export_gate`` and
``contacted_universe_audit`` context for suppression / no-repeat policy.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.archive_outreach_queue import (
    fetch_archive_outreach_candidates,
)
from origenlab_email_pipeline.business_mart import domain_of
from origenlab_email_pipeline.candidate_export_gate import normalize_export_email
from origenlab_email_pipeline.campaigns.cyber_campaign_gate import classify_safety, product_angle
from origenlab_email_pipeline.campaigns.cyber_campaign_templates import (
    apply_templates_to_row,
    render_email_templates_markdown,
)
from origenlab_email_pipeline.campaigns.cyber_campaign_types import (
    CYBER_CAMPAIGN_SLUG,
    CSV_FIELDS,
    SAFETY_BLOCKED,
    SAFETY_SAME_DOMAIN,
    SEGMENT_EXCLUDED,
    SEGMENT_NET_NEW,
    SEGMENT_PREVIOUS,
    SEGMENT_SAME_DOMAIN,
    SEGMENT_WARM,
    CyberCampaignRow,
)
from origenlab_email_pipeline.leads.contacted_universe_audit import (
    build_contacted_universe,
    build_contacted_universe_context,
)
from origenlab_email_pipeline.campaigns.cyber_campaign_quality import (
    CSV_FIELDS_EXTENDED,
    GEO_MANUAL_FIELDS,
    apply_quality_pass,
    gather_lead_research_net_new,
    row_to_extended_csv,
    row_to_geo_manual_csv,
    finalize_lead_research_candidates,
)
from origenlab_email_pipeline.next_marketing_queue import compute_next_marketing_recipients

_SEGMENT_PRIORITY: dict[str, int] = {
    SEGMENT_WARM: 0,
    SEGMENT_PREVIOUS: 1,
    SEGMENT_NET_NEW: 2,
}


@dataclass
class CyberCampaignBuildResult:
    warm: list[CyberCampaignRow] = field(default_factory=list)
    previous_buyers: list[CyberCampaignRow] = field(default_factory=list)
    net_new: list[CyberCampaignRow] = field(default_factory=list)
    net_new_lead_research: list[CyberCampaignRow] = field(default_factory=list)
    same_domain: list[CyberCampaignRow] = field(default_factory=list)
    excluded: list[CyberCampaignRow] = field(default_factory=list)
    manual_geo: list[CyberCampaignRow] = field(default_factory=list)
    top25: list[CyberCampaignRow] = field(default_factory=list)
    top25_deduped: list[CyberCampaignRow] = field(default_factory=list)
    top25_raw: list[CyberCampaignRow] = field(default_factory=list)
    meta_by_email: dict[str, dict[str, Any]] = field(default_factory=dict)
    quality_metrics: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    templates_markdown: str = ""


def _gather_warm_sources(
    conn: sqlite3.Connection,
    universe_ctx: Any,
    *,
    archive_fetch_cap: int,
    archive_scan_limit: int,
) -> list[dict[str, Any]]:
    warm_emails = universe_ctx.warm_opportunity_contacts
    out: list[dict[str, Any]] = []
    candidates = fetch_archive_outreach_candidates(
        conn, fetch_cap=archive_fetch_cap, limit=archive_scan_limit
    )
    for c in candidates:
        if c.warmth_band == "weak":
            continue
        if c.is_supplier_like or c.is_admin_transactional_like:
            continue
        quote = c.contact_quote_email_count + c.org_quote_email_count
        purchase = c.contact_purchase_email_count + c.org_purchase_email_count
        has_signal = (
            quote > 0
            or purchase > 0
            or c.dormant_signal_count > 0
            or c.contact_email in warm_emails
            or c.warmth_band == "strong"
        )
        if not has_signal:
            continue
        reason_parts = []
        if c.contact_email in warm_emails:
            reason_parts.append("señal oportunidad activa")
        if quote > 0:
            reason_parts.append(f"cotizaciones históricas ({quote})")
        if purchase > 0:
            reason_parts.append(f"compras históricas ({purchase})")
        if c.dormant_signal_count > 0:
            reason_parts.append("señales dormidas/reactivación")
        if c.warmth_band == "strong":
            reason_parts.append("calor fuerte en archivo")
        out.append(
            {
                "email": c.contact_email,
                "organization": c.institution_name,
                "contact_name": c.recipient_name,
                "segment": SEGMENT_WARM,
                "reason_for_inclusion": "; ".join(reason_parts) or "caso cálido archivo",
                "product_angle": product_angle(
                    quote_count=quote, purchase_count=purchase
                ),
                "priority_score": float(c.warmth_score)
                + (30.0 if c.contact_email in warm_emails else 0.0),
            }
        )
    return out


def _gather_previous_buyer_sources(
    conn: sqlite3.Connection,
    universe_result: Any,
    universe_ctx: Any,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in universe_result.filtered.follow_up_candidates:
        em = normalize_export_email(row.get("normalized_email") or "") or ""
        if not em or em in seen:
            continue
        seen.add(em)
        angle = row.get("recommended_follow_up_angle") or ""
        sent_n = int(row.get("sent_count") or 0)
        recv_n = int(row.get("received_count") or 0)
        replied = (row.get("replied_bool") or "").lower() == "true"
        reason = []
        if replied:
            reason.append("respondió en hilo previo")
        if sent_n > 0:
            reason.append(f"envíos previos ({sent_n})")
        if recv_n > 0:
            reason.append(f"respuestas entrantes ({recv_n})")
        if angle:
            reason.append(f"ángulo: {angle}")
        out.append(
            {
                "email": em,
                "organization": row.get("organization_name") or "",
                "contact_name": row.get("display_name") or "",
                "segment": SEGMENT_PREVIOUS,
                "reason_for_inclusion": "; ".join(reason) or "relación comercial previa",
                "product_angle": product_angle(),
                "priority_score": 50.0 + (20.0 if replied else 0.0) + min(sent_n, 10) * 2.0,
            }
        )

    candidates = fetch_archive_outreach_candidates(conn, fetch_cap=8000, limit=400)
    for c in candidates:
        if c.contact_email in seen:
            continue
        purchase = c.contact_purchase_email_count + c.org_purchase_email_count
        invoice = c.contact_invoice_email_count + c.org_invoice_email_count
        if purchase == 0 and invoice == 0:
            continue
        seen.add(c.contact_email)
        out.append(
            {
                "email": c.contact_email,
                "organization": c.institution_name,
                "contact_name": c.recipient_name,
                "segment": SEGMENT_PREVIOUS,
                "reason_for_inclusion": (
                    f"compras archivo ({purchase}); facturación ({invoice})"
                ),
                "product_angle": product_angle(purchase_count=purchase),
                "priority_score": 40.0 + purchase * 3.0 + invoice * 2.0,
            }
        )
    return out


def _gather_net_new_sources(
    conn: sqlite3.Connection,
    *,
    gmail_user: str,
    sent_folders: tuple[str, ...],
    limit: int,
    fetch_cap: int,
) -> list[dict[str, Any]]:
    rows, _stats = compute_next_marketing_recipients(
        conn,
        gmail_user=gmail_user,
        sent_folders=sent_folders,
        limit=limit,
        fetch_cap=fetch_cap,
    )
    out: list[dict[str, Any]] = []
    for d in rows:
        em = str(d.get("contact_email") or "").strip().lower()
        if not em:
            continue
        fit = str(d.get("fit_bucket") or "")
        pri = float(d.get("priority_score") or 0.0)
        out.append(
            {
                "email": em,
                "organization": str(d.get("institution_name") or ""),
                "contact_name": str(d.get("recipient_name") or ""),
                "segment": SEGMENT_NET_NEW,
                "reason_for_inclusion": (
                    f"lead_master encaje {fit}; prioridad {pri:.0f}; "
                    f"fuente {d.get('email_source') or 'lead'}"
                ),
                "product_angle": product_angle(fit_bucket=fit),
                "priority_score": pri + (10.0 if fit == "high_fit" else 0.0),
            }
        )
    return out


def _merge_raw_candidates(
    *batches: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Dedupe by email; keep highest-priority segment."""
    merged: dict[str, dict[str, Any]] = {}
    for batch in batches:
        for raw in batch:
            em = normalize_export_email(raw.get("email") or "") or ""
            if not em:
                continue
            raw = {**raw, "email": em}
            prev = merged.get(em)
            if prev is None:
                merged[em] = raw
                continue
            if _SEGMENT_PRIORITY.get(raw["segment"], 99) < _SEGMENT_PRIORITY.get(
                prev["segment"], 99
            ):
                merged[em] = raw
            elif raw["segment"] == prev["segment"]:
                if float(raw.get("priority_score") or 0) > float(
                    prev.get("priority_score") or 0
                ):
                    merged[em] = raw
    return merged


def _finalize_rows(
    merged: dict[str, dict[str, Any]],
    *,
    gate_ctx: Any,
    universe_ctx: Any,
) -> tuple[
    list[CyberCampaignRow],
    list[CyberCampaignRow],
    list[CyberCampaignRow],
    list[CyberCampaignRow],
    list[CyberCampaignRow],
]:
    warm: list[CyberCampaignRow] = []
    previous: list[CyberCampaignRow] = []
    net_new: list[CyberCampaignRow] = []
    same_domain: list[CyberCampaignRow] = []
    excluded: list[CyberCampaignRow] = []

    for raw in merged.values():
        em = raw["email"]
        org = str(raw.get("organization") or "")
        safety, excl = classify_safety(em, org or None, gate_ctx=gate_ctx, universe_ctx=universe_ctx)
        base = CyberCampaignRow(
            email=em,
            organization=org,
            contact_name=str(raw.get("contact_name") or ""),
            segment=str(raw.get("segment") or ""),
            reason_for_inclusion=str(raw.get("reason_for_inclusion") or ""),
            product_angle=str(raw.get("product_angle") or ""),
            suggested_subject="",
            suggested_message="",
            safety_status=safety,
            exclusion_reason=excl,
            priority_score=float(raw.get("priority_score") or 0.0),
        )
        if safety == SAFETY_SAME_DOMAIN:
            row = replace(
                base,
                segment=SEGMENT_SAME_DOMAIN,
                safety_status=SAFETY_SAME_DOMAIN,
            )
            same_domain.append(row)
        elif safety == SAFETY_BLOCKED:
            row = replace(
                base,
                segment=SEGMENT_EXCLUDED,
                safety_status=SAFETY_BLOCKED,
            )
            excluded.append(row)
        else:
            row = apply_templates_to_row(base)
            seg = row.segment
            if seg == SEGMENT_WARM:
                warm.append(row)
            elif seg == SEGMENT_PREVIOUS:
                previous.append(row)
            else:
                net_new.append(row)

    warm.sort(key=lambda r: -r.priority_score)
    previous.sort(key=lambda r: -r.priority_score)
    net_new.sort(key=lambda r: -r.priority_score)
    same_domain.sort(key=lambda r: (-r.priority_score, r.email))
    excluded.sort(key=lambda r: (r.exclusion_reason, r.email))
    return warm, previous, net_new, same_domain, excluded


def _populate_meta_from_merged(
    merged: dict[str, dict[str, Any]],
    meta_by_email: dict[str, dict[str, Any]],
) -> None:
    for em, raw in merged.items():
        meta = dict(raw.get("_meta") or {})
        if not meta.get("domain"):
            meta["domain"] = domain_of(em) or ""
        meta_by_email.setdefault(em, {}).update(meta)


def _excluded_breakdown(excluded: list[CyberCampaignRow]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in excluded:
        key = r.exclusion_reason or "sin motivo"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: (-x[1], x[0])))


def build_cyber_outreach_campaign(
    conn: sqlite3.Connection,
    *,
    gmail_user: str,
    sent_folders: tuple[str, ...],
    do_not_repeat_csv: Path | None = None,
    lead_research_review_csv: Path | None = None,
    warm_archive_scan_limit: int = 350,
    net_new_limit: int = 60,
    net_new_fetch_cap: int = 4000,
) -> CyberCampaignBuildResult:
    """Build segmented Cyber review lists (read-only)."""
    universe_ctx, _activity, _sent_rows = build_contacted_universe_context(
        conn,
        gmail_user=gmail_user,
        sent_folders=sent_folders,
        do_not_repeat_csv=do_not_repeat_csv,
    )
    universe_result = build_contacted_universe(
        conn,
        gmail_user=gmail_user,
        sent_folders=sent_folders,
        do_not_repeat_csv=do_not_repeat_csv,
    )
    gate_ctx = universe_ctx.gate

    warm_raw = _gather_warm_sources(
        conn,
        universe_ctx,
        archive_fetch_cap=12000,
        archive_scan_limit=warm_archive_scan_limit,
    )
    prev_raw = _gather_previous_buyer_sources(conn, universe_result, universe_ctx)
    net_raw = _gather_net_new_sources(
        conn,
        gmail_user=gmail_user,
        sent_folders=sent_folders,
        limit=net_new_limit,
        fetch_cap=net_new_fetch_cap,
    )
    merged = _merge_raw_candidates(warm_raw, prev_raw, net_raw)
    meta_by_email: dict[str, dict[str, Any]] = {}
    _populate_meta_from_merged(merged, meta_by_email)

    warm, previous, net_new, same_domain, excluded = _finalize_rows(
        merged, gate_ctx=gate_ctx, universe_ctx=universe_ctx
    )

    review_csv = lead_research_review_csv
    if review_csv is None:
        review_csv = Path("reports/out/active/current") / "new_customer_targets_review.csv"
    lr_raw = gather_lead_research_net_new(conn, review_csv=review_csv)
    for raw in lr_raw:
        em = raw.get("email") or ""
        if em:
            meta_by_email.setdefault(em, {}).update(dict(raw.get("_meta") or {}))
    lr_eligible, lr_manual, lr_excluded = finalize_lead_research_candidates(
        lr_raw,
        gate_ctx=gate_ctx,
        universe_ctx=universe_ctx,
        meta_by_email=meta_by_email,
    )
    for row in lr_eligible + lr_manual:
        meta_by_email.setdefault(row.email, {})

    counts_before_quality = {
        SEGMENT_WARM: len(warm),
        SEGMENT_PREVIOUS: len(previous),
        SEGMENT_NET_NEW: len(net_new),
        "lead_research_net_new_pre_quality": len(lr_eligible),
    }

    quality = apply_quality_pass(
        warm=warm,
        previous=previous,
        net_new=net_new,
        same_domain=same_domain,
        excluded=excluded,
        lead_research_eligible=lr_eligible,
        lead_research_manual=lr_manual,
        lead_research_excluded=lr_excluded,
        meta_by_email=meta_by_email,
    )

    raw_counts = {
        "warm_sources": len(warm_raw),
        "previous_sources": len(prev_raw),
        "net_new_lead_master_sources": len(net_raw),
        "lead_research_sources": len(lr_raw),
        "unique_candidates_merged": len(merged),
    }
    summary = {
        "campaign_slug": CYBER_CAMPAIGN_SLUG,
        "phase": "1.1_quality_pass",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gmail_user": gmail_user,
        "sent_folders": list(sent_folders),
        "read_only": True,
        "no_gmail_send": True,
        "no_outreach_state_writes": True,
        "raw_source_counts": raw_counts,
        "eligible_before_quality": counts_before_quality,
        "eligible_by_segment": {
            SEGMENT_WARM: len(quality.warm),
            SEGMENT_PREVIOUS: len(quality.previous_buyers),
            SEGMENT_NET_NEW: len(quality.net_new),
            SEGMENT_SAME_DOMAIN: len(quality.same_domain),
            SEGMENT_EXCLUDED: len(quality.excluded),
            "manual_geo_review": len(quality.manual_geo),
            "net_new_lead_research": len(quality.net_new_lead_research),
        },
        "quality_metrics": quality.metrics,
        "excluded_breakdown_es": _excluded_breakdown(quality.excluded),
        "top25_raw_count": len(quality.top25_raw),
        "top25_deduped_count": len(quality.top25_deduped),
        "contacted_universe": {
            k: universe_result.summary.get(k)
            for k in (
                "unique_outbound_recipient_emails",
                "contacts_blocked_from_outreach",
                "follow_up_candidates_review",
                "active_warm_opportunity_contacts",
            )
        },
    }
    return CyberCampaignBuildResult(
        warm=quality.warm,
        previous_buyers=quality.previous_buyers,
        net_new=quality.net_new,
        net_new_lead_research=quality.net_new_lead_research,
        same_domain=quality.same_domain,
        excluded=quality.excluded,
        manual_geo=quality.manual_geo,
        top25=quality.top25_deduped,
        top25_deduped=quality.top25_deduped,
        top25_raw=quality.top25_raw,
        meta_by_email=meta_by_email,
        quality_metrics=quality.metrics,
        summary=summary,
        templates_markdown=render_email_templates_markdown(),
    )


def _write_csv(path: Path, rows: list[CyberCampaignRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(CSV_FIELDS), lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r.to_csv_dict())


def _write_extended_csv(
    path: Path,
    rows: list[CyberCampaignRow],
    meta_by_email: dict[str, dict[str, Any]],
    *,
    fieldnames: tuple[str, ...] = CSV_FIELDS_EXTENDED,
    row_mapper=row_to_extended_csv,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(fieldnames), lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(row_mapper(r, meta_by_email))


def write_cyber_campaign_outputs(result: CyberCampaignBuildResult, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "warm": out_dir / "cyber_warm_contacts_review.csv",
        "previous": out_dir / "cyber_previous_buyers_review.csv",
        "net_new": out_dir / "cyber_net_new_safe_review.csv",
        "excluded": out_dir / "cyber_excluded_blocked.csv",
        "same_domain": out_dir / "cyber_same_domain_review.csv",
        "top25": out_dir / "cyber_top25_recommended.csv",
        "summary_json": out_dir / "cyber_campaign_summary.json",
        "report_md": out_dir / "cyber_campaign_report.md",
        "templates": out_dir / "cyber_email_templates_es.md",
    }
    meta = result.meta_by_email
    _write_extended_csv(paths["warm"], result.warm, meta)
    _write_extended_csv(paths["previous"], result.previous_buyers, meta)
    _write_extended_csv(paths["net_new"], result.net_new, meta)
    _write_csv(paths["excluded"], result.excluded)
    _write_csv(paths["same_domain"], result.same_domain)
    _write_extended_csv(paths["top25"], result.top25_deduped, meta)
    paths["top25_org_deduped"] = out_dir / "cyber_top25_org_deduped.csv"
    paths["manual_geo"] = out_dir / "cyber_manual_review_geo_or_domain.csv"
    paths["net_new_lr"] = out_dir / "cyber_net_new_from_lead_research_review.csv"
    paths["quality_report"] = out_dir / "cyber_campaign_quality_report.md"
    _write_extended_csv(paths["top25_org_deduped"], result.top25_deduped, meta)
    _write_extended_csv(
        paths["manual_geo"],
        result.manual_geo,
        meta,
        fieldnames=GEO_MANUAL_FIELDS,
        row_mapper=row_to_geo_manual_csv,
    )
    _write_extended_csv(paths["net_new_lr"], result.net_new_lead_research, meta)
    paths["summary_json"].write_text(
        json.dumps(result.summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    paths["templates"].write_text(result.templates_markdown, encoding="utf-8")
    paths["report_md"].write_text(_render_report_md(result), encoding="utf-8")
    paths["quality_report"].write_text(_render_quality_report_md(result), encoding="utf-8")
    return paths


def _render_report_md(result: CyberCampaignBuildResult) -> str:
    s = result.summary
    elig = s.get("eligible_by_segment") or {}
    excl = s.get("excluded_breakdown_es") or {}
    lines = [
        "# Informe campaña Cyber — OrigenLab (solo revisión)",
        "",
        f"- **Generado:** {s.get('generated_at', '')}",
        f"- **Slug:** `{s.get('campaign_slug', '')}`",
        "- **Modo:** lectura; sin envíos Gmail; sin escritura de outreach state",
        "",
        "## Candidatos por segmento (elegibles para revisión)",
        "",
        "| Segmento | Elegibles |",
        "|----------|----------:|",
        f"| Warm / casos abiertos | {elig.get(SEGMENT_WARM, 0)} |",
        f"| Compradores / respondedores previos | {elig.get(SEGMENT_PREVIOUS, 0)} |",
        f"| Net-new seguro | {elig.get(SEGMENT_NET_NEW, 0)} |",
        f"| Mismo dominio (revisión aparte) | {elig.get(SEGMENT_SAME_DOMAIN, 0)} |",
        f"| Excluidos / bloqueados | {elig.get(SEGMENT_EXCLUDED, 0)} |",
        "",
        "## Excluidos — motivo",
        "",
    ]
    if excl:
        for reason, count in excl.items():
            lines.append(f"- {reason}: **{count}**")
    else:
        lines.append("- (ninguno)")
    lines.extend(["", "## Top 25 (org-deduped)", ""])
    for i, r in enumerate(result.top25_deduped, start=1):
        lines.append(
            f"{i}. `{r.email}` — {r.organization or '—'} "
            f"({r.segment}; score {r.priority_score:.0f})"
        )
    lines.extend(
        [
            "",
            "## Archivos",
            "",
            "- `cyber_warm_contacts_review.csv`",
            "- `cyber_previous_buyers_review.csv`",
            "- `cyber_net_new_safe_review.csv`",
            "- `cyber_net_new_from_lead_research_review.csv`",
            "- `cyber_same_domain_review.csv`",
            "- `cyber_excluded_blocked.csv`",
            "- `cyber_manual_review_geo_or_domain.csv`",
            "- `cyber_top25_recommended.csv`",
            "- `cyber_top25_org_deduped.csv`",
            "- `cyber_campaign_quality_report.md`",
            "- `cyber_email_templates_es.md`",
            "",
            "**No enviar** hasta revisión humana y aprobación explícita.",
        ]
    )
    return "\n".join(lines) + "\n"


def _render_quality_report_md(result: CyberCampaignBuildResult) -> str:
    s = result.summary
    qm = result.quality_metrics or {}
    org = qm.get("org_dedupe") or {}
    before = s.get("eligible_before_quality") or {}
    after = s.get("eligible_by_segment") or {}
    lines = [
        "# Cyber Phase 1.1 — informe de calidad (solo revisión)",
        "",
        f"- **Generado:** {s.get('generated_at', '')}",
        "- **Sin envíos** · sin Gmail · sin outreach writes",
        "",
        "## Net-new Phase 10D recuperados",
        "",
        f"- Fuentes lead research cargadas: {s.get('raw_source_counts', {}).get('lead_research_sources', 0)}",
        f"- Elegibles gate+Chile (lead research): **{qm.get('lead_research_net_new_recovered', 0)}**",
        f"- Manual geo (lead research): {qm.get('lead_research_manual_geo', 0)}",
        f"- Bloqueados gate (lead research): {qm.get('lead_research_gate_excluded', 0)}",
        f"- Net-new combinado post-merge: **{after.get('net_new_safe', 0)}**",
        "",
        "## Dedupe por organización/dominio",
        "",
    ]
    for label, key in (
        ("Warm", "warm"),
        ("Previous buyers", "previous"),
        ("Net-new", "net_new"),
        ("Lead research", "lead_research"),
    ):
        st = org.get(key) or {}
        lines.append(
            f"- **{label}:** {st.get('rows_before', 0)} → {st.get('rows_after', 0)} filas "
            f"({st.get('alternate_contacts_collapsed', 0)} contactos alternos colapsados)"
        )
    lines.extend(
        [
            "",
            "## Antes / después filtro Chile",
            "",
            "| Segmento | Antes | Después |",
            "|----------|------:|--------:|",
            f"| Warm | {before.get('warm_open', 0)} | {after.get('warm_open', 0)} |",
            f"| Previous | {before.get('previous_buyer_responder', 0)} | {after.get('previous_buyer_responder', 0)} |",
            f"| Net-new | {before.get('net_new_safe', 0)} | {after.get('net_new_safe', 0)} |",
            f"| Manual geo (total) | — | {after.get('manual_geo_review', 0)} |",
            "",
            "## Exclusiones geografía / dominio extranjero",
            "",
            f"- Total en `cyber_manual_review_geo_or_domain.csv`: **{after.get('manual_geo_review', 0)}**",
            "- Incluye dominios no `.cl` sin evidencia Chile (p. ej. `gsa.gov.gh`)",
            "",
            "## Top 10 recomendados (org-deduped) — por qué",
            "",
        ]
    )
    for i, r in enumerate(result.top25_deduped[:10], start=1):
        meta = result.meta_by_email.get(r.email, {})
        why = meta.get("selection_rationale") or r.reason_for_inclusion
        sec = meta.get("secondary_contact_emails") or ""
        alt = f" · alternos: {sec}" if sec else ""
        lines.append(
            f"{i}. **{r.email}** — {r.organization or '—'} · {r.segment} · {why}{alt}"
        )
    lines.extend(
        [
            "",
            "## Top 25 sin dedupe org (referencia)",
            "",
            f"Filas: {len(result.top25_raw)} — puede repetir dominio (p. ej. Saval). "
            "Usar `cyber_top25_org_deduped.csv` para envío.",
            "",
            "**No enviar automáticamente.**",
        ]
    )
    return "\n".join(lines) + "\n"
