"""Read-only ingest and safety review for legacy contact workbook (2016–2019).

Never classifies rows as ``net_new_safe`` or send-ready. Default action is manual
review only (``review_legacy_contact``).
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from origenlab_email_pipeline.business_mart import domain_of, emails_in
from origenlab_email_pipeline.candidate_export_gate import normalize_export_email
from origenlab_email_pipeline.contact_domain_suppression import load_suppressed_contact_domain_norms
from origenlab_email_pipeline.lead_research.lead_research_builder import infer_campaign_bucket, make_prospect_key
from origenlab_email_pipeline.lead_research.lead_research_schema import ensure_lead_research_tables
from origenlab_email_pipeline.leads.new_customer_research import ExclusionLists, load_exclusion_lists
from origenlab_email_pipeline.marketing_export_context import load_sent_recipient_norms, load_suppressed_norms
from origenlab_email_pipeline.marketing_supplier_domains import is_supplier_email_domain, supplier_email_domains

# ---------------------------------------------------------------------------
# 1. Constants / status codes / output columns
# ---------------------------------------------------------------------------

SOURCE_TYPE_LEGACY = "legacy_2016_2019"
BATCH_KEY_LEGACY = "legacy_contacts_2016_2019"
SOURCE_NAME_LEGACY = "legacy_contacts_2016_2019_import"
DATASET_LABEL_LEGACY = "Base de datos 2016–2019"
STATUS_REVIEW_LEGACY = "review_legacy_contact"
CLASSIFICATION_LEGACY = "legacy_contact_review"
DEFAULT_SUGGESTED_ACTION = "review_legacy_contact"

STATUS_ALREADY_CONTACTED_EXACT = "already_contacted_exact"
STATUS_DOMAIN_HAS_HISTORY = "domain_has_history"
STATUS_BOUNCED_SUPPRESSED = "bounced_or_suppressed"
STATUS_INVALID_EMAIL = "invalid_email"
STATUS_SUPPLIER_VENDOR = "supplier_or_vendor"
STATUS_LIKELY_PERSONAL = "likely_personal_email"
STATUS_GENERIC_ORG = "generic_org_email"
STATUS_POSSIBLE_BUYER = "possible_buyer_review"
STATUS_DUP_EMAIL = "duplicate_email"
STATUS_DUP_DOMAIN_SECONDARY = "duplicate_domain_secondary"
STATUS_NO_EMAIL = "no_email_or_incomplete"

REVIEW_OUTPUT_COLUMNS: tuple[str, ...] = (
    "email",
    "domain",
    "organization",
    "contact_name",
    "phone",
    "region",
    "source_sheet",
    "source_row",
    "original_notes",
    "normalized_status",
    "safety_reason",
    "suggested_action",
    "product_angle",
    "confidence",
)

PERSONAL_EMAIL_DOMAINS: frozenset[str] = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "hotmail.com",
        "outlook.com",
        "live.com",
        "yahoo.com",
        "yahoo.es",
        "icloud.com",
        "me.com",
        "msn.com",
        "vtr.net",
        "terra.cl",
        "entel.cl",
        "gmx.com",
        "protonmail.com",
        "proton.me",
    }
)

_GENERIC_LOCAL_PARTS: frozenset[str] = frozenset(
    {
        "info",
        "contacto",
        "contact",
        "ventas",
        "administracion",
        "recepcion",
        "secretaria",
        "laboratorio",
        "lab",
        "comercial",
        "ventas",
        "servicioalcliente",
        "atencion",
        "atencionalcliente",
    }
)

_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "contact_name": ("contacto", "nombre", "name", "contact"),
    "email": ("correo", "email", "e-mail", "mail"),
    "organization": ("empresa", "organizacion", "organización", "company", "institucion"),
    "product_angle": ("producto", "interes", "interés", "equipo"),
    "region": ("direccion", "dirección", "region", "región", "ciudad", "comuna", "address"),
    "phone": ("telefono", "teléfono", "fono", "phone", "celular"),
    "notes": ("nota", "notas", "observacion", "observación", "comentario"),
    "category": ("categoria", "categoría", "tipo", "rubro"),
    "date": ("fecha", "date"),
}

_EMAIL_CELL_SPLIT_RE = re.compile(r"[\s,;|/]+")

_SUMMARY_MARKDOWN_BUCKET_ORDER: tuple[str, ...] = (
    STATUS_POSSIBLE_BUYER,
    STATUS_ALREADY_CONTACTED_EXACT,
    STATUS_DOMAIN_HAS_HISTORY,
    STATUS_BOUNCED_SUPPRESSED,
    STATUS_INVALID_EMAIL,
    STATUS_NO_EMAIL,
    STATUS_SUPPLIER_VENDOR,
    STATUS_LIKELY_PERSONAL,
    STATUS_GENERIC_ORG,
    STATUS_DUP_EMAIL,
    STATUS_DUP_DOMAIN_SECONDARY,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _collapse_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


# ---------------------------------------------------------------------------
# 2. Dataclasses / inspection markdown
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LegacySafetyContext:
    exclusion: ExclusionLists
    sqlite_suppressed_emails: frozenset[str]
    sqlite_suppressed_domains: frozenset[str]
    gmail_sent_emails: frozenset[str]
    lead_research_emails: frozenset[str]
    lead_research_domains: frozenset[str]
    supplier_domains_sqlite: frozenset[str]


@dataclass
class LegacyRawRow:
    source_sheet: str
    source_row: int
    cells: dict[str, str]


@dataclass
class LegacyNormalizedRow:
    email: str
    domain: str
    organization: str
    contact_name: str
    phone: str
    region: str
    source_sheet: str
    source_row: int
    original_notes: str
    product_angle: str
    category: str
    source_type: str = SOURCE_TYPE_LEGACY
    dataset_label: str = DATASET_LABEL_LEGACY
    normalized_status: str = ""
    safety_reason: str = ""
    suggested_action: str = DEFAULT_SUGGESTED_ACTION
    confidence: str = "baja"
    raw_email_cell: str = ""


@dataclass
class WorkbookInspection:
    path: str
    sheets: list[dict[str, Any]] = field(default_factory=list)

    def to_markdown(self) -> str:
        return _workbook_inspection_markdown(self)


@dataclass
class LegacyReviewBuildResult:
    inspection: WorkbookInspection
    normalized_rows: list[LegacyNormalizedRow]
    summary: dict[str, Any]
    buckets: dict[str, list[LegacyNormalizedRow]]


def _workbook_inspection_markdown(inspection: WorkbookInspection) -> str:
    lines = [
        "# Legacy contacts 2016–2019 — workbook inspection",
        "",
        f"- **Path:** `{inspection.path}`",
        f"- **Sheets:** {len(inspection.sheets)}",
        "",
    ]
    for sh in inspection.sheets:
        _append_workbook_sheet_markdown(lines, sh)
    return "\n".join(lines)


def _append_workbook_sheet_markdown(lines: list[str], sh: dict[str, Any]) -> None:
    lines.append(f"## {sh['name']}")
    lines.append("")
    lines.append(f"- Rows: **{sh['row_count']}**")
    lines.append(f"- Columns ({len(sh['columns'])}): `{', '.join(sh['columns'])}`")
    if sh.get("inferred_mapping"):
        lines.append("- Inferred mapping:")
        for k, v in sh["inferred_mapping"].items():
            lines.append(f"  - `{k}` ← `{v}`")
    if sh.get("sample_rows"):
        lines.append("")
        lines.append("Sample rows (first non-empty):")
        lines.append("")
        lines.append("| row | " + " | ".join(sh["columns"][:6]) + " |")
        lines.append("| --- | " + " | ".join(["---"] * min(6, len(sh["columns"]))) + " |")
        for sr in sh["sample_rows"][:5]:
            vals = [str(sr.get(c, "")).replace("|", "/")[:40] for c in sh["columns"][:6]]
            lines.append("| " + str(sr.get("_row", "")) + " | " + " | ".join(vals) + " |")
    lines.append("")


# ---------------------------------------------------------------------------
# 3. Header alias and email parsing helpers
# ---------------------------------------------------------------------------


def normalize_organization_name(raw: str | None) -> str:
    return _collapse_ws(str(raw or ""))


def split_emails_from_cell(raw: str | None) -> list[str]:
    """Extract all mailbox addresses from a spreadsheet cell."""
    text = (raw or "").strip()
    if not text:
        return []
    found = emails_in(text)
    if found:
        return list(dict.fromkeys(found))
    parts = [p.strip() for p in _EMAIL_CELL_SPLIT_RE.split(text) if p.strip()]
    out: list[str] = []
    for part in parts:
        if "@" not in part:
            continue
        em = normalize_export_email(part)
        if em and em not in out:
            out.append(em)
    return out


def is_plausible_email(email: str) -> bool:
    em = (email or "").strip().lower()
    if not em or "@" not in em:
        return False
    local, _, dom = em.partition("@")
    if not local or not dom or "." not in dom:
        return False
    if len(em) > 254:
        return False
    return bool(re.match(r"^[\w.+-]+@[\w.-]+\.[a-z]{2,}$", em, re.I))


def infer_column_mapping(columns: Iterable[str]) -> dict[str, str]:
    """Map logical field names to actual column headers."""
    norm_cols = {str(c).strip(): str(c).strip() for c in columns if str(c).strip()}
    lowered = {c.lower(): c for c in norm_cols}
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for field_name, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            for col_lower, original in lowered.items():
                if col_lower in used:
                    continue
                if alias in col_lower or col_lower == alias:
                    mapping[field_name] = original
                    used.add(col_lower)
                    break
            if field_name in mapping:
                break
    cols_list = list(norm_cols.keys())
    if "email" not in mapping:
        for c in cols_list:
            if "correo" in c.lower() or c.lower() == "email":
                mapping["email"] = c
                break
    if "contact_name" not in mapping:
        for c in cols_list:
            if "contacto" in c.lower():
                mapping["contact_name"] = c
                break
    if "organization" not in mapping:
        for c in cols_list:
            if "empresa" in c.lower():
                mapping["organization"] = c
                break
    unnamed = [c for c in cols_list if c.lower().startswith("unnamed")]
    if "product_angle" not in mapping and len(unnamed) >= 1:
        mapping["product_angle"] = unnamed[0]
    if "region" not in mapping and len(unnamed) >= 2:
        mapping["region"] = unnamed[1]
    if "category" not in mapping and len(unnamed) >= 3:
        mapping["category"] = unnamed[2]
    return mapping


def _mapped_cell(raw: LegacyRawRow, mapping: dict[str, str], field: str) -> str:
    col = mapping.get(field, "")
    return raw.cells.get(col, "") if col else ""


def _parse_legacy_raw_row(raw: LegacyRawRow) -> tuple[str, list[str], str, str, str, str, str, str, str]:
    """Return email cell, emails, org, contact, product, region, phone, category, notes."""
    mapping = infer_column_mapping(raw.cells.keys())
    raw_email_cell = _mapped_cell(raw, mapping, "email")
    emails = split_emails_from_cell(raw_email_cell)
    org = normalize_organization_name(_mapped_cell(raw, mapping, "organization"))
    contact = _collapse_ws(_mapped_cell(raw, mapping, "contact_name"))
    product = _collapse_ws(_mapped_cell(raw, mapping, "product_angle"))
    region = _collapse_ws(_mapped_cell(raw, mapping, "region"))
    phone = _collapse_ws(_mapped_cell(raw, mapping, "phone"))
    category = _collapse_ws(_mapped_cell(raw, mapping, "category"))
    notes_parts = [_mapped_cell(raw, mapping, "notes"), category]
    original_notes = _collapse_ws("; ".join(p for p in notes_parts if p))
    return raw_email_cell, emails, org, contact, product, region, phone, category, original_notes


# ---------------------------------------------------------------------------
# 4. Workbook reading / raw-row normalization
# ---------------------------------------------------------------------------


def _cell_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value).strip()


def read_legacy_workbook_xls(path: Path) -> tuple[list[LegacyRawRow], WorkbookInspection]:
    """Read all non-empty sheets from a legacy ``.xls`` workbook (xlrd)."""
    try:
        import xlrd
    except ImportError as exc:
        raise ImportError(
            "xlrd is required for .xls legacy imports. Install with: uv sync --group data-tools"
        ) from exc

    path = path.resolve()
    book = xlrd.open_workbook(str(path))
    raw_rows: list[LegacyRawRow] = []
    inspection = WorkbookInspection(path=str(path))

    for sheet_name in book.sheet_names():
        sheet = book.sheet_by_name(sheet_name)
        if sheet.nrows == 0:
            inspection.sheets.append(
                {"name": sheet_name, "row_count": 0, "columns": [], "inferred_mapping": {}, "sample_rows": []}
            )
            continue
        headers = [_cell_str(sheet.cell_value(0, c)) for c in range(sheet.ncols)]
        headers = [h if h else f"Unnamed: {i}" for i, h in enumerate(headers)]
        mapping = infer_column_mapping(headers)
        sample: list[dict[str, Any]] = []
        data_row_count = 0
        for r in range(1, sheet.nrows):
            cells = {_cell_str(headers[c]): _cell_str(sheet.cell_value(r, c)) for c in range(sheet.ncols)}
            if not any(v for v in cells.values()):
                continue
            data_row_count += 1
            raw_rows.append(LegacyRawRow(source_sheet=sheet_name, source_row=r + 1, cells=cells))
            if len(sample) < 8:
                sample.append({"_row": r + 1, **cells})
        inspection.sheets.append(
            {
                "name": sheet_name,
                "row_count": data_row_count,
                "columns": headers,
                "inferred_mapping": mapping,
                "sample_rows": sample,
            }
        )
    return raw_rows, inspection


def _no_email_normalized_row(
    raw: LegacyRawRow,
    *,
    organization: str,
    contact_name: str,
    phone: str,
    region: str,
    original_notes: str,
    product_angle: str,
    category: str,
    raw_email_cell: str,
) -> LegacyNormalizedRow:
    return LegacyNormalizedRow(
        email="",
        domain="",
        organization=organization,
        contact_name=contact_name,
        phone=phone,
        region=region,
        source_sheet=raw.source_sheet,
        source_row=raw.source_row,
        original_notes=original_notes,
        product_angle=product_angle,
        category=category,
        raw_email_cell=raw_email_cell,
        normalized_status=STATUS_NO_EMAIL,
        safety_reason="sin_correo_en_celda",
        suggested_action=DEFAULT_SUGGESTED_ACTION,
    )


def normalize_legacy_raw_rows(raw_rows: Iterable[LegacyRawRow]) -> list[LegacyNormalizedRow]:
    """Expand raw sheet rows to one row per extracted email."""
    out: list[LegacyNormalizedRow] = []
    for raw in raw_rows:
        raw_email_cell, emails, org, contact, product, region, phone, category, original_notes = (
            _parse_legacy_raw_row(raw)
        )
        if not emails:
            out.append(
                _no_email_normalized_row(
                    raw,
                    organization=org,
                    contact_name=contact,
                    phone=phone,
                    region=region,
                    original_notes=original_notes,
                    product_angle=product,
                    category=category,
                    raw_email_cell=raw_email_cell,
                )
            )
            continue

        for em in emails:
            dom = domain_of(em) or ""
            out.append(
                LegacyNormalizedRow(
                    email=em,
                    domain=dom,
                    organization=org,
                    contact_name=contact,
                    phone=phone,
                    region=region,
                    source_sheet=raw.source_sheet,
                    source_row=raw.source_row,
                    original_notes=original_notes,
                    product_angle=product,
                    category=category,
                    raw_email_cell=raw_email_cell,
                )
            )
    return out


# ---------------------------------------------------------------------------
# 5. Safety context loading
# ---------------------------------------------------------------------------


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return bool(row)


def _load_active_lead_research_email_domain_sets(
    conn: sqlite3.Connection,
) -> tuple[set[str], set[str]]:
    emails: set[str] = set()
    domains: set[str] = set()
    if not _table_exists(conn, "lead_research_prospect"):
        return emails, domains
    for em, dom in conn.execute(
        """
        SELECT lower(trim(email)), lower(trim(domain))
        FROM lead_research_prospect
        WHERE is_active = 1 AND email IS NOT NULL AND trim(email) != ''
        """
    ):
        if em:
            emails.add(str(em))
        if dom:
            domains.add(str(dom))
    return emails, domains


def load_legacy_safety_context(
    conn: sqlite3.Connection | None,
    exclusion_dir: Path,
    *,
    gmail_user: str = "",
    sent_folders: tuple[str, ...] = (),
) -> LegacySafetyContext:
    excl = load_exclusion_lists(exclusion_dir)
    sqlite_suppressed: set[str] = set()
    sqlite_domains: set[str] = set()
    gmail_sent: set[str] = set()
    lr_emails: set[str] = set()
    lr_domains: set[str] = set()
    supplier_sqlite: frozenset[str] = frozenset()

    if conn is not None:
        sqlite_suppressed |= load_suppressed_norms(conn)
        sqlite_domains |= set(load_suppressed_contact_domain_norms(conn))
        supplier_sqlite = supplier_email_domains(conn)
        if gmail_user and sent_folders:
            gmail_sent |= load_sent_recipient_norms(conn, gmail_user=gmail_user, sent_folders=sent_folders)
        lr_emails, lr_domains = _load_active_lead_research_email_domain_sets(conn)

    return LegacySafetyContext(
        exclusion=excl,
        sqlite_suppressed_emails=frozenset(sqlite_suppressed),
        sqlite_suppressed_domains=frozenset(sqlite_domains),
        gmail_sent_emails=frozenset(gmail_sent),
        lead_research_emails=frozenset(lr_emails),
        lead_research_domains=frozenset(lr_domains),
        supplier_domains_sqlite=supplier_sqlite,
    )


# ---------------------------------------------------------------------------
# 6. Classification and duplicate labeling
# ---------------------------------------------------------------------------


def _is_generic_mailbox(email: str) -> bool:
    local = email.split("@", 1)[0].lower()
    return local in _GENERIC_LOCAL_PARTS or local.startswith("laboratorio")


def _base_safety_status(row: LegacyNormalizedRow, ctx: LegacySafetyContext) -> tuple[str, str]:
    if not row.email:
        return STATUS_NO_EMAIL, "sin_correo"
    if not is_plausible_email(row.email):
        return STATUS_INVALID_EMAIL, "formato_correo_invalido"

    excl = ctx.exclusion
    em, dom = row.email, row.domain

    if em in excl.contacted_emails or em in ctx.gmail_sent_emails or em in ctx.lead_research_emails:
        return STATUS_ALREADY_CONTACTED_EXACT, "email_ya_contactado"

    if em in excl.bounced_emails or em in excl.suppressed_emails or em in ctx.sqlite_suppressed_emails:
        return STATUS_BOUNCED_SUPPRESSED, "email_rebote_o_suprimido"

    if dom and dom in excl.bounced_domains:
        return STATUS_BOUNCED_SUPPRESSED, "dominio_con_rebotes"

    all_supplier = excl.supplier_domains | ctx.supplier_domains_sqlite
    if dom in excl.internal_domains:
        return STATUS_SUPPLIER_VENDOR, "dominio_interno_origenlab"
    if dom in all_supplier or is_supplier_email_domain(f"x@{dom}", all_supplier):
        return STATUS_SUPPLIER_VENDOR, "dominio_proveedor"

    if dom and dom in excl.contacted_domains:
        return STATUS_DOMAIN_HAS_HISTORY, "dominio_con_historial_envios"

    if dom and (dom in ctx.lead_research_domains or dom in ctx.sqlite_suppressed_domains):
        return STATUS_DOMAIN_HAS_HISTORY, "dominio_en_lead_research_o_suprimido"

    if dom in PERSONAL_EMAIL_DOMAINS:
        return STATUS_LIKELY_PERSONAL, "dominio_correo_personal"

    if _is_generic_mailbox(em):
        return STATUS_GENERIC_ORG, "buzon_generico"

    return STATUS_POSSIBLE_BUYER, "legacy_sin_historial_negativo"


def _apply_row_classification(row: LegacyNormalizedRow, ctx: LegacySafetyContext) -> None:
    status, reason = _base_safety_status(row, ctx)
    row.normalized_status = status
    row.safety_reason = reason
    if status == STATUS_POSSIBLE_BUYER:
        row.confidence = "media" if row.contact_name and row.organization else "baja"
    else:
        row.confidence = "baja"
    row.suggested_action = DEFAULT_SUGGESTED_ACTION


def _mark_duplicate_emails(rows: list[LegacyNormalizedRow]) -> None:
    seen_email: dict[str, int] = {}
    for idx, row in enumerate(rows):
        if row.normalized_status in (STATUS_NO_EMAIL, STATUS_INVALID_EMAIL):
            continue
        em = row.email
        if em in seen_email:
            row.normalized_status = STATUS_DUP_EMAIL
            row.safety_reason = "email_duplicado_en_archivo"
            row.confidence = "baja"
            continue
        seen_email[em] = idx


def _mark_duplicate_domains(rows: list[LegacyNormalizedRow]) -> None:
    seen_domain_primary: dict[str, str] = {}
    for row in rows:
        if row.normalized_status not in ("", STATUS_POSSIBLE_BUYER):
            continue
        if not row.domain:
            continue
        primary = seen_domain_primary.get(row.domain)
        if primary is None:
            seen_domain_primary[row.domain] = row.email
            continue
        if primary != row.email:
            row.normalized_status = STATUS_DUP_DOMAIN_SECONDARY
            row.safety_reason = "dominio_duplicado_secundario_en_archivo"
            row.confidence = "baja"


def apply_duplicate_labels(rows: list[LegacyNormalizedRow]) -> None:
    _mark_duplicate_emails(rows)
    _mark_duplicate_domains(rows)


def classify_legacy_rows(
    rows: list[LegacyNormalizedRow],
    ctx: LegacySafetyContext,
) -> None:
    for row in rows:
        if row.normalized_status == STATUS_NO_EMAIL:
            continue
        _apply_row_classification(row, ctx)
    apply_duplicate_labels(rows)


# ---------------------------------------------------------------------------
# 7. Bucket / summary / output writing
# ---------------------------------------------------------------------------


def row_to_review_dict(row: LegacyNormalizedRow) -> dict[str, str]:
    return {
        "email": row.email,
        "domain": row.domain,
        "organization": row.organization,
        "contact_name": row.contact_name,
        "phone": row.phone,
        "region": row.region,
        "source_sheet": row.source_sheet,
        "source_row": str(row.source_row),
        "original_notes": row.original_notes,
        "normalized_status": row.normalized_status,
        "safety_reason": row.safety_reason,
        "suggested_action": row.suggested_action,
        "product_angle": row.product_angle,
        "confidence": row.confidence,
    }


def bucket_legacy_rows(rows: list[LegacyNormalizedRow]) -> dict[str, list[LegacyNormalizedRow]]:
    buckets: dict[str, list[LegacyNormalizedRow]] = defaultdict(list)
    for row in rows:
        st = row.normalized_status or STATUS_NO_EMAIL
        buckets[st].append(row)
    return dict(buckets)


def build_summary(
    inspection: WorkbookInspection,
    rows: list[LegacyNormalizedRow],
    buckets: dict[str, list[LegacyNormalizedRow]],
) -> dict[str, Any]:
    raw_row_count = sum(sh["row_count"] for sh in inspection.sheets)
    emails_extracted = sum(1 for r in rows if r.email)
    valid_emails = [r.email for r in rows if r.email and is_plausible_email(r.email)]
    unique_valid = len(set(valid_emails))
    status_counts = Counter(r.normalized_status for r in rows)
    top_buyers = sorted(
        buckets.get(STATUS_POSSIBLE_BUYER, []),
        key=lambda r: (r.confidence != "media", r.organization, r.email),
    )[:25]
    return {
        "workbook_path": inspection.path,
        "sheets": [
            {"name": sh["name"], "row_count": sh["row_count"], "columns": sh["columns"]}
            for sh in inspection.sheets
        ],
        "raw_rows": raw_row_count,
        "normalized_rows": len(rows),
        "emails_extracted": emails_extracted,
        "unique_valid_emails": unique_valid,
        "invalid_emails": status_counts.get(STATUS_INVALID_EMAIL, 0),
        "no_email_or_incomplete": status_counts.get(STATUS_NO_EMAIL, 0),
        "already_contacted_exact": status_counts.get(STATUS_ALREADY_CONTACTED_EXACT, 0),
        "domain_has_history": status_counts.get(STATUS_DOMAIN_HAS_HISTORY, 0),
        "bounced_or_suppressed": status_counts.get(STATUS_BOUNCED_SUPPRESSED, 0),
        "supplier_or_vendor": status_counts.get(STATUS_SUPPLIER_VENDOR, 0),
        "likely_personal_email": status_counts.get(STATUS_LIKELY_PERSONAL, 0),
        "generic_org_email": status_counts.get(STATUS_GENERIC_ORG, 0),
        "possible_buyer_review": status_counts.get(STATUS_POSSIBLE_BUYER, 0),
        "duplicate_email": status_counts.get(STATUS_DUP_EMAIL, 0),
        "duplicate_domain_secondary": status_counts.get(STATUS_DUP_DOMAIN_SECONDARY, 0),
        "status_counts": dict(status_counts),
        "top_possible_buyers": [
            {
                "email": r.email,
                "organization": r.organization,
                "contact_name": r.contact_name,
                "product_angle": r.product_angle,
            }
            for r in top_buyers
        ],
        "source_type": SOURCE_TYPE_LEGACY,
        "dataset_label": DATASET_LABEL_LEGACY,
        "never_net_new_safe": True,
        "built_at": _utc_now(),
    }


def build_legacy_contacts_review(
    xls_path: Path,
    *,
    exclusion_dir: Path,
    conn: sqlite3.Connection | None = None,
    gmail_user: str = "",
    sent_folders: tuple[str, ...] = (),
) -> LegacyReviewBuildResult:
    raw_rows, inspection = read_legacy_workbook_xls(xls_path)
    normalized = normalize_legacy_raw_rows(raw_rows)
    ctx = load_legacy_safety_context(
        conn, exclusion_dir, gmail_user=gmail_user, sent_folders=sent_folders
    )
    classify_legacy_rows(normalized, ctx)
    buckets = bucket_legacy_rows(normalized)
    summary = build_summary(inspection, normalized, buckets)
    return LegacyReviewBuildResult(
        inspection=inspection,
        normalized_rows=normalized,
        summary=summary,
        buckets=buckets,
    )


def _write_review_csv(path: Path, rows: list[LegacyNormalizedRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(REVIEW_OUTPUT_COLUMNS))
        w.writeheader()
        for row in rows:
            w.writerow(row_to_review_dict(row))


def summary_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Legacy contacts 2016–2019 — review summary",
        "",
        f"- Built at: `{summary.get('built_at')}`",
        f"- Workbook: `{summary.get('workbook_path')}`",
        f"- Raw data rows: **{summary.get('raw_rows')}**",
        f"- Normalized rows (incl. multi-email splits): **{summary.get('normalized_rows')}**",
        f"- Emails extracted: **{summary.get('emails_extracted')}**",
        f"- Unique valid emails: **{summary.get('unique_valid_emails')}**",
        "",
        "## Safety buckets",
        "",
        "| Bucket | Count |",
        "| --- | ---: |",
    ]
    sc = summary.get("status_counts") or {}
    for key in _SUMMARY_MARKDOWN_BUCKET_ORDER:
        lines.append(f"| {key} | {sc.get(key, 0)} |")
    lines.extend(["", "## Top possible buyers (review only)", ""])
    for i, b in enumerate(summary.get("top_possible_buyers") or [], 1):
        lines.append(
            f"{i}. **{b.get('email')}** — {b.get('organization') or '—'} "
            f"({b.get('contact_name') or 'sin nombre'}) · {b.get('product_angle') or 'sin producto'}"
        )
    lines.extend(
        [
            "",
            "> Review-only. Not send-ready. Never classified as `net_new_safe`.",
            "",
        ]
    )
    return "\n".join(lines)


def write_legacy_review_outputs(result: LegacyReviewBuildResult, out_dir: Path) -> dict[str, Path]:
    out_dir = out_dir.resolve()
    paths: dict[str, Path] = {}
    buckets = result.buckets

    paths["all"] = out_dir / "legacy_contacts_2016_2019_all_normalized.csv"
    _write_review_csv(paths["all"], result.normalized_rows)

    paths["possible_buyers"] = out_dir / "legacy_contacts_2016_2019_possible_buyers_review.csv"
    _write_review_csv(paths["possible_buyers"], buckets.get(STATUS_POSSIBLE_BUYER, []))

    paths["already_contacted"] = out_dir / "legacy_contacts_2016_2019_already_contacted.csv"
    _write_review_csv(paths["already_contacted"], buckets.get(STATUS_ALREADY_CONTACTED_EXACT, []))

    paths["bounced_suppressed"] = out_dir / "legacy_contacts_2016_2019_bounced_suppressed.csv"
    _write_review_csv(paths["bounced_suppressed"], buckets.get(STATUS_BOUNCED_SUPPRESSED, []))

    invalid = buckets.get(STATUS_INVALID_EMAIL, []) + buckets.get(STATUS_NO_EMAIL, [])
    paths["invalid_incomplete"] = out_dir / "legacy_contacts_2016_2019_invalid_or_incomplete.csv"
    _write_review_csv(paths["invalid_incomplete"], invalid)

    domain_dups = buckets.get(STATUS_DUP_DOMAIN_SECONDARY, []) + buckets.get(STATUS_DUP_EMAIL, [])
    paths["domain_duplicates"] = out_dir / "legacy_contacts_2016_2019_domain_duplicates.csv"
    _write_review_csv(paths["domain_duplicates"], domain_dups)

    paths["inspection"] = out_dir / "legacy_contacts_2016_2019_inspection.md"
    paths["inspection"].write_text(result.inspection.to_markdown(), encoding="utf-8")

    paths["summary_md"] = out_dir / "legacy_contacts_2016_2019_summary.md"
    paths["summary_md"].write_text(summary_markdown(result.summary), encoding="utf-8")

    paths["summary_json"] = out_dir / "legacy_contacts_2016_2019_summary.json"
    paths["summary_json"].write_text(
        json.dumps(result.summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return paths


# ---------------------------------------------------------------------------
# Optional SQLite staging (review-only; no outreach writes)
# ---------------------------------------------------------------------------

LEGACY_RAW_DDL = """
CREATE TABLE IF NOT EXISTS legacy_contact_raw (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_sheet TEXT NOT NULL,
  source_row INTEGER NOT NULL,
  cells_json TEXT NOT NULL,
  imported_at TEXT NOT NULL
);
"""

LEGACY_NORMALIZED_DDL = """
CREATE TABLE IF NOT EXISTS legacy_contact_normalized (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT,
  domain TEXT,
  organization TEXT,
  contact_name TEXT,
  phone TEXT,
  region TEXT,
  source_sheet TEXT NOT NULL,
  source_row INTEGER NOT NULL,
  original_notes TEXT,
  product_angle TEXT,
  normalized_status TEXT NOT NULL,
  safety_reason TEXT,
  suggested_action TEXT NOT NULL,
  source_type TEXT NOT NULL DEFAULT 'legacy_2016_2019',
  dataset_label TEXT NOT NULL,
  imported_at TEXT NOT NULL
);
"""


def ensure_legacy_contact_staging_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(LEGACY_RAW_DDL + LEGACY_NORMALIZED_DDL)


def _stage_legacy_raw_row(conn: sqlite3.Connection, raw: LegacyRawRow, imported_at: str) -> None:
    conn.execute(
        """
        INSERT INTO legacy_contact_raw (source_sheet, source_row, cells_json, imported_at)
        VALUES (?,?,?,?)
        """,
        (raw.source_sheet, raw.source_row, json.dumps(raw.cells, ensure_ascii=False), imported_at),
    )


def _stage_legacy_normalized_row(conn: sqlite3.Connection, row: LegacyNormalizedRow, imported_at: str) -> None:
    conn.execute(
        """
        INSERT INTO legacy_contact_normalized (
          email, domain, organization, contact_name, phone, region,
          source_sheet, source_row, original_notes, product_angle,
          normalized_status, safety_reason, suggested_action,
          source_type, dataset_label, imported_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            row.email or None,
            row.domain or None,
            row.organization or None,
            row.contact_name or None,
            row.phone or None,
            row.region or None,
            row.source_sheet,
            row.source_row,
            row.original_notes or None,
            row.product_angle or None,
            row.normalized_status,
            row.safety_reason,
            row.suggested_action,
            row.source_type,
            row.dataset_label,
            imported_at,
        ),
    )


