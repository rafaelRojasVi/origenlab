"""API settings (read-only paths)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import PrivateAttr
from pydantic_settings import BaseSettings, SettingsConfigDict

ApiBackend = Literal["sqlite", "postgres"]

_API_ROOT = Path(__file__).resolve().parents[2]
_EMAIL_PIPELINE_ROOT = _API_ROOT.parent / "email-pipeline"
_DEFAULT_ACTIVE_CURRENT = _EMAIL_PIPELINE_ROOT / "reports" / "out" / "active" / "current"
_DEFAULT_INSTITUTION_PROSPECT_DIR = _DEFAULT_ACTIVE_CURRENT / "institution_prospects"

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def env_flag_truthy(name: str) -> bool:
    """Read a boolean-like process environment flag (names only; no dotenv)."""
    return (os.environ.get(name) or "").strip().lower() in _TRUTHY


def recovery_mode_requested_from_environ() -> bool:
    """
    Detect explicit recovery request from the process environment only.

    Must run before BaseSettings reads project ``.env`` files. Both immutable and
    offline-confirmation flags are required (matching admission policy).
    """
    return env_flag_truthy("ORIGENLAB_SQLITE_IMMUTABLE_RO") and env_flag_truthy(
        "ORIGENLAB_SQLITE_CONFIRM_OFFLINE_COPY"
    )


def dotenv_disabled_from_environ() -> bool:
    """True when recovery mode is requested or an explicit disable flag is set."""
    return recovery_mode_requested_from_environ() or env_flag_truthy(
        "ORIGENLAB_DISABLE_DOTENV"
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ORIGENLAB_",
        env_file=".env",
        extra="ignore",
    )

    sqlite_path: Path | None = None
    """Request immutable recovery opens (insufficient alone — see confirm + manifest)."""
    sqlite_immutable_ro: bool = False
    """Explicit offline confirmation required with sqlite_immutable_ro for recovery mode."""
    sqlite_confirm_offline_copy: bool = False
    """Completed compaction manifest path required for recovery mode."""
    sqlite_compaction_manifest: Path | None = None
    active_current: Path | None = None
    institution_prospect_dir: Path | None = None
    api_backend: str | None = None
    postgres_url: str | None = None
    postgres_statement_timeout_ms: int = 30_000
    postgres_pool_size: int = 5
    """Comma-separated browser origins for dashboard static site (no wildcards)."""
    api_cors_origins: str | None = None
    """Comma-separated Host header values allowed in production (e.g. api.origenlab.cl)."""
    api_allowed_hosts: str | None = None
    """When true, hide /docs, /redoc, /openapi.json (also off when ORIGENLAB_ENV=production)."""
    api_disable_docs: bool = False
    """Set to production|prod to enable production defaults (docs off, stricter validation)."""
    env: str | None = None
    """Bearer/API-key token required for non-public routes when ORIGENLAB_ENV=production."""
    api_auth_token: str | None = None

    _dotenv_disabled: bool = PrivateAttr(default=False)

    @property
    def dotenv_disabled(self) -> bool:
        """Sanitized indicator: project dotenv was not loaded for this settings object."""
        return bool(self._dotenv_disabled)

    def production_mode(self) -> bool:
        return (self.env or "").strip().lower() in ("production", "prod")

    def parsed_cors_origins(self) -> list[str]:
        raw = (self.api_cors_origins or "").strip()
        if not raw:
            return []
        return [part.strip() for part in raw.split(",") if part.strip()]

    def parsed_allowed_hosts(self) -> tuple[str, ...]:
        raw = (self.api_allowed_hosts or "").strip()
        if not raw:
            return ()
        seen: set[str] = set()
        out: list[str] = []
        for part in raw.split(","):
            host = part.strip()
            if not host:
                continue
            if ":" in host:
                host = host.split(":", 1)[0].strip()
            normalized = host.lower()
            if normalized and normalized not in seen:
                seen.add(normalized)
                out.append(normalized)
        return tuple(out)

    def resolved_api_backend(self) -> ApiBackend:
        raw = (self.api_backend or "sqlite").strip().lower()
        if raw not in ("sqlite", "postgres"):
            raise ValueError(
                f"Invalid ORIGENLAB_API_BACKEND={raw!r} (expected 'sqlite' or 'postgres')"
            )
        return raw  # type: ignore[return-value]

    def postgres_configured(self) -> bool:
        return bool((self.postgres_url or "").strip())

    def require_postgres_url(self) -> str:
        url = (self.postgres_url or "").strip()
        if not url:
            raise ValueError(
                "ORIGENLAB_POSTGRES_URL is required when ORIGENLAB_API_BACKEND=postgres"
            )
        return url

    def resolved_sqlite_path(self) -> Path:
        if self.sqlite_path is not None:
            return self.sqlite_path.expanduser().resolve()
        if self.dotenv_disabled or recovery_mode_requested_from_environ():
            raise ValueError(
                "recovery mode requires ORIGENLAB_SQLITE_PATH "
                "(refusing email-pipeline dotenv fallback)"
            )
        from origenlab_email_pipeline.config import load_settings

        return load_settings().resolved_sqlite_path()

    def resolved_active_current(self) -> Path:
        if self.active_current is not None:
            return self.active_current.expanduser().resolve()
        return _DEFAULT_ACTIVE_CURRENT.resolve()

    def resolved_manifest_path(self) -> Path:
        return self.resolved_active_current() / "manifest.json"

    def resolved_institution_prospect_dir(self) -> Path:
        if self.institution_prospect_dir is not None:
            return self.institution_prospect_dir.expanduser().resolve()
        return _DEFAULT_INSTITUTION_PROSPECT_DIR.resolve()


def build_settings(*, dotenv_disabled: bool | None = None) -> Settings:
    """
    Construct Settings with optional dotenv isolation.

    When ``dotenv_disabled`` is true (or recovery/disable flags are in the process
    environment), project ``.env`` files are not read. Process environment variables
    remain visible to Pydantic and to recovery admission.

    ``ORIGENLAB_DISABLE_DOTENV=1`` disables dotenv only; it does not grant recovery
    admission (immutable + confirm + manifest are still required).
    """
    disable = (
        dotenv_disabled_from_environ() if dotenv_disabled is None else bool(dotenv_disabled)
    )
    if disable:
        settings = Settings(_env_file=None)
        settings._dotenv_disabled = True
        return settings
    settings = Settings()
    settings._dotenv_disabled = False
    return settings


@lru_cache(maxsize=8)
def _get_settings_cached(recovery_requested: bool, dotenv_disabled: bool) -> Settings:
    """Cache keyed by mode flags so recovery/normal settings never share an entry."""
    return build_settings(dotenv_disabled=dotenv_disabled)


def clear_settings_cache() -> None:
    _get_settings_cached.cache_clear()


def get_settings() -> Settings:
    return _get_settings_cached(
        recovery_mode_requested_from_environ(),
        dotenv_disabled_from_environ(),
    )


# Tests and callers historically use get_settings.cache_clear().
get_settings.cache_clear = clear_settings_cache  # type: ignore[attr-defined]
