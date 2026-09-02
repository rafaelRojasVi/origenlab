"""CRM-Q2 structural tests for the customer-quote revision workflow schema.

Same style as ``test_customer_quote_business_numbering_migration.py``: these
assert the migration *text* only (the default validate.sh suite runs
SQLite-backed and never touches real Postgres DDL). Real execution against a
live Postgres -- upgrade, downgrade on an empty table, and downgrade's
fail-closed guards on seeded data -- is exercised separately in
``apps/api/tests/test_customer_quote_workflow_repository_postgres.py``.

CRM-Q2 adds the revision-level commercial workflow (draft /
pending_approval / adjustments_requested / approved / sent / superseded)
and "Incorporar al CRM" adoption identity (``quote_origin``). Concurrency
for every new command is governed by the aggregate's existing
``customer_quote.version`` -- deliberately NOT a second version counter on
``customer_quote_revision``.
"""

from __future__ import annotations

import re
from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic/versions/20260902_0043_customer_quote_workflow_v1.py"
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


def test_revision_follows_customer_quote_business_numbering_v1() -> None:
    text = _text()

    assert 'revision: str = "20260902_0043"' in text
    assert 'down_revision: Union[str, Sequence[str], None] = "20260901_0042"' in text


def test_widens_revision_status_check_to_full_workflow_vocabulary() -> None:
    upgrade = _norm(_upgrade())

    assert (
        "ALTER TABLE commercial.customer_quote_revision"
        " DROP CONSTRAINT customer_quote_revision_status_check" in upgrade
    )
    assert re.search(
        r"ADD CONSTRAINT customer_quote_revision_status_check\s*"
        r"CHECK \(\s*status IN \(\s*'draft',\s*'pending_approval',\s*"
        r"'adjustments_requested',\s*'approved',\s*'sent',\s*'superseded'\s*\)\s*\)",
        upgrade,
    )


def test_never_adds_a_version_column_to_customer_quote_revision() -> None:
    """customer_quote.version is the sole CAS token for every workflow
    command -- a second, competing version counter on the revision table
    must never be introduced."""

    upgrade = _norm(_upgrade())

    assert "customer_quote_revision ADD COLUMN version" not in upgrade
    assert "customer_quote_revision\n          ADD COLUMN version" not in _upgrade()
    # The only new revision columns are the audit-trail pair.
    assert (
        "ALTER TABLE commercial.customer_quote_revision"
        " ADD COLUMN updated_by TEXT, ADD COLUMN updated_at TIMESTAMPTZ"
        in upgrade
    )


def test_revision_updated_columns_backfill_from_created_columns() -> None:
    upgrade = _norm(_upgrade())

    assert (
        "UPDATE commercial.customer_quote_revision SET updated_by = created_by,"
        " updated_at = created_at WHERE updated_by IS NULL" in upgrade
    )
    assert "ALTER COLUMN updated_by SET NOT NULL" in upgrade
    assert "ALTER COLUMN updated_at SET NOT NULL" in upgrade


def test_adds_quote_origin_with_sum_type_shape_check() -> None:
    upgrade = _norm(_upgrade())

    assert (
        "ADD COLUMN quote_origin TEXT NOT NULL DEFAULT 'generated'" in upgrade
    )
    assert "ALTER COLUMN serial DROP NOT NULL" in upgrade
    assert "ALTER COLUMN issue_year DROP NOT NULL" in upgrade

    assert (
        "ADD CONSTRAINT customer_quote_origin_check"
        " CHECK ( quote_origin IN ('generated', 'adopted') )" in upgrade
    )
    assert re.search(
        r"ADD CONSTRAINT customer_quote_origin_serial_shape_check\s*CHECK \(\s*"
        r"\(\s*quote_origin = 'generated'\s*AND serial IS NOT NULL\s*"
        r"AND issue_year IS NOT NULL\s*\)\s*OR\s*\(\s*"
        r"quote_origin = 'adopted'\s*AND serial IS NULL\s*"
        r"AND issue_year IS NULL\s*\)\s*\)",
        upgrade,
    )