def stage_legacy_contacts_to_sqlite(
    conn: sqlite3.Connection,
    raw_rows: list[LegacyRawRow],
    normalized: list[LegacyNormalizedRow],
    *,
    replace: bool = True,
) -> dict[str, int]:
    ensure_legacy_contact_staging_tables(conn)
    now = _utc_now()
    if replace:
        conn.execute("DELETE FROM legacy_contact_raw")
        conn.execute("DELETE FROM legacy_contact_normalized")
    for raw in raw_rows:
        _stage_legacy_raw_row(conn, raw, now)
    for row in normalized:
        _stage_legacy_normalized_row(conn, row, now)
    conn.commit()
    return {"legacy_contact_raw": len(raw_rows), "legacy_contact_normalized": len(normalized)}


# ---------------------------------------------------------------------------
# 8. lead_research payload + merge
# ---------------------------------------------------------------------------


def legacy_row_to_lead_research_payload(row: LegacyNormalizedRow) -> dict[str, Any] | None:
    """Map a possible-buyer legacy row to lead_research insert shape (review-only)."""
    if row.normalized_status != STATUS_POSSIBLE_BUYER:
        return None
    if not row.email or not is_plausible_email(row.email):
        return None
    org = row.organization or row.domain or "—"
    dom = row.domain or domain_of(row.email) or ""
    return {
        "prospect_key": make_prospect_key(org, row.email, dom),
        "organization_name": org,
        "contact_name": row.contact_name or None,
        "email": row.email,
        "domain": dom,
        "product_angle": row.product_angle or None,
        "region": row.region or None,
        "classification": CLASSIFICATION_LEGACY,
        "status": STATUS_REVIEW_LEGACY,
        "source_type": SOURCE_TYPE_LEGACY,
        "dataset_label": DATASET_LABEL_LEGACY,
        "block_or_review_reason": row.safety_reason,
        "recommended_next_action": DEFAULT_SUGGESTED_ACTION,
        "spanish_message_angle": row.product_angle or "Base antigua 2016–2019 — revisión manual",
        "is_blocked": 0,
        "final_score": 0,
        "input_priority_score": 0,
        "confidence": row.confidence,
        "source": "legacy_2016_2019_import",
        "campaign_bucket": infer_campaign_bucket(CLASSIFICATION_LEGACY, "", ""),
    }


