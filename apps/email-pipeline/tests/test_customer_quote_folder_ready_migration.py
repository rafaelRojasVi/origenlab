"""CRM-Q2 follow-up: folder_ready provisioning status + event type.

Source-level checks only (matching tests/test_customer_quote_migration.py's
existing pattern) -- no live Postgres required. This migration is additive:
it only widens two CHECK constraints so a Drive workspace that has a folder
but never attempted the (currently gated-off) template copy step can be
represented honestly, distinct from a fully provisioned 'ready' workspace.
"""

from __future__ import annotations

from pathlib import Path

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260902_0044_customer_quote_folder_ready_v1.py"
)


def _read_migration() -> str:
    return _MIGRATION_PATH.read_text(encoding="utf-8")


def test_migration_file_exists_with_correct_revision_chain() -> None:
    source = _read_migration()
    assert 'revision: str = "20260902_0044"' in source
    assert (
        'down_revision: Union[str, Sequence[str], None] = "20260902_0043"'
        in source
    )


def test_widens_provisioning_status_check_to_include_folder_ready() -> None:
    source = _read_migration()
    upgrade = source.split("def upgrade()")[1].split("def downgrade()")[0]
    assert "customer_quote_drive_workspace_provisioning_status_check" in upgrade
    assert "'folder_ready'" in upgrade
    # Must not silently drop any previously-legal value.
    for value in ("'pending'", "'ready'", "'failed'"):
        assert value in upgrade


def test_widens_event_type_check_to_include_folder_ready_event() -> None:
    source = _read_migration()
    upgrade = source.split("def upgrade()")[1].split("def downgrade()")[0]
    assert "customer_quote_event_event_type_check" in upgrade
    assert "'drive_workspace_folder_ready'" in upgrade


def test_migration_seeds_no_numbering_series() -> None:
    source = _read_migration()
    upgrade = source.split("def upgrade()")[1].split("def downgrade()")[0]
    assert "customer_quote_number_series" not in upgrade
    assert "INSERT INTO commercial.customer_quote" not in upgrade


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
        assert forbidden not in upgrade, f"unexpected {forbidden!r} in migration 0044"


def test_downgrade_fails_closed_on_folder_ready_rows() -> None:
    downgrade = _read_migration().split("def downgrade()")[1]
    assert "folder_ready" in downgrade
    assert "RAISE EXCEPTION" in downgrade


def test_downgrade_fails_closed_on_folder_ready_events() -> None:
    downgrade = _read_migration().split("def downgrade()")[1]
    assert "drive_workspace_folder_ready" in downgrade
