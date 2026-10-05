"""FastAPI application for the OrigenLab operator plane."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from pydantic import SecretStr

from origenlab_api.backends.factory import validate_api_settings
from origenlab_api.errors import register_exception_handlers
from origenlab_api.http_security import configure_http_security, openapi_docs_enabled
from origenlab_api.request_id import RequestIdMiddleware
from origenlab_api.request_logging import RequestLoggingMiddleware
from origenlab_api.response_timing import ResponseTimingMiddleware
from origenlab_api.mirror import router as mirror_router
from origenlab_api.routes import (
    cases,
    contacts,
    emails,
    health,
    institutions,
    operations,
    operator,
    opportunities,
)
from origenlab_api.settings import Settings, get_settings


@asynccontextmanager
async def _app_lifespan(app: FastAPI):  # type: ignore[type-arg]
    """Open the V2 connection pool at startup; close it at shutdown.

    ``app.state.v2_pool`` is set by ``_mount_v2_read_boundary`` before the
    app starts serving, so the pool is always ready when the lifespan runs.
    When V2 is not configured the attribute is absent and the lifespan is a
    no-op — which preserves the current V1-only behaviour.
    """
    pool = getattr(app.state, "v2_pool", None)
    if pool is not None:
        pool.open()
    try:
        yield
    finally:
        if pool is not None:
            pool.close()


def create_app() -> FastAPI:
    settings = get_settings()
    docs_on = openapi_docs_enabled(settings)
    app = FastAPI(
        lifespan=_app_lifespan,
        title="OrigenLab API",
        description=(
            "Operator API (SQLite-first). "
            "Postgres mirror routes under /mirror/* remain read-only reporting. "
            "Does not send email or ingest Gmail, except the one switch-gated POST /v2/commands/send-campaign-test (one admin test to one address, from the shared mailbox). "
            "SQLite remains read-only. "
            "Durable commercial-operations writes are permitted only through "
            "the explicitly allowlisted /operations/* command routes when "
            "commercial writes are enabled and trusted operator identity is present. "
            "The procurement tender workflow permits one explicit file-backed "
            "operator document import; other contact/outreach mutations remain "
            "outside this API."
        ),
        version="0.1.0",
        docs_url="/docs" if docs_on else None,
        redoc_url="/redoc" if docs_on else None,
        openapi_url="/openapi.json" if docs_on else None,
    )
    configure_http_security(app, settings)
    # Starlette runs the last-added middleware outermost.  Add in this order so the
    # outermost (last-added) middleware wraps all others:
    #   RequestLoggingMiddleware → ResponseTimingMiddleware → RequestIdMiddleware
    # RequestLoggingMiddleware is outermost so it measures total dispatch time.
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(ResponseTimingMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(operator.router)
    app.include_router(emails.router)
    app.include_router(cases.router)
    app.include_router(opportunities.router)
    app.include_router(operations.router)
    app.include_router(institutions.router)
    app.include_router(mirror_router)
    app.include_router(contacts.router)
    _mount_v2_read_boundary(app, settings)
    validate_api_settings(settings)
    return app


def _mount_v2_read_boundary(app: FastAPI, settings: Settings) -> None:
    """Mount `/v2/*` only when a V2 database is configured.

    An unconfigured deployment gets no V2 surface at all — not a surface that answers 503 on
    every request. That keeps the running V1 API byte-identical until someone deliberately
    points this app at the V2 durable core, which is the only safe default while the hosted
    phase is frozen (`docs/OPERATIONS.md` §1.1).
    """
    _refuse_unsafe_login_settings(settings)
    if not settings.v2_configured():
        return

    import psycopg

    from origenlab_api.v2.auth_routes import auth_router, google_auth_router
    from origenlab_api.v2.cockpit_repository import CockpitRepository
    from origenlab_api.v2.cockpit_routes import cockpit_router
    from origenlab_api.v2.connection_pool import V2ConnectionPool
    from origenlab_api.v2.google_oidc import build_google_auth_config
    from origenlab_api.v2.identity import build_identity_port
    from origenlab_api.v2.repository import V2Repository
    from origenlab_api.v2.routes import router as v2_router

    target = settings.v2_database_target()
    dsn = target.dsn
    if target.remote:
        from origenlab_api.v2.remote_database import verify_runtime_connection

        # Prove the role and the TLS from inside one ephemeral connection before the pool opens.
        verify_runtime_connection(target, psycopg.connect)
    # One process-wide connection pool.  The pool is opened in _app_lifespan (wait=False),
    # so startup does not block even if the database is momentarily unreachable.
    # Every repository shares this pool instead of opening a new connection per call.
    pool = V2ConnectionPool(
        dsn,
        connect_kwargs=target.connect_options,  # TLS options (sslmode, sslrootcert, …) or {}
    )
    app.state.v2_pool = pool
    # ``connect`` retains the same context-manager API that every repository uses.
    connect = pool.connect
    session_secret = _secret(settings.auth_session_secret)
    if not (session_secret or "").strip() and settings.production_mode():
        # Whatever the identity adapter, production needs one stable address-ref key shared
        # by every worker; a random per-process key is a development convenience only.
        raise ValueError(
            "ORIGENLAB_AUTH_SESSION_SECRET is required when ORIGENLAB_ENV=production and "
            "ORIGENLAB_V2_DATABASE_URL is set"
        )
    if (session_secret or "").strip():
        from origenlab_api.v2.marketing_audience import configure_address_ref_key

        # Pins the key of the opaque address refs viewers join reads by; without the secret
        # each process keeps its own random key (refs then only join within one process).
        configure_address_ref_key(session_secret)
    repository = V2Repository(
        connect, dsn, statement_timeout_ms=settings.v2_statement_timeout_ms
    )
    app.state.v2_repository = repository
    app.state.cockpit_repository = CockpitRepository(
        connect, dsn, statement_timeout_ms=settings.v2_statement_timeout_ms
    )
    google = build_google_auth_config(
        enabled=settings.google_auth_enabled,
        client_id=settings.google_client_id,
        client_secret=_secret(settings.google_client_secret),
        workspace_domain=settings.google_workspace_domain,
        public_base_url=settings.auth_public_base_url,
        session_secret=_secret(settings.auth_session_secret),
        session_ttl_seconds=settings.auth_session_ttl_seconds,
        production=settings.production_mode(),
    )
    app.state.v2_google_auth = google
    profile_login = _build_profile_login(settings, google, connect, dsn)
    app.state.v2_profile_login = profile_login
    # Every Google session — an operator's own account or a shared principal — is a revocable
    # platform.auth_session row (20260928195000); an operator's lives here.
    auth_sessions = None
    if google is not None:
        from origenlab_api.v2.auth_session_store import AuthSessionStore

        auth_sessions = AuthSessionStore(connect, dsn, statement_timeout_ms=settings.v2_statement_timeout_ms)
    app.state.v2_auth_sessions = auth_sessions
    if google is not None:
        from origenlab_api.v2.google_jwks import GoogleJwks

        # One cached copy of Google's signing keys per process; fetched on first sign-in.
        app.state.v2_google_jwks = GoogleJwks()
    # Constructing the port here, at startup, is deliberate: a misconfigured identity must
    # fail the process rather than surface as a per-request error that looks like a bad
    # credential.
    app.state.v2_identity = build_identity_port(
        jwks_url=settings.v2_jwks_url,
        database_url=dsn,
        lookup=repository,
        google=google,
        dev_login_enabled=settings.dev_login_enabled,
        production=settings.production_mode(),
        profiles=profile_login.profiles if profile_login is not None else None,
        sessions=auth_sessions,
        session_signer=profile_login.signer if profile_login is not None else None,
        session_cookie_name=profile_login.cookie_names.session if profile_login is not None else None,
    )
    app.include_router(v2_router)
    app.include_router(cockpit_router)
    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository, load_drive_ledgers
    from origenlab_api.v2.crm_workspace_routes import workspace_router

    ledgers = [p.strip() for p in (settings.v2_drive_archive_ledgers or "").split(",") if p.strip()]
    app.state.crm_workspace = CrmWorkspaceRepository(
        connect,
        dsn,
        drive=load_drive_ledgers(ledgers),
        statement_timeout_ms=settings.v2_statement_timeout_ms,
    )
    app.state.v1_lane_content_dir = settings.v2_v1_lane_content_dir
    app.state.org_suggestions_file = settings.v2_org_suggestions_file
    app.include_router(workspace_router)
    if settings.v2_import_review_plan_dir:
        from origenlab_api.v2.quote_import_review import QuoteImportReviewRepository, load_plan
        from origenlab_api.v2.quote_import_review_routes import import_review_router

        app.state.import_review = (
            load_plan(settings.v2_import_review_plan_dir),
            QuoteImportReviewRepository(
                connect, dsn, statement_timeout_ms=settings.v2_statement_timeout_ms
            ),
            settings.v2_import_review_documents_root,
        )
        app.include_router(import_review_router)
    if settings.v2_case_archive_dir:
        from origenlab_api.v2.quote_case_workspace import load_inputs
        from origenlab_api.v2.quote_case_workspace_routes import case_archive_router

        reports = [r.strip() for r in (settings.v2_case_archive_upload_reports or "").split(",") if r.strip()]
        app.state.case_archive = load_inputs(settings.v2_case_archive_dir, reports)
        app.include_router(case_archive_router)
    app.include_router(auth_router)
    if google is not None:
        app.include_router(google_auth_router)
    _mount_profile_login(app, settings, profile_login, dsn)
    _mount_v2_command_boundary(app, settings, dsn, connect)
    _mount_crm_authoring(app, settings, dsn, connect)
    _mount_catalog(app, settings, dsn, connect)
    _mount_campaign_drafts(app, settings, dsn, connect)
    _mount_audience_freeze(app, settings, dsn, connect)
    _mount_campaign_planning(app, settings, dsn, connect)
    _mount_campaign_blocks(app, settings, dsn, connect)
    _mount_campaign_test_send(app, settings, dsn, connect)
    _mount_unsubscribe(app, settings, dsn, connect)


def _mount_unsubscribe(app: FastAPI, settings: Settings, dsn: str, connect: Any) -> None:
    """W10: the preview is a read and always mounted with V2; the apply command has its own switch."""
    from origenlab_api.v2.unsubscribe_commands import V2UnsubscribeRepository
    from origenlab_api.v2.unsubscribe_routes import unsubscribe_apply_router, unsubscribe_preview_router

    app.state.unsubscribe_apply_enabled = settings.v2_unsubscribe_apply_configured()
    app.state.unsubscribe_repository = V2UnsubscribeRepository(
        connect, dsn, statement_timeout_ms=settings.v2_statement_timeout_ms
    )
    app.include_router(unsubscribe_preview_router)
    if app.state.unsubscribe_apply_enabled:
        app.include_router(unsubscribe_apply_router)


def _mount_audience_freeze(app: FastAPI, settings: Settings, dsn: str, connect: Any) -> None:
    """Mount the audience-freeze command only behind its own switch; W12 rides on it."""
    app.state.audience_freeze_enabled = settings.v2_audience_freeze_configured()
    # The preview and the command read the same flag, so they plan under the same policy.
    app.state.recontact_review_enabled = settings.v2_recontact_review_configured()
    if not app.state.audience_freeze_enabled:
        return

    from origenlab_api.v2.audience_freeze import V2AudienceFreezeRepository
    from origenlab_api.v2.audience_freeze_routes import audience_freeze_router

    app.state.audience_freeze_repository = V2AudienceFreezeRepository(
        connect, dsn, statement_timeout_ms=settings.v2_statement_timeout_ms,
        recontact_review_enabled=app.state.recontact_review_enabled,
    )
    app.include_router(audience_freeze_router)


def _mount_campaign_planning(app: FastAPI, settings: Settings, dsn: str, connect: Any) -> None:
    """Mount the planning command only behind its own switch. It schedules nothing."""
    app.state.campaign_planning_enabled = settings.v2_campaign_planning_configured()
    if not app.state.campaign_planning_enabled:
        return

    from origenlab_api.v2.campaign_planning import V2CampaignPlanningRepository
    from origenlab_api.v2.campaign_planning_routes import campaign_planning_router

    app.state.campaign_planning_repository = V2CampaignPlanningRepository(
        connect, dsn, statement_timeout_ms=settings.v2_statement_timeout_ms
    )
    app.include_router(campaign_planning_router)


def _mount_campaign_test_send(app: FastAPI, settings: Settings, dsn: str, connect: Any) -> None:
    """«Enviar prueba» behind its own switch and a valid token; a bad token leaves it off, never down."""
    app.state.campaign_test_send_enabled = False
    if not settings.v2_campaign_test_send_configured():
        return
    from origenlab_api.v2.gmail_send import GmailSender, load_send_token

    try:
        token = load_send_token(settings.v2_test_send_token_file or "")
    except ValueError as exc:
        logging.getLogger(__name__).warning("campaign test send disabled: %s", exc)
        return
    from origenlab_api.v2.campaign_test_send import V2CampaignTestSendRepository
    from origenlab_api.v2.campaign_test_send_routes import campaign_test_send_router

    app.state.campaign_test_send_repository = V2CampaignTestSendRepository(
        connect, dsn, GmailSender(token), settings.v2_v1_lane_content_dir,
        statement_timeout_ms=settings.v2_statement_timeout_ms,
    )
    app.include_router(campaign_test_send_router)
    app.state.campaign_test_send_enabled = True


def _mount_campaign_blocks(app: FastAPI, settings: Settings, dsn: str, connect: Any) -> None:
    """Mount the admin block/unblock commands only behind their own switch.

    Enforcement lives in the database and the read of the current holds is a workspace route,
    so an existing block is shown and refused whether or not this switch is on.
    """
    app.state.campaign_blocks_enabled = settings.v2_campaign_blocks_configured()
    if not app.state.campaign_blocks_enabled:
        return

    from origenlab_api.v2.campaign_block_routes import campaign_block_router
    from origenlab_api.v2.campaign_blocks import V2CampaignBlockRepository

    app.state.campaign_block_repository = V2CampaignBlockRepository(
        connect, dsn, statement_timeout_ms=settings.v2_statement_timeout_ms
    )
    app.include_router(campaign_block_router)


def _mount_crm_authoring(app: FastAPI, settings: Settings, dsn: str, connect: Any) -> None:
    """Mount the 29 CRM authoring commands only behind their own switch.

    Both a DSN and ``ORIGENLAB_V2_CRM_AUTHORING_ENABLED`` are required: reading the CRM and
    writing freeform people/organizations into it are separate permissions.  Off, every
    authoring path is a 404, which is the right answer for a surface that does not exist.
    """
    app.state.crm_authoring_enabled = settings.crm_authoring_configured()
    if not app.state.crm_authoring_enabled:
        return

    from origenlab_api.v2.crm_authoring import V2CrmAuthoringRepository
    from origenlab_api.v2.crm_authoring_routes import crm_authoring_router

    app.state.crm_authoring_repository = V2CrmAuthoringRepository(
        connect, dsn, statement_timeout_ms=settings.v2_statement_timeout_ms
    )
    app.include_router(crm_authoring_router)


def _mount_catalog(app: FastAPI, settings: Settings, dsn: str, connect: Any) -> None:
    """Mount the catalog reads only behind ``ORIGENLAB_V2_QUOTING_ENABLED``; off, they are a 404."""
    app.state.quoting_enabled = settings.quoting_configured()
    if not app.state.quoting_enabled:
        return

    from origenlab_api.v2.catalog.reads import V2CatalogReads
    from origenlab_api.v2.catalog.routes import catalog_read_router

    app.state.catalog_reads = V2CatalogReads(
        connect, dsn, statement_timeout_ms=settings.v2_statement_timeout_ms
    )
    app.include_router(catalog_read_router)


def _mount_campaign_drafts(app: FastAPI, settings: Settings, dsn: str, connect: Any) -> None:
    """Mount the two campaign-draft commands only behind their own switch."""
    app.state.campaign_drafts_enabled = settings.v2_campaign_drafts_configured()
    if not app.state.campaign_drafts_enabled:
        return

    from origenlab_api.v2.campaign_draft_routes import campaign_draft_router
    from origenlab_api.v2.campaign_drafts import V2CampaignDraftRepository

    app.state.campaign_draft_repository = V2CampaignDraftRepository(
        connect, dsn, statement_timeout_ms=settings.v2_statement_timeout_ms
    )
    app.include_router(campaign_draft_router)


def _build_profile_login(settings: Settings, google: Any, connect: Any, dsn: str) -> Any:
    """The shared-sign-in profile configuration, or None when the switch is off.

    Fails the process — never a request — when the switch is on and anything it needs is
    missing or weak: the pepper (`profile_pin.validate_pepper`, the same rule in and out of
    production), the session secret, or a way to obtain a principal session at all (Google
    sign-in, or the local development shortcut).
    """
    if not settings.profile_login_enabled:
        if settings.dev_profile_principal_email:
            raise ValueError(
                "ORIGENLAB_DEV_PROFILE_PRINCIPAL_EMAIL needs ORIGENLAB_PROFILE_LOGIN_ENABLED=true"
            )
        return None

    from origenlab_api.v2.auth_session import CookieNames, CookieSigner
    from origenlab_api.v2.profile_auth import ProfileAuthRepository
    from origenlab_api.v2.profile_pin import PinHasher, validate_pepper
    from origenlab_api.v2.profile_routes import ProfileLoginConfig, is_reserved_dev_address

    session_secret = _secret(settings.auth_session_secret)
    pepper = validate_pepper(_secret(settings.profile_pin_pepper), session_secret=session_secret)
    if not (session_secret or "").strip():
        raise ValueError("ORIGENLAB_PROFILE_LOGIN_ENABLED requires ORIGENLAB_AUTH_SESSION_SECRET")
    dev_email = (settings.dev_profile_principal_email or "").strip().lower() or None
    if dev_email is not None:
        if settings.production_mode():
            raise ValueError("ORIGENLAB_DEV_PROFILE_PRINCIPAL_EMAIL is refused when ORIGENLAB_ENV=production")
        if not settings.dev_login_enabled:
            raise ValueError("ORIGENLAB_DEV_PROFILE_PRINCIPAL_EMAIL needs ORIGENLAB_DEV_LOGIN_ENABLED=true")
        if not is_reserved_dev_address(dev_email):
            raise ValueError(
                "ORIGENLAB_DEV_PROFILE_PRINCIPAL_EMAIL must be an invented address under a reserved "
                "test domain (.test, .invalid, .example, .localhost, example.com/org/net)"
            )
    if google is None and dev_email is None:
        raise ValueError(
            "ORIGENLAB_PROFILE_LOGIN_ENABLED needs a way to sign in: ORIGENLAB_GOOGLE_AUTH_ENABLED, "
            "or, for local development only, ORIGENLAB_DEV_PROFILE_PRINCIPAL_EMAIL"
        )
    secure = google.secure_cookies if google is not None else False
    return ProfileLoginConfig(
        profiles=ProfileAuthRepository(connect, dsn, statement_timeout_ms=settings.v2_statement_timeout_ms),
        hasher=PinHasher(pepper),
        signer=google.signer if google is not None else CookieSigner(session_secret or ""),
        cookie_names=google.cookie_names if google is not None else CookieNames.for_secure(secure),
        secure_cookies=secure,
        session_ttl_seconds=settings.auth_session_ttl_seconds,
        production=settings.production_mode(),
        dev_principal_email=dev_email,
    )


def _mount_profile_login(app: FastAPI, settings: Settings, profile_login: Any, dsn: str) -> None:
    """Mount the profile routes when the switch is on; the dev shortcut only locally."""
    app.state.v2_dev_profile_login = False
    if profile_login is None:
        return
    from origenlab_api.v2.identity import is_loopback_dsn
    from origenlab_api.v2.profile_routes import dev_profile_router, profile_router

    app.include_router(profile_router)
    if profile_login.dev_principal_email is not None:
        # Checked again here, on the database this process will actually read: the shortcut
        # never exists against anything but a literal loopback address, and never in production.
        if settings.production_mode() or not is_loopback_dsn(dsn):
            raise ValueError(
                "the local profile sign-in shortcut refuses to load: production, or a database "
                "that is not on a literal loopback address"
            )
        app.include_router(dev_profile_router)
        app.state.v2_dev_profile_login = True


def _secret(value: SecretStr | None) -> str | None:
    return value.get_secret_value() if value is not None else None


def _refuse_unsafe_login_settings(settings: Settings) -> None:
    """Refuse login settings that are unsafe whether or not the V2 boundary is mounted.

    Google sign-in maps the address to `platform.operator`, which only the V2 database holds,
    so switching it on without one would mount nothing and look like it worked. The
    development header login is refused in production even where it would have no effect,
    so that a deployed environment carrying it is caught at the first start.
    """
    if settings.production_mode() and settings.dev_login_enabled:
        raise ValueError(
            "ORIGENLAB_DEV_LOGIN_ENABLED is refused when ORIGENLAB_ENV=production"
        )
    if settings.profile_login_enabled and not settings.v2_configured():
        raise ValueError(
            "ORIGENLAB_PROFILE_LOGIN_ENABLED requires ORIGENLAB_V2_DATABASE_URL: profiles and "
            "their PINs live in the V2 database"
        )
    if settings.production_mode() and settings.dev_profile_principal_email:
        raise ValueError("ORIGENLAB_DEV_PROFILE_PRINCIPAL_EMAIL is refused when ORIGENLAB_ENV=production")
    if settings.google_auth_enabled and not settings.v2_configured():
        raise ValueError(
            "ORIGENLAB_GOOGLE_AUTH_ENABLED requires ORIGENLAB_V2_DATABASE_URL: the signed-in "
            "address is mapped to platform.operator in the V2 database"
        )


def _mount_v2_command_boundary(
    app: FastAPI, settings: Settings, dsn: str, connect: Any
) -> None:
    """Mount `POST /v2/commands/*` only when it has been switched on deliberately.

    Configuring a V2 database says "read this". It does not say "record durable human
    decisions into it", and those are different permissions to grant — so the command router
    needs `ORIGENLAB_V2_COMMANDS_ENABLED` as well as the DSN. Off, the router is absent and
    every command path is a 404, which is the right answer for a surface that does not exist
    rather than a 503 for one that does but will not talk.
    """
    if not settings.v2_commands_configured():
        return

    from origenlab_api.v2.case_command_repository import V2CaseCommandRepository
    from origenlab_api.v2.case_command_routes import case_command_router
    from origenlab_api.v2.command_repository import V2CommandRepository
    from origenlab_api.v2.command_routes import command_router

    app.state.v2_command_repository = V2CommandRepository(
        connect, dsn, statement_timeout_ms=settings.v2_statement_timeout_ms
    )
    # The commercial-case commands run on the same database, as the same role, behind the
    # same switch. A second repository rather than a second connection pool: both are
    # `CommandTransaction`, and neither knows anything the other does not.
    app.state.v2_case_command_repository = V2CaseCommandRepository(
        connect, dsn, statement_timeout_ms=settings.v2_statement_timeout_ms
    )
    app.include_router(command_router)
    app.include_router(case_command_router)


app = create_app()