def _resolve_legacy_batch_id(conn: sqlite3.Connection, now: str) -> int:
    existing_batch = conn.execute(
        "SELECT id FROM lead_research_batch WHERE batch_key = ?", (BATCH_KEY_LEGACY,)
    ).fetchone()
    if existing_batch:
        return int(existing_batch[0])
    batch_row = conn.execute(
        """
        INSERT INTO lead_research_batch
          (batch_key, source_name, generated_at, input_file_name, row_count, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            BATCH_KEY_LEGACY,
            SOURCE_NAME_LEGACY,
            now,
            DATASET_LABEL_LEGACY,
            0,
            now,
        ),
    )
    return int(batch_row.lastrowid)


def _insert_legacy_lead_research_prospect(
    conn: sqlite3.Connection,
    batch_id: int,
    payload: dict[str, Any],
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO lead_research_prospect (
          batch_id, prospect_key, organization_name, contact_name, email, domain,
          product_angle, region, classification, status, source_type, dataset_label,
          block_or_review_reason, recommended_next_action, spanish_message_angle,
          is_blocked, final_score, input_priority_score, confidence, source,
          campaign_bucket, is_active, created_at
        ) VALUES (
          ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
        )
        """,
        (
            batch_id,
            payload["prospect_key"],
            payload["organization_name"],
            payload.get("contact_name"),
            payload["email"],
            payload.get("domain"),
            payload.get("product_angle"),
            payload.get("region"),
            payload["classification"],
            payload["status"],
            payload["source_type"],
            payload["dataset_label"],
            payload["block_or_review_reason"],
            payload["recommended_next_action"],
            payload["spanish_message_angle"],
            payload["is_blocked"],
            payload["final_score"],
            payload["input_priority_score"],
            payload["confidence"],
            payload["source"],
            payload["campaign_bucket"],
            1,
            created_at,
        ),
    )


def merge_legacy_possible_buyers_to_lead_research(
    conn: sqlite3.Connection,
    rows: list[LegacyNormalizedRow],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Insert review-only legacy rows; skips existing active prospect keys."""
    ensure_lead_research_tables(conn)
    existing = {
        str(r[0])
        for r in conn.execute(
            "SELECT prospect_key FROM lead_research_prospect WHERE is_active = 1"
        )
    }
    to_insert: list[dict[str, Any]] = []
    skipped = 0
    for row in rows:
        payload = legacy_row_to_lead_research_payload(row)
        if not payload:
            continue
        if payload["prospect_key"] in existing:
            skipped += 1
            continue
        to_insert.append(payload)
    inserted = 0
    if not dry_run and to_insert:
        now = _utc_now()
        batch_id = _resolve_legacy_batch_id(conn, now)
        for payload in to_insert:
            _insert_legacy_lead_research_prospect(conn, batch_id, payload, now)
            inserted += 1
        conn.commit()
    return {
        "candidates": len(to_insert),
        "inserted": inserted,
        "skipped_existing": skipped,
        "dry_run": dry_run,
        "classification": CLASSIFICATION_LEGACY,
        "never_net_new_safe": True,
    }
