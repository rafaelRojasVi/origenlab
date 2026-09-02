"""Durable Postgres customer-quote command repository (CRM-Q1).

Quote numbers are allocated by a single row-locked
``UPDATE commercial.customer_quote_number_series ... RETURNING`` in the same
transaction as the quote INSERT. Concurrent allocations serialize on the
series row lock and the ``uq_customer_quote_number`` unique constraint is the
database-level backstop -- never ``MAX(...) + 1``, browser-side numbering,
timestamps, or random numbers.

When no numbering configuration exists the repository fails closed with
``QuoteNumberingNotConfiguredError``: the production sequence start is a
business decision recorded via explicit operator configuration, never
invented here.
"""

from __future__ import annotations

import json
import re
import uuid

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import NoReturn
from zoneinfo import ZoneInfo

from origenlab_api.repositories.postgres.commercial_operations import (
    CommercialOperationConflictError,
    CommercialOperationNotFoundError,
    _claim_idempotency,
    _store_idempotency_result,
)
from origenlab_api.repositories.postgres.common import require_psycopg
from origenlab_api.repositories.postgres.write_common import (
    postgres_write_connection,
)
from origenlab_api.settings import QuoteNumberingConfig, Settings

__all__ = [
    "CUSTOMER_QUOTE_SERIES_KEY",
    "PROVISION_ATTEMPT_LEASE_SECONDS",
    "CustomerQuote",
    "CustomerQuoteBundle",
    "CustomerQuoteDriveWorkspace",
    "CustomerQuoteEvent",
    "CustomerQuoteRevision",
    "PostgresCustomerQuoteRepository",
    "QuoteNumberingConfig",
    "QuoteNumberingNotConfiguredError",
    "QuoteNumberingPolicyMismatchError",
    "chile_issue_year",
]


# OrigenLab is a Chilean business: the issue year is the business-local
# calendar year at allocation time, never UTC's -- a naive UTC-based year
# would occasionally allocate the wrong year for quotes created shortly
# after UTC midnight (America/Santiago is behind UTC, so its own local new
# year always arrives later).
_CHILE_TZ = ZoneInfo("America/Santiago")


def chile_issue_year(moment: datetime) -> int:
    """The America/Santiago business-local calendar year for ``moment``."""
    return moment.astimezone(_CHILE_TZ).year


# The single logical customer-quote numbering series. This is a fixed
# identity, deliberately NOT the configured prefix: the configured prefix is
# only ever a seed for this series' first allocation. Keying the series row
# by prefix would let a later prefix typo or environment change silently
# start a second series from the seed instead of failing closed against the
# durable policy already recorded by the first allocation.
CUSTOMER_QUOTE_SERIES_KEY = "customer_quote"

# Server-owned active-attempt lease duration. A begun attempt exclusively
# owns Drive-provider calls for at most this long; after it expires the
# attempt is safely reclaimable (crash recovery). This must stay strictly
# above the worst-case wall-clock duration of one provisioning attempt, not
# an unexplained arbitrary number:
#
#   up to 5 Drive HTTP calls per attempt (verify_destination, find_folder,
#   create_folder, find_sheet, copy_template_sheet) + up to 1 credential
#   refresh (at most once per attempt: once refreshed, the token stays
#   valid for its ~1 hour lifetime, far longer than one attempt) = 6 calls,
#   each individually bounded by the single Drive request timeout
#   (origenlab_api.drive.factory._REQUEST_TIMEOUT_SECONDS /
#   _TOKEN_REFRESH_TIMEOUT_SECONDS, both 20s) = 6 * 20s = 120s worst case.
#
# 300s gives a >2x safety margin over that bound. This relationship is
# asserted directly (not just documented) by
# tests/test_customer_quote_drive_provision_fencing_postgres.py.
PROVISION_ATTEMPT_LEASE_SECONDS = 300


class QuoteNumberingNotConfiguredError(RuntimeError):
    """Quote-number allocation attempted before the numbering business
    decision (prefix / pad width / next serial) was configured."""


class QuoteNumberingPolicyMismatchError(RuntimeError):
    """Configured prefix/pad width disagrees with the durable series policy
    already established by the first allocation.

    The environment seed only ever affects first initialization; after that
    the durable ``customer_quote_number_series`` row is the counter truth.
    A later configuration drift (prefix typo, environment change) must fail
    closed here -- it must never silently start a second series from the
    seed."""


# Redacted failure categories only: a safe slug, never provider payloads,
# exception text, URLs, or credentials.
_FAILURE_CATEGORY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

_CREATE_COMMAND_KIND = "customer_quote_create"
_ADOPT_COMMAND_KIND = "customer_quote_adopt_drive"
_CLOSE_COMMAND_KIND = "customer_quote_close"

_CLOSED_STATUS_BY_OUTCOME = {"won": "closed_won", "null": "closed_null"}


@dataclass(frozen=True)
class CustomerQuote:
    quote_id: str
    sales_opportunity_id: str

    # The allocated base serial and the two distinct identifiers derived
    # from it: quote_number is the human customer-facing business number
    # (<serial>-<issue_year 2-digit>, e.g. "01183-26"); document_number is
    # the separate Drive document stem (<document_prefix><serial>, e.g.
    # "CN01183"). Neither is parsed from the other at read time.
    #
    # serial/issue_year are NULL when quote_origin == "adopted": an adopted
    # quote's identifiers were never allocated by customer_quote_number_series
    # (see adopt_drive_folder) -- quote_number/document_number stay
    # mandatory and unique regardless of origin.
    quote_number: str
    serial: int | None
    issue_year: int | None
    document_number: str
    quote_origin: str

    status: str
    version: int
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class CustomerQuoteRevision:
    quote_id: str
    revision_number: int
    template_reference: str | None
    status: str
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime


