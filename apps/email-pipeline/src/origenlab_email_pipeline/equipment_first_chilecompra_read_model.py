"""Publish ChileCompra equipment rows directly to the typed Postgres read model.

CSV remains an export/audit artifact. This module lets the operator auto-refresh
path pass the already-built ChileCompra rows straight into the typed read-model
loader instead of relying on a later CSV reopen by the dashboard mirror.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.equipment_first_chilecompra_publish import (
    _prepare_published_dashboard_rows,
    default_canonical_operator_queue_path,
)
from origenlab_email_pipeline.equipment_opportunity_mirror import date_suffix_from_path
from origenlab_email_pipeline.equipment_opportunity_read_model_loader import (
    DEFAULT_DIRECT_CANONICAL_REASON,
    apply_direct_rows_load,
    preview_direct_rows_load,
)

_DIRECT_SYNC_SUMMARY_KEYS: tuple[str, ...] = (
    "applied",
    "source_input",
    "source_key",
    "source_id",
    "rows_inserted",
    "row_count",
    "source_kind",
    "artifact_basename",
    "canonical_reason",
    "replaced_source",
    "is_canonical",
    "error",
    "warning",
)


def _load_manifest_payload(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _campaign_mode_from_manifest(payload: dict[str, Any]) -> str | None:
    value = payload.get("campaign_mode")
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _source_key_from_export_path(path: Path) -> str:
    # The DB table still stores this value in csv_path for compatibility; source_kind
    # tells clients the write input was typed rows, not a CSV SQL loader.
    return str(path)


def _date_suffix_for_source(path: Path, now: datetime | None) -> str:
    return date_suffix_from_path(path) or (now or datetime.now(timezone.utc)).strftime("%Y%m%d")


def _default_export_path(now: datetime | None) -> Path:
    return default_canonical_operator_queue_path(Path("."), now=now)


def _decorate_summary(
    summary: dict[str, Any],
    *,
    source_rows: list[dict[str, str]],
    published_rows: list[dict[str, str]],
    publish_stats: dict[str, int],
) -> dict[str, Any]:
    decorated = dict(summary)
    decorated.update(
        {
            "source_rows": len(source_rows),
            "output_rows": len(published_rows),
            "coalesced_duplicate_rows": publish_stats["coalesced_duplicate_rows"],
            "unique_codigo_count": publish_stats["unique_codigo_count"],
        }
    )
    return decorated


def preview_chilecompra_equipment_read_model(
    source_rows: list[dict[str, str]],
    *,
    source_manifest: Path | None = None,
    export_csv_path: Path | None = None,
    now: datetime | None = None,
    pg_url: str | None = None,
    replace_source: bool = True,
) -> dict[str, Any]:
    published_rows, stats = _prepare_published_dashboard_rows(source_rows)
    export_path = export_csv_path or _default_export_path(now)
    manifest = _load_manifest_payload(source_manifest)
    summary = preview_direct_rows_load(
        published_rows,
        source_key=_source_key_from_export_path(export_path),
        manifest_path=str(source_manifest or ""),
        date_suffix=_date_suffix_for_source(export_path, now),
        campaign_mode=_campaign_mode_from_manifest(manifest) or "equipment_first",
        artifact_basename=export_path.name,
        canonical_reason=DEFAULT_DIRECT_CANONICAL_REASON,
        pg_url=pg_url,
        replace_source=replace_source,
    )
    return _decorate_summary(
        summary,
        source_rows=source_rows,
        published_rows=published_rows,
        publish_stats=stats,
    )


def apply_chilecompra_equipment_read_model(
    pg_url: str,
    source_rows: list[dict[str, str]],
    *,
    source_manifest: Path | None = None,
    export_csv_path: Path | None = None,
    now: datetime | None = None,
    updated_by: str,
    reason: str,
    sync_run_id: int | None = None,
    replace_source: bool = True,
) -> dict[str, Any]:
    published_rows, stats = _prepare_published_dashboard_rows(source_rows)
    export_path = export_csv_path or _default_export_path(now)
    manifest = _load_manifest_payload(source_manifest)
    summary = apply_direct_rows_load(
        pg_url,
        published_rows,
        source_key=_source_key_from_export_path(export_path),
        manifest_path=str(source_manifest or ""),
        date_suffix=_date_suffix_for_source(export_path, now),
        campaign_mode=_campaign_mode_from_manifest(manifest) or "equipment_first",
        artifact_basename=export_path.name,
        canonical_reason=DEFAULT_DIRECT_CANONICAL_REASON,
        updated_by=updated_by,
        reason=reason,
        sync_run_id=sync_run_id,
        replace_source=replace_source,
    )
    return _decorate_summary(
        summary,
        source_rows=source_rows,
        published_rows=published_rows,
        publish_stats=stats,
    )


def compact_read_model_sync_summary(summary: dict[str, Any]) -> dict[str, Any]:
    compact = {key: summary[key] for key in _DIRECT_SYNC_SUMMARY_KEYS if key in summary}
    compact["recorded_at_utc"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return compact


def record_chilecompra_read_model_sync(
    active_current: Path,
    summary: dict[str, Any],
) -> dict[str, Any]:
    """Attach direct read-model sync metadata to active/current manifest."""
    manifest_path = active_current / "manifest.json"
    manifest: dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {}
    publish_meta = manifest.setdefault("chilecompra_api_publish", {})
    if not isinstance(publish_meta, dict):
        publish_meta = {}
        manifest["chilecompra_api_publish"] = publish_meta
    publish_meta["read_model_sync"] = compact_read_model_sync_summary(summary)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"manifest_path": str(manifest_path), "read_model_sync": publish_meta["read_model_sync"]}
