"""Publish ChileCompra API equipment queue CSV for dashboard/API consumption."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from collections import defaultdict

from origenlab_email_pipeline.chilecompra_api import (
    VALIDITY_STATUS_CLOSES_TODAY,
    VALIDITY_STATUS_MISSING_CLOSE_DATE,
    VALIDITY_STATUS_OPEN,
)
from origenlab_email_pipeline.equipment_first_licitacion_queue import (
    detect_equipment_categories,
    equipment_detection_blob,
    parse_close_date,
)
from origenlab_email_pipeline.equipment_first_operator_queue import OPERATOR_FIELDS

CHILECOMPRA_REVIEW_NOTE = (
    "ChileCompra API candidate; revisar bases, fechas, proveedor y margen antes de actuar."
)
CHILECOMPRA_SAFE_CHANNEL = "mercado_publico_only"
CHILECOMPRA_CONTACT_STATUS = "review_required"
CHILECOMPRA_CONTACT_STATUS_MISSING_CLOSE_DATE = "review_required_missing_close_date"

MERCADO_PUBLICO_SEARCH_URL_TEMPLATE = (
    "https://www.mercadopublico.cl/BuscarLicitacion?IsFirstTableDesign=true&codigoLicitacion={codigo}"
)

CHILECOMPRA_ITEM_METADATA_FIELDS: tuple[str, ...] = (
    "fecha_publicacion",
    "descripcion",
    "line_description",
    "unspsc_code",
    "unidad",
    "cantidad",
    "producto",
    "nivel_1",
    "nivel_2",
    "nivel_3",
    "anexos_json",
)

DASHBOARD_ACTIVE_VALIDITY_STATUSES = frozenset(
    {
        VALIDITY_STATUS_OPEN,
        VALIDITY_STATUS_CLOSES_TODAY,
        VALIDITY_STATUS_MISSING_CLOSE_DATE,
    }
)

NEXT_ACTION_SORT_ORDER: dict[str, int] = {
    "quote_now": 0,
    "needs_supplier_quote": 1,
    "contact_after_close": 2,
    "account_intelligence_only": 3,
    "skip_consumables": 4,
    "skip_maintenance_service": 5,
}

PUBLISHED_DASHBOARD_FIELDS: tuple[str, ...] = (
    *OPERATOR_FIELDS,
    "title",
    "fit_score",
    "reason",
    "contact_email",
    "chilecompra_status_code",
    "chilecompra_status",
    "validity_status",
    "api_checked_at_utc",
    "source",
    *CHILECOMPRA_ITEM_METADATA_FIELDS,
    "mercado_publico_url",
)

_CHILECOMPRA_API_QUEUE_PREFIX = "equipment_first_operator_queue_chilecompra_api_"


def default_canonical_operator_queue_path(
    reports_dir: Path,
    *,
    now: datetime | None = None,
) -> Path:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d")
    return (
        reports_dir
        / "active"
        / "current"
        / f"equipment_first_operator_queue_{stamp}.csv"
    )


def load_chilecompra_source_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f"ChileCompra source CSV missing: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _parse_fit_score(value: str | None) -> int:
    try:
        return int(str(value or "").strip())
    except ValueError:
        return 0


def _supplier_needed_value(next_action: str) -> str:
    return "yes" if next_action in ("quote_now", "needs_supplier_quote") else "no"


def _row_rank_key(row: dict[str, str]) -> tuple[int, int, float]:
    next_action = (row.get("next_action") or "").strip()
    close_dt = parse_close_date(row.get("close_date") or "")
    close_sort = close_dt.timestamp() if close_dt is not None else float("inf")
    return (
        -_parse_fit_score(row.get("fit_score")),
        NEXT_ACTION_SORT_ORDER.get(next_action, 99),
        close_sort,
    )


def _best_next_action(rows: list[dict[str, str]]) -> str:
    actions = [
        (row.get("next_action") or "").strip()
        for row in rows
        if (row.get("next_action") or "").strip()
    ]
    if not actions:
        return ""
    return min(actions, key=lambda action: NEXT_ACTION_SORT_ORDER.get(action, 99))


def _unique_join(values: list[str], separator: str, *, max_len: int) -> str:
    seen: list[str] = []
    for value in values:
        text = (value or "").strip()
        if text and text not in seen:
            seen.append(text)
    return separator.join(seen)[:max_len]


def build_mercado_publico_search_url(codigo_licitacion: str) -> str:
    """Public Mercado Público search URL by licitación code (no API ticket)."""
    codigo = (codigo_licitacion or "").strip()
    if not codigo:
        return ""
    return MERCADO_PUBLICO_SEARCH_URL_TEMPLATE.format(codigo=quote(codigo, safe=""))


def _merge_cantidad(values: list[str], *, max_len: int = 80) -> str:
    numeric: list[float] = []
    text_values: list[str] = []
    for value in values:
        text = (value or "").strip()
        if not text:
            continue
        try:
            numeric.append(float(text.replace(",", ".")))
        except ValueError:
            text_values.append(text)
    if numeric:
        total = sum(numeric)
        if total == int(total):
            rendered = str(int(total))
        else:
            rendered = str(total)
        return rendered[:max_len]
    return _unique_join(text_values, "; ", max_len=max_len)


def _pick_first_non_empty(values: list[str]) -> str:
    for value in values:
        text = (value or "").strip()
        if text:
            return text
    return ""


def aggregate_chilecompra_item_metadata(
    normalized_rows: list[dict[str, str]],
) -> dict[str, dict[str, str]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in normalized_rows:
        codigo = (row.get("codigo") or row.get("codigo_licitacion") or "").strip()
        if codigo:
            grouped[codigo].append(row)

    aggregated: dict[str, dict[str, str]] = {}
    for codigo, rows in grouped.items():
        aggregated[codigo] = {
            "fecha_publicacion": _pick_first_non_empty(
                [item.get("fecha_publicacion", "") for item in rows]
            ),
            "descripcion": _pick_first_non_empty([item.get("descripcion", "") for item in rows])[:500],
            "line_description": _unique_join(
                [item.get("line_description", "") for item in rows],
                " || ",
                max_len=500,
            ),
            "unspsc_code": _unique_join(
                [item.get("unspsc_code", "") for item in rows],
                "; ",
                max_len=120,
            ),
            "unidad": _unique_join([item.get("unidad", "") for item in rows], "; ", max_len=80),
            "cantidad": _merge_cantidad([item.get("cantidad", "") for item in rows]),
            "producto": _unique_join([item.get("producto", "") for item in rows], "; ", max_len=200),
            "nivel_1": _unique_join([item.get("nivel_1", "") for item in rows], "; ", max_len=200),
            "nivel_2": _unique_join([item.get("nivel_2", "") for item in rows], "; ", max_len=200),
            "nivel_3": _unique_join([item.get("nivel_3", "") for item in rows], "; ", max_len=200),
            "mercado_publico_url": build_mercado_publico_search_url(codigo),
        }
    return aggregated


def aggregate_chilecompra_item_metadata_by_category(
    normalized_rows: list[dict[str, str]],
) -> dict[tuple[str, str], dict[str, str]]:
    """Aggregate only item lines that independently support each category."""
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)

    for row in normalized_rows:
        codigo = (
            row.get("codigo")
            or row.get("codigo_licitacion")
            or ""
        ).strip()
        if not codigo:
            continue

        categories = {
            category
            for category, _matched_span in detect_equipment_categories(
                equipment_detection_blob(row)
            )
        }
        for category in sorted(categories):
            grouped[(codigo, category)].append(row)

    aggregated: dict[tuple[str, str], dict[str, str]] = {}
    for key, rows in sorted(grouped.items()):
        codigo, _category = key
        metadata = aggregate_chilecompra_item_metadata(rows)
        aggregated[key] = metadata[codigo]

    return aggregated


def attach_item_metadata_to_queue_rows(
    queue_rows: list[dict[str, str]],
    normalized_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    metadata_by_codigo = aggregate_chilecompra_item_metadata(normalized_rows)
    metadata_by_category = aggregate_chilecompra_item_metadata_by_category(
        normalized_rows
    )
    attached: list[dict[str, str]] = []

    for row in queue_rows:
        merged = dict(row)
        codigo = (row.get("codigo_licitacion") or "").strip()
        category = (row.get("equipment_category") or "").strip()

        category_meta = metadata_by_category.get((codigo, category))
        if category_meta is not None:
            meta = category_meta
        else:
            # Preserve descriptive tender metadata, but do not attribute the
            # tender-wide quantity to a category without item-level evidence.
            meta = dict(metadata_by_codigo.get(codigo, {}))
            meta["cantidad"] = ""

        for field in CHILECOMPRA_ITEM_METADATA_FIELDS:
            value = (meta.get(field) or "").strip()
            if value:
                merged[field] = value
            elif field == "cantidad":
                merged[field] = ""

        if not (merged.get("mercado_publico_url") or "").strip():
            merged["mercado_publico_url"] = build_mercado_publico_search_url(codigo)

        attached.append(merged)

    return attached


def coalesce_dashboard_rows_by_codigo(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Merge multiple dashboard rows for the same codigo_licitacion into one row."""
    grouped: dict[str, list[dict[str, str]]] = {}
    uncoded_rows: list[dict[str, str]] = []
    for row in rows:
        codigo = (row.get("codigo_licitacion") or "").strip()
        if not codigo:
            uncoded_rows.append(dict(row))
            continue
        grouped.setdefault(codigo, []).append(dict(row))

    coalesced: list[dict[str, str]] = []
    for codigo in sorted(grouped):
        group = grouped[codigo]
        if len(group) == 1:
            coalesced.append(group[0])
            continue
        base = min(group, key=_row_rank_key)
        merged = dict(base)
        merged["equipment_category"] = _unique_join(
            [row.get("equipment_category", "") for row in group],
            "; ",
            max_len=200,
        )
        merged["item_description"] = _unique_join(
            [row.get("item_description", "") for row in group],
            " || ",
            max_len=500,
        )
        merged["reason"] = _unique_join(
            [row.get("reason", "") for row in group],
            "; ",
            max_len=400,
        )
        merged["operator_note"] = _unique_join(
            [row.get("operator_note", "") for row in group],
            " | ",
            max_len=400,
        )
        merged["fit_score"] = str(max(_parse_fit_score(row.get("fit_score")) for row in group))
        merged["next_action"] = _best_next_action(group)
        merged["fecha_publicacion"] = _pick_first_non_empty(
            [row.get("fecha_publicacion", "") for row in group]
        )
        merged["descripcion"] = _pick_first_non_empty([row.get("descripcion", "") for row in group])[:500]
        merged["line_description"] = _unique_join(
            [row.get("line_description", "") for row in group],
            " || ",
            max_len=500,
        )
        for field in ("unspsc_code", "producto", "nivel_1", "nivel_2", "nivel_3", "unidad"):
            merged[field] = _unique_join(
                [row.get(field, "") for row in group],
                "; ",
                max_len=200 if field.startswith("nivel") else 120,
            )
        merged["cantidad"] = _merge_cantidad([row.get("cantidad", "") for row in group])
        merged["mercado_publico_url"] = _pick_first_non_empty(
            [row.get("mercado_publico_url", "") for row in group]
        ) or build_mercado_publico_search_url(codigo)
        merged["anexos_json"] = _pick_first_non_empty(
            [row.get("anexos_json", "") for row in group]
        )
        supplier_needed = any((row.get("supplier_needed") or "").strip().lower() == "yes" for row in group)
        merged["supplier_needed"] = "yes" if supplier_needed else "no"
        merged["supplier_contact"] = "yes" if supplier_needed else ""
        coalesced.append(merged)

    return coalesced + uncoded_rows