def test_never_derives_adopted_identity_from_document_number() -> None:
    """Adoption must never parse/guess serial or quote_number from a
    discovered document identifier (e.g. "CN01191") -- there must be no
    UPDATE/backfill touching customer_quote at all in this migration."""

    ddl = _norm(_upgrade())

    assert "UPDATE commercial.customer_quote SET" not in ddl
    assert "substring(" not in ddl.lower()
    assert "split_part(" not in ddl.lower()


def test_widens_event_type_allowlist_with_domain_specific_names() -> None:
    upgrade = _norm(_upgrade())

    assert (
        "ALTER TABLE commercial.customer_quote_event"
        " DROP CONSTRAINT customer_quote_event_event_type_check" in upgrade
    )
    for event_type in (
        "quote_adopted_from_drive",
        "quote_submitted_for_review",
        "quote_adjustments_requested",
        "quote_approved",
        "quote_send_confirmed",
    ):
        assert f"'{event_type}'" in upgrade

    # Never a generic catch-all kind.
    assert "'quote_transitioned'" not in upgrade
    assert "'workflow_transition'" not in upgrade


def test_widens_command_kind_allowlist_with_domain_specific_names() -> None:
    upgrade = _norm(_upgrade())

    assert (
        "ALTER TABLE commercial.command_idempotency"
        " DROP CONSTRAINT command_idempotency_command_kind_check" in upgrade
    )
    for command_kind in (
        "customer_quote_adopt_drive",
        "customer_quote_submit_for_review",
        "customer_quote_request_adjustments",
        "customer_quote_approve",
        "customer_quote_confirm_send",
    ):
        assert f"'{command_kind}'" in upgrade

    assert "'customer_quote_revision_transition'" not in upgrade


def test_grants_update_directly_on_raw_tables_never_on_views() -> None:
    upgrade = _norm(_upgrade())

    assert (
        "GRANT UPDATE ON commercial.customer_quote,"
        " commercial.customer_quote_revision TO origenlab_api_rw" in upgrade
    )

    # The read views (and customer_quote_event, which stays append-only)
    # must never receive an UPDATE/DELETE grant anywhere in this migration.
    assert "GRANT UPDATE ON api.v_" not in upgrade
    assert "GRANT UPDATE ON commercial.customer_quote_event" not in upgrade
    assert "GRANT DELETE" not in upgrade


def test_adds_read_only_event_view_for_the_drawer() -> None:
    upgrade = _norm(_upgrade())

    assert "CREATE VIEW api.v_commercial_customer_quote_event AS" in upgrade
    assert (
        "GRANT SELECT ON api.v_commercial_customer_quote_event"
        " TO origenlab_api_ro" in upgrade
    )
    assert (
        "GRANT SELECT ON api.v_commercial_customer_quote_event"
        " TO origenlab_api_rw" in upgrade
    )


def test_recreates_quote_view_with_quote_origin_appended_at_the_end() -> None:
    upgrade = _norm(_upgrade())

    assert "CREATE OR REPLACE VIEW api.v_commercial_customer_quote AS" in upgrade
    assert re.search(
        r"document_number,\s*quote_origin\s*FROM commercial\.customer_quote",
        upgrade,
    )


def test_recreates_revision_view_with_updated_columns_appended_at_the_end() -> None:
    upgrade = _norm(_upgrade())

    assert (
        "CREATE OR REPLACE VIEW api.v_commercial_customer_quote_revision AS"
        in upgrade
    )
    assert re.search(
        r"created_at,\s*updated_by,\s*updated_at\s*FROM"
        r" commercial\.customer_quote_revision",
        upgrade,
    )


def test_downgrade_fails_closed_on_adopted_rows() -> None:
    downgrade = _norm(_downgrade())

    assert (
        "IF EXISTS ( SELECT 1 FROM commercial.customer_quote"
        " WHERE quote_origin = 'adopted' ) THEN" in downgrade
    )
    assert "RAISE EXCEPTION" in downgrade


