"""CRM-Q1 structural tests for the durable customer-quote schema.

These assert the migration *text* only (same style as
``test_crm_organization_contact_migration.py``). CRM-Q1 introduces the durable
customer-quote aggregate: quote + revision + Drive-workspace provisioning state
+ append-only events + the transactional quote-number series allocator. It must
not seed any production numbering series and must never allocate numbers with
``MAX(...) + 1``.
"""

from __future__ import annotations

import re
from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic/versions/20260830_0040_customer_quote_v1.py"
)


def _text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _upgrade() -> str:
    return _text().split("def downgrade() -> None:", 1)[0]


def _downgrade() -> str:
    return _text().split("def downgrade() -> None:", 1)[1]


def _norm(value: str) -> str:
    """Collapse whitespace so structural assertions ignore SQL indentation."""

    return re.sub(r"\s+", " ", value).strip()


def _executed_sql() -> str:
    """Only the executed upgrade/downgrade SQL: everything after the module
    docstring, with ``--`` SQL comment lines removed, so prose about what
    must NOT happen cannot satisfy or trip structural assertions."""

    code = _text().split("def upgrade() -> None:", 1)[1]
    lines = [
        line
        for line in code.splitlines()
        if not line.lstrip().startswith("--")
    ]
    return _norm("\n".join(lines))


def test_crm_q1_follows_crm4a_writer_grants() -> None:
    text = _text()

    assert 'revision: str = "20260830_0040"' in text
    assert 'down_revision: Union[str, Sequence[str], None] = "20260828_0039"' in text


def test_creates_durable_customer_quote_table() -> None:
    upgrade = _norm(_upgrade())

    assert "CREATE TABLE commercial.customer_quote (" in upgrade
    assert "quote_id TEXT PRIMARY KEY" in upgrade
    assert "sales_opportunity_id TEXT NOT NULL" in upgrade
    assert (
        "REFERENCES commercial.sales_opportunity(sales_opportunity_id) ON DELETE RESTRICT"
        in upgrade
    )
    assert "quote_number TEXT NOT NULL" in upgrade
    assert "CONSTRAINT uq_customer_quote_number UNIQUE (quote_number)" in upgrade
    # V1 commercial lifecycle is draft-only; widening requires a new migration.
    assert "status IN ( 'draft' )" in upgrade
    assert "version INTEGER NOT NULL DEFAULT 1" in upgrade
    assert "version >= 1" in upgrade
    assert "created_by TEXT NOT NULL" in upgrade
    assert "updated_by TEXT NOT NULL" in upgrade
    assert "created_at TIMESTAMPTZ NOT NULL DEFAULT now()" in upgrade
    assert "updated_at TIMESTAMPTZ NOT NULL DEFAULT now()" in upgrade
    assert "length(trim(quote_id)) > 0" in upgrade
    assert "length(quote_id) <= 128" in upgrade
    assert "length(trim(quote_number)) > 0" in upgrade
    assert "length(quote_number) <= 32" in upgrade


def test_creates_customer_quote_revision_table() -> None:
    upgrade = _norm(_upgrade())

    assert "CREATE TABLE commercial.customer_quote_revision (" in upgrade
    assert (
        "REFERENCES commercial.customer_quote(quote_id) ON DELETE RESTRICT"
        in upgrade
    )
    assert "revision_number INTEGER NOT NULL" in upgrade
    assert "revision_number >= 1" in upgrade
    assert (
        "CONSTRAINT pk_customer_quote_revision PRIMARY KEY (quote_id, revision_number)"
        in upgrade
    )
    assert "template_reference TEXT" in upgrade


def test_creates_drive_workspace_provisioning_table() -> None:
    upgrade = _norm(_upgrade())

    assert "CREATE TABLE commercial.customer_quote_drive_workspace (" in upgrade
    # Provisioning state is deliberately separate from commercial status.
    assert "provisioning_status IN ( 'pending', 'ready', 'failed' )" in upgrade
    assert "provider IN ( 'google_drive' )" in upgrade
    assert "folder_id TEXT" in upgrade
    assert "folder_web_url TEXT" in upgrade
    assert "sheet_file_id TEXT" in upgrade
    assert "sheet_web_url TEXT" in upgrade
    assert "failure_category TEXT" in upgrade
    assert "attempt_count INTEGER NOT NULL DEFAULT 0" in upgrade
    # Only safe https URLs may ever be stored.
    assert "folder_web_url LIKE 'https://%'" in upgrade
    assert "sheet_web_url LIKE 'https://%'" in upgrade


def test_duplicate_drive_references_are_prevented() -> None:
    upgrade = _norm(_upgrade())

    assert (
        "CREATE UNIQUE INDEX uq_customer_quote_drive_folder ON"
        " commercial.customer_quote_drive_workspace ( folder_id )"
        " WHERE folder_id IS NOT NULL" in upgrade
    )
    assert (
        "CREATE UNIQUE INDEX uq_customer_quote_drive_sheet ON"
        " commercial.customer_quote_drive_workspace ( sheet_file_id )"
        " WHERE sheet_file_id IS NOT NULL" in upgrade
    )


