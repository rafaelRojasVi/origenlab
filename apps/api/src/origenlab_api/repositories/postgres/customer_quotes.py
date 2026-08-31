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
from datetime import datetime, timezone

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
    "CustomerQuote",
    "CustomerQuoteBundle",
    "CustomerQuoteDriveWorkspace",
    "CustomerQuoteRevision",
    "PostgresCustomerQuoteRepository",
    "QuoteNumberingConfig",
    "QuoteNumberingNotConfiguredError",
]


class QuoteNumberingNotConfiguredError(RuntimeError):
    """Quote-number allocation attempted before the numbering business
    decision (prefix / pad width / next serial) was configured."""


# Redacted failure categories only: a safe slug, never provider payloads,
# exception text, URLs, or credentials.
_FAILURE_CATEGORY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

_CREATE_COMMAND_KIND = "customer_quote_create"


@dataclass(frozen=True)
class CustomerQuote:
    quote_id: str
    sales_opportunity_id: str
    quote_number: str
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
                # the durable row (not env config) is the counter truth.
                cur.execute(
                    """
                    INSERT INTO commercial.customer_quote_number_series (
                      series_key,
                      prefix,
                      pad_width,
                      next_serial,
                      created_by,
                      updated_by,
                      created_at,
                      updated_at
                    )
                    VALUES (
                      %(series_key)s,
                      %(prefix)s,
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
                    DO NOTHING
                    """,
                    {
                        "series_key": numbering.prefix,
                        "prefix": numbering.prefix,
                        "pad_width": numbering.pad_width,
                        "next_serial": numbering.seed_next_serial,
                        "operator": operator,
                        "now": now,
                    },
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
                      prefix,
                      pad_width,
                      next_serial - 1 AS allocated_serial
                    """,
                    {
                        "series_key": numbering.prefix,
                        "operator": operator,
                        "now": now,
                    },
                )

                allocated = cur.fetchone()

                if allocated is None:
                    raise RuntimeError(
                        "Quote number series row disappeared during allocation"
                    )

                quote_number = (
                    f"{allocated['prefix']}"
                    f"{str(allocated['allocated_serial']).zfill(int(allocated['pad_width']))}"
                )

                cur.execute(
                    """
                    INSERT INTO commercial.customer_quote (
                      quote_id,
                      sales_opportunity_id,
                      quote_number,
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
                      created_at
                    )
                    VALUES (
                      %(quote_id)s,
                      %(revision_number)s,
                      %(template_reference)s,
                      'draft',
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

        with postgres_write_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                cur.execute(
                    """
                    UPDATE commercial.customer_quote_drive_workspace
                    SET
                      attempt_count = attempt_count + 1,
                      version = version + 1,
                      provisioning_status = 'pending',
                      failure_category = NULL,
                      requested_at = %(now)s,
                      updated_by = %(operator)s,
                      updated_at = %(now)s
                    WHERE quote_id = %(quote_id)s
                      AND version = %(expected_version)s
                      AND provisioning_status <> 'ready'
                    RETURNING *
                    """,
                    {
                        "quote_id": quote_id,
                        "expected_version": expected_version,
                        "operator": operator,
                        "now": now,
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
        folder_id: str,
        folder_web_url: str,
        sheet_file_id: str,
        sheet_web_url: str,
    ) -> CustomerQuoteDriveWorkspace:
        safe_folder_url = _require_https_url(
            folder_web_url,
            field="folder_web_url",
        )
        safe_sheet_url = _require_https_url(
            sheet_web_url,
            field="sheet_web_url",
        )

        pg = require_psycopg()
        now = _utcnow()

        with postgres_write_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                cur.execute(
                    """
                    UPDATE commercial.customer_quote_drive_workspace
                    SET
                      provisioning_status = 'ready',
                      folder_id = %(folder_id)s,
                      folder_web_url = %(folder_web_url)s,
                      sheet_file_id = %(sheet_file_id)s,
                      sheet_web_url = %(sheet_web_url)s,
                      failure_category = NULL,
                      completed_at = %(now)s,
                      version = version + 1,
                      updated_by = %(operator)s,
                      updated_at = %(now)s
                    WHERE quote_id = %(quote_id)s
                    RETURNING *
                    """,
                    {
                        "quote_id": quote_id,
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
                    raise CommercialOperationNotFoundError(
                        f"Customer quote workspace not found: {quote_id}"
                    )

                workspace = CustomerQuoteDriveWorkspace(**dict(row))

                _insert_event(
                    cur,
                    quote_id=quote_id,
                    event_type="drive_workspace_ready",
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
                cur.execute(
                    """
                    UPDATE commercial.customer_quote_drive_workspace
                    SET
                      provisioning_status = 'failed',
                      failure_category = %(failure_category)s,
                      folder_id = COALESCE(%(folder_id)s, folder_id),
                      folder_web_url = COALESCE(%(folder_web_url)s, folder_web_url),
                      version = version + 1,
                      updated_by = %(operator)s,
                      updated_at = %(now)s
                    WHERE quote_id = %(quote_id)s
                    RETURNING *
                    """,
                    {
                        "quote_id": quote_id,
                        "failure_category": safe_category,
                        "folder_id": folder_id,
                        "folder_web_url": safe_folder_url,
                        "operator": operator,
                        "now": now,
                    },
                )

                row = cur.fetchone()

                if row is None:
                    raise CommercialOperationNotFoundError(
                        f"Customer quote workspace not found: {quote_id}"
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