def test_downgrade_fails_closed_on_non_draft_revision_statuses() -> None:
    downgrade = _norm(_downgrade())

    assert (
        "IF EXISTS ( SELECT 1 FROM commercial.customer_quote_revision"
        " WHERE status <> 'draft' ) THEN" in downgrade
    )


def test_downgrade_fails_closed_on_new_event_types_and_command_kinds() -> None:
    downgrade = _norm(_downgrade())

    assert "WHERE event_type NOT IN (" in downgrade
    assert (
        "WHERE command_kind IN ( 'customer_quote_adopt_drive'," in downgrade
    )


def test_downgrade_drops_the_new_event_view_and_restores_originals() -> None:
    downgrade = _norm(_downgrade())

    assert "DROP VIEW IF EXISTS api.v_commercial_customer_quote_event" in downgrade
    assert "DROP COLUMN quote_origin" in downgrade
    assert "DROP COLUMN updated_by" in downgrade
    assert "DROP COLUMN updated_at" in downgrade
    assert "ALTER COLUMN serial SET NOT NULL" in downgrade
    assert "ALTER COLUMN issue_year SET NOT NULL" in downgrade


def test_downgrade_revokes_the_update_grant_on_both_raw_tables() -> None:
    """Dropping a view drops its grants, but REVOKE on the raw tables is
    independent of the view drop/recreate below -- the downgrade must undo
    upgrade step 6's GRANT UPDATE explicitly, guarded the same way the
    GRANT itself is."""

    downgrade = _norm(_downgrade())

    assert re.search(
        r"IF EXISTS \(\s*SELECT 1\s*FROM pg_roles\s*"
        r"WHERE rolname = 'origenlab_api_rw'\s*\)\s*THEN\s*"
        r"REVOKE UPDATE ON\s*commercial\.customer_quote,\s*"
        r"commercial\.customer_quote_revision\s*FROM origenlab_api_rw",
        downgrade,
    )

    # Never revoke SELECT (never granted for UPDATE) or touch the ro role,
    # which never received the UPDATE grant in the first place.
    assert "REVOKE UPDATE ON api.v_" not in downgrade
    assert "FROM origenlab_api_ro" not in downgrade


def test_downgrade_restores_select_grants_on_both_recreated_views() -> None:
    """Recreating a dropped view does NOT restore its grants -- the
    downgrade must GRANT SELECT again for both roles, immediately after
    each view is recreated, matching 0041's guarded pattern."""

    downgrade = _norm(_downgrade())

    for view in (
        "api.v_commercial_customer_quote",
        "api.v_commercial_customer_quote_revision",
    ):
        for role in ("origenlab_api_ro", "origenlab_api_rw"):
            assert re.search(
                rf"IF EXISTS \(\s*SELECT 1\s*FROM pg_roles\s*"
                rf"WHERE rolname = '{role}'\s*\)\s*THEN\s*"
                rf"GRANT SELECT ON {re.escape(view)}\s*TO {role}",
                downgrade,
            ), f"missing guarded SELECT re-grant for {view} -> {role}"


def test_downgrade_regrants_happen_after_each_views_create() -> None:
    """The SELECT re-grant for each view must textually follow that view's
    own CREATE VIEW, not merely appear anywhere in the downgrade (which
    would be true even if it were misplaced before the view exists)."""

    downgrade = _downgrade()

    quote_view_create = downgrade.index("CREATE VIEW api.v_commercial_customer_quote AS")
    quote_view_grant = downgrade.index(
        "GRANT SELECT ON api.v_commercial_customer_quote\n"
    )
    assert quote_view_create < quote_view_grant

    revision_view_create = downgrade.index(
        "CREATE VIEW api.v_commercial_customer_quote_revision AS"
    )
    revision_view_grant = downgrade.index(
        "GRANT SELECT ON api.v_commercial_customer_quote_revision\n"
    )
    assert revision_view_create < revision_view_grant
