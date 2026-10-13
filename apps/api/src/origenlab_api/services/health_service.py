"""Health response builder."""

from __future__ import annotations

import os
import re

from origenlab_api.schemas.health import HealthResponse
from origenlab_api.settings import Settings
from origenlab_api.sqlite_ro import connection_mode_flags


def deployed_version() -> str | None:
    """The commit Render built this service from, or None. Only a full hex SHA is echoed."""
    value = os.environ.get("RENDER_GIT_COMMIT", "").strip().lower()
    return value if re.fullmatch(r"[0-9a-f]{40}", value) else None


def build_health_response(settings: Settings) -> HealthResponse:
    backend = settings.resolved_api_backend()
    postgres_configured = settings.postgres_configured()
    flags = connection_mode_flags(settings)
    if backend == "postgres":
        mode = "operator-postgres-mirror-readonly"
    elif flags["recovery_mode"]:
        mode = "operator-sqlite-recovery-readonly"
    else:
        mode = "operator-sqlite-readonly"
    return HealthResponse(
        ok=True,
        service="origenlab-api",
        mode=mode,
        backend=backend,
        postgres_configured=postgres_configured,
        sqlite_immutable=flags["sqlite_immutable"],
        sqlite_query_only=flags["sqlite_query_only"],
        recovery_mode=flags["recovery_mode"],
        dotenv_disabled=bool(getattr(settings, "dotenv_disabled", False)),
        version=deployed_version(),
    )
