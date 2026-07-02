"""Load typed equipment opportunity rows into the Postgres read model.

This is the bridge-retirement path for equipment opportunities: callers can pass the
already-normalized dashboard/read-model rows directly instead of asking the loader to
re-open the exported CSV. CSV can remain an audit/export artifact while the typed
rows become the writer input.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.equipment_opportunity_mirror import (
    LOADER_VERSION,
    _insert_opportunity_rows,
    _require_psycopg,
    _set_canonical_for_source,
    find_duplicate_codigos,
    lookup_existing_source_id,
    normalize_postgres_url,
    psycopg,
)

DIRECT_LOADER_VERSION = f"{LOADER_VERSION}+direct_rows_v1"
DEFAULT_DIRECT_SOURCE_KIND = "typed_read_model"
DEFAULT_DIRECT_CANONICAL_REASON = "chilecompra_api_direct_rows"
_SOURCE_ALREADY_LOADED_HINT = "Use replace_source=True to refresh this typed source safely."


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_str(value: object | None) -> str:
    return str(value or "").strip()


def _source_basename(source_key: str) -> str:
    text = _safe_str(source_key)
    if not text:
        return "typed_equipment_read_model"
    return Path(text).name or text


def build_direct_rows_summary(
    *,
    rows: list[dict[str, str]],
    source_key: str,
    manifest_path: str = "",
    date_suffix: str = "direct",
    campaign_mode: str | None = None,
    source_kind: str = DEFAULT_DIRECT_SOURCE_KIND,
    artifact_basename: str | None = None,
    canonical_reason: str = DEFAULT_DIRECT_CANONICAL_REASON,
    dry_run: bool = True,
) -> dict[str, Any]:
    basename = _safe_str(artifact_basename) or _source_basename(source_key)
    return {
        "dry_run": dry_run,
        "applied": False,
        "source_input": "typed_rows",
        "source_key": _safe_str(source_key),
        # Legacy table compatibility: commercial.equipment_opportunity_source still
        # stores the source key in csv_path until the DB schema is renamed.
        "csv_path": _safe_str(source_key),
        "manifest_path": _safe_str(manifest_path),
        "date_suffix": _safe_str(date_suffix) or "direct",
        "file_sha256": None,
        "file_mtime": None,
        "row_count": len(rows),
        "campaign_mode": campaign_mode,
        "duplicate_codigos": find_duplicate_codigos(rows),
        "source_kind": _safe_str(source_kind) or DEFAULT_DIRECT_SOURCE_KIND,
        "artifact_basename": basename,
        "canonical_reason": _safe_str(canonical_reason) or DEFAULT_DIRECT_CANONICAL_REASON,
    }


def preview_direct_rows_load(
    rows: list[dict[str, str]],
    *,
    source_key: str,
    manifest_path: str = "",
    date_suffix: str = "direct",
    campaign_mode: str | None = None,
    source_kind: str = DEFAULT_DIRECT_SOURCE_KIND,
    artifact_basename: str | None = None,
    canonical_reason: str = DEFAULT_DIRECT_CANONICAL_REASON,
    pg_url: str | None = None,
    replace_source: bool = True,
) -> dict[str, Any]:
    summary = build_direct_rows_summary(
        rows=rows,
        source_key=source_key,
        manifest_path=manifest_path,
        date_suffix=date_suffix,
        campaign_mode=campaign_mode,
        source_kind=source_kind,
        artifact_basename=artifact_basename,
        canonical_reason=canonical_reason,
        dry_run=True,
    )
    summary["sample_rows"] = [
        (row.get("codigo_licitacion") or "").strip()
        for row in rows[:3]
        if (row.get("codigo_licitacion") or "").strip()
    ]

    existing_source_id: int | None = None
    if pg_url:
        _require_psycopg()
        assert psycopg is not None
        with psycopg.connect(normalize_postgres_url(pg_url)) as conn:
            with conn.cursor() as cur:
                existing_source_id = lookup_existing_source_id(cur, summary["csv_path"])

    if existing_source_id is None:
        summary.update(
            {
                "existing_source_id": None,
                "would_fail_without_replace": False,
                "would_replace_source": False,
                "would_insert_source": True,
            }
        )
    else:
        summary.update(
            {
                "existing_source_id": existing_source_id,
                "would_fail_without_replace": not replace_source,
                "would_replace_source": replace_source,
                "would_insert_source": False,
            }
        )
    return summary


def apply_direct_rows_load(
    pg_url: str,
    rows: list[dict[str, str]],
    *,
    source_key: str,
    manifest_path: str = "",
    date_suffix: str = "direct",
    campaign_mode: str | None = None,
    updated_by: str,
    reason: str,
    sync_run_id: int | None = None,
    replace_source: bool = True,
    source_kind: str = DEFAULT_DIRECT_SOURCE_KIND,
    artifact_basename: str | None = None,
    canonical_reason: str = DEFAULT_DIRECT_CANONICAL_REASON,
) -> dict[str, Any]:
    """Apply typed rows to commercial.equipment_opportunity*.

    This writes only Postgres read-model tables. It does not touch Gmail, SQLite,
    sends, drafts, archives, NDR handling, or ChileCompra network calls.
    """
    _require_psycopg()
    assert psycopg is not None

    summary = build_direct_rows_summary(
        rows=rows,
        source_key=source_key,
        manifest_path=manifest_path,
        date_suffix=date_suffix,
        campaign_mode=campaign_mode,
        source_kind=source_kind,
        artifact_basename=artifact_basename,
        canonical_reason=canonical_reason,
        dry_run=False,
    )
    summary.update(
        {
            "updated_by": updated_by,
            "reason": reason,
            "sync_run_id": sync_run_id,
            "source_id": None,
            "rows_inserted": 0,
            "is_canonical": False,
            "replaced_source": False,
        }
    )

    if summary["duplicate_codigos"]:
        summary["error"] = "duplicate_codigo_licitacion_in_rows"
        return summary
    if not rows:
        summary["warning"] = "empty_rows"
        return summary

    pg_url_norm = normalize_postgres_url(pg_url)
    source_key_value = summary["csv_path"]
    now = _utc_now()

    with psycopg.connect(pg_url_norm, autocommit=False) as conn:
        with conn.cursor() as cur:
            existing_source_id = lookup_existing_source_id(cur, source_key_value)
            if existing_source_id is not None and not replace_source:
                summary["existing_source_id"] = existing_source_id
                summary["source_id"] = existing_source_id
                summary["error"] = "source_already_loaded"
                summary["hint"] = _SOURCE_ALREADY_LOADED_HINT
                return summary

            if existing_source_id is not None:
                source_id = existing_source_id
                summary["replaced_source"] = True
                cur.execute(
                    """
                    UPDATE commercial.equipment_opportunity_source
                    SET manifest_path = %s,
                        date_suffix = %s,
                        campaign_mode = %s,
                        row_count = %s,
                        file_sha256 = NULL,
                        file_mtime = %s,
                        synced_at = now(),
                        sync_run_id = %s,
                        loader_version = %s,
                        is_canonical = TRUE,
                        source_kind = %s,
                        artifact_basename = %s,
                        canonical_reason = %s
                    WHERE id = %s
                    """,
                    (
                        summary["manifest_path"],
                        summary["date_suffix"],
                        campaign_mode,
                        len(rows),
                        now,
                        sync_run_id,
                        DIRECT_LOADER_VERSION,
                        summary["source_kind"],
                        summary["artifact_basename"],
                        summary["canonical_reason"],
                        source_id,
                    ),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO commercial.equipment_opportunity_source (
                      manifest_path, csv_path, date_suffix, campaign_mode, row_count,
                      file_sha256, file_mtime, is_canonical, sync_run_id, loader_version,
                      source_kind, artifact_basename, canonical_reason
                    ) VALUES (%s, %s, %s, %s, %s, NULL, %s, TRUE, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        summary["manifest_path"],
                        source_key_value,
                        summary["date_suffix"],
                        campaign_mode,
                        len(rows),
                        now,
                        sync_run_id,
                        DIRECT_LOADER_VERSION,
                        summary["source_kind"],
                        summary["artifact_basename"],
                        summary["canonical_reason"],
                    ),
                )
                source_id = int(cur.fetchone()[0])

            summary["source_id"] = source_id
            _set_canonical_for_source(cur, date_suffix=summary["date_suffix"], source_id=source_id)
            summary["is_canonical"] = True

            cur.execute(
                "DELETE FROM commercial.equipment_opportunity WHERE source_id = %s",
                (source_id,),
            )
            inserted = _insert_opportunity_rows(cur, source_id=source_id, rows=rows)
            cur.execute(
                """
                UPDATE commercial.equipment_opportunity_source
                SET row_count = %s
                WHERE id = %s
                """,
                (inserted, source_id),
            )
            summary["rows_inserted"] = inserted
            summary["row_count"] = inserted
            summary["applied"] = True

        conn.commit()

    return summary
