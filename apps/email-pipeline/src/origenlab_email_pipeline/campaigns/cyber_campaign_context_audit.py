"""Read-only Cyber campaign context audit — open-quote safety before send.

No Gmail sends, no outreach-state writes. Consumes existing Cyber CSV outputs
and SQLite email archive for per-contact thread context.
"""

from __future__ import annotations

import csv
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.business_mart import domain_of
from origenlab_email_pipeline.campaigns.cyber_campaign_quality import is_active_warm_sales_thread

AUDIT_CSV_FIELDS: tuple[str, ...] = (
    "email",
    "organization",
    "segment",
    "latest_subject_safe",
    "latest_contact_date",
    "context_summary",
    "open_quote_status",
    "quote_safety_classification",
    "recommended_action",
    "recommended_template",
    "reason",
)

CLASS_ACTIVE_WAITING_SUPPLIER = "active_waiting_supplier_quote"
CLASS_ACTIVE_WAITING_ORIGENLAB = "active_waiting_origenlab_response"
CLASS_RECENT_CLIENT_REPLY = "recent_client_reply"
CLASS_OLD_QUOTE_NO_REPLY = "old_quote_no_reply"
CLASS_PREVIOUS_INACTIVE = "previous_contact_inactive"
CLASS_NET_NEW_SAFE = "net_new_safe"
CLASS_SUPPLIER_EXCLUDE = "supplier_or_admin_exclude"
CLASS_UNKNOWN = "unknown_manual_review"

ACTION_GENERIC = "A_generic_cyber"
ACTION_WARM = "B_warm_cyber_followup"
ACTION_DO_NOT_SEND = "C_do_not_contact_now"
ACTION_MANUAL = "D_manual_review_first"

TEMPLATE_GENERIC = "generic_cyber"
TEMPLATE_WARM = "warm_cyber_followup"
TEMPLATE_NONE = "none"
TEMPLATE_MANUAL = "manual_review"

RECENT_REPLY_DAYS = 14
OLD_QUOTE_MIN_DAYS = 7
INACTIVE_DAYS = 60

_QUOTE_SENT_RE = re.compile(
    r"\b(cotizaci[oó]n|cotizacion|presupuesto|propuesta|oferta)\b", re.I
)
_QUOTE_REQ_RE = re.compile(
    r"\b(solicit\w*|requer\w*|cotiz\w*|precio\w*|presupuesto\w*|ficha\w*|disponibilidad\w*)\b",
    re.I,
)
_SUPPLIER_RE = re.compile(
    r"\b(hielscher|bandelin|ika|thermo|crtop|proveedor|supplier|dhl|mybill)\b", re.I
)
_ADMIN_LOCAL_RE = re.compile(r"^(noreply|no-reply|mailer-daemon|postmaster|admin)\b", re.I)

_CATALOGUE_FOLLOWUP_ORGS = ("cesmec", "bureau veritas", "bureauveritas")
_SUPPLIER_CONFIG_ORGS = ("unach", "adventista", "hielscher", "uip2000")


@dataclass
class ThreadMessage:
    date_iso: str
    folder: str
    subject: str
    body: str
    direction: str  # inbound | outbound


@dataclass
class ContactContext:
    email: str
    organization: str
    segment: str
    reason_for_inclusion: str
    selection_rationale: str
    domain: str
    contact_quote_count: int = 0
    contact_purchase_count: int = 0
    org_quote_count: int = 0
    org_purchase_count: int = 0
    org_last_seen_at: str = ""
    contact_last_seen_at: str = ""
    thread: list[ThreadMessage] = field(default_factory=list)
    in_same_domain_review: bool = False
    in_excluded_blocked: bool = False
    exclusion_reason: str = ""


@dataclass
class AuditRow:
    email: str
    organization: str
    segment: str
    latest_subject_safe: str
    latest_contact_date: str
    context_summary: str
    open_quote_status: str
    quote_safety_classification: str
    recommended_action: str
    recommended_template: str
    reason: str

    def to_csv_dict(self) -> dict[str, str]:
        return {
            "email": self.email,
            "organization": self.organization,
            "segment": self.segment,
            "latest_subject_safe": self.latest_subject_safe,
            "latest_contact_date": self.latest_contact_date,
            "context_summary": self.context_summary,
            "open_quote_status": self.open_quote_status,
            "quote_safety_classification": self.quote_safety_classification,
            "recommended_action": self.recommended_action,
            "recommended_template": self.recommended_template,
            "reason": self.reason,
        }


