"""Load equipment operator rows into Postgres commercial.* tables (DB-2A).

The public entrypoints keep accepting the canonical active/current queue CSV for
compatibility, but the default write path now converts that export into typed rows
and delegates to ``equipment_opportunity_read_model_loader``. The legacy CSV SQL
loader remains available with ``use_direct_rows=False`` as a rollback path.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from origenlab_email_pipeline.active_current_manifest import (
    load_manifest,
    resolve_equipment_operator_queue_csv,
)
from origenlab_email_pipeline.equipment_first_operator_queue import OPERATOR_FIELDS, _load_csv
from origenlab_email_pipeline.mart_core_postgres_migrate import normalize_postgres_url

try:
    import psycopg
    from psycopg.types.json import Json
except ImportError as exc:  # pragma: no cover
    psycopg = None  # type: ignore[misc, assignment]
    Json = None  # type: ignore[misc, assignment]
    _PSYCOPG_IMPORT_ERROR = exc
else:
    _PSYCOPG_IMPORT_ERROR = None

LOADER_VERSION = "db2_equipment_loader_v1"
_SOURCE_ALREADY_LOADED_HINT = "Use --replace-source to reload this source safely."
_DATE_SUFFIX_RE = re.compile(r"_(\d{8})\.csv$", re.IGNORECASE)
_SANTIAGO = ZoneInfo("America/Santiago")

_FORBIDDEN_PATH_FRAGMENT = "buyer_opportunity_crosscheck"

_EXTRA_OPERATOR_KEYS = frozenset({"supplier_contact", "gmail_prior_thread", "outreach_state"})
_DEFAULT_SOURCE_SLUG = "equipment_queue"
_SOURCE_SLUG_RE = re.compile(r"[^a-z0-9_:-]+")
_SOURCE_SLUG_TRIM_RE = re.compile(r"^_+|_+$")


def _require_psycopg() -> None:
    if psycopg is None:
        raise RuntimeError(
            f"psycopg is required (uv sync --group postgres). ({_PSYCOPG_IMPORT_ERROR})"
        )


def assert_queue_path_allowed(path: Path) -> None:
    if _FORBIDDEN_PATH_FRAGMENT in path.name.lower():
        raise ValueError(f"forbidden queue path (stale crosscheck artifact): {path}")


def date_suffix_from_path(path: Path) -> str | None:
    match = _DATE_SUFFIX_RE.search(path.name)
    return match.group(1) if match else None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_mtime_utc(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def parse_close_at(close_date: str | None) -> datetime | None:
    """Parse Chilean and ISO close_date strings to TIMESTAMPTZ (America/Santiago)."""
    text = (close_date or "").strip()
    if not text:
        return None
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            naive = datetime.strptime(text, fmt)
            return naive.replace(tzinfo=_SANTIAGO)
        except ValueError:
            continue

    iso_candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_candidate)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
            try:
                parsed = datetime.strptime(text, fmt)
                return parsed.replace(tzinfo=_SANTIAGO)
            except ValueError:
                continue
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=_SANTIAGO)
    return parsed.astimezone(_SANTIAGO)


def parse_priority_rank(value: str | None) -> int | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def find_duplicate_codigos(rows: list[dict[str, str]]) -> list[str]:
    seen: dict[str, int] = {}
    dupes: list[str] = []
    for row in rows:
        codigo = (row.get("codigo_licitacion") or "").strip()
        if not codigo:
            continue
        seen[codigo] = seen.get(codigo, 0) + 1
        if seen[codigo] == 2:
            dupes.append(codigo)
    return sorted(dupes)


def build_extra_json(row: dict[str, str]) -> dict[str, str]:
    extra: dict[str, str] = {}
    for key, value in row.items():
        text = (value or "").strip()
        if not text:
            continue
        if key in _EXTRA_OPERATOR_KEYS or key not in OPERATOR_FIELDS:
            extra[key] = text
    return extra


def normalize_source_slug(raw: str | None) -> str:
    text = (raw or "").strip().lower()
    if not text:
        return _DEFAULT_SOURCE_SLUG
    slug = _SOURCE_SLUG_RE.sub("_", text)
    slug = _SOURCE_SLUG_TRIM_RE.sub("", slug)
    return slug or _DEFAULT_SOURCE_SLUG


def derive_opportunity_key(row: dict[str, str]) -> str:
    """Stable business/correlation key: equipment:<source_slug>:<codigo_lower>."""
    codigo = (row.get("codigo_licitacion") or "").strip()
    if not codigo:
        raise ValueError("codigo_licitacion required for opportunity_key")
    extra = build_extra_json(row)
    source_raw = (row.get("source") or extra.get("source") or "").strip()
    source_slug = normalize_source_slug(source_raw)
    return f"equipment:{source_slug}:{codigo.lower()}"


def row_to_opportunity_values(row: dict[str, str]) -> dict[str, Any]:
    close_date = (row.get("close_date") or "").strip() or None
    codigo = (row.get("codigo_licitacion") or "").strip()
    extra_json = build_extra_json(row)
    return {
        "priority_rank": parse_priority_rank(row.get("priority_rank")),
        "codigo_licitacion": codigo,
        "buyer": (row.get("buyer") or "").strip() or None,
        "region": (row.get("region") or "").strip() or None,
        "close_date": close_date,
        "close_at": parse_close_at(close_date),
        "equipment_category": (row.get("equipment_category") or "").strip() or None,
        "item_description": (row.get("item_description") or "").strip() or None,
        "next_action": (row.get("next_action") or "").strip() or None,
        "safe_channel": (row.get("safe_channel") or "").strip() or None,
        "supplier_needed": (row.get("supplier_needed") or "").strip() or None,
        "contact_status": (row.get("contact_status") or "").strip() or None,
        "operator_note": (row.get("operator_note") or "").strip() or None,
        "dnr_flags": (row.get("dnr_flags") or "").strip() or None,
        "extra_json": extra_json,
        "opportunity_key": derive_opportunity_key(row) if codigo else None,
    }


def _stale_paths(manifest: dict[str, Any]) -> set[str]:
    return {
        str(entry.get("path") or "").strip()
        for entry in (manifest.get("stale_files") or [])
        if entry.get("path")
    }


def is_manifest_canonical_queue(manifest: dict[str, Any], csv_path: Path, active_current: Path) -> bool:
    rel = csv_path.name
    if rel in _stale_paths(manifest):
        return False
    canonical = {str(p).strip() for p in (manifest.get("canonical_files") or [])}
    return rel in canonical


def should_promote_equipment_source_as_canonical(
    manifest: dict[str, Any], csv_path: Path, active_current: Path
) -> bool:
    """True when this CSV is the operator active/current equipment queue (dashboard read path).

    Matches manifest ``canonical_files`` or the same path ``resolve_equipment_operator_queue_csv``
    would pick (including newest ``equipment_first_operator_queue_*.csv`` when manifest is empty).
    """
    if csv_path.name in _stale_paths(manifest):
        return False
    if is_manifest_canonical_queue(manifest, csv_path, active_current):
        return True
    resolved = resolve_equipment_operator_queue_csv(active_current, manifest)
    return resolved is not None and resolved.resolve() == csv_path.resolve()


def derive_source_artifact_metadata(
    manifest: dict[str, Any],
    csv_path: Path,
    active_current: Path,
) -> dict[str, str | None]:
    """Semantic source metadata for commercial.equipment_opportunity_source (bridge CSV loads)."""
    canonical_reason: str | None = None
    if should_promote_equipment_source_as_canonical(manifest, csv_path, active_current):
        if is_manifest_canonical_queue(manifest, csv_path, active_current):
            canonical_reason = "manifest_canonical"
        else:
            canonical_reason = "resolved_active_current_queue"
    return {
        "source_kind": "csv_artifact",
        "artifact_basename": csv_path.name,
        "canonical_reason": canonical_reason,
    }


def _typed_rows_canonical_reason(artifact_reason: str | None) -> str:
    if artifact_reason:
        return f"typed_rows_from_{artifact_reason}"
    return "typed_rows_from_queue_export"


def _sync_direct_loader_module() -> Any:
    """Import direct loader after this module is initialized; keep monkeypatches in sync."""
    from origenlab_email_pipeline import equipment_opportunity_read_model_loader as direct_loader

    direct_loader.psycopg = psycopg
    return direct_loader


def _merge_export_metadata(
    summary: dict[str, Any],
    *,
    resolved: Path,
    manifest: dict[str, Any],
    active_current: Path,
) -> dict[str, Any]:
    summary = dict(summary)
    summary["csv_export_path"] = str(resolved)
    summary["legacy_csv_loader_used"] = False
    summary["file_sha256"] = file_sha256(resolved)
    summary["file_mtime"] = file_mtime_utc(resolved).isoformat()
    summary["is_manifest_canonical"] = is_manifest_canonical_queue(manifest, resolved, active_current)
    summary["should_promote_canonical"] = should_promote_equipment_source_as_canonical(
        manifest, resolved, active_current
    )
    return summary


def resolve_load_context(
    active_current: Path,
    *,
    csv_path: Path | None = None,
) -> tuple[Path, dict[str, Any], str, str]:
    active_current = active_current.resolve()
    manifest_path = active_current / "manifest.json"
    manifest = load_manifest(manifest_path) if manifest_path.is_file() else {}

    if csv_path is not None:
        resolved = csv_path.expanduser().resolve()
        assert_queue_path_allowed(resolved)
        if resolved.name in _stale_paths(manifest):
            raise ValueError(f"queue CSV is listed in manifest stale_files: {resolved.name}")
    else:
        resolved = resolve_equipment_operator_queue_csv(active_current, manifest)
        if resolved is None:
            raise FileNotFoundError(
                "canonical equipment_first_operator_queue_*.csv not found under active/current"
            )
        assert_queue_path_allowed(resolved)

    suffix = date_suffix_from_path(resolved)
    if not suffix:
        raise ValueError(f"could not parse date_suffix from filename: {resolved.name}")

    return resolved, manifest, str(manifest_path.resolve()), suffix


def lookup_existing_source_id(cur: Any, csv_path: str) -> int | None:
    cur.execute(
        """
        SELECT id FROM commercial.equipment_opportunity_source
        WHERE csv_path = %s
        LIMIT 1
        """,
        (csv_path,),
    )
    row = cur.fetchone()
    return int(row[0]) if row else None


def _base_summary_fields(
    *,
    resolved: Path,
    manifest: dict[str, Any],
    manifest_path: str,
    date_suffix: str,
    rows: list[dict[str, str]],
    dry_run: bool,
    active_current: Path,
) -> dict[str, Any]:
    campaign_mode = manifest.get("campaign_mode")
    if isinstance(campaign_mode, str):
        campaign_mode = campaign_mode.strip() or None
    else:
        campaign_mode = None
    artifact = derive_source_artifact_metadata(manifest, resolved, active_current)
    return {
        "dry_run": dry_run,
        "applied": False,
        "csv_path": str(resolved),
        "manifest_path": manifest_path,
        "date_suffix": date_suffix,
        "file_sha256": file_sha256(resolved),
        "file_mtime": file_mtime_utc(resolved).isoformat(),
        "row_count": len(rows),
        "campaign_mode": campaign_mode,
        "duplicate_codigos": find_duplicate_codigos(rows),
        **artifact,
    }


def _existing_source_preview_fields(
    existing_source_id: int | None,
    *,
    replace_source: bool,
) -> dict[str, Any]:
    if existing_source_id is None:
        return {
            "existing_source_id": None,
            "would_fail_without_replace": False,
            "would_replace_source": False,
            "would_insert_source": True,
        }
    return {
        "existing_source_id": existing_source_id,
        "would_fail_without_replace": not replace_source,
        "would_replace_source": replace_source,
        "would_insert_source": False,
    }


def preview_load(
    active_current: Path,
    *,
    csv_path: Path | None = None,
    pg_url: str | None = None,
    replace_source: bool = False,
    use_direct_rows: bool = True,
) -> dict[str, Any]:
    resolved, manifest, manifest_path, date_suffix = resolve_load_context(
        active_current, csv_path=csv_path
    )
    rows = _load_csv(resolved)
    if use_direct_rows:
        artifact = derive_source_artifact_metadata(manifest, resolved, active_current)
        direct_loader = _sync_direct_loader_module()
        summary = direct_loader.preview_direct_rows_load(
            rows,
            source_key=str(resolved),
            manifest_path=manifest_path,
            date_suffix=date_suffix,
            campaign_mode=(manifest.get("campaign_mode") if isinstance(manifest.get("campaign_mode"), str) else None),
            artifact_basename=artifact["artifact_basename"],
            canonical_reason=_typed_rows_canonical_reason(artifact["canonical_reason"]),
            pg_url=pg_url,
            replace_source=replace_source
            or should_promote_equipment_source_as_canonical(manifest, resolved, active_current),
        )
        return _merge_export_metadata(
            summary,
            resolved=resolved,
            manifest=manifest,
            active_current=active_current,
        )

    summary = _base_summary_fields(
        resolved=resolved,
        manifest=manifest,
        manifest_path=manifest_path,
        date_suffix=date_suffix,
        rows=rows,
        dry_run=True,
        active_current=active_current,
    )
    summary["is_manifest_canonical"] = is_manifest_canonical_queue(manifest, resolved, active_current)
    summary["should_promote_canonical"] = should_promote_equipment_source_as_canonical(
        manifest, resolved, active_current
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
                existing_source_id = lookup_existing_source_id(cur, str(resolved))

    summary.update(_existing_source_preview_fields(existing_source_id, replace_source=replace_source))
    return summary


def _set_canonical_for_source(cur: Any, *, date_suffix: str, source_id: int) -> None:
    """Mark one source canonical for ``api.v_equipment_opportunity``; clear all others."""
    del date_suffix  # kept for call-site stability; promotion is global per load
    cur.execute(
        """
        UPDATE commercial.equipment_opportunity_source
        SET is_canonical = FALSE
        WHERE id <> %s
        """,
        (source_id,),
    )
    cur.execute(
        """
        UPDATE commercial.equipment_opportunity_source
        SET is_canonical = TRUE
        WHERE id = %s
        """,
        (source_id,),
    )


def _insert_opportunity_rows(
    cur: Any,
    *,
    source_id: int,
    rows: list[dict[str, str]],
) -> int:
    assert Json is not None
    inserted = 0
    for row in rows:
        values = row_to_opportunity_values(row)
        codigo = values["codigo_licitacion"]
        if not codigo:
            continue
        cur.execute(
            """
            INSERT INTO commercial.equipment_opportunity (
              source_id, opportunity_key, priority_rank, codigo_licitacion, buyer, region,
              close_date, close_at, equipment_category, item_description,
              next_action, safe_channel, supplier_needed, contact_status,
              operator_note, dnr_flags, extra_json
            ) VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                source_id,
                values["opportunity_key"],
                values["priority_rank"],
                codigo,
                values["buyer"],
                values["region"],
                values["close_date"],
                values["close_at"],
                values["equipment_category"],
                values["item_description"],
                values["next_action"],
                values["safe_channel"],
                values["supplier_needed"],
                values["contact_status"],
                values["operator_note"],
                values["dnr_flags"],
                Json(values["extra_json"]),
            ),
        )
        inserted += 1
    return inserted