@dataclass(frozen=True)
class CustomerQuoteEvent:
    event_id: str
    quote_id: str
    event_type: str
    actor_key: str
    payload: dict[str, object]
    created_at: datetime


@dataclass(frozen=True)
class CustomerQuoteDriveWorkspace:
    quote_id: str
    provider: str
    provisioning_status: str
    folder_id: str | None
    folder_web_url: str | None
    sheet_file_id: str | None
    sheet_web_url: str | None
    failure_category: str | None
    attempt_count: int
    version: int
    lease_expires_at: datetime | None
    requested_at: datetime | None
    completed_at: datetime | None
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class CustomerQuoteBundle:
    quote: CustomerQuote
    revision: CustomerQuoteRevision
    workspace: CustomerQuoteDriveWorkspace
    sales_opportunity_title: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _require_https_url(value: str, *, field: str) -> str:
    normalized = value.strip()

    if not normalized.startswith("https://") or len(normalized) > 2048:
        raise ValueError(f"{field} must be a safe https URL")

    return normalized


def _require_safe_failure_category(value: str) -> str:
    normalized = value.strip()

    if _FAILURE_CATEGORY_RE.fullmatch(normalized) is None:
        raise ValueError(
            "failure_category must be a redacted category slug"
        )

    return normalized


def _quote_from_row(row: dict[str, object]) -> CustomerQuote:
    data = {
        key: row[key]
        for key in (
            "quote_id",
            "sales_opportunity_id",
            "quote_number",
            "serial",
            "issue_year",
            "document_number",
            "quote_origin",
            "status",
            "version",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        )
    }
    return CustomerQuote(**data)  # type: ignore[arg-type]


def _fetch_bundle(cur: object, *, quote_id: str) -> CustomerQuoteBundle | None:
    cur.execute(
        """
        SELECT
          q.*,
          so.title AS sales_opportunity_title
        FROM commercial.customer_quote q
        JOIN commercial.sales_opportunity so
          ON so.sales_opportunity_id = q.sales_opportunity_id
        WHERE q.quote_id = %(quote_id)s
        LIMIT 1
        """,
        {"quote_id": quote_id},
    )

    quote_row = cur.fetchone()

    if quote_row is None:
        return None

    title = str(quote_row["sales_opportunity_title"])

    cur.execute(
        """
        SELECT *
        FROM commercial.customer_quote_revision
        WHERE quote_id = %(quote_id)s
        ORDER BY revision_number DESC
        LIMIT 1
        """,
        {"quote_id": quote_id},
    )

    revision_row = cur.fetchone()

    if revision_row is None:
        raise RuntimeError(f"Customer quote has no revision: {quote_id}")

    cur.execute(
        """
        SELECT *
        FROM commercial.customer_quote_drive_workspace
        WHERE quote_id = %(quote_id)s
        LIMIT 1
        """,
        {"quote_id": quote_id},
    )

    workspace_row = cur.fetchone()

    if workspace_row is None:
        raise RuntimeError(f"Customer quote has no workspace row: {quote_id}")

    return CustomerQuoteBundle(
        quote=_quote_from_row(dict(quote_row)),
        revision=CustomerQuoteRevision(**dict(revision_row)),
        workspace=CustomerQuoteDriveWorkspace(**dict(workspace_row)),
        sales_opportunity_title=title,
    )


def _insert_event(
    cur: object,
    *,
    quote_id: str,
    event_type: str,
    actor_key: str,
    payload: dict[str, object],
    created_at: datetime,
) -> None:
    cur.execute(
        """
        INSERT INTO commercial.customer_quote_event (
          event_id,
          quote_id,
          event_type,
          actor_key,
          payload,
          created_at
        )
        VALUES (
          %(event_id)s,
          %(quote_id)s,
          %(event_type)s,
          %(actor_key)s,
          %(payload)s,
          %(created_at)s
        )
        """,
        {
            "event_id": str(uuid.uuid4()),
            "quote_id": quote_id,
            "event_type": event_type,
            "actor_key": actor_key,
            "payload": json.dumps(payload),
            "created_at": created_at,
        },
    )


def _raise_stale_or_missing_workspace(
    cur: object, *, quote_id: str, attempt_version: int
) -> NoReturn:
    """A complete/fail CAS update matched zero rows: distinguish "workspace
    does not exist" from "stale attempt token" (already superseded by a
    newer attempt, or the workspace is no longer 'pending') and raise the
    appropriate error. Never modifies anything -- read-only diagnosis."""

    cur.execute(
        """
        SELECT version, provisioning_status
        FROM commercial.customer_quote_drive_workspace
        WHERE quote_id = %(quote_id)s
        LIMIT 1
        """,
        {"quote_id": quote_id},
    )

    existing = cur.fetchone()

    if existing is None:
        raise CommercialOperationNotFoundError(
            f"Customer quote workspace not found: {quote_id}"
        )

    raise CommercialOperationConflictError(
        "Stale Drive provisioning attempt: token "
        f"{attempt_version} no longer matches current state "
        f"(version={existing['version']}, "
        f"status={existing['provisioning_status']})"
    )