@dataclass
class CyberContextAuditResult:
    top25_audits: list[AuditRow]
    send_now_generic: list[AuditRow]
    send_now_warm: list[AuditRow]
    do_not_send_active: list[AuditRow]
    manual_review_open_quotes: list[AuditRow]
    summary: dict[str, Any]


def _parse_date(value: str | None) -> datetime | None:
    if not value or not str(value).strip():
        return None
    raw = str(value).strip()
    try:
        if "T" in raw:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return datetime.fromisoformat(raw[:10]).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _is_sent_folder(folder: str | None) -> bool:
    f = (folder or "").lower()
    return "enviad" in f or "sent" in f


def _safe_subject(subject: str | None) -> str:
    s = (subject or "").strip()
    if s.startswith("=?"):
        return "(encoded subject — ver hilo Gmail)"
    return s[:100] if s else ""


def _days_since(dt: datetime | None, *, now: datetime) -> int | None:
    if dt is None:
        return None
    return max(0, (now - dt).days)


def _load_csv_index(path: Path, key: str = "email") -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        em = (row.get(key) or "").strip().lower()
        if em:
            out[em] = row
    return out


def _load_thread(conn: sqlite3.Connection, email: str, *, limit: int = 10) -> list[ThreadMessage]:
    em = email.strip().lower()
    cur = conn.execute(
        """
        SELECT date_iso, folder, subject,
               COALESCE(top_reply_clean, body_text_clean, body, '') AS body
        FROM emails
        WHERE lower(sender) LIKE ? OR lower(recipients) LIKE ?
        ORDER BY date_iso DESC
        LIMIT ?
        """,
        (f"%{em}%", f"%{em}%", limit),
    )
    out: list[ThreadMessage] = []
    for row in cur.fetchall():
        folder = str(row[1] or "")
        out.append(
            ThreadMessage(
                date_iso=str(row[0] or ""),
                folder=folder,
                subject=str(row[2] or ""),
                body=str(row[3] or "")[:800],
                direction="outbound" if _is_sent_folder(folder) else "inbound",
            )
        )
    return out


def _load_contact_stats(conn: sqlite3.Connection, email: str, domain: str) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "contact_quote_count": 0,
        "contact_purchase_count": 0,
        "contact_last_seen_at": "",
        "org_quote_count": 0,
        "org_purchase_count": 0,
        "org_last_seen_at": "",
    }
    cm = conn.execute(
        """
        SELECT quote_email_count, purchase_email_count, last_seen_at
        FROM contact_master WHERE lower(email) = ?
        """,
        (email.strip().lower(),),
    ).fetchone()
    if cm:
        stats["contact_quote_count"] = int(cm[0] or 0)
        stats["contact_purchase_count"] = int(cm[1] or 0)
        stats["contact_last_seen_at"] = str(cm[2] or "")
    om = conn.execute(
        """
        SELECT quote_email_count, purchase_email_count, last_seen_at
        FROM organization_master WHERE lower(domain) = ?
        """,
        (domain.strip().lower(),),
    ).fetchone()
    if om:
        stats["org_quote_count"] = int(om[0] or 0)
        stats["org_purchase_count"] = int(om[1] or 0)
        stats["org_last_seen_at"] = str(om[2] or "")
    return stats


def build_contact_context(
    conn: sqlite3.Connection,
    *,
    campaign_row: dict[str, str],
    same_domain_index: dict[str, dict[str, str]],
    excluded_index: dict[str, dict[str, str]],
) -> ContactContext:
    email = (campaign_row.get("email") or "").strip().lower()
    domain = (campaign_row.get("domain") or domain_of(email) or "").strip().lower()
    stats = _load_contact_stats(conn, email, domain)
    excl = excluded_index.get(email, {})
    same = same_domain_index.get(email, {})
    return ContactContext(
        email=email,
        organization=str(campaign_row.get("organization") or ""),
        segment=str(campaign_row.get("segment") or ""),
        reason_for_inclusion=str(campaign_row.get("reason_for_inclusion") or ""),
        selection_rationale=str(campaign_row.get("selection_rationale") or ""),
        domain=domain,
        contact_quote_count=int(stats["contact_quote_count"]),
        contact_purchase_count=int(stats["contact_purchase_count"]),
        org_quote_count=int(stats["org_quote_count"]),
        org_purchase_count=int(stats["org_purchase_count"]),
        org_last_seen_at=str(stats["org_last_seen_at"]),
        contact_last_seen_at=str(stats["contact_last_seen_at"]),
        thread=_load_thread(conn, email),
        in_same_domain_review=bool(same),
        in_excluded_blocked=bool(excl),
        exclusion_reason=str(excl.get("exclusion_reason") or ""),
    )