def apply_load(
    pg_url: str,
    active_current: Path,
    *,
    csv_path: Path | None = None,
    updated_by: str,
    reason: str,
    sync_run_id: int | None = None,
    replace_source: bool = False,
    use_direct_rows: bool = True,
) -> dict[str, Any]:
    _require_psycopg()
    assert psycopg is not None and Json is not None

    resolved, manifest, manifest_path, date_suffix = resolve_load_context(
        active_current, csv_path=csv_path
    )
    rows = _load_csv(resolved)

    if use_direct_rows:
        artifact = derive_source_artifact_metadata(manifest, resolved, active_current)
        direct_loader = _sync_direct_loader_module()
        summary = direct_loader.apply_direct_rows_load(
            pg_url,
            rows,
            source_key=str(resolved),
            manifest_path=manifest_path,
            date_suffix=date_suffix,
            campaign_mode=(manifest.get("campaign_mode") if isinstance(manifest.get("campaign_mode"), str) else None),
            updated_by=updated_by,
            reason=reason,
            sync_run_id=sync_run_id,
            replace_source=replace_source
            or should_promote_equipment_source_as_canonical(manifest, resolved, active_current),
            artifact_basename=artifact["artifact_basename"],
            canonical_reason=_typed_rows_canonical_reason(artifact["canonical_reason"]),
        )
        if summary.get("error") == "duplicate_codigo_licitacion_in_rows":
            summary["error"] = "duplicate_codigo_licitacion_in_csv_export_rows"
        if summary.get("warning") == "empty_rows":
            summary["warning"] = "empty_csv_export_rows"
        return _merge_export_metadata(
            summary,
            resolved=resolved,
            manifest=manifest,
            active_current=active_current,
        )

    summary = _base_summary_fields(
        resolved=resolved,
        manifest=manifest,
        manifest_path=manifest_path,
        date_suffix=date_suffix,
        rows=rows,
        dry_run=False,
        active_current=active_current,
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
            "legacy_csv_loader_used": True,
        }
    )

    if summary["duplicate_codigos"]:
        summary["error"] = "duplicate_codigo_licitacion_in_csv"
        return summary

    if not rows:
        summary["warning"] = "empty_csv"
        return summary

    sha = summary["file_sha256"]
    mtime = file_mtime_utc(resolved)
    promote_canonical = should_promote_equipment_source_as_canonical(manifest, resolved, active_current)
    artifact = derive_source_artifact_metadata(manifest, resolved, active_current)
    csv_path_str = str(resolved)
    pg_url_norm = normalize_postgres_url(pg_url)

    with psycopg.connect(pg_url_norm, autocommit=False) as conn:
        with conn.cursor() as cur:
            existing_source_id = lookup_existing_source_id(cur, csv_path_str)
            if existing_source_id is not None and not replace_source:
                summary["existing_source_id"] = existing_source_id
                summary["source_id"] = existing_source_id
                if promote_canonical:
                    _set_canonical_for_source(
                        cur, date_suffix=date_suffix, source_id=existing_source_id
                    )
                    summary["is_canonical"] = True
                    summary["idempotent"] = "canonical_source_already_loaded"
                    conn.commit()
                    return summary
                summary["error"] = "source_already_loaded"
                summary["hint"] = _SOURCE_ALREADY_LOADED_HINT
                return summary

            if existing_source_id is not None and replace_source:
                source_id = existing_source_id
                summary["replaced_source"] = True
                cur.execute(
                    """
                    UPDATE commercial.equipment_opportunity_source
                    SET manifest_path = %s,
                        date_suffix = %s,
                        campaign_mode = %s,
                        file_sha256 = %s,
                        file_mtime = %s,
                        synced_at = now(),
                        sync_run_id = %s,
                        loader_version = %s,
                        is_canonical = %s,
                        source_kind = %s,
                        artifact_basename = %s,
                        canonical_reason = %s
                    WHERE id = %s
                    """,
                    (
                        manifest_path,
                        date_suffix,
                        summary["campaign_mode"],
                        sha,
                        mtime,
                        sync_run_id,
                        LOADER_VERSION,
                        promote_canonical,
                        artifact["source_kind"],
                        artifact["artifact_basename"],
                        artifact["canonical_reason"],
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
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        manifest_path,
                        csv_path_str,
                        date_suffix,
                        summary["campaign_mode"],
                        len(rows),
                        sha,
                        mtime,
                        promote_canonical,
                        sync_run_id,
                        LOADER_VERSION,
                        artifact["source_kind"],
                        artifact["artifact_basename"],
                        artifact["canonical_reason"],
                    ),
                )
                source_id = int(cur.fetchone()[0])

            summary["source_id"] = source_id

            if promote_canonical:
                _set_canonical_for_source(cur, date_suffix=date_suffix, source_id=source_id)
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
