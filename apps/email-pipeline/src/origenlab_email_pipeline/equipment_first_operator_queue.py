"""Canonical equipment-first operator queue (tenders + safety cross-check)."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.equipment_first_licitacion_queue import (
    STOP_CONSUMABLES_OUTREACH_CODES,
)

PRIORITY_RANK: dict[str, int] = {
    "1057898-51-LP26": 1,
    "2171-3-LE26": 2,
    "1057501-252-LP26": 3,
    "1057536-33-LP26": 4,
    "1057500-25-L126": 5,
    "5586-53-LE26": 6,
    "1171142-61-LP26": 7,
    "1057545-48-LP26": 8,
    "5067-29-L126": 9,
}

SUPPLIER_HINTS: dict[str, tuple[str, str]] = {
    "centrifuge": (
        "Ortoalresa / refrigerated & clinical centrifuge line",
        "Request UMT/clinical centrifuge model match + Chile list price lead time",
    ),
    "lab_ultrasonic_processor": (
        "Bandelin (Matachana service) / Hielscher if processor purchase",
        "Confirm model (DT 1050 CH, MC 1001, etc.) and maintenance vs supply scope",
    ),
    "balance": (
        "Kern / Sartorius / Mettler Toledo distributor",
        "Analytical 0.1 mg specs or annual calibration service quote",
    ),
    "incubator": (
        "Memmert or OEM for refrigerated incubator / lab oven",
        "Refrigerated incubator + installation specs; or maintenance SLA by brand",
    ),
}

OPERATOR_FIELDS = [
    "priority_rank",
    "codigo_licitacion",
    "buyer",
    "region",
    "close_date",
    "equipment_category",
    "item_description",
    "next_action",
    "contact_status",
    "safe_channel",
    "supplier_needed",
    "supplier_contact",
    "gmail_prior_thread",
    "outreach_state",
    "operator_note",
]


def _load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _gmail_touch(conn: sqlite3.Connection, *, codigo: str, buyer: str) -> str:
    needle_buyer = buyer[:35].lower()
    rows = conn.execute(
        """
        SELECT id, date_iso, subject FROM emails
        WHERE source_file LIKE 'gmail:contacto@origenlab.cl/%'
          AND date_iso >= date('now', '-365 days')
          AND (
            lower(subject) LIKE ?
            OR lower(body_text_clean) LIKE ?
            OR lower(body_text_clean) LIKE ?
          )
        ORDER BY date_iso DESC
        LIMIT 3
        """,
        (f"%{codigo.lower()}%", f"%{needle_buyer}%", f"%{codigo.lower()}%"),
    ).fetchall()
    if not rows:
        return "none"
    return "; ".join(f"{r[0]}:{str(r[1])[:10]}:{(r[2] or '')[:50]}" for r in rows)


def classify_safe_channel(
    *,
    next_action: str,
    codigo: str,
    title: str,
) -> str:
    if next_action == "contact_after_close":
        return "contact_after_close"
    if next_action in ("account_intelligence_only", "skip_consumables"):
        return "account_intelligence_only"
    if next_action == "needs_supplier_quote":
        return "supplier_quote_request"
    # quote_now
    upper = f"{codigo} {title}".upper()
    if "-LP" in upper or "LICITACIÓN PÚBLICA" in upper or "-LE" in codigo:
        return "mercado_publico_bid"
    return "mercado_publico_question"


def _contact_status_for_tender(
    conn: sqlite3.Connection,
    *,
    buyer: str,
    crosscheck_rows: list[dict[str, str]],
) -> tuple[str, str, str]:
    """Return contact_status, outreach_state aggregate, operator email note."""
    emails: list[str] = []
    for row in crosscheck_rows:
        if (row.get("account") or "").strip().upper() != buyer.strip().upper():
            continue
        raw = row.get("contact_email") or ""
        if raw and "@" in raw:
            emails.extend(e.strip() for e in raw.split(";") if "@" in e)
    if not emails:
        return "no_verified_buyer_email", "", "Do not invent email — use Mercado Público channel"
    em = emails[0].lower()
    st = conn.execute(
        "SELECT state FROM outreach_contact_state WHERE contact_email_norm = ?",
        (em,),
    ).fetchone()
    state = st[0] if st else ""
    return f"email_on_file:{em}", state, "Verify before any direct email"


def build_operator_rows(
    equipment_rows: list[dict[str, str]],
    *,
    db_path: Path,
    crosscheck_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    conn = sqlite3.connect(db_path)
    out: list[dict[str, str]] = []

    for row in equipment_rows:
        codigo = (row.get("codigo_licitacion") or "").strip()
        if codigo in STOP_CONSUMABLES_OUTREACH_CODES:
            continue
        buyer = row.get("buyer", "")
        next_action = row.get("next_action", "")
        category = row.get("equipment_category", "")
        supplier_needed, supplier_note = SUPPLIER_HINTS.get(
            category, ("Category-specific OEM/distributor", "Confirm specs from bases")
        )
        needs_supplier = "yes" if next_action in ("quote_now", "needs_supplier_quote") else "no"
        contact_status, ocs_state, contact_note = _contact_status_for_tender(
            conn, buyer=buyer, crosscheck_rows=crosscheck_rows
        )
        gmail = _gmail_touch(conn, codigo=codigo, buyer=buyer)
        safe = classify_safe_channel(
            next_action=next_action, codigo=codigo, title=row.get("title", "")
        )
        rank = PRIORITY_RANK.get(codigo, 99)
        notes = [
            row.get("reason", ""),
            contact_note,
            f"fit_score={row.get('fit_score', '')}",
        ]
        if row.get("reason", "").find("consumable") >= 0:
            notes.append("mixed_lines_consumables_present")
        out.append(
            {
                "priority_rank": str(rank),
                "codigo_licitacion": codigo,
                "buyer": buyer,
                "region": row.get("region", ""),
                "close_date": row.get("close_date", ""),
                "equipment_category": category,
                "item_description": (row.get("item_description") or "")[:500],
                "next_action": next_action,
                "contact_status": contact_status,
                "safe_channel": safe,
                "supplier_needed": needs_supplier,
                "supplier_contact": supplier_needed if needs_supplier == "yes" else "",
                "gmail_prior_thread": gmail,
                "outreach_state": ocs_state or "n/a_tender_account",
                "operator_note": " | ".join(n for n in notes if n)[:400],
            }
        )

    conn.close()
    out.sort(key=lambda r: int(r["priority_rank"]))
    return out


def write_operator_csv(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OPERATOR_FIELDS)
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in OPERATOR_FIELDS} for r in rows)


def aligned_ab_queue_rows(operator_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Equipment-only replacement for buyer_opportunity_ab_queue."""
    rows: list[dict[str, str]] = []
    for r in operator_rows:
        rows.append(
            {
                "account": r["buyer"],
                "contact_name": "",
                "contact_email": "",
                "subject": r["item_description"][:200],
                "latest_date": r["close_date"],
                "product_line": r["equipment_category"],
                "warm_signal": r["item_description"][:250],
                "current_status": "needs_manual_contact_research",
                "recommended_next_action": (
                    f"Equipment-first: {r['next_action']} via {r['safe_channel']}"
                ),
                "priority": "high" if int(r["priority_rank"]) <= 3 else "medium",
                "owner_note": r["operator_note"],
                "gmail_search_terms": r["codigo_licitacion"],
                "tender_codes": r["codigo_licitacion"],
                "source_type": "equipment_first_operator",
                "ab_queue": "B",
                "suppression_flags": "",
            }
        )
    return rows


