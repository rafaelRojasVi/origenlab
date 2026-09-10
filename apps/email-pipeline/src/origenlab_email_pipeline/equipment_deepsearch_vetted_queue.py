"""Equipment-first gate for deep-search opportunity CSV (read-only safety checks)."""

from __future__ import annotations

import csv
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.business_mart import emails_in
from origenlab_email_pipeline.equipment_first_licitacion_queue import (
    CLINICAL_ULTRASOUND_RE,
    EXCLUDE_CONSUMABLES_RE,
    NEONATAL_INCUBATOR_RE,
    STOP_CONSUMABLES_OUTREACH_CODES,
    consumables_exclusion_reason,
    detect_equipment_categories,
    line_blob,
)
from origenlab_email_pipeline.equipment_first_operator_queue import classify_safe_channel
from origenlab_email_pipeline.outreach_contact_state import ensure_outreach_contact_state_table

VETTED_CLASSIFICATIONS: frozenset[str] = frozenset(
    {
        "quote_now",
        "supplier_quote_request",
        "mercado_publico_question",
        "mercado_publico_bid",
        "contact_after_close",
        "account_intelligence_only",
        "skip_noise",
        "duplicate_or_contacted",
    }
)

BLOCKING_OUTREACH_STATES = frozenset({"contacted", "replied", "snoozed"})

_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "codigo_licitacion": (
        "codigo_licitacion",
        "codigo_licitacion_or_source_id",
        "tender_code",
        "codigo",
        "licitacion",
        "tender_codes",
    ),
    "buyer": ("buyer", "account", "institution", "organismo"),
    "contact_email": ("contact_email", "email", "mailbox"),
    "title": ("title", "subject", "tender_title"),
    "description": (
        "description",
        "opportunity_description",
        "item_description",
        "warm_signal",
        "notes",
        "product_line",
    ),
    "source_type": ("source_type", "record_type", "lane", "kind"),
    "equipment_category": ("equipment_category", "product_line", "category"),
    "next_action_hint": (
        "recommended_action",
        "next_action",
        "recommended_next_action",
        "safe_channel",
    ),
}

_USACH_BUYER_RE = re.compile(
    r"universidad de santiago|\busach\b|u\.?\s*s\.?\s*a\.?\s*c\.?\s*h",
    re.I,
)
_ISP_BUYER_RE = re.compile(
    r"instituto de salud p[uú]blica|\bispch\b|salud p[uú]blica de chile",
    re.I,
)

ISP_GMAIL_CAUTION_NOTE = (
    "ISP: high-fit equipment but NOT clean for cold outreach. Prior Gmail contact "
    "with 3 institutional inboxes (2 presentations sent, 1 bounced); see the contact "
    "history in the CRM rather than here — this repository is public and does not "
    "store third-party contact addresses. Award/adjudication watch only; not send-ready."
)


def _is_usach_account(buyer: str) -> bool:
    return bool(_USACH_BUYER_RE.search(buyer or ""))


def _is_isp_account(buyer: str) -> bool:
    return bool(_ISP_BUYER_RE.search(buyer or ""))


def _source_type_skip(source_type: str) -> str | None:
    st = (source_type or "").lower()
    if "noise" in st or "downranked" in st:
        return f"source_type:{source_type}"
    return None


def normalize_recommended_action(hint: str) -> str:
    h = (hint or "").strip().lower()
    mapping = {
        "ask_supplier_quote": "supplier_quote_request",
        "needs_supplier_quote": "supplier_quote_request",
        "supplier_quote_request": "supplier_quote_request",
        "contact_after_close": "contact_after_close",
        "account_intelligence_only": "account_intelligence_only",
        "skip_noise": "skip_noise",
        "quote_now": "quote_now",
        "mercado_publico_bid": "mercado_publico_bid",
        "mercado_publico_question": "mercado_publico_question",
    }
    return mapping.get(h, hint if hint in VETTED_CLASSIFICATIONS else "")


def _pick(row: dict[str, str], key: str) -> str:
    for alias in _COLUMN_ALIASES.get(key, (key,)):
        v = (row.get(alias) or "").strip()
        if v:
            return v
    return ""


