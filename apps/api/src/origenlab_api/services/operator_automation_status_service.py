"""Operator automation status service (backend-aware snapshot vs filesystem)."""

from __future__ import annotations

from typing import Any

from origenlab_api.path_redaction import enrich_automation_status_paths
from origenlab_api.repositories.automation_status_fs import get_automation_status_from_active_current
from origenlab_api.repositories.postgres import dashboard_snapshots as snapshot_repo
from origenlab_api.schemas.operator_automation import OperatorAutomationStatusResponse
from origenlab_api.settings import Settings


def _filesystem_automation_status(
    settings: Settings,
    *,
    mirror_cooldown_seconds: int,
    dashboard_mirror_sync: dict[str, Any] | None,
) -> OperatorAutomationStatusResponse:
    data = get_automation_status_from_active_current(
        settings,
        mirror_cooldown_seconds=mirror_cooldown_seconds,
    )
    data["source"] = "filesystem_active_current"
    data["snapshot_updated_at"] = None
    data["snapshot_stale"] = None
    data["dashboard_mirror_sync"] = dashboard_mirror_sync
    return OperatorAutomationStatusResponse.model_validate(enrich_automation_status_paths(data))


def _postgres_snapshot_response(
    pg_row: dict[str, Any],
    *,
    dashboard_mirror_sync: dict[str, Any] | None,
    snapshot_max_age_seconds: int,
) -> OperatorAutomationStatusResponse:
    snapshot = pg_row["snapshot"]
    updated_at = pg_row["updated_at"]
    stale = snapshot_repo.snapshot_is_stale(
        updated_at,
        max_age_seconds=snapshot_max_age_seconds,
    )
    enriched = enrich_automation_status_paths(
        {
            **snapshot,
            "source": "postgres_snapshot",
            "snapshot_updated_at": updated_at,
            "snapshot_stale": stale,
            "dashboard_mirror_sync": dashboard_mirror_sync,
        }
    )
    return OperatorAutomationStatusResponse.model_validate(enriched)


def build_operator_automation_status_response(
    settings: Settings,
    *,
    mirror_cooldown_seconds: int = 60,
    snapshot_max_age_seconds: int = snapshot_repo.DEFAULT_SNAPSHOT_MAX_AGE_SECONDS,
) -> OperatorAutomationStatusResponse:
    dashboard_mirror_sync = snapshot_repo.get_latest_dashboard_sync_snapshot(settings)
    backend = settings.resolved_api_backend()

    # Local SQLite API must reflect live active/current automation state even when
    # a Postgres automation snapshot exists from a prior mirror publish.
    if backend == "sqlite":
        return _filesystem_automation_status(
            settings,
            mirror_cooldown_seconds=mirror_cooldown_seconds,
            dashboard_mirror_sync=dashboard_mirror_sync,
        )

    pg_row = snapshot_repo.get_operator_automation_status_snapshot(settings)
    if pg_row is not None:
        return _postgres_snapshot_response(
            pg_row,
            dashboard_mirror_sync=dashboard_mirror_sync,
            snapshot_max_age_seconds=snapshot_max_age_seconds,
        )

    return _filesystem_automation_status(
        settings,
        mirror_cooldown_seconds=mirror_cooldown_seconds,
        dashboard_mirror_sync=dashboard_mirror_sync,
    )
