"""CRM-2 structural tests for durable sales-opportunity lifecycle state."""

from __future__ import annotations

from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic/versions/20260826_0036_sales_opportunity_lifecycle_v1.py"
)


def _text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_lifecycle_migration_follows_crm1() -> None:
    text = _text()

    assert 'revision: str = "20260826_0036"' in text
    assert 'down_revision: Union[str, Sequence[str], None] = "20260825_0035"' in text


def test_lifecycle_expands_controlled_stage_vocabulary() -> None:
    text = _text()

    for stage in (
        "new",
        "qualifying",
        "qualified",
        "quoting",
        "negotiating",
        "won",
        "lost",
        "dormant",
    ):
        assert f"'{stage}'" in text

    assert "sales_opportunity_stage_check" in text


def test_lifecycle_adds_optimistic_concurrency_metadata() -> None:
    text = _text()

    assert "ADD COLUMN version INTEGER NOT NULL DEFAULT 1" in text
    assert "CHECK (version >= 1)" in text
    assert "ADD COLUMN updated_by TEXT" in text
    assert "ADD COLUMN updated_at TIMESTAMPTZ" in text


def test_existing_rows_are_backfilled_from_creation_provenance() -> None:
    text = _text()

    assert "updated_by = created_by" in text
    assert "updated_at = created_at" in text
    assert "ALTER COLUMN updated_by SET NOT NULL" in text
    assert "ALTER COLUMN updated_at SET NOT NULL" in text


def test_stage_changes_have_append_only_audit_event_type() -> None:
    text = _text()

    assert "'created'" in text
    assert "'stage_changed'" in text
    assert "sales_opportunity_event_event_type_check" in text

    # CRM-2 still does not grant mutation of audit history.
    assert "UPDATE (\n              event_type" not in text
    assert "DELETE ON commercial.sales_opportunity_event" not in text


def test_read_view_exposes_lifecycle_metadata() -> None:
    text = _text()

    assert "CREATE OR REPLACE VIEW api.v_commercial_sales_opportunity AS" in text
    assert "version," in text
    assert "updated_by," in text
    assert "updated_at" in text


def test_writer_gets_only_lifecycle_column_updates() -> None:
    text = _text()

    assert (
        "GRANT UPDATE (\n"
        "              stage,\n"
        "              version,\n"
        "              updated_by,\n"
        "              updated_at\n"
        "            ) ON commercial.sales_opportunity"
    ) in text

    assert "GRANT UPDATE ON commercial.sales_opportunity" not in text
    assert (
        "GRANT SELECT, INSERT, UPDATE ON\n              commercial.sales_opportunity"
        not in text
    )
    assert "DELETE ON commercial.sales_opportunity" not in text


def test_crm2_does_not_add_pr3_foreign_key() -> None:
    text = _text()

    assert "REFERENCES commercial.opportunity(opportunity_id)" not in text


def test_crm2_does_not_extend_create_command_idempotency() -> None:
    text = _text()

    assert "command_idempotency_command_kind_check" not in text
    assert "sales_opportunity_stage_change" not in text


def test_downgrade_fails_closed_after_real_lifecycle_history() -> None:
    text = _text()
    downgrade = text.split("def downgrade() -> None:", 1)[1]

    assert "WHERE stage <> 'new'" in downgrade
    assert "WHERE event_type = 'stage_changed'" in downgrade
    assert "RAISE EXCEPTION" in downgrade


def test_downgrade_revokes_lifecycle_update_and_restores_new_only() -> None:
    text = _text()
    downgrade = text.split("def downgrade() -> None:", 1)[1]

    assert "REVOKE UPDATE (" in downgrade
    assert "DROP COLUMN version" in downgrade
    assert "DROP COLUMN updated_by" in downgrade
    assert "DROP COLUMN updated_at" in downgrade

    assert ("stage IN (\n              'new'\n            )") in downgrade
