"""Deterministic production case selection for PR5A (read-only SQLite)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from origenlab_email_pipeline.commercial_procurement.walkthrough_redaction import (
    redact_account,
    redact_domain,
    redact_source,
    redact_tender,
    redact_token,
)

EXCLUSION_KEYWORDS = (
    "reactivo",
    "insumo",
    "arriendo",
    "comodato",
    "mantenimiento",
)
EQUIPMENT_KEYWORDS = (
    "centrifug",
    "balanza",
    "autoclave",
    "microscop",
    "espectrofotom",
    "hplc",
    "cromatograf",
    "incubador",
)

SELECTION_ALGORITHMS = {
    "case_b": (
        "Linked PR4 signals ordered by line_item_count DESC, procurement_id ASC; "
        "first whose external_leads_raw.raw_json contains an equipment keyword."
    ),
    "case_c": (
        "PR4 signals with external_leads_raw evidence ordered by procurement_id ASC; "
        "first whose raw_json contains an exclusion keyword."
    ),
    "case_d": (
        "Linked PR4 resolutions ordered by PR2 contact_count ASC, procurement_id ASC; "
        "first with contact_count=0."
    ),
    "case_e": (
        "Linked PR4 resolutions with ≥1 PR2 contact ordered by contact_count DESC, "
        "procurement_id ASC; first row."
    ),
}


@dataclass
class ProductionCaseSelection:
    case_b: dict[str, Any]
    case_c: dict[str, Any]
    case_d: dict[str, Any]
    case_e: dict[str, Any]
    forbidden_values: set[str]
    selection_meta: dict[str, Any]


def _collect_forbidden(values: list[Any], bucket: set[str]) -> None:
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            bucket.add(s)
            bucket.add(s.lower())


def select_production_cases(
    conn: sqlite3.Connection,
    *,
    pr4_semantic_digest: str,
    pr2_identity_fingerprint: str,
) -> ProductionCaseSelection:
    """Select Cases B–E inside an already-open read-only transaction."""
    forbidden: set[str] = set()
    typed_ids: dict[str, Any] = {}

    # Case B
    case_b_raw = None
    for row in conn.execute(
        """
        SELECT s.procurement_id, s.canonical_tender_key, s.procurement_context,
               s.close_at, s.status_code, s.status_name, s.line_item_count,
               r.resolution_status, r.link_route, r.account_id, r.confidence,
               ev.source_record_id
        FROM commercial_procurement_signal s
        JOIN commercial_procurement_account_resolution r USING (procurement_id)
        JOIN commercial_procurement_evidence ev
          ON ev.subject_id = s.procurement_id AND ev.subject_kind = 'signal'
        WHERE r.resolution_status = 'linked'
          AND ev.source_table = 'external_leads_raw'
        ORDER BY s.line_item_count DESC, s.procurement_id ASC
        LIMIT 400
        """
    ):
        raw = conn.execute(
            "SELECT raw_json FROM external_leads_raw WHERE source_record_id = ?",
            (row["source_record_id"],),
        ).fetchone()
        if not raw:
            continue
        text = (raw[0] or "").lower()
        hit = next((k for k in EQUIPMENT_KEYWORDS if k in text), None)
        if hit:
            case_b_raw = dict(row)
            case_b_raw["equipment_keyword_hit"] = hit
            case_b_raw["raw_json_len"] = len(raw[0] or "")
            _collect_forbidden(
                [
                    row["canonical_tender_key"],
                    row["account_id"],
                    row["source_record_id"],
                    row["procurement_id"],
                ],
                forbidden,
            )
            # org-ish from signal if present
            break
    if case_b_raw is None:
        raise RuntimeError("Case B selection failed: no historical equipment match")

    typed_ids["case_b"] = {
        "procurement_id": case_b_raw["procurement_id"],
        "canonical_tender_key": case_b_raw["canonical_tender_key"],
        "account_id": case_b_raw["account_id"],
        "source_record_id": case_b_raw["source_record_id"],
    }

    # Case C
    case_c_raw = None
    for row in conn.execute(
        """
        SELECT s.procurement_id, s.canonical_tender_key, s.close_at, s.status_name,
               s.status_code, s.procurement_context, ev.source_record_id
        FROM commercial_procurement_signal s
        JOIN commercial_procurement_evidence ev
          ON ev.subject_id = s.procurement_id AND ev.subject_kind = 'signal'
        WHERE ev.source_table = 'external_leads_raw'
        ORDER BY s.procurement_id ASC
        LIMIT 800
        """
    ):
        raw = conn.execute(
            "SELECT raw_json FROM external_leads_raw WHERE source_record_id = ?",
            (row["source_record_id"],),
        ).fetchone()
        if not raw:
            continue
        text = (raw[0] or "").lower()
        hit = next((k for k in EXCLUSION_KEYWORDS if k in text), None)
        if hit:
            case_c_raw = dict(row)
            case_c_raw["exclusion_keyword"] = hit
            _collect_forbidden(
                [
                    row["canonical_tender_key"],
                    row["source_record_id"],
                    row["procurement_id"],
                ],
                forbidden,
            )
            break
    if case_c_raw is None:
        raise RuntimeError("Case C selection failed: no exclusion keyword hit")

    typed_ids["case_c"] = {
        "procurement_id": case_c_raw["procurement_id"],
        "canonical_tender_key": case_c_raw["canonical_tender_key"],
        "source_record_id": case_c_raw["source_record_id"],
    }

    # Case D — linked, zero contacts
    case_d_raw = conn.execute(
        """
        SELECT s.procurement_id, s.canonical_tender_key, s.close_at, s.status_name,
               s.procurement_context, r.account_id, r.link_route, r.resolution_status,
               (
                 SELECT COUNT(*) FROM commercial_identity_contact c
                 WHERE c.account_id = r.account_id
               ) AS contact_n
        FROM commercial_procurement_signal s
        JOIN commercial_procurement_account_resolution r USING (procurement_id)
        WHERE r.resolution_status = 'linked'
        ORDER BY contact_n ASC, s.procurement_id ASC
        LIMIT 1
        """
    ).fetchone()
    if case_d_raw is None:
        raise RuntimeError("Case D selection failed")
    case_d_raw = dict(case_d_raw)
    _collect_forbidden(
        [
            case_d_raw["canonical_tender_key"],
            case_d_raw["account_id"],
            case_d_raw["procurement_id"],
        ],
        forbidden,
    )
    typed_ids["case_d"] = {
        "procurement_id": case_d_raw["procurement_id"],
        "canonical_tender_key": case_d_raw["canonical_tender_key"],
        "account_id": case_d_raw["account_id"],
        "contact_n": case_d_raw["contact_n"],
    }

    # Case E — linked with contacts
    case_e_raw = conn.execute(
        """
        SELECT s.procurement_id, s.canonical_tender_key, s.close_at, s.status_name,
               s.procurement_context, r.account_id, r.link_route, r.resolution_status,
               (
                 SELECT COUNT(*) FROM commercial_identity_contact c
                 WHERE c.account_id = r.account_id
               ) AS contact_n
        FROM commercial_procurement_signal s
        JOIN commercial_procurement_account_resolution r USING (procurement_id)
        WHERE r.resolution_status = 'linked'
          AND EXISTS (
            SELECT 1 FROM commercial_identity_contact c WHERE c.account_id = r.account_id
          )
        ORDER BY contact_n DESC, s.procurement_id ASC
        LIMIT 1
        """
    ).fetchone()
    if case_e_raw is None:
        raise RuntimeError("Case E selection failed")
    case_e_raw = dict(case_e_raw)
    _collect_forbidden(
        [
            case_e_raw["canonical_tender_key"],
            case_e_raw["account_id"],
            case_e_raw["procurement_id"],
        ],
        forbidden,
    )

    ccols = [r[1] for r in conn.execute("PRAGMA table_info(commercial_identity_contact)")]
    email_col = (
        "normalized_email"
        if "normalized_email" in ccols
        else ("email" if "email" in ccols else None)
    )
    contacts_raw = []
    suppressed_n = 0
    for c in conn.execute(
        f"""
        SELECT contact_id, role, display_name
               {"," + email_col + " AS em" if email_col else ", NULL AS em"}
        FROM commercial_identity_contact
        WHERE account_id = ?
        LIMIT 8
        """,
        (case_e_raw["account_id"],),
    ):
        em = c["em"]
        if em:
            _collect_forbidden([em, em.split("@")[-1] if "@" in em else None], forbidden)
            scols = [
                r[1] for r in conn.execute("PRAGMA table_info(contact_email_suppression)")
            ]
            scol = (
                "email_norm"
                if "email_norm" in scols
                else ("normalized_email" if "normalized_email" in scols else None)
            )
            if scol and conn.execute(
                f"SELECT 1 FROM contact_email_suppression WHERE {scol}=? LIMIT 1",
                (em,),
            ).fetchone():
                suppressed_n += 1
        _collect_forbidden([c["contact_id"], c["display_name"], c["role"]], forbidden)
        contacts_raw.append(dict(c))

    typed_ids["case_e"] = {
        "procurement_id": case_e_raw["procurement_id"],
        "canonical_tender_key": case_e_raw["canonical_tender_key"],
        "account_id": case_e_raw["account_id"],
        "contact_n": case_e_raw["contact_n"],
        "contact_ids": [c["contact_id"] for c in contacts_raw],
    }

    def redact_case(d: dict[str, Any], *, contacts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        out = {
            "procurement_id": d.get("procurement_id"),
            "tender_key_redacted": redact_tender(d.get("canonical_tender_key")),
            "account_id_redacted": redact_account(d.get("account_id")),
            "source_record_id_redacted": redact_source(d.get("source_record_id"))
            if d.get("source_record_id")
            else None,
            "procurement_context": d.get("procurement_context"),
            "close_at": d.get("close_at"),
            "status_code": d.get("status_code"),
            "status_name": d.get("status_name"),
            "resolution_status": d.get("resolution_status"),
            "link_route": d.get("link_route"),
            "confidence": d.get("confidence"),
            "line_item_count": d.get("line_item_count"),
            "equipment_keyword_hit": d.get("equipment_keyword_hit"),
            "exclusion_keyword": d.get("exclusion_keyword"),
            "contact_n": d.get("contact_n"),
        }
        if contacts is not None:
            out["suppressed_email_count"] = suppressed_n
            out["contacts_redacted"] = [
                {
                    "contact_id_redacted": redact_token("contact", c["contact_id"]),
                    "has_role": bool(c.get("role")),
                    "has_email": bool(c.get("em")),
                    "email_domain_redacted": redact_domain(
                        (c.get("em") or "").split("@")[-1]
                    )
                    if c.get("em") and "@" in str(c.get("em"))
                    else None,
                    "has_display_name": bool(c.get("display_name")),
                }
                for c in contacts
            ]
        return {k: v for k, v in out.items() if v is not None}

    meta = {
        "selection_algorithms": SELECTION_ALGORITHMS,
        "stable_ordering": "documented in selection_algorithms",
        "typed_ids_before_redaction": typed_ids,
        "pr4_semantic_digest": pr4_semantic_digest,
        "pr2_identity_fingerprint": pr2_identity_fingerprint,
        "transaction_snapshot": "pinned BEGIN + query_only on caller connection",
        "production_derived": True,
        "fixture_seeds_used": False,
    }
    return ProductionCaseSelection(
        case_b=redact_case(case_b_raw),
        case_c=redact_case(case_c_raw),
        case_d=redact_case(case_d_raw),
        case_e=redact_case(case_e_raw, contacts=contacts_raw),
        forbidden_values=forbidden,
        selection_meta=meta,
    )