def test_workspace_never_stores_tokens_or_sheet_contents() -> None:
    executed = _executed_sql().lower()
    # Inspect executed DDL only (COMMENT ON prose legitimately names the
    # forbidden concepts when documenting the safety rule).
    ddl = re.sub(r"comment on [^\"]+", "", executed)

    for forbidden_column in (
        "access_token",
        "refresh_token",
        "credential",
        "oauth",
        "sheet_contents",
        "cell_",
    ):
        assert forbidden_column not in ddl


def test_creates_append_only_customer_quote_event_table() -> None:
    upgrade = _norm(_upgrade())

    assert "CREATE TABLE commercial.customer_quote_event (" in upgrade
    assert "event_id TEXT PRIMARY KEY" in upgrade
    assert (
        "event_type IN ( 'quote_created', 'drive_provision_requested',"
        " 'drive_workspace_ready', 'drive_provision_failed' )" in upgrade
    )
    assert "actor_key TEXT NOT NULL" in upgrade
    assert "payload JSONB NOT NULL" in upgrade


def test_creates_quote_number_series_allocator_table() -> None:
    upgrade = _norm(_upgrade())

    assert "CREATE TABLE commercial.customer_quote_number_series (" in upgrade
    assert "series_key TEXT PRIMARY KEY" in upgrade
    assert "prefix TEXT NOT NULL" in upgrade
    assert "pad_width INTEGER NOT NULL" in upgrade
    assert "pad_width BETWEEN 1 AND 10" in upgrade
    assert "next_serial BIGINT NOT NULL" in upgrade
    assert "next_serial >= 1" in upgrade


def test_migration_seeds_no_numbering_series() -> None:
    upgrade = _norm(_upgrade())

    # The production sequence start is a business decision recorded via
    # explicit operator configuration, never invented by a migration.
    assert "INSERT INTO commercial.customer_quote_number_series" not in upgrade


def test_no_max_plus_one_numbering_anywhere() -> None:
    ddl = re.sub(r"COMMENT ON [^\"]+", "", _executed_sql(), flags=re.IGNORECASE)

    assert "MAX(" not in ddl.upper().replace(" ", "")


def test_grants_are_least_privilege() -> None:
    upgrade = _norm(_upgrade())

    assert (
        "GRANT SELECT, INSERT ON commercial.customer_quote,"
        " commercial.customer_quote_revision,"
        " commercial.customer_quote_event TO origenlab_api_rw" in upgrade
    )
    assert (
        "GRANT SELECT, INSERT, UPDATE ON"
        " commercial.customer_quote_drive_workspace,"
        " commercial.customer_quote_number_series TO origenlab_api_rw" in upgrade
    )
    # Append-only audit and durable quote rows: no UPDATE/DELETE for anyone.
    assert "GRANT DELETE" not in upgrade
    assert "UPDATE ON commercial.customer_quote_event" not in upgrade
    assert "UPDATE ON commercial.customer_quote," not in upgrade


def test_creates_api_read_views_with_read_grants() -> None:
    upgrade = _norm(_upgrade())

    assert "CREATE VIEW api.v_commercial_customer_quote AS" in upgrade
    assert "CREATE VIEW api.v_commercial_customer_quote_revision AS" in upgrade
    assert "CREATE VIEW api.v_commercial_customer_quote_drive_workspace AS" in upgrade
    assert (
        "GRANT SELECT ON api.v_commercial_customer_quote,"
        " api.v_commercial_customer_quote_revision,"
        " api.v_commercial_customer_quote_drive_workspace TO origenlab_api_ro"
        in upgrade
    )
    assert (
        "GRANT SELECT ON api.v_commercial_customer_quote,"
        " api.v_commercial_customer_quote_revision,"
        " api.v_commercial_customer_quote_drive_workspace TO origenlab_api_rw"
        in upgrade
    )


def test_downgrade_fails_closed_on_durable_quote_rows() -> None:
    downgrade = _norm(_downgrade())

    assert "IF EXISTS (SELECT 1 FROM commercial.customer_quote) THEN" in downgrade
    assert (
        "IF EXISTS (SELECT 1 FROM commercial.customer_quote_event) THEN" in downgrade
    )
    assert "RAISE EXCEPTION" in downgrade
    # FK-safe teardown order: children before commercial.customer_quote.
    # The trailing quote anchors the exact parent-table statement (every
    # child drop statement shares the parent name as a prefix).
    quote_drop = downgrade.index(
        'DROP TABLE IF EXISTS commercial.customer_quote"'
    )
    for child in (
        "DROP TABLE IF EXISTS commercial.customer_quote_event",
        "DROP TABLE IF EXISTS commercial.customer_quote_drive_workspace",
        "DROP TABLE IF EXISTS commercial.customer_quote_revision",
    ):
        assert downgrade.index(child) < quote_drop