def _latest_messages(thread: list[ThreadMessage]) -> tuple[ThreadMessage | None, ThreadMessage | None, ThreadMessage | None]:
    latest = thread[0] if thread else None
    last_out: ThreadMessage | None = None
    last_in: ThreadMessage | None = None
    for msg in thread:
        if msg.direction == "outbound" and last_out is None:
            last_out = msg
        if msg.direction == "inbound" and last_in is None:
            last_in = msg
    return latest, last_out, last_in


def _blob(msg: ThreadMessage | None) -> str:
    if msg is None:
        return ""
    return f"{msg.subject} {msg.body}".lower()


def _segment_family(segment: str) -> str:
    if segment == "warm_open":
        return "warm"
    if segment == "previous_buyer_responder":
        return "previous"
    if segment in ("net_new_safe", "net_new_lead_research"):
        return "net_new"
    if segment == "same_domain_review":
        return "same_domain"
    return segment


def classify_contact_for_cyber(
    ctx: ContactContext,
    *,
    now: datetime | None = None,
) -> AuditRow:
    """Apply open-quote safety rules to one campaign contact."""
    ref = now or datetime.now(timezone.utc)
    latest, last_out, last_in = _latest_messages(ctx.thread)
    latest_dt = _parse_date(latest.date_iso if latest else ctx.contact_last_seen_at or ctx.org_last_seen_at)
    days = _days_since(latest_dt, now=ref)

    quote_count = max(ctx.contact_quote_count, ctx.org_quote_count)
    purchase_count = max(ctx.contact_purchase_count, ctx.org_purchase_count)
    org_l = ctx.organization.lower()
    em_l = ctx.email.lower()

    open_quote_status = "none"
    classification = CLASS_UNKNOWN
    action = ACTION_MANUAL
    template = TEMPLATE_MANUAL
    reasons: list[str] = []

    blocked, block_reason = is_active_warm_sales_thread(
        ctx.email, organization=ctx.organization, domain=ctx.domain
    )

    if ctx.in_excluded_blocked:
        classification = CLASS_SUPPLIER_EXCLUDE if "proveedor" in ctx.exclusion_reason else CLASS_UNKNOWN
        open_quote_status = "blocked_by_gate"
        action = ACTION_DO_NOT_SEND
        template = TEMPLATE_NONE
        reasons.append(f"bloqueado campaña: {ctx.exclusion_reason}")
    elif ctx.in_same_domain_review:
        classification = CLASS_UNKNOWN
        open_quote_status = "same_domain_pending"
        action = ACTION_MANUAL
        template = TEMPLATE_MANUAL
        reasons.append("mismo dominio ya contactado — revisión aparte")
    elif _ADMIN_LOCAL_RE.match(em_l.split("@", 1)[0]) or _SUPPLIER_RE.search(em_l):
        classification = CLASS_SUPPLIER_EXCLUDE
        open_quote_status = "admin_or_supplier"
        action = ACTION_DO_NOT_SEND
        template = TEMPLATE_NONE
        reasons.append("contacto admin/proveedor/ruido")
    elif any(m in org_l or m in em_l for m in _SUPPLIER_CONFIG_ORGS) or (
        blocked and any(m in (block_reason or "").lower() for m in ("unach", "hielscher", "adventista"))
    ):
        classification = CLASS_ACTIVE_WAITING_SUPPLIER
        open_quote_status = "active_supplier_configuration"
        action = ACTION_DO_NOT_SEND
        template = TEMPLATE_NONE
        reasons.append(block_reason or "UNACH/Hielscher — espera configuración/cotización proveedor")
    elif blocked or any(m in org_l for m in _CATALOGUE_FOLLOWUP_ORGS):
        classification = CLASS_RECENT_CLIENT_REPLY
        open_quote_status = "active_catalogue_followup"
        action = ACTION_DO_NOT_SEND
        template = TEMPLATE_NONE
        reasons.append(block_reason or "CESMEC — requiere seguimiento catálogo, no Cyber genérico")
    elif not ctx.thread and ctx.segment in ("net_new_safe", "net_new_lead_research"):
        classification = CLASS_NET_NEW_SAFE
        open_quote_status = "no_prior_thread"
        action = ACTION_GENERIC
        template = TEMPLATE_GENERIC
        reasons.append("sin historial Gmail — net-new Phase 10D verificado")
    elif days is not None and days <= RECENT_REPLY_DAYS and last_in and (
        last_out is None or _parse_date(last_in.date_iso) >= _parse_date(last_out.date_iso)
    ):
        if _QUOTE_REQ_RE.search(_blob(last_in)):
            classification = CLASS_ACTIVE_WAITING_ORIGENLAB
            open_quote_status = "client_waiting_origenlab_response"
            action = ACTION_DO_NOT_SEND
            template = TEMPLATE_NONE
            reasons.append(f"cliente respondió hace {days}d — OrigenLab debe responder antes de Cyber")
        else:
            classification = CLASS_RECENT_CLIENT_REPLY
            open_quote_status = "recent_client_reply"
            action = ACTION_DO_NOT_SEND
            template = TEMPLATE_NONE
            reasons.append(f"respuesta cliente reciente ({days}d) — excluir promo genérica")
    elif last_out and _SUPPLIER_RE.search(_blob(last_out)) and days is not None and days <= RECENT_REPLY_DAYS:
        classification = CLASS_ACTIVE_WAITING_SUPPLIER
        open_quote_status = "waiting_supplier_quote"
        action = ACTION_DO_NOT_SEND
        template = TEMPLATE_NONE
        reasons.append("hilo reciente apunta a espera cotización proveedor")
    elif last_out and _QUOTE_SENT_RE.search(_blob(last_out)):
        if days is not None and days >= OLD_QUOTE_MIN_DAYS:
            classification = CLASS_OLD_QUOTE_NO_REPLY
            open_quote_status = "quote_sent_no_reply"
            action = ACTION_WARM
            template = TEMPLATE_WARM
            reasons.append(f"cotización enviada hace {days}d sin respuesta — Cyber suave, no genérico")
        elif days is not None:
            classification = CLASS_RECENT_CLIENT_REPLY
            open_quote_status = "quote_sent_recent"
            action = ACTION_DO_NOT_SEND
            template = TEMPLATE_NONE
            reasons.append(f"cotización enviada hace solo {days}d — esperar antes de Cyber")
    elif ctx.segment == "warm_open":
        classification = CLASS_PREVIOUS_INACTIVE if (days or 0) > INACTIVE_DAYS else CLASS_RECENT_CLIENT_REPLY
        open_quote_status = "warm_historical_org_quotes" if quote_count > 0 else "warm_historical"
        if (days or 0) > INACTIVE_DAYS:
            action = ACTION_WARM
            template = TEMPLATE_WARM
            reasons.append(
                f"warm org ({quote_count} cotiz / {purchase_count} compras archivo); "
                f"contacto inactivo {days}d — seguimiento Cyber suave"
            )
        else:
            action = ACTION_DO_NOT_SEND
            template = TEMPLATE_NONE
            reasons.append(f"warm con actividad reciente ({days}d) — no promo masiva")
    elif ctx.segment == "previous_buyer_responder":
        classification = CLASS_PREVIOUS_INACTIVE
        open_quote_status = "closed_commercial_history"
        action = ACTION_GENERIC if (days or 0) > INACTIVE_DAYS else ACTION_WARM
        template = TEMPLATE_GENERIC if (days or 0) > INACTIVE_DAYS else TEMPLATE_WARM
        reasons.append(
            f"comprador/respondedor previo ({purchase_count} compras); "
            f"último contacto hace {days if days is not None else '?'}d"
        )
    elif ctx.segment in ("net_new_safe", "net_new_lead_research"):
        if (days or 9999) > INACTIVE_DAYS or not ctx.thread:
            classification = CLASS_NET_NEW_SAFE
            open_quote_status = "net_new_or_dormant"
            action = ACTION_GENERIC
            template = TEMPLATE_GENERIC
            reasons.append("net-new seguro o contacto dormido sin hilo activo")
        else:
            classification = CLASS_UNKNOWN
            open_quote_status = "unexpected_thread"
            action = ACTION_MANUAL
            template = TEMPLATE_MANUAL
            reasons.append("net-new con historial reciente — revisión manual")
    else:
        classification = CLASS_UNKNOWN
        open_quote_status = "unclassified"
        action = ACTION_MANUAL
        template = TEMPLATE_MANUAL
        reasons.append("evidencia insuficiente — revisión manual")

    context_bits = [
        f"segment={ctx.segment}",
        f"family={_segment_family(ctx.segment)}",
        f"inclusion={ctx.reason_for_inclusion[:120]}" if ctx.reason_for_inclusion else "",
        f"quotes_contact={ctx.contact_quote_count}/org={ctx.org_quote_count}",
        f"purchases_contact={ctx.contact_purchase_count}/org={ctx.org_purchase_count}",
    ]
    if ctx.selection_rationale:
        context_bits.append(f"selection={ctx.selection_rationale}")

    return AuditRow(
        email=ctx.email,
        organization=ctx.organization,
        segment=ctx.segment,
        latest_subject_safe=_safe_subject(latest.subject if latest else ""),
        latest_contact_date=(latest.date_iso[:10] if latest and latest.date_iso else ""),
        context_summary="; ".join(x for x in context_bits if x),
        open_quote_status=open_quote_status,
        quote_safety_classification=classification,
        recommended_action=action,
        recommended_template=template,
        reason="; ".join(reasons),
    )


