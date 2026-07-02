"""Resolve and load canonical equipment-first operator queue artifact (read-only local fallback)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.equipment_first_operator_queue import _load_csv
from origenlab_email_pipeline.equipment_opportunity_mirror import derive_opportunity_key

from origenlab_api.repositories.equipment_detail_fields import merge_equipment_detail_fields
from origenlab_api.schemas.opportunities import EquipmentOpportunitiesMeta

_OPERATOR_QUEUE_PREFIX = "equipment_first_operator_queue_"
_STALE_CROSSCHECK_FRAGMENT = "buyer_opportunity_crosscheck"
_ACCOUNT_INTEL_SAFE = frozenset({"account_intelligence_only"})
_ACCOUNT_INTEL_ACTIONS = frozenset({"account_intelligence_only", "skip_consumables"})
_LOCAL_CSV_SOURCE_KIND = "csv_artifact"
_LOCAL_CSV_CANONICAL_REASON = "active_current_csv_fallback"


def load_manifest(active_current: Path) -> dict[str, Any]:
    path = active_current / "manifest.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _stale_paths(manifest: dict[str, Any]) -> set[str]:
    return {
        str(entry.get("path") or "").strip()
        for entry in (manifest.get("stale_files") or [])
        if entry.get("path")
    }


def _is_forbidden_queue_name(name: str) -> bool:
    lower = name.lower()
    return _STALE_CROSSCHECK_FRAGMENT in lower or "tender_buyer_outreach_queue" in lower


def resolve_operator_queue_csv(active_current: Path, manifest: dict[str, Any]) -> Path | None:
    """Pick canonical operator queue CSV; never stale crosscheck artifacts."""
    stale = _stale_paths(manifest)
    active_current = active_current.resolve()

    for rel in manifest.get("canonical_files") or []:
        rel_s = str(rel).strip()
        if not rel_s or rel_s in stale:
            continue
        if _is_forbidden_queue_name(rel_s):
            continue
        if not (rel_s.startswith(_OPERATOR_QUEUE_PREFIX) and rel_s.endswith(".csv")):
            continue
        candidate = active_current / rel_s
        if candidate.is_file():
            return candidate

    globs = sorted(active_current.glob(f"{_OPERATOR_QUEUE_PREFIX}*.csv"))
    for candidate in reversed(globs):
        if candidate.name in stale or _is_forbidden_queue_name(candidate.name):
            continue
        return candidate
    return None


def _parse_priority(value: str | None) -> int:
    try:
        return int(str(value or "").strip())
    except ValueError:
        return 0


def row_to_equipment_item(row: dict[str, str]) -> dict[str, Any]:
    codigo = (row.get("codigo_licitacion") or "").strip()
    base = merge_equipment_detail_fields(
        {
            "opportunity_key": derive_opportunity_key(row) if codigo else "",
            "priority_rank": _parse_priority(row.get("priority_rank")),
            "codigo_licitacion": codigo,
            "buyer": (row.get("buyer") or "").strip(),
            "region": (row.get("region") or "").strip(),
            "close_date": (row.get("close_date") or "").strip(),
            "equipment_category": (row.get("equipment_category") or "").strip(),
            "item_description": (row.get("item_description") or "").strip(),
            "next_action": (row.get("next_action") or "").strip(),
            "safe_channel": (row.get("safe_channel") or "").strip(),
            "supplier_needed": (row.get("supplier_needed") or "").strip(),
            "contact_status": (row.get("contact_status") or "").strip(),
            "contact_email": (row.get("contact_email") or "").strip(),
            "operator_note": (row.get("operator_note") or "").strip(),
        },
        row,
    )
    return base


def fetch_equipment_opportunities(
    active_current: Path,
    *,
    limit: int = 50,
    priority: int | None = None,
    next_action: str | None = None,
    safe_channel: str | None = None,
    include_account_intelligence: bool = True,
) -> tuple[list[dict[str, Any]], EquipmentOpportunitiesMeta]:
    manifest = load_manifest(active_current)
    campaign_mode = manifest.get("campaign_mode")
    if isinstance(campaign_mode, str):
        campaign_mode = campaign_mode.strip() or None
    else:
        campaign_mode = None

    csv_path = resolve_operator_queue_csv(active_current, manifest)
    if csv_path is None:
        return [], EquipmentOpportunitiesMeta(
            data_source="active_current_csv",
            read_only=True,
            count=0,
            source_path="",
            source_kind="csv_artifact",
            artifact_basename="",
            canonical_reason="missing_active_current_queue",
            campaign_mode=campaign_mode,
            reduced_mode=True,
            note="Canonical equipment_first_operator_queue_*.csv not found under active/current.",
        )

    raw_rows = _load_csv(csv_path)
    cap = max(1, min(int(limit), 200))
    next_action_f = (next_action or "").strip().lower() or None
    safe_channel_f = (safe_channel or "").strip().lower() or None

    items: list[dict[str, Any]] = []
    for row in raw_rows:
        item = row_to_equipment_item(row)
        if priority is not None and item["priority_rank"] != priority:
            continue
        if next_action_f and (item["next_action"] or "").lower() != next_action_f:
            continue
        sc = (item["safe_channel"] or "").lower()
        if safe_channel_f and sc != safe_channel_f:
            continue
        if not include_account_intelligence:
            na = (item["next_action"] or "").lower()
            if sc in _ACCOUNT_INTEL_SAFE or na in _ACCOUNT_INTEL_ACTIONS:
                continue
        items.append(item)
        if len(items) >= cap:
            break

    return (
        items,
        EquipmentOpportunitiesMeta(
            data_source="active_current_csv",
            read_only=True,
            count=len(items),
            source_path=str(csv_path),
            source_kind=_LOCAL_CSV_SOURCE_KIND,
            artifact_basename=csv_path.name,
            canonical_reason=_LOCAL_CSV_CANONICAL_REASON,
            campaign_mode=campaign_mode,
            reduced_mode=False,
            note="",
        ),
    )