def write_ab_queue_csv(rows: list[dict[str, str]], path: Path) -> None:
    fields = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def write_operator_markdown(
    rows: list[dict[str, str]],
    path: Path,
    *,
    date_suffix: str,
) -> None:
    top3 = rows[:3]
    lines = [
        f"# Equipment-first operator queue — {date_suffix}",
        "",
        "**Canonical file:** `equipment_first_operator_queue_{date_suffix}.csv`",
        "",
        "Aligned with equipment-first filter from `Licitacion_Publicada.csv`.",
        "No emails sent. No Gmail drafts. No invented buyer contacts.",
        "",
        "---",
        "",
        "## Executive summary",
        "",
        f"- **{len(rows)}** active equipment-first public tenders in queue.",
        "- Focus: centrifuges (Ñuble, La Ligua, Sótero maintenance), lab ultrasonic washers,",
        "  balance service, lab incubators.",
        f"- **Excluded consumables tenders:** {', '.join(sorted(STOP_CONSUMABLES_OUTREACH_CODES))}.",
        "- Private-lab Queue A targets removed from `buyer_opportunity_ab_queue` — not equipment-first.",
        "",
        "---",
        "",
        "## Top 3 actions for today",
        "",
    ]
    for i, r in enumerate(top3, 1):
        lines.append(
            f"{i}. **`{r['codigo_licitacion']}`** — {r['buyer'][:50]} — "
            f"{r['equipment_category']} — **{r['next_action']}** "
            f"(channel: `{r['safe_channel']}`)"
        )
    lines.extend(
        [
            "",
            "---",
            "",
            "## What to ask suppliers",
            "",
            "| Category | Ask |",
            "|----------|-----|",
            "| **Centrifuge (UMT / clinical)** | Model match to bases, refrigerated vs bench, rotor package, warranty, Chile stock/lead time, MP bid format |",
            "| **Centrifuge maintenance** | OEM coverage (Abbott, Thermo, Clay Adams, Kubota), SLA, parts vs full service |",
            "| **Ultrasonic washer** | Bandelin/Matachana model, new unit vs maintenance contract scope |",
            "| **Balance** | Analytical 0.1 mg spec (USACH line) or annual calibration/certification service |",
            "| **Incubator** | Refrigerated lab incubator specs (U. Frontera) or maintenance by brand (SS Los Ríos) |",
            "",
            "---",
            "",
            "## What not to pursue",
            "",
            "- **Consumables/reagents tenders:** `1497-6-LE26`, `1497-3-LE26`, `1657-8-LE26`, `1511-30-LP26`",
            "  (not OrigenLab equipment-first unless confirmed capital-equipment line).",
            "- **Cold email** to hospital/SEREMI without Mercado Público path — no verified buyer emails in import.",
            "- **Private-lab blast** from prior buyer A/B queue (CERTILAB, LabCo, etc.) — separate track, not this file.",
            "- **May 2026 centrifuge manual sends** (Red Salud, UFRO, Sanderson) — already on DNR.",
            "",
            "---",
            "",
            "## Tender channel safety notes",
            "",
            "| `safe_channel` | Use when |",
            "|----------------|----------|",
            "| `mercado_publico_bid` | Active LP/LE equipment purchase with bases reviewed (`quote_now`) |",
            "| `mercado_publico_question` | Clarification only — specs, compatibilidad, plazos |",
            "| `supplier_quote_request` | Need distributor/OEM quote **before** bidding or maintenance proposal |",
            "| `contact_after_close` | Cierre passed — CRM/account only (`5067-29-L126` USACH balance line) |",
            "| `account_intelligence_only` | No MP action; monitor future equipment lines |",
            "",
            "**Rules:** Preserve `codigo_licitacion` on all MP interactions. Do not mutate Gmail.",
            "Register supplier quotes in CRM linked to tender code.",
            "",
            "---",
            "",
            "## Full queue",
            "",
            "| Rank | Código | Buyer | Category | Close | Next | Channel |",
            "|------|--------|-------|----------|-------|------|---------|",
        ]
    )
    for r in rows:
        lines.append(
            f"| {r['priority_rank']} | `{r['codigo_licitacion']}` | {r['buyer'][:40]} | "
            f"{r['equipment_category']} | {r['close_date'][:16]} | {r['next_action']} | "
            f"{r['safe_channel']} |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def build_all(
    *,
    reports_dir: Path,
    db_path: Path,
    date_suffix: str = "20260518",
) -> dict[str, Any]:
    equip_path = reports_dir / f"equipment_first_opportunity_queue_{date_suffix}.csv"
    cross_path = reports_dir / f"buyer_opportunity_crosscheck_{date_suffix}.csv"
    equipment_rows = _load_csv(equip_path)
    cross_rows = _load_csv(cross_path)

    operator_rows = build_operator_rows(
        equipment_rows, db_path=db_path, crosscheck_rows=cross_rows
    )
    op_csv = reports_dir / f"equipment_first_operator_queue_{date_suffix}.csv"
    write_operator_csv(operator_rows, op_csv)

    ab_rows = aligned_ab_queue_rows(operator_rows)
    ab_path = reports_dir / f"buyer_opportunity_ab_queue_{date_suffix}.csv"
    write_ab_queue_csv(ab_rows, ab_path)

    md_path = reports_dir / f"equipment_first_operator_queue_{date_suffix}.md"
    write_operator_markdown(operator_rows, md_path, date_suffix=date_suffix)

    return {
        "operator_rows": len(operator_rows),
        "ab_rows": len(ab_rows),
        "operator_csv": str(op_csv),
        "ab_csv": str(ab_path),
        "markdown": str(md_path),
    }
