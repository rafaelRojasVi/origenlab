"""Postgres equipment opportunities (read-only ``api.v_equipment_opportunity_current``)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from origenlab_api.repositories.equipment_opportunities import (
    _ACCOUNT_INTEL_ACTIONS,
    _ACCOUNT_INTEL_SAFE,
)
from origenlab_api.repositories.equipment_detail_fields import merge_equipment_detail_fields
from origenlab_api.repositories.postgres.common import postgres_connection, require_psycopg
from origenlab_api.schemas.opportunities import EquipmentOpportunitiesMeta
from origenlab_api.settings import Settings

_EQUIPMENT_SQL = """
SELECT
  opportunity_key,
  priority_rank,
  codigo_licitacion,
  buyer,
  region,
  close_date,
  close_at,
  equipment_category,
  item_description,
  next_action,
  safe_channel,
  supplier_needed,
  contact_status,
  operator_note,
  extra_json,
  source_path,
  source_kind,
  artifact_basename,
  canonical_reason,
  campaign_mode
FROM api.v_equipment_opportunity_current
WHERE (%(priority)s::int IS NULL OR priority_rank = %(priority)s)
  AND (
    %(next_action)s::text IS NULL
    OR lower(trim(COALESCE(next_action, ''))) = %(next_action)s
  )
  AND (
    %(safe_channel)s::text IS NULL
    OR lower(trim(COALESCE(safe_channel, ''))) = %(safe_channel)s
  )
  AND (
    %(include_account_intel)s::boolean IS TRUE
    OR (
      NOT (lower(trim(COALESCE(safe_channel, ''))) = ANY(%(account_intel_safe)s))
      AND NOT (lower(trim(COALESCE(next_action, ''))) = ANY(%(account_intel_actions)s))
    )
  )
ORDER BY priority_rank NULLS LAST, close_at DESC NULLS LAST
LIMIT %(limit)s
"""

_EMPTY_NOTE = (
    "Postgres mirror has no equipment opportunities; "
    "run sync with --include-equipment-opportunities."
)


def map_equipment_row(row: dict[str, Any]) -> dict[str, Any]:
    priority = row.get("priority_rank")
    try:
        priority_rank = int(priority) if priority is not None else 0
    except (TypeError, ValueError):
        priority_rank = 0

    return merge_equipment_detail_fields(
        {
            "opportunity_key": _str_field(row.get("opportunity_key")),
            "priority_rank": priority_rank,
            "codigo_licitacion": _str_field(row.get("codigo_licitacion")),
            "buyer": _str_field(row.get("buyer")),
            "region": _str_field(row.get("region")),
            "close_date": _format_close_date(row.get("close_date")),
            "equipment_category": _str_field(row.get("equipment_category")),
            "item_description": _str_field(row.get("item_description")),
            "next_action": _str_field(row.get("next_action")),
            "safe_channel": _str_field(row.get("safe_channel")),
            "supplier_needed": _str_field(row.get("supplier_needed")),
            "contact_status": _str_field(row.get("contact_status")),
            "operator_note": _str_field(row.get("operator_note")),
        },
        row,
    )


def build_equipment_meta(
    *,
    items: list[dict[str, Any]],
    source_path: str | None,
    campaign_mode: str | None,
    source_kind: str | None = None,
    artifact_basename: str | None = None,
    canonical_reason: str | None = None,
) -> EquipmentOpportunitiesMeta:
    if items:
        return EquipmentOpportunitiesMeta(
            data_source="postgres_mirror",
            read_only=True,
            count=len(items),
            source_path=(source_path or "").strip(),
            source_kind=(source_kind or "").strip(),
            artifact_basename=(artifact_basename or "").strip(),
            canonical_reason=(canonical_reason or "").strip(),
            campaign_mode=campaign_mode,
            reduced_mode=False,
            note="",
        )
    return EquipmentOpportunitiesMeta(
        data_source="postgres_mirror",
        read_only=True,
        count=0,
        source_path=(source_path or "").strip(),
        source_kind=(source_kind or "").strip(),
        artifact_basename=(artifact_basename or "").strip(),
        canonical_reason=(canonical_reason or "").strip(),
        campaign_mode=campaign_mode,
        reduced_mode=True,
        note=_EMPTY_NOTE,
    )


def _str_field(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _format_close_date(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()


def _normalize_campaign_mode(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


class PostgresEquipmentOpportunityRepository:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def list_equipment(
        self,
        *,
        limit: int = 50,
        priority: int | None = None,
        next_action: str | None = None,
        safe_channel: str | None = None,
        include_account_intelligence: bool = True,
    ) -> tuple[list[dict[str, Any]], EquipmentOpportunitiesMeta]:
        require_psycopg()
        cap = max(1, min(int(limit), 200))
        next_action_f = (next_action or "").strip().lower() or None
        safe_channel_f = (safe_channel or "").strip().lower() or None
        params = {
            "limit": cap,
            "priority": priority,
            "next_action": next_action_f,
            "safe_channel": safe_channel_f,
            "include_account_intel": include_account_intelligence,
            "account_intel_safe": list(_ACCOUNT_INTEL_SAFE),
            "account_intel_actions": list(_ACCOUNT_INTEL_ACTIONS),
        }

        rows: list[dict[str, Any]] = []
        source_path: str | None = None
        source_kind: str | None = None
        artifact_basename: str | None = None
        canonical_reason: str | None = None
        campaign_mode: str | None = None

        pg = require_psycopg()
        with postgres_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                cur.execute(_EQUIPMENT_SQL, params)
                for raw in cur.fetchall():
                    row = dict(raw)
                    if source_path is None:
                        source_path = _str_field(row.get("source_path")) or None
                    if source_kind is None:
                        source_kind = _str_field(row.get("source_kind")) or None
                    if artifact_basename is None:
                        artifact_basename = _str_field(row.get("artifact_basename")) or None
                    if canonical_reason is None:
                        canonical_reason = _str_field(row.get("canonical_reason")) or None
                    if campaign_mode is None:
                        campaign_mode = _normalize_campaign_mode(row.get("campaign_mode"))
                    rows.append(map_equipment_row(row))

        meta = build_equipment_meta(
            items=rows,
            source_path=source_path,
            source_kind=source_kind,
            artifact_basename=artifact_basename,
            canonical_reason=canonical_reason,
            campaign_mode=campaign_mode,
        )
        return rows, meta