def _contact_status_for_validity(validity_status: str) -> str:
    if validity_status == VALIDITY_STATUS_MISSING_CLOSE_DATE:
        return CHILECOMPRA_CONTACT_STATUS_MISSING_CLOSE_DATE
    return CHILECOMPRA_CONTACT_STATUS


def is_dashboard_active_chilecompra_row(row: dict[str, str]) -> bool:
    validity_status = (row.get("validity_status") or "").strip()
    return validity_status in DASHBOARD_ACTIVE_VALIDITY_STATUSES


def enrich_chilecompra_row_for_dashboard(row: dict[str, str]) -> dict[str, str]:
    """Add dashboard/operator enrichment columns without inventing buyer emails."""
    next_action = (row.get("next_action") or "").strip()
    supplier_needed = _supplier_needed_value(next_action)
    validity_status = (row.get("validity_status") or "").strip()
    contact_status = _contact_status_for_validity(validity_status)
    note_parts = [
        CHILECOMPRA_REVIEW_NOTE,
        (row.get("reason") or "").strip(),
        f"fit_score={row.get('fit_score', '').strip()}",
    ]
    if validity_status == VALIDITY_STATUS_MISSING_CLOSE_DATE:
        note_parts.append("missing_close_date; revisar fecha de cierre en Mercado Público")
    codigo = (row.get("codigo_licitacion") or "").strip()
    enriched = {
        "priority_rank": (row.get("priority_rank") or "").strip(),
        "codigo_licitacion": codigo,
        "buyer": (row.get("buyer") or "").strip(),
        "region": (row.get("region") or "").strip(),
        "close_date": (row.get("close_date") or "").strip(),
        "equipment_category": (row.get("equipment_category") or "").strip(),
        "item_description": (row.get("item_description") or "")[:500],
        "next_action": next_action,
        "contact_status": contact_status,
        "safe_channel": CHILECOMPRA_SAFE_CHANNEL,
        "supplier_needed": supplier_needed,
        "supplier_contact": supplier_needed if supplier_needed == "yes" else "",
        "gmail_prior_thread": (row.get("gmail_prior_thread") or "none").strip() or "none",
        "outreach_state": (row.get("outreach_state") or "review_required").strip() or "review_required",
        "operator_note": " | ".join(part for part in note_parts if part)[:400],
        "title": (row.get("title") or "").strip(),
        "fit_score": str(row.get("fit_score") or "").strip(),
        "reason": (row.get("reason") or "").strip(),
        "contact_email": "",
        "chilecompra_status_code": (row.get("chilecompra_status_code") or "").strip(),
        "chilecompra_status": (row.get("chilecompra_status") or "").strip(),
        "validity_status": validity_status,
        "api_checked_at_utc": (row.get("api_checked_at_utc") or "").strip(),
        "source": (row.get("source") or "chilecompra_api").strip() or "chilecompra_api",
        "mercado_publico_url": (row.get("mercado_publico_url") or "").strip()
        or build_mercado_publico_search_url(codigo),
    }
    for field in CHILECOMPRA_ITEM_METADATA_FIELDS:
        enriched[field] = (row.get(field) or "").strip()
    return enriched