class PostgresCustomerQuoteRepository:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def create_quote(
        self,
        *,
        quote_id: str,
        sales_opportunity_id: str,
        operator: str,
        idempotency_key: str,
        request_fingerprint: str,
        numbering: QuoteNumberingConfig | None,
        template_reference: str | None,
    ) -> CustomerQuoteBundle:
        pg = require_psycopg()
        now = _utcnow()

        with postgres_write_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                replay_result_id = _claim_idempotency(
                    cur,
                    operator=operator,
                    idempotency_key=idempotency_key,
                    command_kind=_CREATE_COMMAND_KIND,
                    request_fingerprint=request_fingerprint,
                )

                if replay_result_id is not None:
                    replay = _fetch_bundle(cur, quote_id=replay_result_id)

                    if replay is None:
                        raise CommercialOperationConflictError(
                            "Idempotency result customer quote is missing"
                        )

                    return replay

                cur.execute(
                    """
                    SELECT
                      sales_opportunity_id,
                      title
                    FROM commercial.sales_opportunity
                    WHERE sales_opportunity_id = %(sales_opportunity_id)s
                    LIMIT 1
                    """,
                    {"sales_opportunity_id": sales_opportunity_id},
                )

                source = cur.fetchone()

                if source is None:
                    raise CommercialOperationNotFoundError(
                        f"Sales opportunity not found: {sales_opportunity_id}"
                    )

                title = str(source["title"])

                if numbering is None:
                    # Fail closed before touching the series or quote tables:
                    # the numbering business decision has not been recorded.
                    raise QuoteNumberingNotConfiguredError(
                        "quote_numbering_not_configured"
                    )

                # Seed the series only on very first allocation; afterwards
                # the durable row (not env config) is the counter truth. The
                # series identity is the fixed CUSTOMER_QUOTE_SERIES_KEY, not
                # the configured prefix -- so a later prefix/pad-width
                # change can never silently start a second series. The
                # no-op DO UPDATE (versus DO NOTHING) makes this an
                # upsert-returning-always: on conflict it changes nothing
                # (series_key back to itself) but still locks the row and
                # returns its current, already-durable prefix/pad_width so
                # they can be compared against the configured values below.
                cur.execute(
                    """
                    INSERT INTO commercial.customer_quote_number_series (
                      series_key,
                      document_prefix,
                      pad_width,
                      next_serial,
                      created_by,
                      updated_by,
                      created_at,
                      updated_at
                    )
                    VALUES (
                      %(series_key)s,
                      %(document_prefix)s,
                      %(pad_width)s,
                      %(next_serial)s,
                      %(operator)s,
                      %(operator)s,
                      %(now)s,
                      %(now)s
                    )
                    ON CONFLICT (
                      series_key
                    )
                    DO UPDATE SET
                      series_key = EXCLUDED.series_key
                    RETURNING
                      document_prefix,
                      pad_width
                    """,
                    {
                        "series_key": CUSTOMER_QUOTE_SERIES_KEY,
                        "document_prefix": numbering.document_prefix,
                        "pad_width": numbering.serial_pad_width,
                        "next_serial": numbering.seed_next_serial,
                        "operator": operator,
                        "now": now,
                    },
                )

                policy_row = cur.fetchone()

                if policy_row is None:
                    raise RuntimeError(
                        "Quote number series policy row missing after upsert"
                    )

                if (
                    str(policy_row["document_prefix"]) != numbering.document_prefix
                    or int(policy_row["pad_width"]) != numbering.serial_pad_width
                ):
                    raise QuoteNumberingPolicyMismatchError(
                        "quote_numbering_policy_mismatch: configured "
                        f"document prefix/pad width ({numbering.document_prefix}/"
                        f"{numbering.serial_pad_width}) disagrees with the "
                        "durable series policy "
                        f"({policy_row['document_prefix']}/"
                        f"{policy_row['pad_width']}) established by the "
                        "first allocation"
                    )

                # Atomic allocation: the row lock serializes concurrent
                # allocations; uq_customer_quote_number is the DB backstop.
                cur.execute(
                    """
                    UPDATE commercial.customer_quote_number_series
                    SET
                      next_serial = next_serial + 1,
                      updated_by = %(operator)s,
                      updated_at = %(now)s
                    WHERE series_key = %(series_key)s
                    RETURNING
                      document_prefix,
                      pad_width,
                      next_serial - 1 AS allocated_serial
                    """,
                    {
                        "series_key": CUSTOMER_QUOTE_SERIES_KEY,
                        "operator": operator,
                        "now": now,
                    },
                )

                allocated = cur.fetchone()

                if allocated is None:
                    raise RuntimeError(
                        "Quote number series row disappeared during allocation"
                    )

                allocated_serial = int(allocated["allocated_serial"])
                padded_serial = str(allocated_serial).zfill(
                    int(allocated["pad_width"])
                )
                issue_year = chile_issue_year(now)
                quote_number = f"{padded_serial}-{issue_year % 100:02d}"
                document_number = f"{allocated['document_prefix']}{padded_serial}"

                cur.execute(
                    """
                    INSERT INTO commercial.customer_quote (
                      quote_id,
                      sales_opportunity_id,
                      quote_number,
                      serial,
                      issue_year,
                      document_number,
                      status,
                      version,
                      created_by,
                      updated_by,
                      created_at,
                      updated_at
                    )
                    VALUES (
                      %(quote_id)s,
                      %(sales_opportunity_id)s,
                      %(quote_number)s,
                      %(serial)s,
                      %(issue_year)s,
                      %(document_number)s,
                      'draft',
                      1,
                      %(operator)s,
                      %(operator)s,
                      %(now)s,
                      %(now)s
                    )
                    RETURNING *
                    """,
                    {
                        "quote_id": quote_id,
                        "sales_opportunity_id": sales_opportunity_id,
                        "quote_number": quote_number,
                        "serial": allocated_serial,
                        "issue_year": issue_year,
                        "document_number": document_number,
                        "operator": operator,
                        "now": now,
                    },
                )

                quote_row = cur.fetchone()

                if quote_row is None:
                    raise RuntimeError("Customer quote insert returned no row")

                quote = _quote_from_row(dict(quote_row))

                cur.execute(
                    """
                    INSERT INTO commercial.customer_quote_revision (
                      quote_id,
                      revision_number,
                      template_reference,
                      status,
                      created_by,
                      created_at,
                      updated_by,
                      updated_at
                    )
                    VALUES (
                      %(quote_id)s,
                      %(revision_number)s,
                      %(template_reference)s,
                      'draft',
                      %(operator)s,
                      %(now)s,
                      %(operator)s,
                      %(now)s
                    )
                    RETURNING *
                    """,
                    {
                        "quote_id": quote_id,
                        "revision_number": 1,
                        "template_reference": template_reference,
                        "operator": operator,
                        "now": now,
                    },
                )

                revision_row = cur.fetchone()

                if revision_row is None:
                    raise RuntimeError(
                        "Customer quote revision insert returned no row"
                    )

                cur.execute(
                    """
                    INSERT INTO commercial.customer_quote_drive_workspace (
                      quote_id,
                      provider,
                      provisioning_status,
                      attempt_count,
                      version,
                      created_by,
                      updated_by,
                      created_at,
                      updated_at
                    )
                    VALUES (
                      %(quote_id)s,
                      'google_drive',
                      'pending',
                      0,
                      1,
                      %(operator)s,
                      %(operator)s,
                      %(now)s,
                      %(now)s
                    )
                    RETURNING *
                    """,
                    {
                        "quote_id": quote_id,
                        "operator": operator,
                        "now": now,
                    },
                )

                workspace_row = cur.fetchone()

                if workspace_row is None:
                    raise RuntimeError(
                        "Customer quote workspace insert returned no row"
                    )

                _insert_event(
                    cur,
                    quote_id=quote_id,
                    event_type="quote_created",
                    actor_key=operator,
                    payload={
                        "quote_number": quote.quote_number,
                        "document_number": quote.document_number,
                        "sales_opportunity_id": sales_opportunity_id,
                        "revision_number": 1,
                        "template_reference": template_reference,
                    },
                    created_at=now,
                )

                _store_idempotency_result(
                    cur,
                    operator=operator,
                    idempotency_key=idempotency_key,
                    result_id=quote_id,
                )

                return CustomerQuoteBundle(
                    quote=quote,
                    revision=CustomerQuoteRevision(**dict(revision_row)),
                    workspace=CustomerQuoteDriveWorkspace(**dict(workspace_row)),
                    sales_opportunity_title=title,
                )

    def get_quote_bundle(self, *, quote_id: str) -> CustomerQuoteBundle | None:
        pg = require_psycopg()

        with postgres_write_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                return _fetch_bundle(cur, quote_id=quote_id)

    def begin_drive_provision_attempt(
        self,
        *,
        quote_id: str,
        operator: str,
        expected_version: int,
    ) -> CustomerQuoteDriveWorkspace:
        pg = require_psycopg()
        now = _utcnow()
        lease_expires_at = now + timedelta(seconds=PROVISION_ATTEMPT_LEASE_SECONDS)

        with postgres_write_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                # The version check alone only rejects a STALE version. The
                # additional lease clause is what actually prevents two
                # overlapping Drive-provider calls: an in-flight attempt's
                # own begin_drive_provision_attempt already committed (this
                # method's transaction ends here, well before the service
                # layer calls Drive) and bumped the version, so a second
                # caller reading that new version and retrying would
                # otherwise satisfy the version check while the first
                # attempt is still actively running.
                cur.execute(
                    """
                    UPDATE commercial.customer_quote_drive_workspace
                    SET
                      attempt_count = attempt_count + 1,
                      version = version + 1,
                      provisioning_status = 'pending',
                      failure_category = NULL,
                      lease_expires_at = %(lease_expires_at)s,
                      requested_at = %(now)s,
                      updated_by = %(operator)s,
                      updated_at = %(now)s
                    WHERE quote_id = %(quote_id)s
                      AND version = %(expected_version)s
                      AND provisioning_status <> 'ready'
                      AND (
                        provisioning_status <> 'pending'
                        OR lease_expires_at IS NULL
                        OR lease_expires_at <= %(now)s
                      )
                    RETURNING *
                    """,
                    {
                        "quote_id": quote_id,
                        "expected_version": expected_version,
                        "operator": operator,
                        "now": now,
                        "lease_expires_at": lease_expires_at,
                    },
                )

                row = cur.fetchone()

                if row is None:
                    cur.execute(
                        """
                        SELECT *
                        FROM commercial.customer_quote_drive_workspace
                        WHERE quote_id = %(quote_id)s
                        LIMIT 1
                        """,
                        {"quote_id": quote_id},
                    )

                    existing = cur.fetchone()

                    if existing is None:
                        raise CommercialOperationNotFoundError(
                            f"Customer quote workspace not found: {quote_id}"
                        )

                    if existing["provisioning_status"] == "ready":
                        raise CommercialOperationConflictError(
                            "Drive workspace already provisioned"
                        )

                    if (
                        existing["version"] == expected_version
                        and existing["provisioning_status"] == "pending"
                        and existing["lease_expires_at"] is not None
                        and existing["lease_expires_at"] > now
                    ):
                        raise CommercialOperationConflictError(
                            "Drive workspace provisioning attempt already "
                            "active: retry after "
                            f"{existing['lease_expires_at'].isoformat()}"
                        )

                    raise CommercialOperationConflictError(
                        "Drive workspace version conflict: expected "
                        f"{expected_version}, found {existing['version']}"
                    )

                workspace = CustomerQuoteDriveWorkspace(**dict(row))

                _insert_event(
                    cur,
                    quote_id=quote_id,
                    event_type="drive_provision_requested",
                    actor_key=operator,
                    payload={
                        "attempt_count": workspace.attempt_count,
                    },
                    created_at=now,
                )

                return workspace

    def complete_drive_provision(
        self,
        *,
        quote_id: str,
        operator: str,
        attempt_version: int,
        folder_id: str,
        folder_web_url: str,
        sheet_file_id: str | None = None,
        sheet_web_url: str | None = None,
    ) -> CustomerQuoteDriveWorkspace:
        safe_folder_url = _require_https_url(
            folder_web_url,
            field="folder_web_url",
        )

        if (sheet_file_id is None) != (sheet_web_url is None):
            raise ValueError(
                "sheet_file_id and sheet_web_url must both be set or both "
                "be None"
            )

        safe_sheet_url = (
            _require_https_url(sheet_web_url, field="sheet_web_url")
            if sheet_web_url is not None
            else None
        )

        # Template-document provisioning is an explicit, separately-gated
        # step (see origenlab_api.drive.factory): completing with no sheet
        # means the workspace is honestly folder_ready, never ready (which
        # means folder + copied template document both exist).
        target_status = "ready" if sheet_file_id is not None else "folder_ready"
        event_type = (
            "drive_workspace_ready"
            if sheet_file_id is not None
            else "drive_workspace_folder_ready"
        )

        pg = require_psycopg()
        now = _utcnow()

        with postgres_write_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                # Compare-and-set against the exact attempt token
                # begin_drive_provision_attempt returned: a stale completion
                # (an attempt whose lease already expired and was reclaimed
                # by a newer attempt) must conflict here rather than
                # overwrite whatever the newer attempt is doing.
                cur.execute(
                    """
                    UPDATE commercial.customer_quote_drive_workspace
                    SET
                      provisioning_status = %(target_status)s,
                      folder_id = %(folder_id)s,
                      folder_web_url = %(folder_web_url)s,
                      sheet_file_id = %(sheet_file_id)s,
                      sheet_web_url = %(sheet_web_url)s,
                      failure_category = NULL,
                      completed_at = %(now)s,
                      version = version + 1,
                      lease_expires_at = NULL,
                      updated_by = %(operator)s,
                      updated_at = %(now)s
                    WHERE quote_id = %(quote_id)s
                      AND version = %(attempt_version)s
                      AND provisioning_status = 'pending'
                    RETURNING *
                    """,
                    {
                        "quote_id": quote_id,
                        "attempt_version": attempt_version,
                        "target_status": target_status,
                        "folder_id": folder_id,
                        "folder_web_url": safe_folder_url,
                        "sheet_file_id": sheet_file_id,
                        "sheet_web_url": safe_sheet_url,
                        "operator": operator,
                        "now": now,
                    },
                )

                row = cur.fetchone()

                if row is None:
                    _raise_stale_or_missing_workspace(
                        cur, quote_id=quote_id, attempt_version=attempt_version
                    )

                workspace = CustomerQuoteDriveWorkspace(**dict(row))

                _insert_event(
                    cur,
                    quote_id=quote_id,
                    event_type=event_type,
                    actor_key=operator,
                    payload={
                        "folder_id": folder_id,
                        "sheet_file_id": sheet_file_id,
                    },
                    created_at=now,
                )

                return workspace

    def fail_drive_provision(
        self,
        *,
        quote_id: str,
        operator: str,
        attempt_version: int,
        failure_category: str,
        folder_id: str | None = None,
        folder_web_url: str | None = None,
    ) -> CustomerQuoteDriveWorkspace:
        safe_category = _require_safe_failure_category(failure_category)
        safe_folder_url = (
            _require_https_url(folder_web_url, field="folder_web_url")
            if folder_web_url is not None
            else None
        )

        pg = require_psycopg()
        now = _utcnow()

        with postgres_write_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                # COALESCE keeps any partial artifacts discoverable: a folder
                # created before a later step failed must never be dropped
                # from durable state (and is never deleted in Drive).
                # Compare-and-set against the exact attempt token, scoped to
                # provisioning_status = 'pending': a stale failure (this
                # attempt's lease already expired and was reclaimed by a
                # newer attempt, or a newer attempt already completed it)
                # must conflict rather than overwrite a newer attempt's
                # state -- in particular it must never flip an already
                # 'ready' workspace back to 'failed'.
                cur.execute(
                    """
                    UPDATE commercial.customer_quote_drive_workspace
                    SET
                      provisioning_status = 'failed',
                      failure_category = %(failure_category)s,
                      folder_id = COALESCE(%(folder_id)s, folder_id),
                      folder_web_url = COALESCE(%(folder_web_url)s, folder_web_url),
                      version = version + 1,
                      lease_expires_at = NULL,
                      updated_by = %(operator)s,
                      updated_at = %(now)s
                    WHERE quote_id = %(quote_id)s
                      AND version = %(attempt_version)s
                      AND provisioning_status = 'pending'
                    RETURNING *
                    """,
                    {
                        "quote_id": quote_id,
                        "attempt_version": attempt_version,
                        "failure_category": safe_category,
                        "folder_id": folder_id,
                        "folder_web_url": safe_folder_url,
                        "operator": operator,
                        "now": now,
                    },
                )

                row = cur.fetchone()

                if row is None:
                    _raise_stale_or_missing_workspace(
                        cur, quote_id=quote_id, attempt_version=attempt_version
                    )

                workspace = CustomerQuoteDriveWorkspace(**dict(row))

                _insert_event(
                    cur,
                    quote_id=quote_id,
                    event_type="drive_provision_failed",
                    actor_key=operator,
                    payload={
                        "failure_category": safe_category,
                        "partial_folder": folder_id is not None,
                    },
                    created_at=now,
                )

                return workspace

    def _transition_revision(
        self,
        *,
        quote_id: str,
        operator: str,
        expected_version: int,
        legal_from_statuses: frozenset[str],
        to_status: str,
        event_type: str,
    ) -> CustomerQuoteBundle:
        """Shared CAS/event-append plumbing for the four revision-workflow
        commands. The public methods below each hardcode their own
        (legal_from_statuses, to_status, event_type) -- a caller of this
        repository can only ever invoke one of those four fixed
        transitions, never construct an arbitrary one.

        Concurrency is customer_quote.version alone: the revision itself
        carries no version column. Locking the customer_quote row FOR
        UPDATE first serializes every transition/adoption attempt against
        this quote_id, so a second SELECT ... FOR UPDATE on the revision
        row is unnecessary.
        """

        pg = require_psycopg()
        now = _utcnow()

        with postgres_write_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                cur.execute(
                    """
                    SELECT version
                    FROM commercial.customer_quote
                    WHERE quote_id = %(quote_id)s
                    FOR UPDATE
                    """,
                    {"quote_id": quote_id},
                )

                existing_quote = cur.fetchone()

                if existing_quote is None:
                    raise CommercialOperationNotFoundError(
                        f"Customer quote not found: {quote_id}"
                    )

                current_version = int(existing_quote["version"])

                if expected_version != current_version:
                    raise CommercialOperationConflictError(
                        "Customer quote version conflict"
                    )

                cur.execute(
                    """
                    SELECT revision_number, status
                    FROM commercial.customer_quote_revision
                    WHERE quote_id = %(quote_id)s
                    ORDER BY revision_number DESC
                    LIMIT 1
                    """,
                    {"quote_id": quote_id},
                )

                existing_revision = cur.fetchone()

                if existing_revision is None:
                    raise RuntimeError(
                        f"Customer quote has no revision: {quote_id}"
                    )

                revision_number = int(existing_revision["revision_number"])
                current_status = str(existing_revision["status"])

                if current_status not in legal_from_statuses:
                    raise CommercialOperationConflictError(
                        f"Customer quote revision cannot transition from "
                        f"'{current_status}' to '{to_status}'"
                    )

                cur.execute(
                    """
                    UPDATE commercial.customer_quote
                    SET
                      version = version + 1,
                      updated_by = %(operator)s,
                      updated_at = %(now)s
                    WHERE quote_id = %(quote_id)s
                      AND version = %(current_version)s
                    RETURNING version
                    """,
                    {
                        "quote_id": quote_id,
                        "operator": operator,
                        "now": now,
                        "current_version": current_version,
                    },
                )

                if cur.fetchone() is None:
                    raise CommercialOperationConflictError(
                        "Customer quote concurrent update conflict"
                    )

                cur.execute(
                    """
                    UPDATE commercial.customer_quote_revision
                    SET
                      status = %(to_status)s,
                      updated_by = %(operator)s,
                      updated_at = %(now)s
                    WHERE quote_id = %(quote_id)s
                      AND revision_number = %(revision_number)s
                    """,
                    {
                        "quote_id": quote_id,
                        "revision_number": revision_number,
                        "to_status": to_status,
                        "operator": operator,
                        "now": now,
                    },
                )

                _insert_event(
                    cur,
                    quote_id=quote_id,
                    event_type=event_type,
                    actor_key=operator,
                    payload={
                        "revision_number": revision_number,
                        "from_status": current_status,
                        "to_status": to_status,
                    },
                    created_at=now,
                )

                bundle = _fetch_bundle(cur, quote_id=quote_id)

                if bundle is None:
                    raise RuntimeError(
                        f"Customer quote disappeared mid-transition: {quote_id}"
                    )

                return bundle

    def submit_for_review(
        self, *, quote_id: str, operator: str, expected_version: int
    ) -> CustomerQuoteBundle:
        return self._transition_revision(
            quote_id=quote_id,
            operator=operator,
            expected_version=expected_version,
            legal_from_statuses=frozenset({"draft", "adjustments_requested"}),
            to_status="pending_approval",
            event_type="quote_submitted_for_review",
        )

    def request_adjustments(
        self, *, quote_id: str, operator: str, expected_version: int
    ) -> CustomerQuoteBundle:
        return self._transition_revision(
            quote_id=quote_id,
            operator=operator,
            expected_version=expected_version,
            legal_from_statuses=frozenset({"pending_approval"}),
            to_status="adjustments_requested",
            event_type="quote_adjustments_requested",
        )

    def approve(
        self, *, quote_id: str, operator: str, expected_version: int
    ) -> CustomerQuoteBundle:
        return self._transition_revision(
            quote_id=quote_id,
            operator=operator,
            expected_version=expected_version,
            legal_from_statuses=frozenset({"pending_approval"}),
            to_status="approved",
            event_type="quote_approved",
        )

    def confirm_send(
        self, *, quote_id: str, operator: str, expected_version: int
    ) -> CustomerQuoteBundle:
        return self._transition_revision(
            quote_id=quote_id,
            operator=operator,
            expected_version=expected_version,
            legal_from_statuses=frozenset({"approved"}),
            to_status="sent",
            event_type="quote_send_confirmed",
        )

    def close_quote(
        self,
        *,
        quote_id: str,
        operator: str,
        expected_version: int,
        outcome: str,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> CustomerQuoteBundle:
        """Explicit terminal outcome for a sent quote ("Cerrar cotización").

        Unlike the four revision-workflow transitions above, this command
        carries a real idempotency claim (like create_quote/adopt_drive_folder):
        a retry after a lost response must never append a second
        quote_closed event. Never touches commercial.sales_opportunity --
        that stays a separate, operator-visible action in Ventas."""

        to_status = _CLOSED_STATUS_BY_OUTCOME[outcome]

        pg = require_psycopg()
        now = _utcnow()

        with postgres_write_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                replay_result_id = _claim_idempotency(
                    cur,
                    operator=operator,
                    idempotency_key=idempotency_key,
                    command_kind=_CLOSE_COMMAND_KIND,
                    request_fingerprint=request_fingerprint,
                )

                if replay_result_id is not None:
                    replay = _fetch_bundle(cur, quote_id=replay_result_id)

                    if replay is None:
                        raise CommercialOperationConflictError(
                            "Idempotency result customer quote is missing"
                        )

                    return replay

                cur.execute(
                    """
                    SELECT version
                    FROM commercial.customer_quote
                    WHERE quote_id = %(quote_id)s
                    FOR UPDATE
                    """,
                    {"quote_id": quote_id},
                )

                existing_quote = cur.fetchone()

                if existing_quote is None:
                    raise CommercialOperationNotFoundError(
                        f"customer_quote_not_found: {quote_id}"
                    )

                current_version = int(existing_quote["version"])

                if expected_version != current_version:
                    raise CommercialOperationConflictError(
                        "customer_quote_version_conflict: expected "
                        f"{expected_version}, found {current_version}"
                    )

                cur.execute(
                    """
                    SELECT revision_number, status
                    FROM commercial.customer_quote_revision
                    WHERE quote_id = %(quote_id)s
                    ORDER BY revision_number DESC
                    LIMIT 1
                    """,
                    {"quote_id": quote_id},
                )

                existing_revision = cur.fetchone()

                if existing_revision is None:
                    raise RuntimeError(
                        f"Customer quote has no revision: {quote_id}"
                    )

                revision_number = int(existing_revision["revision_number"])
                current_status = str(existing_revision["status"])

                if current_status != "sent":
                    raise CommercialOperationConflictError(
                        "customer_quote_illegal_transition: cannot close "
                        f"from '{current_status}' (only 'sent' quotes can "
                        "be closed)"
                    )

                cur.execute(
                    """
                    UPDATE commercial.customer_quote
                    SET
                      version = version + 1,
                      updated_by = %(operator)s,
                      updated_at = %(now)s
                    WHERE quote_id = %(quote_id)s
                      AND version = %(current_version)s
                    RETURNING version
                    """,
                    {
                        "quote_id": quote_id,
                        "operator": operator,
                        "now": now,
                        "current_version": current_version,
                    },
                )

                if cur.fetchone() is None:
                    raise CommercialOperationConflictError(
                        "customer_quote_version_conflict: concurrent update"
                    )

                cur.execute(
                    """
                    UPDATE commercial.customer_quote_revision
                    SET
                      status = %(to_status)s,
                      updated_by = %(operator)s,
                      updated_at = %(now)s
                    WHERE quote_id = %(quote_id)s
                      AND revision_number = %(revision_number)s
                    """,
                    {
                        "quote_id": quote_id,
                        "revision_number": revision_number,
                        "to_status": to_status,
                        "operator": operator,
                        "now": now,
                    },
                )

                _insert_event(
                    cur,
                    quote_id=quote_id,
                    event_type="quote_closed",
                    actor_key=operator,
                    payload={
                        "revision_number": revision_number,
                        "from_status": current_status,
                        "to_status": to_status,
                        "outcome": outcome,
                    },
                    created_at=now,
                )

                _store_idempotency_result(
                    cur,
                    operator=operator,
                    idempotency_key=idempotency_key,
                    result_id=quote_id,
                )

                bundle = _fetch_bundle(cur, quote_id=quote_id)

                if bundle is None:
                    raise RuntimeError(
                        f"Customer quote disappeared mid-close: {quote_id}"
                    )

                return bundle

    def adopt_drive_folder(
        self,
        *,
        quote_id: str,
        sales_opportunity_id: str,
        document_number: str,
        quote_number: str,
        folder_id: str,
        folder_web_url: str,
        operator: str,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> CustomerQuoteBundle:
        """Attach a pre-existing Drive folder to a brand-new durable quote
        ("Incorporar al CRM"). Never allocates from
        customer_quote_number_series (serial/issue_year stay NULL --
        quote_origin='adopted'), never calls the Drive provider (folder_id/
        folder_web_url are recorded as given; the workspace is inserted
        already 'folder_ready' -- a folder-level workspace with no template/
        document step performed, never the fully-provisioned 'ready'
        meaning), and never derives quote_number from document_number --
        both are mandatory, independent, operator-confirmed inputs.
        """

        safe_folder_url = _require_https_url(
            folder_web_url,
            field="folder_web_url",
        )

        pg = require_psycopg()
        now = _utcnow()

        with postgres_write_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                replay_result_id = _claim_idempotency(
                    cur,
                    operator=operator,
                    idempotency_key=idempotency_key,
                    command_kind=_ADOPT_COMMAND_KIND,
                    request_fingerprint=request_fingerprint,
                )

                if replay_result_id is not None:
                    replay = _fetch_bundle(cur, quote_id=replay_result_id)

                    if replay is None:
                        raise CommercialOperationConflictError(
                            "Idempotency result customer quote is missing"
                        )

                    return replay

                cur.execute(
                    """
                    SELECT
                      sales_opportunity_id,
                      title
                    FROM commercial.sales_opportunity
                    WHERE sales_opportunity_id = %(sales_opportunity_id)s
                    LIMIT 1
                    """,
                    {"sales_opportunity_id": sales_opportunity_id},
                )

                source = cur.fetchone()

                if source is None:
                    raise CommercialOperationNotFoundError(
                        f"Sales opportunity not found: {sales_opportunity_id}"
                    )

                title = str(source["title"])

                try:
                    cur.execute(
                        """
                        INSERT INTO commercial.customer_quote (
                          quote_id,
                          sales_opportunity_id,
                          quote_number,
                          document_number,
                          quote_origin,
                          serial,
                          issue_year,
                          status,
                          version,
                          created_by,
                          updated_by,
                          created_at,
                          updated_at
                        )
                        VALUES (
                          %(quote_id)s,
                          %(sales_opportunity_id)s,
                          %(quote_number)s,
                          %(document_number)s,
                          'adopted',
                          NULL,
                          NULL,
                          'draft',
                          1,
                          %(operator)s,
                          %(operator)s,
                          %(now)s,
                          %(now)s
                        )
                        RETURNING *
                        """,
                        {
                            "quote_id": quote_id,
                            "sales_opportunity_id": sales_opportunity_id,
                            "quote_number": quote_number,
                            "document_number": document_number,
                            "operator": operator,
                            "now": now,
                        },
                    )
                except pg.errors.UniqueViolation as exc:
                    raise CommercialOperationConflictError(
                        "quote_number or document_number already in use"
                    ) from exc

                quote_row = cur.fetchone()

                if quote_row is None:
                    raise RuntimeError("Customer quote insert returned no row")

                quote = _quote_from_row(dict(quote_row))

                cur.execute(
                    """
                    INSERT INTO commercial.customer_quote_revision (
                      quote_id,
                      revision_number,
                      template_reference,
                      status,
                      created_by,
                      created_at,
                      updated_by,
                      updated_at
                    )
                    VALUES (
                      %(quote_id)s,
                      %(revision_number)s,
                      NULL,
                      'draft',
                      %(operator)s,
                      %(now)s,
                      %(operator)s,
                      %(now)s
                    )
                    RETURNING *
                    """,
                    {
                        "quote_id": quote_id,
                        "revision_number": 1,
                        "operator": operator,
                        "now": now,
                    },
                )

                revision_row = cur.fetchone()

                if revision_row is None:
                    raise RuntimeError(
                        "Customer quote revision insert returned no row"
                    )

                try:
                    cur.execute(
                        """
                        INSERT INTO commercial.customer_quote_drive_workspace (
                          quote_id,
                          provider,
                          provisioning_status,
                          folder_id,
                          folder_web_url,
                          attempt_count,
                          version,
                          completed_at,
                          created_by,
                          updated_by,
                          created_at,
                          updated_at
                        )
                        VALUES (
                          %(quote_id)s,
                          'google_drive',
                          'folder_ready',
                          %(folder_id)s,
                          %(folder_web_url)s,
                          0,
                          1,
                          %(now)s,
                          %(operator)s,
                          %(operator)s,
                          %(now)s,
                          %(now)s
                        )
                        RETURNING *
                        """,
                        {
                            "quote_id": quote_id,
                            "folder_id": folder_id,
                            "folder_web_url": safe_folder_url,
                            "operator": operator,
                            "now": now,
                        },
                    )
                except pg.errors.UniqueViolation as exc:
                    raise CommercialOperationConflictError(
                        "Drive folder is already attached to a durable quote"
                    ) from exc

                workspace_row = cur.fetchone()

                if workspace_row is None:
                    raise RuntimeError(
                        "Customer quote workspace insert returned no row"
                    )

                _insert_event(
                    cur,
                    quote_id=quote_id,
                    event_type="quote_adopted_from_drive",
                    actor_key=operator,
                    payload={
                        "revision_number": 1,
                        "document_number": document_number,
                        "folder_id": folder_id,
                        "sales_opportunity_id": sales_opportunity_id,
                    },
                    created_at=now,
                )

                _store_idempotency_result(
                    cur,
                    operator=operator,
                    idempotency_key=idempotency_key,
                    result_id=quote_id,
                )

                return CustomerQuoteBundle(
                    quote=quote,
                    revision=CustomerQuoteRevision(**dict(revision_row)),
                    workspace=CustomerQuoteDriveWorkspace(**dict(workspace_row)),
                    sales_opportunity_title=title,
                )
