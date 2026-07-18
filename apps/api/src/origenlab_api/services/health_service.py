"""Health response builder."""

from __future__ import annotations

from origenlab_api.schemas.health import HealthResponse
from origenlab_api.settings import Settings
from origenlab_api.sqlite_ro import connection_mode_flags


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
    )
