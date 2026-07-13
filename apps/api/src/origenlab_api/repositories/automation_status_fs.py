"""Filesystem-backed operator automation status (no Gmail/Postgres/SQLite)."""

from __future__ import annotations

from typing import Any

from origenlab_email_pipeline.operator_cli.operator_automation_status import (
    OperatorAutomationStatusOptions,
    build_operator_automation_status,
)

from origenlab_api.settings import Settings


def get_automation_status_from_active_current(
    settings: Settings,
    *,
    mirror_cooldown_seconds: int = 60,
) -> dict[str, Any]:
    active_current = settings.resolved_active_current()
    reports_dir = active_current.parent.parent
    return build_operator_automation_status(
        reports_dir=reports_dir,
        options=OperatorAutomationStatusOptions(
            mirror_cooldown_seconds=mirror_cooldown_seconds,
            skip_cron_inspection=True,
            cron_note="not inspected by API",
        ),
    )
