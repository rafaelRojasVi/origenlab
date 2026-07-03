"""Repository backend factory (SQLite default; hybrid Postgres for DB-3)."""

from __future__ import annotations

from dataclasses import dataclass

from origenlab_api.repositories.postgres.contact import PostgresContactRepository
from origenlab_api.repositories.postgres.email import PostgresEmailRecentRepository
from origenlab_api.repositories.postgres.equipment import PostgresEquipmentOpportunityRepository
from origenlab_api.repositories.postgres.operator import PostgresOperatorStatusRepository
from origenlab_api.repositories.postgres.warm_cases import PostgresWarmCaseRepository
from origenlab_api.repositories.protocols import (
    ContactRepository,
    EmailRecentRepository,
    EquipmentOpportunityRepository,
    OperatorStatusRepository,
    WarmCaseRepository,
)
from origenlab_api.repositories.sqlite.contact import SqliteContactRepository
from origenlab_api.repositories.sqlite.email import SqliteEmailRecentRepository
from origenlab_api.repositories.sqlite.equipment import SqliteEquipmentOpportunityRepository
from origenlab_api.repositories.sqlite.operator import SqliteOperatorStatusRepository
from origenlab_api.repositories.sqlite.warm_cases import SqliteWarmCaseRepository
from origenlab_api.settings import Settings


@dataclass(frozen=True)
class RepositoryBundle:
    """Hybrid bundle: Postgres repos for implemented routes; SQLite for the rest."""

    operator: OperatorStatusRepository
    equipment: EquipmentOpportunityRepository
    warm_cases: WarmCaseRepository
    email_recent: EmailRecentRepository
    contact: ContactRepository


def validate_api_settings(settings: Settings) -> None:
    """Fail fast when postgres backend is selected without a DSN."""
    from origenlab_api.http_security import validate_http_security_settings

    validate_http_security_settings(settings)
    backend = settings.resolved_api_backend()
    if backend == "postgres":
        settings.require_postgres_url()
    if settings.production_mode() and backend != "postgres":
        raise ValueError(
            "ORIGENLAB_ENV=production requires ORIGENLAB_API_BACKEND=postgres "
            "(SQLite is local-dev only)"
        )
    if settings.production_mode() and not settings.parsed_cors_origins():
        raise ValueError(
            "ORIGENLAB_ENV=production requires ORIGENLAB_API_CORS_ORIGINS "
            "(explicit dashboard origin, no wildcard)"
        )
    if settings.production_mode() and not (settings.api_auth_token or "").strip():
        raise ValueError(
            "ORIGENLAB_ENV=production requires ORIGENLAB_API_AUTH_TOKEN"
        )


def get_repository_bundle(settings: Settings) -> RepositoryBundle:
    validate_api_settings(settings)
    backend = settings.resolved_api_backend()
    if backend == "postgres":
        return RepositoryBundle(
            operator=PostgresOperatorStatusRepository(settings),
            equipment=PostgresEquipmentOpportunityRepository(settings),
            warm_cases=PostgresWarmCaseRepository(settings),
            email_recent=PostgresEmailRecentRepository(settings),
            contact=PostgresContactRepository(settings),
        )
    return RepositoryBundle(
        operator=SqliteOperatorStatusRepository(settings),
        equipment=SqliteEquipmentOpportunityRepository(settings),
        warm_cases=SqliteWarmCaseRepository(settings),
        email_recent=SqliteEmailRecentRepository(settings),
        contact=SqliteContactRepository(settings),
    )


def get_repositories(settings: Settings) -> RepositoryBundle:
    """FastAPI dependency wrapper."""
    return get_repository_bundle(settings)
