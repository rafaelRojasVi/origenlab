"""CRM-Q1D structural tests for the corrected quote-numbering schema.

These assert the migration *text* only (same style as
``test_customer_quote_migration.py``). CRM-Q1's original numbering model
incorrectly collapsed two distinct business identifiers into one
``quote_number`` field (``CN`` + padded serial). This migration corrects
that: the durable series' ``prefix`` column is renamed ``document_prefix``
(it was never part of the human quote_number), and
``commercial.customer_quote`` gains the structural columns the corrected
model needs (``serial``, ``issue_year``, ``document_number``) so the
business identifier never has to be reparsed from a string.

CRM-Q1 was never activated in production, so this migration must refuse to
guess/backfill if any durable quote or series row already exists.
"""

from __future__ import annotations

import re
from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic/versions/20260831_0041_customer_quote_business_numbering_v1.py"
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


def test_revision_follows_customer_quote_v1() -> None:
    text = _text()

    assert 'revision: str = "20260831_0041"' in text
    assert 'down_revision: Union[str, Sequence[str], None] = "20260830_0040"' in text


def test_upgrade_fails_closed_if_any_durable_quote_or_series_row_exists() -> None:
    upgrade = _norm(_upgrade())

    # CRM-Q1 was never activated in production: there is no ambiguous
    # historical CNxxxxxx format to migrate. A durable row existing at
    # upgrade time means the guard must stop the migration rather than
    # guess/convert it into alleged corrected business truth.
    assert "IF EXISTS (SELECT 1 FROM commercial.customer_quote) THEN" in upgrade
    assert (
        "IF EXISTS (SELECT 1 FROM commercial.customer_quote_number_series) THEN"
        in upgrade
    )
    assert "RAISE EXCEPTION" in upgrade

    # The guard must run before any structural change.
    guard_index = upgrade.index("IF EXISTS (SELECT 1 FROM commercial.customer_quote)")
    rename_index = upgrade.index("RENAME COLUMN prefix TO document_prefix")
    add_column_index = upgrade.index("ADD COLUMN serial BIGINT NOT NULL")

    assert guard_index < rename_index
    assert guard_index < add_column_index


def test_renames_series_prefix_to_document_prefix() -> None:
    upgrade = _norm(_upgrade())

    assert (
        "ALTER TABLE commercial.customer_quote_number_series"
        " RENAME COLUMN prefix TO document_prefix" in upgrade
    )
    # pad_width/next_serial are already honestly named (shared by both the
    # human quote_number and the document_number) and are untouched.
    assert "RENAME COLUMN pad_width" not in upgrade
    assert "RENAME COLUMN next_serial" not in upgrade


def test_adds_structural_columns_to_customer_quote() -> None:
    upgrade = _norm(_upgrade())

    assert "ADD COLUMN serial BIGINT NOT NULL" in upgrade
    assert "ADD COLUMN issue_year SMALLINT NOT NULL" in upgrade
    assert "ADD COLUMN document_number TEXT NOT NULL" in upgrade

    assert "CHECK (serial >= 1)" in upgrade
    assert "CHECK (issue_year BETWEEN 2000 AND 2999)" in upgrade
    assert "length(trim(document_number)) > 0" in upgrade
    assert "length(document_number) <= 32" in upgrade

    assert "ADD CONSTRAINT uq_customer_quote_serial UNIQUE (serial)" in upgrade
    assert (
        "ADD CONSTRAINT uq_customer_quote_document_number UNIQUE (document_number)"
        in upgrade
    )


def test_no_backfill_or_data_guess_anywhere() -> None:
    ddl = _executed_sql()

    # Never converts a historical CNxxxxxx quote_number into serial/
    # issue_year/document_number -- the guard above makes that unreachable,
    # and there must be no UPDATE statement attempting it regardless.
    assert "UPDATE commercial.customer_quote SET" not in ddl
    assert "substring(" not in ddl.lower()


def test_recreates_read_view_with_new_columns_appended_at_the_end() -> None:
    upgrade = _norm(_upgrade())

    assert "CREATE OR REPLACE VIEW api.v_commercial_customer_quote AS" in upgrade
    # CREATE OR REPLACE VIEW only allows appending trailing columns -- the
    # new columns must come after every column the original view already
    # had (quote_id..updated_at), never inserted before an existing one.
    assert re.search(
        r"updated_at,\s*serial,\s*issue_year,\s*document_number\s*FROM"
        r" commercial\.customer_quote",
        upgrade,
    )


def test_downgrade_fails_closed_on_durable_quote_rows() -> None:
    downgrade = _norm(_downgrade())

    assert "IF EXISTS (SELECT 1 FROM commercial.customer_quote) THEN" in downgrade
    assert "RAISE EXCEPTION" in downgrade

    assert (
        "ALTER TABLE commercial.customer_quote_number_series"
        " RENAME COLUMN document_prefix TO prefix" in downgrade
    )
    assert "DROP COLUMN serial" in downgrade
    assert "DROP COLUMN issue_year" in downgrade
    assert "DROP COLUMN document_number" in downgrade
