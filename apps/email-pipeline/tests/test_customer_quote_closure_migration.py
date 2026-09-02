"""Additive closure vocabulary (CRM-Q2B): closed_won/closed_null revision
statuses, quote_closed event, customer_quote_close command kind. No table/
column/grant/view change -- three CHECK constraints widened only, following
20260902_0044's exact structural pattern."""

from __future__ import annotations

from pathlib import Path

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260902_0045_customer_quote_closure_v1.py"
)


def _read_migration() -> str:
    return _MIGRATION_PATH.read_text(encoding="utf-8")


def test_migration_file_exists_with_correct_revision_chain() -> None:
    source = _read_migration()
    assert 'revision: str = "20260902_0045"' in source
    assert (
        'down_revision: Union[str, Sequence[str], None] = "20260902_0044"'
        in source
    )


def test_widens_revision_status_check_to_include_closure_outcomes() -> None:
    upgrade = _read_migration().split("def upgrade()")[1].split("def downgrade()")[0]
    assert "customer_quote_revision_status_check" in upgrade
    assert "'closed_won'" in upgrade
    assert "'closed_null'" in upgrade
    for value in (
        "'draft'",
        "'pending_approval'",
        "'adjustments_requested'",
        "'approved'",
        "'sent'",
        "'superseded'",
    ):
        assert value in upgrade


def test_widens_event_type_check_to_include_quote_closed() -> None:
    upgrade = _read_migration().split("def upgrade()")[1].split("def downgrade()")[0]
    assert "customer_quote_event_event_type_check" in upgrade
    assert "'quote_closed'" in upgrade


def test_widens_command_kind_check_to_include_customer_quote_close() -> None:
    upgrade = _read_migration().split("def upgrade()")[1].split("def downgrade()")[0]
    assert "command_idempotency_command_kind_check" in upgrade
    assert "'customer_quote_close'" in upgrade


def test_migration_makes_no_other_structural_change() -> None:
    upgrade = _read_migration().split("def upgrade()")[1].split("def downgrade()")[0]
    for forbidden in (
        "CREATE TABLE",
        "DROP TABLE",
        "ADD COLUMN",
        "DROP COLUMN",
        "CREATE VIEW",
        "CREATE OR REPLACE VIEW",
    ):
        assert forbidden not in upgrade, f"unexpected {forbidden!r} in migration 0045"


def test_downgrade_fails_closed_on_closed_revisions() -> None:
    downgrade = _read_migration().split("def downgrade()")[1]
    assert "closed_won" in downgrade
    assert "closed_null" in downgrade
    assert "RAISE EXCEPTION" in downgrade


def test_downgrade_fails_closed_on_quote_closed_events() -> None:
    downgrade = _read_migration().split("def downgrade()")[1]
    assert "quote_closed" in downgrade
    assert "RAISE EXCEPTION" in downgrade


def test_downgrade_fails_closed_on_close_idempotency_rows() -> None:
    downgrade = _read_migration().split("def downgrade()")[1]
    assert "customer_quote_close" in downgrade
    assert "RAISE EXCEPTION" in downgrade
