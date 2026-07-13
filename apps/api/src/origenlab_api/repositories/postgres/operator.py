"""Postgres mirror operator status (read-only ``api.v_operator_status``)."""

from __future__ import annotations

import json
from typing import Any

from origenlab_api.repositories.automation_status_fs import get_automation_status_from_active_current
from origenlab_api.repositories.postgres.common import postgres_connection, require_psycopg
from origenlab_api.settings import Settings

_OPERATOR_STATUS_SQL = """
SELECT
  verdict,
  sqlite_path_redacted,
  campaign_mode,
  warnings_json,
  outbound_readiness_json
FROM api.v_operator_status
LIMIT 1
"""


def map_operator_status_row(row: dict[str, Any]) -> dict[str, Any]:
    warnings = _parse_warnings(row.get("warnings_json"))
    outbound_readiness = _parse_outbound_readiness(row.get("outbound_readiness_json"))
    sqlite_path = str(row.get("sqlite_path_redacted") or "").strip()
    campaign_mode = row.get("campaign_mode")
    if isinstance(campaign_mode, str):
        campaign_mode = campaign_mode.strip() or None
    else:
        campaign_mode = None

    return {
        "verdict": str(row.get("verdict") or "BLOCKED"),
        "sqlite_path": sqlite_path,
        "campaign_mode": campaign_mode,
        "operator_focus": None,
        "outbound_readiness": outbound_readiness,
        "warnings": warnings,
    }


def _parse_warnings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(w) for w in value if str(w).strip()]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value] if value.strip() else []
        if isinstance(parsed, list):
            return [str(w) for w in parsed if str(w).strip()]
        return []
    return [str(value)] if str(value).strip() else []


def _parse_outbound_readiness(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value.strip() or "n/a"
    if isinstance(value, dict):
        verdict = value.get("verdict")
        if verdict is None:
            return "n/a"
        return str(verdict)
    return "n/a"


class PostgresOperatorStatusRepository:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def get_status(self, *, max_staleness_days: float = 14.0) -> dict[str, Any]:
        del max_staleness_days  # Postgres mirror uses view-derived verdict, not SQLite staleness probe.
        pg = require_psycopg()
        with postgres_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                cur.execute(_OPERATOR_STATUS_SQL)
                row = cur.fetchone()
        if row is None:
            return {
                "verdict": "BLOCKED",
                "sqlite_path": "",
                "campaign_mode": None,
                "operator_focus": None,
                "outbound_readiness": "mirror_blocked",
                "warnings": ["Postgres mirror: api.v_operator_status returned no row"],
            }
        return map_operator_status_row(dict(row))

    def get_automation_status(self, *, mirror_cooldown_seconds: int = 60) -> dict[str, Any]:
        return get_automation_status_from_active_current(
            self._settings,
            mirror_cooldown_seconds=mirror_cooldown_seconds,
        )