def audit_cyber_top25(
    conn: sqlite3.Connection,
    *,
    out_dir: Path,
    now: datetime | None = None,
) -> CyberContextAuditResult:
    """Audit org-deduped top25 against SQLite thread context."""
    out_dir = out_dir.resolve()
    top25_path = out_dir / "cyber_top25_org_deduped.csv"
    if not top25_path.is_file():
        raise FileNotFoundError(f"Missing Cyber output: {top25_path}")

    same_domain_index = _load_csv_index(out_dir / "cyber_same_domain_review.csv")
    excluded_index = _load_csv_index(out_dir / "cyber_excluded_blocked.csv")

    with top25_path.open(encoding="utf-8-sig", newline="") as f:
        top25_rows = list(csv.DictReader(f))

    ref = now or datetime.now(timezone.utc)
    audits: list[AuditRow] = []
    for row in top25_rows:
        ctx = build_contact_context(
            conn,
            campaign_row=row,
            same_domain_index=same_domain_index,
            excluded_index=excluded_index,
        )
        audits.append(classify_contact_for_cyber(ctx, now=ref))

    send_generic = [a for a in audits if a.recommended_action == ACTION_GENERIC]
    send_warm = [a for a in audits if a.recommended_action == ACTION_WARM]
    do_not_send = [a for a in audits if a.recommended_action == ACTION_DO_NOT_SEND]
    manual = [a for a in audits if a.recommended_action == ACTION_MANUAL]

    summary = {
        "generated_at": ref.isoformat(),
        "top25_count": len(audits),
        "send_now_generic": len(send_generic),
        "send_now_warm_followup": len(send_warm),
        "do_not_send_active_or_blocked": len(do_not_send),
        "manual_review": len(manual),
        "safest_first_10": [a.email for a in sorted(
            send_generic + send_warm,
            key=lambda r: (
                0 if r.quote_safety_classification == CLASS_NET_NEW_SAFE else 1,
                0 if r.recommended_action == ACTION_GENERIC else 1,
                r.email,
            ),
        )[:10]],
        "remove_from_top25": [
            a.email for a in audits
            if a.recommended_action in (ACTION_DO_NOT_SEND, ACTION_MANUAL)
        ],
        "normal_follow_up_instead": [
            {
                "email": a.email,
                "organization": a.organization,
                "open_quote_status": a.open_quote_status,
                "reason": a.reason,
            }
            for a in do_not_send
        ],
    }
    return CyberContextAuditResult(
        top25_audits=audits,
        send_now_generic=send_generic,
        send_now_warm=send_warm,
        do_not_send_active=do_not_send,
        manual_review_open_quotes=manual,
        summary=summary,
    )