def sort_chilecompra_dashboard_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Sort by fit_score desc, next_action priority, close_date asc."""

    def _sort_key(row: dict[str, str]) -> tuple[int, int, int, float, str]:
        codigo = (row.get("codigo_licitacion") or "").strip()
        next_action = (row.get("next_action") or "").strip()
        close_dt = parse_close_date(row.get("close_date") or "")
        close_sort = close_dt.timestamp() if close_dt is not None else float("inf")
        return (
            1 if not codigo else 0,
            -_parse_fit_score(row.get("fit_score")),
            NEXT_ACTION_SORT_ORDER.get(next_action, 99),
            close_sort,
            codigo,
        )

    sorted_rows = sorted(rows, key=_sort_key)
    ranked: list[dict[str, str]] = []
    for index, row in enumerate(sorted_rows, start=1):
        updated = dict(row)
        updated["priority_rank"] = str(index)
        ranked.append(updated)
    return ranked


def _prepare_published_dashboard_rows(
    source_rows: list[dict[str, str]],
) -> tuple[list[dict[str, str]], dict[str, int]]:
    active_rows = [row for row in source_rows if is_dashboard_active_chilecompra_row(row)]
    enriched = [enrich_chilecompra_row_for_dashboard(row) for row in active_rows]
    coalesced = coalesce_dashboard_rows_by_codigo(enriched)
    published = sort_chilecompra_dashboard_rows(coalesced)
    unique_codigos = {
        (row.get("codigo_licitacion") or "").strip()
        for row in coalesced
        if (row.get("codigo_licitacion") or "").strip()
    }
    stats = {
        "coalesced_duplicate_rows": len(enriched) - len(coalesced),
        "unique_codigo_count": len(unique_codigos),
    }
    return published, stats


def publish_chilecompra_equipment_rows(
    source_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    published, _stats = _prepare_published_dashboard_rows(source_rows)
    return published


def write_published_dashboard_csv(rows: list[dict[str, str]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(PUBLISHED_DASHBOARD_FIELDS))
        writer.writeheader()
        writer.writerows(
            {field: row.get(field, "") for field in PUBLISHED_DASHBOARD_FIELDS} for row in rows
        )


def update_active_manifest_canonical_queue(
    active_current: Path,
    *,
    queue_filename: str,
    source_manifest: Path | None = None,
) -> dict[str, Any]:
    """Prepend published queue to manifest canonical_files when manifest already exists."""
    manifest_path = active_current / "manifest.json"
    manifest: dict[str, Any] = {}
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    existing = [str(item).strip() for item in (manifest.get("canonical_files") or []) if str(item).strip()]
    filtered = [
        name
        for name in existing
        if name != queue_filename and not name.startswith(_CHILECOMPRA_API_QUEUE_PREFIX)
    ]
    manifest["canonical_files"] = [queue_filename, *filtered]
    manifest["campaign_mode"] = manifest.get("campaign_mode") or "equipment_first"

    publish_meta: dict[str, Any] = {
        "published_queue": queue_filename,
        "published_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    if source_manifest is not None and source_manifest.is_file():
        try:
            source_payload = json.loads(source_manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            source_payload = {}
        publish_meta["source_manifest"] = source_manifest.name
        publish_meta["source_output_rows"] = source_payload.get("output_rows")
        publish_meta["source_detail_error_count"] = source_payload.get("detail_error_count")
    manifest["chilecompra_api_publish"] = publish_meta

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "manifest_path": str(manifest_path),
        "canonical_files": manifest["canonical_files"],
        "campaign_mode": manifest["campaign_mode"],
        "chilecompra_api_publish": publish_meta,
    }


def publish_chilecompra_equipment_queue_for_dashboard(
    *,
    source_csv: Path,
    out_csv: Path,
    source_manifest: Path | None = None,
    update_manifest: bool = False,
    active_current: Path | None = None,
) -> dict[str, Any]:
    source_rows = load_chilecompra_source_rows(source_csv)
    active_input_rows = [row for row in source_rows if is_dashboard_active_chilecompra_row(row)]
    published_rows, publish_stats = _prepare_published_dashboard_rows(source_rows)
    write_published_dashboard_csv(published_rows, out_csv)

    result: dict[str, Any] = {
        "source_csv": str(source_csv),
        "out_csv": str(out_csv),
        "input_rows": len(source_rows),
        "active_input_rows": len(active_input_rows),
        "output_rows": len(published_rows),
        "excluded_rows": len(source_rows) - len(active_input_rows),
        "coalesced_duplicate_rows": publish_stats["coalesced_duplicate_rows"],
        "unique_codigo_count": publish_stats["unique_codigo_count"],
        "manifest_updated": False,
    }
    if update_manifest:
        target_active = active_current or out_csv.parent
        manifest_result = update_active_manifest_canonical_queue(
            target_active,
            queue_filename=out_csv.name,
            source_manifest=source_manifest,
        )
        result["manifest_updated"] = True
        result.update(manifest_result)
    return result
