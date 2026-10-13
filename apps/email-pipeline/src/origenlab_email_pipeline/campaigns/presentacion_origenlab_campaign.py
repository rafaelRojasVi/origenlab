"""Read-only Presentación OrigenLab review lists (+ mención Cyber suave).

No Gmail sends, no outreach-state writes. Builds operator review CSVs from SQLite
and existing Phase 10 / contacted-universe artifacts.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.business_mart import domain_of, emails_in
from origenlab_email_pipeline.candidate_export_gate import (
    email_domain_under_operator_domain_suppression,
    normalize_export_email,
)
from origenlab_email_pipeline.campaigns.cyber_campaign_gate import product_angle
from origenlab_email_pipeline.campaigns.presentacion_origenlab_templates import (
    render_messages_markdown,
    template_presentacion_send_now_es,
    template_same_domain_review_note,
)
from origenlab_email_pipeline.campaigns.presentacion_origenlab_types import (
    ACTION_HOLD_ACTIVE,
    ACTION_RESEARCH_CONTACT,
    ACTION_REVIEW_HISTORY_ONLY,
    ACTION_SEND_NOW_REVIEW,
    BUCKET_HOLD_ACTIVE,
    BUCKET_MISSING_EMAIL,
    BUCKET_SAME_DOMAIN,
    BUCKET_SEND_NOW,
    PRESENTACION_CAMPAIGN_SLUG,
    REVIEW_CSV_FIELDS,
    PresentacionReviewRow,
)
from origenlab_email_pipeline.leads.contacted_universe_audit import (
    build_contacted_universe_context,
)
from origenlab_email_pipeline.leads.new_customer_research import load_exclusion_lists
from origenlab_email_pipeline.marketing_contact_noise import (
    marketing_outreach_noise_email,
    marketing_outreach_noise_organization_guess,
)
from origenlab_email_pipeline.marketing_supplier_domains import is_supplier_email_domain

_PERSONAL_EMAIL_DOMAINS: frozenset[str] = frozenset(
    {"gmail.com", "hotmail.com", "yahoo.com", "outlook.com", "live.com", "icloud.com"}
)

_CYBERDAY_SUBJECT_RE = re.compile(r"cyber\s*day", re.I)
_PRESENTATION_SUBJECT_RE = re.compile(
    r"presentaci|origenlab|centríf|centrif|cotiz|equipos|laboratorio|sonic|homogen|incub",
    re.I,
)

_BLOCKED_STATUSES: frozenset[str] = frozenset(
    {
        "supplier_do_not_market",
        "internal_do_not_market",
        "bounced_do_not_contact",
    }
)

OUTPUT_SEND_NOW = "presentacion_origenlab_send_now_review.csv"
OUTPUT_SAME_DOMAIN = "presentacion_origenlab_same_domain_review.csv"
OUTPUT_HOLD = "presentacion_origenlab_hold_active_cases.csv"
OUTPUT_MISSING = "presentacion_origenlab_missing_email_research.csv"
OUTPUT_MESSAGES = "presentacion_origenlab_messages.md"


@dataclass
class PresentacionBuildResult:
    send_now: list[PresentacionReviewRow] = field(default_factory=list)
    same_domain: list[PresentacionReviewRow] = field(default_factory=list)
    hold_active: list[PresentacionReviewRow] = field(default_factory=list)
    missing_email: list[PresentacionReviewRow] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    messages_markdown: str = ""


def load_cyberday_sent_emails(path: Path) -> frozenset[str]:
    """Emails from cyber_production_send_log.json (47 production sends)."""
    if not path.is_file():
        return frozenset()
    data = json.loads(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for entry in data.get("sent") or []:
        em = normalize_export_email(str(entry.get("email") or ""))
        if em:
            out.add(em)
    return frozenset(out)


def is_presentacion_hold_active_case(
    email: str,
    *,
    organization: str = "",
    domain: str = "",
) -> tuple[bool, str]:
    """CESMEC, UNACH/Hielscher, ONGO, CRTOP — not whole-domain blocks like all @uach.cl."""
    em = (email or "").strip().lower()
    dom = (domain or domain_of(em) or "").strip().lower()
    org_l = (organization or "").lower()

    hold_domains = frozenset(
        {
            "hielscher.com",
            "ongo.cl",
            "crtopmachine.com",
            "crtop.cl",
            "bureauveritas.com",
            "cesmec.cl",
            "ceaf.cl",
        }
    )
    if dom in hold_domains or any(dom.endswith("." + d) for d in hold_domains):
        return True, f"caso comercial activo — dominio {dom}"

    org_markers = (
        "cesmec",
        "hielscher",
        "ongo",
        "crtop",
        "bureau veritas",
        "bureauveritas",
        "uip2000",
    )
    if any(m in org_l for m in org_markers):
        return True, f"caso comercial activo — organización ({organization})"

    email_markers = ("hielscher", "marcos@hielscher", "hola@ongo", "@ongo.cl", "crtop")
    if any(m in em for m in email_markers):
        return True, "caso comercial activo — contacto en seguimiento"

    return False, ""


def check_presentacion_hard_block(
    email: str,
    organization: str | None,
    *,
    gate_ctx: Any,
    excl: Any,
    cyberday_sent: frozenset[str],
) -> tuple[bool, str]:
    """Hard exclusions for Presentación lists (not net-new gate)."""
    em = normalize_export_email(email) or ""
    if not em:
        return True, "invalid_email"
    if em in cyberday_sent:
        return True, "cyberday_47"
    if em in excl.bounced_emails or em in excl.suppressed_emails:
        return True, "bounced_suppressed"
    dom = em.rsplit("@", 1)[-1].lower()
    if dom in gate_ctx.blocked_domains:
        return True, "internal_domain"
    if em in gate_ctx.suppressed_norms:
        return True, "suppression"
    if email_domain_under_operator_domain_suppression(
        dom, gate_ctx.suppressed_contact_domains
    ):
        return True, "domain_suppression"
    if gate_ctx.supplier_domains and is_supplier_email_domain(
        em, gate_ctx.supplier_domains
    ):
        return True, "supplier_domain"
    if dom in excl.supplier_domains:
        return True, "supplier_domain_csv"
    if marketing_outreach_noise_email(em, strict_contact_graph=True):
        return True, "noise_email"
    if marketing_outreach_noise_organization_guess(organization):
        return True, "noise_organization"
    if dom in excl.bounced_domains:
        return True, "bounced_domain"
    if dom in _PERSONAL_EMAIL_DOMAINS:
        return True, "personal_email"
    return False, ""


def _score_send_now_candidate(
    *,
    sent_count: int,
    domain: str,
    subject: str,
    recommended_status: str,
    previous_buyer: bool,
) -> float:
    score = 0.0
    subj = subject or ""
    if _PRESENTATION_SUBJECT_RE.search(subj):
        score += 40.0
    if "presentación" in subj.lower() or "presentacion" in subj.lower():
        score += 15.0
    if domain.endswith(".cl"):
        score += 20.0
    score += min(sent_count * 3, 15)
    if recommended_status == "follow_up_candidate":
        score += 30.0
    if previous_buyer:
        score += 35.0
    if "cotiz" in subj.lower():
        score += 10.0
    return score


def _load_previous_buyer_emails(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    out: dict[str, dict[str, str]] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            em = normalize_export_email(row.get("email") or "") or ""
            if em:
                out[em] = row
    return out


def _load_contacted_universe_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _gather_hold_seed_from_warm_snapshot(path: Path) -> list[PresentacionReviewRow]:
    """Known active-case contacts from warm_cases_review_snapshot.csv (read-only)."""
    if not path.is_file():
        return []
    out: list[PresentacionReviewRow] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            blob = " ".join(
                row.get(k) or ""
                for k in ("subject", "sender_preview", "contact_email", "account_name")
            )
            for em in emails_in(blob):
                norm = normalize_export_email(em) or ""
                if not norm or norm in seen:
                    continue
                dom = domain_of(norm) or ""
                hold, reason = is_presentacion_hold_active_case(
                    norm,
                    organization=row.get("account_name") or blob,
                    domain=dom,
                )
                if not hold:
                    continue
                seen.add(norm)
                org = row.get("account_name") or dom
                subj, body = template_presentacion_send_now_es(
                    contact_name="",
                    organization=org,
                    product_angle=product_angle(),
                    history_note=f"Caso activo en bandeja tibia — {reason}",
                )
                out.append(
                    PresentacionReviewRow(
                        email=norm,
                        organization=org,
                        contact_name="",
                        bucket=BUCKET_HOLD_ACTIVE,
                        reason_for_inclusion=reason,
                        product_angle=product_angle(),
                        history_note=row.get("subject") or "",
                        suggested_subject=subj,
                        suggested_message=body,
                        recommended_action=ACTION_HOLD_ACTIVE,
                        priority_score=0.0,
                        exclusion_flags="",
                    )
                )
    return out


def _load_prospect_review_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _gather_send_now_rows(
    contacted_rows: list[dict[str, str]],
    previous_buyers: dict[str, dict[str, str]],
    *,
    gate_ctx: Any,
    excl: Any,
    cyberday_sent: frozenset[str],
) -> tuple[list[PresentacionReviewRow], list[PresentacionReviewRow]]:
    """Return (send_now, hold_redirect) from Gmail leftovers / no-response."""
    send_now: list[PresentacionReviewRow] = []
    hold_redirect: list[PresentacionReviewRow] = []
    seen: set[str] = set()

    def consider(
        email: str,
        organization: str,
        contact_name: str,
        *,
        reason: str,
        history: str,
        product: str,
        sent_count: int,
        subject: str,
        status: str,
        previous_buyer: bool,
        source: str,
    ) -> None:
        em = normalize_export_email(email) or ""
        if not em or em in seen:
            return
        seen.add(em)
        dom = domain_of(em) or ""
        blocked, flag = check_presentacion_hard_block(
            em, organization, gate_ctx=gate_ctx, excl=excl, cyberday_sent=cyberday_sent
        )
        if blocked:
            return
        hold, hold_reason = is_presentacion_hold_active_case(
            em, organization=organization, domain=dom
        )
        if hold:
            subj, body = template_presentacion_send_now_es(
                contact_name=contact_name,
                organization=organization,
                product_angle=product,
                history_note=history,
            )
            hold_redirect.append(
                PresentacionReviewRow(
                    email=em,
                    organization=organization,
                    contact_name=contact_name,
                    bucket=BUCKET_HOLD_ACTIVE,
                    reason_for_inclusion=hold_reason,
                    product_angle=product,
                    history_note=history or f"fuente: {source}",
                    suggested_subject=subj,
                    suggested_message=body,
                    recommended_action=ACTION_HOLD_ACTIVE,
                    priority_score=0.0,
                    exclusion_flags=flag if blocked else "",
                )
            )
            return
        if not dom.endswith(".cl"):
            return
        if _CYBERDAY_SUBJECT_RE.search(subject or ""):
            return
        if not (
            _PRESENTATION_SUBJECT_RE.search(subject or "")
            or previous_buyer
            or status == "follow_up_candidate"
        ):
            return
        score = _score_send_now_candidate(
            sent_count=sent_count,
            domain=dom,
            subject=subject,
            recommended_status=status,
            previous_buyer=previous_buyer,
        )
        subj, body = template_presentacion_send_now_es(
            contact_name=contact_name,
            organization=organization,
            product_angle=product,
            history_note=history,
        )
        send_now.append(
            PresentacionReviewRow(
                email=em,
                organization=organization,
                contact_name=contact_name,
                bucket=BUCKET_SEND_NOW,
                reason_for_inclusion=reason,
                product_angle=product,
                history_note=history,
                suggested_subject=subj,
                suggested_message=body,
                recommended_action=ACTION_SEND_NOW_REVIEW,
                priority_score=score,
                exclusion_flags="",
            )
        )

    for row in contacted_rows:
        status = (row.get("recommended_status") or "").strip()
        if status in _BLOCKED_STATUSES:
            continue
        sent_n = int(row.get("sent_count") or 0)
        recv_n = int(row.get("received_count") or 0)
        if sent_n < 1 or recv_n > 0:
            continue
        if (row.get("bounced_bool") or "").lower() == "true":
            continue
        if (row.get("suppressed_bool") or "").lower() == "true":
            continue
        em = row.get("normalized_email") or ""
        pb_row = previous_buyers.get(normalize_export_email(em) or "")
        consider(
            em,
            row.get("organization_name") or "",
            row.get("display_name") or "",
            reason="contacto previo sin respuesta (Gmail Enviados)",
            history=(
                f"Último asunto: {row.get('latest_subject_safe') or '—'}; "
                f"envíos={sent_n}; estado={status}"
            ),
            product=row.get("product_interest_guess")
            or "Equipos e insumos para laboratorio en Chile.",
            sent_count=sent_n,
            subject=row.get("latest_subject_safe") or "",
            status=status,
            previous_buyer=bool(pb_row),
            source="contacted_universe",
        )

    for em, pb in previous_buyers.items():
        if em in seen:
            continue
        consider(
            em,
            pb.get("organization") or "",
            pb.get("contact_name") or "",
            reason=pb.get("reason") or "comprador/respondedor histórico",
            history=f"Último contacto: {pb.get('latest_contact_date') or '—'}",
            product=product_angle(purchase_count=1),
            sent_count=1,
            subject="",
            status="previous_buyer",
            previous_buyer=True,
            source="previous_buyers_review",
        )

    send_now.sort(key=lambda r: (-r.priority_score, r.email))
    return send_now, hold_redirect


def _gather_same_domain_rows(
    prospect_rows: list[dict[str, str]],
    *,
    gate_ctx: Any,
    excl: Any,
    cyberday_sent: frozenset[str],
) -> list[PresentacionReviewRow]:
    out: list[PresentacionReviewRow] = []
    for row in prospect_rows:
        if (row.get("classification") or "") != "same_domain_contacted_review":
            continue
        em = normalize_export_email(row.get("email") or "") or ""
        if not em:
            continue
        org = row.get("organization_name") or ""
        dom = row.get("domain") or domain_of(em) or ""
        blocked, flags = check_presentacion_hard_block(
            em, org, gate_ctx=gate_ctx, excl=excl, cyberday_sent=cyberday_sent
        )
        if blocked:
            continue
        hold, hold_reason = is_presentacion_hold_active_case(
            em, organization=org, domain=dom
        )
        if hold:
            continue
        history = template_same_domain_review_note(
            organization=org,
            domain=dom,
            history_note=row.get("block_or_review_reason") or "dominio ya contactado",
        )
        angle = row.get("product_angle") or row.get("spanish_message_angle") or product_angle()
        subj, body = template_presentacion_send_now_es(
            contact_name=row.get("contact_name") or "",
            organization=org,
            product_angle=angle,
            history_note=history,
        )
        out.append(
            PresentacionReviewRow(
                email=em,
                organization=org,
                contact_name=row.get("contact_name") or "",
                bucket=BUCKET_SAME_DOMAIN,
                reason_for_inclusion=(
                    "Prospecto Phase 10D — mismo dominio ya contactado; revisar historial"
                ),
                product_angle=angle,
                history_note=history,
                suggested_subject=subj,
                suggested_message=body,
                recommended_action=ACTION_REVIEW_HISTORY_ONLY,
                priority_score=float(row.get("final_score") or row.get("input_priority_score") or 0),
                exclusion_flags=flags,
            )
        )
    out.sort(key=lambda r: (-r.priority_score, r.email))
    return out


def _gather_missing_email_rows(prospect_rows: list[dict[str, str]]) -> list[PresentacionReviewRow]:
    out: list[PresentacionReviewRow] = []
    for row in prospect_rows:
        if (row.get("classification") or "") != "research_only_contact_needed":
            continue
        org = row.get("organization_name") or ""
        angle = row.get("product_angle") or row.get("spanish_message_angle") or product_angle()
        out.append(
            PresentacionReviewRow(
                email="",
                organization=org,
                contact_name=row.get("contact_name") or "",
                bucket=BUCKET_MISSING_EMAIL,
                reason_for_inclusion=row.get("block_or_review_reason")
                or "falta email directo del responsable",
                product_angle=angle,
                history_note=row.get("evidence_note") or row.get("evidence_url") or "",
                suggested_subject=f"OrigenLab — equipamiento de laboratorio ({org})",
                suggested_message="",
                recommended_action=ACTION_RESEARCH_CONTACT,
                priority_score=float(row.get("final_score") or 0),
                exclusion_flags=row.get("risk_flags") or "",
            )
        )
    out.sort(key=lambda r: (-r.priority_score, r.organization))
    return out


def build_presentacion_origenlab_review(
    conn: sqlite3.Connection,
    *,
    out_dir: Path,
    gmail_user: str,
    sent_folders: tuple[str, ...],
    cyberday_log: Path | None = None,
    do_not_repeat_csv: Path | None = None,
) -> PresentacionBuildResult:
    """Build all Presentación review buckets (read-only)."""
    out_dir = out_dir.resolve()
    cyber_path = cyberday_log or (out_dir / "cyber_production_send_log.json")
    cyberday_sent = load_cyberday_sent_emails(cyber_path)
    excl = load_exclusion_lists(out_dir)
    universe_ctx, _, _ = build_contacted_universe_context(
        conn,
        gmail_user=gmail_user,
        sent_folders=sent_folders,
        do_not_repeat_csv=do_not_repeat_csv,
    )
    gate_ctx = universe_ctx.gate

    contacted_rows = _load_contacted_universe_rows(out_dir / "contacted_universe_contacts.csv")
    prospect_rows = _load_prospect_review_rows(out_dir / "new_customer_targets_review.csv")
    previous_buyers = _load_previous_buyer_emails(
        out_dir / "cyber_expanded_previous_buyers_review.csv"
    )

    send_now, hold_from_send = _gather_send_now_rows(
        contacted_rows,
        previous_buyers,
        gate_ctx=gate_ctx,
        excl=excl,
        cyberday_sent=cyberday_sent,
    )
    same_domain = _gather_same_domain_rows(
        prospect_rows,
        gate_ctx=gate_ctx,
        excl=excl,
        cyberday_sent=cyberday_sent,
    )
    missing = _gather_missing_email_rows(prospect_rows)

    hold_active = list(hold_from_send)
    hold_emails = {r.email for r in hold_active}
    for seed in _gather_hold_seed_from_warm_snapshot(out_dir / "warm_cases_review_snapshot.csv"):
        if seed.email not in hold_emails:
            hold_active.append(seed)
            hold_emails.add(seed.email)

    send_now = [r for r in send_now if r.email not in hold_emails]
    same_domain = [r for r in same_domain if r.email not in hold_emails]

    top_n = min(25, len(send_now))
    messages_md = render_messages_markdown(send_now[:top_n])

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "campaign_slug": PRESENTACION_CAMPAIGN_SLUG,
        "mode": "read_only_no_sends",
        "cyberday_excluded_count": len(cyberday_sent),
        "bounced_suppressed_excluded": len(excl.bounced_emails) + len(excl.suppressed_emails),
        "counts": {
            "send_now_review": len(send_now),
            "same_domain_review": len(same_domain),
            "hold_active_cases": len(hold_active),
            "missing_email_research": len(missing),
        },
        "top_recommended": [
            {"email": r.email, "organization": r.organization, "priority_score": r.priority_score}
            for r in send_now[:10]
        ],
    }

    return PresentacionBuildResult(
        send_now=send_now,
        same_domain=same_domain,
        hold_active=hold_active,
        missing_email=missing,
        summary=summary,
        messages_markdown=messages_md,
    )


def _write_csv(path: Path, rows: list[PresentacionReviewRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(REVIEW_CSV_FIELDS))
        w.writeheader()
        for row in rows:
            w.writerow(row.to_csv_dict())


def write_presentacion_outputs(result: PresentacionBuildResult, out_dir: Path) -> dict[str, Path]:
    out_dir = out_dir.resolve()
    paths = {
        "send_now": out_dir / OUTPUT_SEND_NOW,
        "same_domain": out_dir / OUTPUT_SAME_DOMAIN,
        "hold": out_dir / OUTPUT_HOLD,
        "missing": out_dir / OUTPUT_MISSING,
        "messages": out_dir / OUTPUT_MESSAGES,
        "summary_json": out_dir / "presentacion_origenlab_summary.json",
    }
    _write_csv(paths["send_now"], result.send_now)
    _write_csv(paths["same_domain"], result.same_domain)
    _write_csv(paths["hold"], result.hold_active)
    _write_csv(paths["missing"], result.missing_email)
    paths["messages"].write_text(result.messages_markdown, encoding="utf-8")
    paths["summary_json"].write_text(
        json.dumps(result.summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return paths