def _write_audit_csv(path: Path, rows: list[AuditRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(AUDIT_CSV_FIELDS), lineterminator="\n")
        w.writeheader()
        for row in rows:
            w.writerow(row.to_csv_dict())


def render_context_audit_markdown(
    result: CyberContextAuditResult,
    *,
    campaign_summary_path: Path | None = None,
) -> str:
    s = result.summary
    lines = [
        "# Cyber campaign context audit (read-only)",
        "",
        f"- **Generated:** {s.get('generated_at', '')}",
        "- **Mode:** read-only · no Gmail · no outreach writes · no sends",
        "",
        "## Campaign builder context",
        "",
        "The Cyber builder (`build_cyber_outreach_campaign`) merges three source lanes:",
        "",
        "1. **Warm / open** — archive outreach candidates with quote/purchase/dormant signals or active warm-opportunity flag.",
        "2. **Previous buyer/responder** — `follow_up_candidates` plus archive purchase/invoice history.",
        "3. **Net-new safe** — Phase 10D `lead_research_prospect` rows (`net_new_safe_review`) via `compute_next_marketing_recipients` gate.",
        "",
        "Phase 1.1 quality pass applies Chile geo filter, org/domain dedupe, and excludes active warm threads (CESMEC, UNACH/Hielscher, DHL).",
        "",
        "## Send safety summary",
        "",
        "| Bucket | Count |",
        "|--------|------:|",
        f"| Generic Cyber now (A) | {s.get('send_now_generic', 0)} |",
        f"| Warm Cyber follow-up (B) | {s.get('send_now_warm_followup', 0)} |",
        f"| Do not send — active/blocked (C) | {s.get('do_not_send_active_or_blocked', 0)} |",
        f"| Manual review first (D) | {s.get('manual_review', 0)} |",
        "",
        "## Top 25 — per-contact audit",
        "",
    ]
    for i, row in enumerate(result.top25_audits, start=1):
        lines.extend([
            f"### {i}. `{row.email}` — {row.organization or '—'}",
            "",
            f"- **Segment:** {row.segment}",
            f"- **Classification:** `{row.quote_safety_classification}`",
            f"- **Open quote status:** {row.open_quote_status}",
            f"- **Latest contact:** {row.latest_contact_date or '—'} — {row.latest_subject_safe or '—'}",
            f"- **Recommended:** {row.recommended_action} → template `{row.recommended_template}`",
            f"- **Reason:** {row.reason}",
            f"- **Context:** {row.context_summary}",
            "",
        ])

    lines.extend([
        "## Top 10 safest to send first",
        "",
    ])
    for i, em in enumerate(s.get("safest_first_10") or [], start=1):
        match = next((r for r in result.top25_audits if r.email == em), None)
        label = match.recommended_action if match else "?"
        lines.append(f"{i}. `{em}` ({label})")
    lines.extend([
        "",
        "## Remove from top25 before generic blast",
        "",
    ])
    remove = s.get("remove_from_top25") or []
    if remove:
        for em in remove:
            lines.append(f"- `{em}`")
    else:
        lines.append("- (none — all top25 cleared for generic or warm send)")
    lines.extend([
        "",
        "## Active cases needing normal follow-up (not Cyber)",
        "",
    ])
    follow = s.get("normal_follow_up_instead") or []
    if follow:
        for item in follow:
            lines.append(
                f"- `{item['email']}` ({item['organization']}): **{item['open_quote_status']}** — {item['reason']}"
            )
    else:
        lines.append("- (none in top25)")
    lines.extend([
        "",
        "## Output files",
        "",
        "- `cyber_send_now_generic_review.csv`",
        "- `cyber_send_now_warm_followup_review.csv`",
        "- `cyber_do_not_send_active_cases.csv`",
        "- `cyber_manual_review_open_quotes.csv`",
        "- `cyber_campaign_context_audit.md`",
        "",
        "**Do not send until human approval.**",
    ])
    if campaign_summary_path and campaign_summary_path.is_file():
        lines.extend(["", f"Campaign summary source: `{campaign_summary_path.name}`"])
    return "\n".join(lines) + "\n"


def write_cyber_context_audit_outputs(
    result: CyberContextAuditResult,
    out_dir: Path,
    *,
    campaign_summary_path: Path | None = None,
) -> dict[str, Path]:
    out_dir = out_dir.resolve()
    paths = {
        "generic": out_dir / "cyber_send_now_generic_review.csv",
        "warm": out_dir / "cyber_send_now_warm_followup_review.csv",
        "do_not_send": out_dir / "cyber_do_not_send_active_cases.csv",
        "manual": out_dir / "cyber_manual_review_open_quotes.csv",
        "report": out_dir / "cyber_campaign_context_audit.md",
    }
    _write_audit_csv(paths["generic"], result.send_now_generic)
    _write_audit_csv(paths["warm"], result.send_now_warm)
    _write_audit_csv(paths["do_not_send"], result.do_not_send_active)
    _write_audit_csv(paths["manual"], result.manual_review_open_quotes)
    paths["report"].write_text(
        render_context_audit_markdown(result, campaign_summary_path=campaign_summary_path),
        encoding="utf-8",
    )
    return paths
