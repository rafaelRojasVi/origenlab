"""Health endpoint schemas."""

from __future__ import annotations

from pydantic import BaseModel


class HealthResponse(BaseModel):
    ok: bool = True
    service: str = "origenlab-api"
    mode: str = "operator-sqlite-readonly"
    backend: str = "sqlite"
    postgres_configured: bool = False
    sqlite_immutable: bool = False
    sqlite_query_only: bool = False
    recovery_mode: bool = False
    dotenv_disabled: bool = False
