"""API settings (read-only paths)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import PrivateAttr, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ApiBackend = Literal["sqlite", "postgres"]

# Uppercase letters only, matching the historical CN-style evidence shape;
# widening the charset is a numbering-policy change, not a convenience edit.
_DOCUMENT_PREFIX_RE = re.compile(r"^[A-Z]{1,8}$")


@dataclass(frozen=True)
class QuoteNumberingConfig:
    """The complete, explicitly configured quote-numbering decision.

    The allocated serial powers two distinct identifiers, never one:
    ``document_prefix`` is only ever a component of the Drive
    ``document_number`` (e.g. "CN01183") -- it must never be treated as part
    of the human-facing ``quote_number`` (e.g. "01183-26").
    """

    document_prefix: str
    serial_pad_width: int
    seed_next_serial: int

# --- The V2 database target boundary -----------------------------------------------------
#
# `ORIGENLAB_V2_DATABASE_URL` decides which database the /v2 read and command boundaries open.
# While the hosted phase is frozen (docs/OPERATIONS.md §1.1) the only legitimate values are
# loopback DSNs: the persistent development database, or the clean-room database
# (supabase/scripts/cleanroom_db.sh). Pointing this variable at a hosted project would let the
# API read and — with commands enabled — write a database no migration slice has adopted.
#
# So the variable is validated rather than trusted, and the API refuses to start on a value it
# does not recognise instead of quietly using it. The rule is the importer's rule, deliberately:
# `origenlab_email_pipeline.migration.v2_import.target` accepts a literal loopback address and
# nothing else, and a second boundary that agreed with it only approximately would be the gap.
#
#   * only `postgres://` or `postgresql://`;
#   * the host must be a literal loopback IP address. A *name* — including `localhost` — is
#     refused, because what we validate (text) and what libpq resolves (an address, via whatever
#     the host's name service says today) are two different things;
#   * no query string and no fragment, so no libpq keyword (`host`, `hostaddr`, `service`) can
#     be smuggled in to redirect a connection whose authority looks local;
#   * no hosted-provider marker anywhere in the string, which catches a hosted DSN that has been
#     edited by hand into looking loopback.

#: Substrings that mark a managed/hosted provider, refused even on a loopback host.
_V2_HOSTED_MARKERS: tuple[str, ...] = (
    "supabase.co",
    "supabase.com",
    "supabase.in",
    "pooler.supabase",
    "render.com",
    "neon.tech",
    "rds.amazonaws.com",
    "azure.com",
    "cloudflare",
)


class V2TargetRefused(ValueError):
    """`ORIGENLAB_V2_DATABASE_URL` names something this API will not open."""


def assert_v2_target_is_local(url: str) -> str:
    """Return ``url`` unchanged, or raise :class:`V2TargetRefused`.

    Validation only. Nothing here connects, resolves a name or reads the environment.
    """
    raw = (url or "").strip()
    if not raw:
        raise V2TargetRefused("ORIGENLAB_V2_DATABASE_URL is empty")

    lowered = raw.lower()
    for marker in _V2_HOSTED_MARKERS:
        if marker in lowered:
            raise V2TargetRefused(
                f"ORIGENLAB_V2_DATABASE_URL names a hosted provider ({marker!r}). "
                "No hosted project has been adopted; see docs/STATUS.md §2.5 and §2.8."
            )

    parts = urlsplit(raw)
    if parts.scheme not in ("postgres", "postgresql"):
        raise V2TargetRefused(
            f"ORIGENLAB_V2_DATABASE_URL must be a postgres:// or postgresql:// URI, not {parts.scheme!r}"
        )
    if parts.query:
        raise V2TargetRefused(
            "ORIGENLAB_V2_DATABASE_URL must carry no query string: libpq reads one as connection "
            "keywords, so a loopback authority there can still reach a remote server"
        )
    if parts.fragment:
        raise V2TargetRefused("ORIGENLAB_V2_DATABASE_URL must carry no fragment")

    host = parts.hostname
    if not host:
        raise V2TargetRefused(
            "ORIGENLAB_V2_DATABASE_URL names no host. A host-less DSN lets libpq fall back to "
            "its own defaults, which is not a target this API validated"
        )
    try:
        address = ip_address(host)
    except ValueError:
        raise V2TargetRefused(
            f"ORIGENLAB_V2_DATABASE_URL host {host!r} is a name, not a literal IP address. "
            "Only a loopback IP literal is accepted: a boundary that an /etc/hosts line can "
            "move is not a boundary"
        ) from None
    if not address.is_loopback:
        raise V2TargetRefused(
            f"ORIGENLAB_V2_DATABASE_URL host {host!r} is not a loopback address"
        )

    return raw


_API_ROOT = Path(__file__).resolve().parents[2]
_EMAIL_PIPELINE_ROOT = _API_ROOT.parent / "email-pipeline"
_DEFAULT_ACTIVE_CURRENT = (
    _EMAIL_PIPELINE_ROOT / "reports" / "out" / "active" / "current"
)
_DEFAULT_INSTITUTION_PROSPECT_DIR = _DEFAULT_ACTIVE_CURRENT / "institution_prospects"
_DEFAULT_TENDER_TERMS_DIR = _DEFAULT_ACTIVE_CURRENT / "tender_terms"

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
    tender_terms_dir: Path | None = None
    operator_tender_import_dir: Path | None = None
    api_backend: str | None = None
    postgres_url: str | None = None
    postgres_write_url: str | None = None
    commercial_operations_writes_enabled: bool = False
    postgres_statement_timeout_ms: int = 30_000
    postgres_pool_size: int = 5

    # --- V2 durable read boundary -------------------------------------------
    # Both fail closed. With no DSN the `/v2/*` router is not mounted at all, so an
    # unconfigured deployment gets no V2 surface rather than one that errors on every
    # request. No hosted credential is added while the hosted phase is frozen.
    """DSN of the V2 durable database; the /v2 router is not mounted without it."""
    v2_database_url: str | None = None
    """Supabase Auth JWKS URL. When set, JWKS verification is used and the local development identity adapter is never constructed."""
    v2_jwks_url: str | None = None
    v2_statement_timeout_ms: int = 15_000
    """When true, mount POST /v2/commands/* — the human-review command boundary.

    Default **false**, like `commercial_operations_writes_enabled` above and for the same
    reason: a durable write path should be switched on deliberately, by whoever is ready to
    have real decisions recorded, and not by the act of pointing the API at a database. With
    it off the command router is absent entirely and every /v2/commands/* path is a 404.
    """
    v2_commands_enabled: bool = False
    """Local review only: a quote_crm_import dry-run directory (intent.json + intent.sha256).

    When set, GET /v2/cockpit/import-review compares that plan with the V2 database. The plan is
    hash-checked at startup; unset (the default), the route does not exist.
    """
    v2_import_review_plan_dir: str | None = None
    """Directory the plan's ``stored_path`` PDF entries are relative to (the audits root)."""
    v2_import_review_documents_root: str | None = None
    """Local review only: a case migration dry-run directory (migration_manifest.json + SHA256SUMS).

    When set, GET /v2/cockpit/case-archive shows each case with its quotations, revisions, Drive
    files and Gmail evidence, CRM status (from the import plan) apart from archive status. Inputs are
    hash-checked at startup; unset (the default), the route does not exist.
    """
    v2_case_archive_dir: str | None = None
    """Comma-separated Drive upload reports whose verified files count as download-hash verified."""
    v2_case_archive_upload_reports: str | None = None
    """Local review only: comma-separated ``archive_links.jsonl`` ledgers from executed case archive
    runs. They give GET /v2/workspace/* the Drive file and folder of each archived quotation PDF,
    matched to CRM revisions by exact SHA-256 only. Loaded once at startup; a missing or
    contradictory ledger fails the process. Unset, the workspace answers without Drive links.
    """
    v2_drive_archive_ledgers: str | None = None

    # --- Dashboard login -----------------------------------------------------
    # Google Workspace sign-in for the V2 boundary (apps/api/docs/PRODUCTION_AUTH.md,
    # "Google Workspace login"). Every field fails closed: with the switch off no /auth/google
    # route exists, and with it on the process refuses to start unless every value below is
    # present and sane. The authenticated address is mapped to `platform.operator`, so this
    # needs `ORIGENLAB_V2_DATABASE_URL` as well.
    """When true, mount GET /auth/google/login and /auth/google/callback."""
    google_auth_enabled: bool = False
    """OAuth 2.0 Web application client ID from Google Cloud Console."""
    google_client_id: str | None = None
    """OAuth 2.0 client secret. Secret store only; never committed."""
    google_client_secret: SecretStr | None = None
    """The only Google Workspace domain whose accounts may sign in."""
    google_workspace_domain: str = "origenlab.cl"
    """Browser-visible base URL under which /auth/* is reached (e.g. https://dashboard.origenlab.cl/api)."""
    auth_public_base_url: str | None = None
    """HMAC key for the session and sign-in cookies; at least 32 characters. Secret store only."""
    auth_session_secret: SecretStr | None = None
    """Lifetime of a dashboard session, in seconds."""
    auth_session_ttl_seconds: int = 8 * 60 * 60
    """Local development only: resolve the V2 operator from X-OriginLab-Operator-Email.

    Default **false**. Refused at startup when ORIGENLAB_ENV=production, and the adapter it
    enables still refuses any non-loopback database. Never set it in a deployed environment.
    """
    dev_login_enabled: bool = False
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

    # CRM-Q1 quote Drive workspace + numbering: every field below fails
    # closed until explicitly configured (placeholders in .env.example).
    """Drive folder ID of the canonical quotations root (e.g. "Cotizaciones"); verified read-only by preflight, never a creation target itself."""
    drive_quotes_root_folder_id: str | None = None
    """Drive folder ID under which every new quote workspace folder is created (e.g. "Pendientes"); required for provisioning."""
    drive_quotes_pending_folder_id: str | None = None
    """Drive folder ID of the post-send container (e.g. "Enviadas"); optional -- verified read-only by preflight when set, not yet used by any write path (no sent lifecycle exists)."""
    drive_quotes_sent_folder_id: str | None = None
    """Drive file ID of the master quotation spreadsheet template."""
    drive_quote_template_file_id: str | None = None
    """Explicit, separately-activated gate for template-document
    provisioning. Fail-safe default false: the master quotation template is
    not yet finalized, so a generated quote's Drive workspace folder must
    provision on its own -- the template copy step is only attempted once an
    operator deliberately turns this on. Never inferred from whether
    drive_quote_template_file_id happens to be set."""
    drive_quote_template_provisioning_enabled: bool = False
    """Explicit auth mode: authorized_user_my_drive | service_account_shared_drive."""
    drive_auth_mode: str | None = None
    """Path to the Google credentials JSON file for the configured auth mode."""
    drive_credentials_file: Path | None = None
    """Shared Drive ID (required in service_account_shared_drive mode)."""
    drive_shared_drive_id: str | None = None
    """Expected Drive principal email; preflight fails closed on any mismatch."""
    drive_expected_principal_email: str | None = None
    """Document-number prefix (e.g. CN) -- never part of the human quote_number."""
    quote_document_prefix: str | None = None
    """Zero-pad width for the allocated serial (1..10), shared by quote_number and document_number."""
    quote_serial_pad_width: int | None = None
    """First serial the durable series is seeded with on first allocation."""
    quote_seed_next_serial: int | None = None

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

    def v2_configured(self) -> bool:
        return bool((self.v2_database_url or "").strip())

    def v2_commands_configured(self) -> bool:
        """The command boundary needs both a database and a deliberate switch.

        Two conditions, not one: pointing the API at the V2 core is a read decision, and
        letting it record durable human decisions is a separate one.
        """
        return self.v2_configured() and bool(self.v2_commands_enabled)

    def require_v2_database_url(self) -> str:
        url = (self.v2_database_url or "").strip()
        if not url:
            raise ValueError(
                "ORIGENLAB_V2_DATABASE_URL is required to mount the /v2 read boundary"
            )
        # Fail closed on a target this API will not open. Raised here rather than at the
        # connection, so a misconfigured process never starts serving /v2 at all.
        return assert_v2_target_is_local(url)

    def require_postgres_url(self) -> str:
        url = (self.postgres_url or "").strip()
        if not url:
            raise ValueError(
                "ORIGENLAB_POSTGRES_URL is required when ORIGENLAB_API_BACKEND=postgres"
            )
        return url

    def postgres_write_configured(self) -> bool:
        return bool((self.postgres_write_url or "").strip())

    def require_postgres_write_url(self) -> str:
        url = (self.postgres_write_url or "").strip()
        if not url:
            raise ValueError(
                "ORIGENLAB_POSTGRES_WRITE_URL is required when "
                "commercial operations writes are enabled"
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

    def resolved_tender_terms_dir(self) -> Path:
        if self.tender_terms_dir is not None:
            return self.tender_terms_dir.expanduser().resolve()
        return _DEFAULT_TENDER_TERMS_DIR.resolve()

    def quote_numbering_config(self) -> QuoteNumberingConfig | None:
        """The configured quote-numbering decision, or None to fail closed.

        A partial or invalid configuration is treated exactly like no
        configuration: quote-number allocation must never guess the missing
        parts of the business decision.
        """
        prefix = (self.quote_document_prefix or "").strip()
        pad_width = self.quote_serial_pad_width
        seed = self.quote_seed_next_serial

        if not prefix or pad_width is None or seed is None:
            return None

        if _DOCUMENT_PREFIX_RE.fullmatch(prefix) is None:
            return None

        if not (1 <= pad_width <= 10) or seed < 1:
            return None

        return QuoteNumberingConfig(
            document_prefix=prefix,
            serial_pad_width=pad_width,
            seed_next_serial=seed,
        )

    def resolved_operator_tender_import_dir(self) -> Path:
        if self.operator_tender_import_dir is not None:
            return self.operator_tender_import_dir.expanduser().resolve()

        # Keep operator-imported T1 evidence on the same storage volume as
        # canonical T1 by default. In production ORIGENLAB_TENDER_TERMS_DIR
        # is /var/data/tender_terms, so this safely resolves to
        # /var/data/operator_tender_imports even if the newer explicit env
        # variable has not yet been synchronized into the Render service.
        tender_terms_dir = self.resolved_tender_terms_dir()
        return (tender_terms_dir.parent / "operator_tender_imports").resolve()


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
        dotenv_disabled_from_environ()
        if dotenv_disabled is None
        else bool(dotenv_disabled)
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