def _norm_email(raw: str) -> str:
    found = emails_in(raw or "")
    return found[0].lower() if found else ""


def load_email_set_from_csv(path: Path, *column_names: str) -> set[str]:
    if not path.is_file():
        return set()
    out: set[str] = set()
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            for col in column_names:
                raw = row.get(col) or ""
                for em in emails_in(raw):
                    out.add(em.lower())
                if "@" in raw and not emails_in(raw):
                    out.add(raw.strip().lower())
    return out


def load_operator_queue_index(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return {(r.get("codigo_licitacion") or "").strip(): r for r in rows if (r.get("codigo_licitacion") or "").strip()}


def _is_public_tender(codigo: str, source_type: str) -> bool:
    st = source_type.lower()
    if st.startswith("mercado_publico") or st.startswith("compra_agil"):
        return True
    if st in ("public_tender", "tender", "licitacion", "equipment_first_operator"):
        return True
    if codigo and re.search(r"-\d+-L[EP]|SE\d+$", codigo, re.I):
        return True
    return bool(codigo) and not st.startswith("private")


def _noise_reason(blob: str, codigo: str) -> str | None:
    if codigo in STOP_CONSUMABLES_OUTREACH_CODES:
        return "stop_consumables_tender_code"
    reason = consumables_exclusion_reason(blob)
    if reason and not detect_equipment_categories(blob):
        return f"consumables:{reason}"
    if CLINICAL_ULTRASOUND_RE.search(blob) and not re.search(
        r"sonicador|sonificador|lavadora\s+ultras", blob, re.I
    ):
        return "clinical_ultrasound_noise"
    if NEONATAL_INCUBATOR_RE.search(blob):
        return "neonatal_incubator_noise"
    if not detect_equipment_categories(blob):
        return "not_equipment_first"
    return None


def _dnr_reason(email: str, *, dnr: set[str], contacted: set[str], marketing: set[str]) -> str | None:
    if not email:
        return None
    if email in dnr:
        return "do_not_repeat_master"
    if email in contacted:
        return "outreach_contacted_all"
    if email in marketing:
        return "all_known_marketing_contacts"
    return None


def _sqlite_contact_blocks(
    conn: sqlite3.Connection | None,
    email: str,
) -> tuple[str | None, str | None]:
    """Return (gmail_sent_block, outreach_state_block)."""
    if not email or conn is None:
        return None, None
    ensure_outreach_contact_state_table(conn)
    row = conn.execute(
        "SELECT state FROM outreach_contact_state WHERE contact_email_norm = ?",
        (email,),
    ).fetchone()
    state = row[0] if row else ""
    if state in BLOCKING_OUTREACH_STATES:
        return None, f"outreach_state:{state}"
    sent = None
    try:
        sent = conn.execute(
            """
            SELECT id FROM emails
            WHERE lower(source_file) LIKE 'gmail:contacto@origenlab.cl/%'
              AND (
                lower(COALESCE(recipients, '')) LIKE ?
                OR lower(COALESCE(body_text_clean, '')) LIKE ?
              )
            LIMIT 1
            """,
            (f"%{email}%", f"%{email}%"),
        ).fetchone()
    except sqlite3.OperationalError:
        try:
            sent = conn.execute(
                """
                SELECT id FROM emails
                WHERE lower(source_file) LIKE 'gmail:contacto@origenlab.cl/%'
                  AND lower(COALESCE(body_text_clean, '')) LIKE ?
                LIMIT 1
                """,
                (f"%{email}%",),
            ).fetchone()
        except sqlite3.OperationalError:
            sent = None
    if sent:
        return f"gmail_sent:{sent[0]}", None
    return None, None


def map_operator_action_to_vetted(
    *,
    next_action: str,
    safe_channel: str,
) -> str:
    if next_action == "quote_now":
        return safe_channel if safe_channel in VETTED_CLASSIFICATIONS else "mercado_publico_bid"
    if next_action == "needs_supplier_quote":
        return "supplier_quote_request"
    if next_action == "contact_after_close":
        return "contact_after_close"
    if next_action in ("account_intelligence_only", "skip_consumables"):
        return "account_intelligence_only"
    if safe_channel in VETTED_CLASSIFICATIONS:
        return safe_channel
    return "account_intelligence_only"


@dataclass
class VettedRow:
    row: dict[str, str]
    vetted_classification: str
    gate_reason: str
    equipment_categories: str
    dnr_flags: str
    operator_note: str


def vet_deepsearch_rows(
    input_rows: list[dict[str, str]],
    *,
    operator_by_code: dict[str, dict[str, str]],
    dnr_emails: set[str],
    contacted_emails: set[str],
    marketing_emails: set[str],
    conn: sqlite3.Connection | None,
) -> list[VettedRow]:
    out: list[VettedRow] = []
    for raw in input_rows:
        codigo = _pick(raw, "codigo_licitacion")
        buyer = _pick(raw, "buyer")
        email = _norm_email(_pick(raw, "contact_email"))
        source_type = _pick(raw, "source_type")
        st_skip = _source_type_skip(source_type)
        if st_skip:
            out.append(
                VettedRow(
                    row=raw,
                    vetted_classification="skip_noise",
                    gate_reason=st_skip,
                    equipment_categories="",
                    dnr_flags="",
                    operator_note=raw.get("risk_notes", "")[:400],
                )
            )
            continue

        if _is_isp_account(buyer):
            blob_early = line_blob(
                {
                    "title": _pick(raw, "title"),
                    "descripcion": _pick(raw, "description"),
                    "line_description": _pick(raw, "description"),
                    "producto": _pick(raw, "equipment_category"),
                    "nivel_1": "",
                    "nivel_2": "",
                    "nivel_3": "",
                }
            )
            cats = detect_equipment_categories(blob_early)
            hint = normalize_recommended_action(_pick(raw, "next_action_hint"))
            vetted = "contact_after_close"
            if hint == "account_intelligence_only":
                vetted = "account_intelligence_only"
            if vetted in ("quote_now", "mercado_publico_bid", "mercado_publico_question"):
                vetted = "contact_after_close"
            out.append(
                VettedRow(
                    row=raw,
                    vetted_classification=vetted,
                    gate_reason="isp_account_caution_prior_gmail",
                    equipment_categories=";".join(c[0] for c in cats),
                    dnr_flags="",
                    operator_note=ISP_GMAIL_CAUTION_NOTE[:400],
                )
            )
            continue

        if _is_usach_account(buyer):
            blob_early = line_blob(
                {
                    "title": _pick(raw, "title"),
                    "descripcion": _pick(raw, "description"),
                    "line_description": _pick(raw, "description"),
                    "producto": _pick(raw, "equipment_category"),
                    "nivel_1": "",
                    "nivel_2": "",
                    "nivel_3": "",
                }
            )
            cats = detect_equipment_categories(blob_early)
            hint = normalize_recommended_action(_pick(raw, "next_action_hint"))
            vetted = hint if hint in VETTED_CLASSIFICATIONS else "account_intelligence_only"
            if vetted in ("quote_now", "mercado_publico_bid"):
                vetted = "account_intelligence_only"
            out.append(
                VettedRow(
                    row=raw,
                    vetted_classification=vetted,
                    gate_reason="usach_account_caution_prior_sends_and_bounces",
                    equipment_categories=";".join(c[0] for c in cats),
                    dnr_flags="",
                    operator_note=(
                        "USACH: prior OrigenLab sends to multiple contacts; "
                        "contacto.vriic@usach.cl bounced — careful targeted follow-up only, not cold outreach"
                    )[:400],
                )
            )
            continue

        blob = line_blob(
            {
                "title": _pick(raw, "title"),
                "descripcion": _pick(raw, "description"),
                "line_description": _pick(raw, "description"),
                "producto": _pick(raw, "equipment_category"),
                "nivel_1": "",
                "nivel_2": "",
                "nivel_3": "",
            }
        )

        noise = _noise_reason(blob, codigo)
        if noise:
            out.append(
                VettedRow(
                    row=raw,
                    vetted_classification="skip_noise",
                    gate_reason=noise,
                    equipment_categories="",
                    dnr_flags="",
                    operator_note="Excluded by equipment-first noise filter",
                )
            )
            continue

        cats = detect_equipment_categories(blob)
        cat_str = ";".join(c[0] for c in cats)

        dnr_parts: list[str] = []
        r = _dnr_reason(email, dnr=dnr_emails, contacted=contacted_emails, marketing=marketing_emails)
        if r:
            dnr_parts.append(r)
        sent_b, state_b = _sqlite_contact_blocks(conn, email)
        if sent_b:
            dnr_parts.append(sent_b)
        if state_b:
            dnr_parts.append(state_b)

        if dnr_parts and email:
            out.append(
                VettedRow(
                    row=raw,
                    vetted_classification="duplicate_or_contacted",
                    gate_reason=";".join(dnr_parts),
                    equipment_categories=cat_str,
                    dnr_flags=";".join(dnr_parts),
                    operator_note="Private or known contact blocked — do not cold blast",
                )
            )
            continue

        op = operator_by_code.get(codigo, {})
        if op:
            na = op.get("next_action", "")
            sc = op.get("safe_channel", "")
            vetted = map_operator_action_to_vetted(next_action=na, safe_channel=sc)
            note = op.get("operator_note", "")
            if _is_public_tender(codigo, source_type) and (op.get("contact_status") or "").startswith(
                "no_verified"
            ):
                if vetted == "quote_now":
                    vetted = sc if sc in VETTED_CLASSIFICATIONS else "mercado_publico_bid"
                note = (note + " | public_tender_no_verified_buyer_email").strip(" |")
            out.append(
                VettedRow(
                    row=raw,
                    vetted_classification=vetted,
                    gate_reason=f"aligned_operator_queue:{na}",
                    equipment_categories=cat_str or op.get("equipment_category", ""),
                    dnr_flags="",
                    operator_note=note[:400],
                )
            )
            continue

        if _is_public_tender(codigo, source_type) or source_type.lower().startswith("mercado_publico"):
            if not codigo:
                vetted = "skip_noise"
                reason = "public_tender_missing_codigo"
            elif email:
                vetted = "skip_noise"
                reason = "public_tender_with_email_not_in_operator_queue_verify_channel"
            else:
                hint = normalize_recommended_action(_pick(raw, "next_action_hint"))
                if hint == "skip_noise":
                    vetted = "skip_noise"
                elif hint in VETTED_CLASSIFICATIONS:
                    vetted = hint
                    if vetted == "quote_now":
                        vetted = classify_safe_channel(
                            next_action="quote_now",
                            codigo=codigo,
                            title=blob[:120],
                        )
                else:
                    vetted = classify_safe_channel(
                        next_action="quote_now",
                        codigo=codigo,
                        title=blob[:120],
                    )
                reason = "public_tender_mp_channel_only"
            note = (raw.get("risk_notes") or "").strip() or (
                "No invented email — Mercado Público or supplier quote only; not send-ready without anti-repeat"
            )
            out.append(
                VettedRow(
                    row=raw,
                    vetted_classification=vetted,
                    gate_reason=reason,
                    equipment_categories=cat_str,
                    dnr_flags="",
                    operator_note=note[:400],
                )
            )
            continue

        if email:
            out.append(
                VettedRow(
                    row=raw,
                    vetted_classification="account_intelligence_only",
                    gate_reason="private_unverified_not_on_operator_queue",
                    equipment_categories=cat_str,
                    dnr_flags="",
                    operator_note="Verify contact and prior thread before any send",
                )
            )
            continue

        out.append(
            VettedRow(
                row=raw,
                vetted_classification="skip_noise",
                gate_reason="insufficient_evidence",
                equipment_categories=cat_str,
                dnr_flags="",
                operator_note="No tender code or contact — do not invent",
            )
        )
    return out


def vetted_rows_to_dicts(vetted: list[VettedRow]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for v in vetted:
        base = dict(v.row)
        base["vetted_classification"] = v.vetted_classification
        base["gate_reason"] = v.gate_reason
        base["equipment_categories"] = v.equipment_categories
        base["dnr_flags"] = v.dnr_flags
        base["vetted_operator_note"] = v.operator_note
        rows.append(base)
    return rows


def write_vetted_csv(rows: list[dict[str, str]], path: Path) -> None:
    if not rows:
        path.write_text("vetted_classification,gate_reason\n", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                fields.append(k)
    for extra in ("vetted_classification", "gate_reason", "equipment_categories", "dnr_flags", "vetted_operator_note"):
        if extra not in seen:
            fields.append(extra)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def write_vetted_markdown(
    rows: list[dict[str, str]],
    path: Path,
    *,
    date_suffix: str,
    input_name: str,
) -> None:
    by_class: dict[str, int] = {}
    for r in rows:
        c = r.get("vetted_classification", "")
        by_class[c] = by_class.get(c, 0) + 1
    lines = [
        f"# Equipment deep-search vetted queue — {date_suffix}",
        "",
        f"**Input:** `{input_name}`",
        "",
        "**Read-only gate.** No Gmail mutations. No invented contacts.",
        "",
        "## Summary by classification",
        "",
        "| Classification | Count |",
        "|----------------|------:|",
    ]
    for c in sorted(VETTED_CLASSIFICATIONS):
        if c in by_class:
            lines.append(f"| `{c}` | {by_class[c]} |")
    lines.extend(["", "## Rows", ""])
    for r in rows[:50]:
        codigo = r.get("codigo_licitacion") or r.get("tender_code") or r.get("codigo") or "—"
        lines.append(
            f"- **{codigo}** — `{r.get('vetted_classification')}` — {r.get('gate_reason', '')[:80]}"
        )
    if len(rows) > 50:
        lines.append(f"\n… and {len(rows) - 50} more (see CSV).")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_vetted_queue(
    *,
    input_path: Path,
    output_csv: Path,
    output_md: Path,
    operator_queue_path: Path,
    active_current: Path,
    active_root: Path,
    db_path: Path | None,
    date_suffix: str,
) -> dict[str, Any]:
    if not input_path.is_file():
        raise FileNotFoundError(
            f"Deep-search input not found: {input_path}\n"
            "Save equipment deep-search output as:\n"
            f"  {input_path.name}\n"
            "under reports/out/active/current/, then re-run this script."
        )

    with input_path.open(newline="", encoding="utf-8-sig") as f:
        input_rows = list(csv.DictReader(f))

    dnr = load_email_set_from_csv(
        active_current / "do_not_repeat_master.csv",
        "email_norm",
        "contact_email",
        "email",
    )
    if not dnr:
        dnr = load_email_set_from_csv(active_root / "do_not_repeat_master.csv", "email_norm", "contact_email", "email")

    contacted = load_email_set_from_csv(
        active_root / "outreach_contacted_all.csv",
        "contact_email",
        "email",
    )
    marketing = load_email_set_from_csv(
        active_root / "all_known_marketing_contacts_dedup.csv",
        "contact_email",
        "email",
        "email_norm",
    )

    operator_by_code = load_operator_queue_index(operator_queue_path)

    conn: sqlite3.Connection | None = None
    if db_path and db_path.is_file():
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    try:
        vetted = vet_deepsearch_rows(
            input_rows,
            operator_by_code=operator_by_code,
            dnr_emails=dnr,
            contacted_emails=contacted,
            marketing_emails=marketing,
            conn=conn,
        )
    finally:
        if conn is not None:
            conn.close()

    out_rows = vetted_rows_to_dicts(vetted)
    write_vetted_csv(out_rows, output_csv)
    write_vetted_markdown(out_rows, output_md, date_suffix=date_suffix, input_name=input_path.name)

    summary: dict[str, int] = {}
    for r in out_rows:
        c = r.get("vetted_classification", "")
        summary[c] = summary.get(c, 0) + 1
    return {"input_rows": len(input_rows), "output_rows": len(out_rows), "by_classification": summary}
