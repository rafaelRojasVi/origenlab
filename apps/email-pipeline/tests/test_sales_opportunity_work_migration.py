"""CRM-3A structural tests for durable sales-opportunity work anchors."""

from __future__ import annotations

from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic/versions/20260826_0037_sales_opportunity_work_v1.py"
)


def _text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _upgrade() -> str:
    return _text().split("def downgrade() -> None:", 1)[0]


def _downgrade() -> str:
    return _text().split("def downgrade() -> None:", 1)[1]


def test_crm3a_follows_crm2() -> None:
    text = _text()

    assert 'revision: str = "20260826_0037"' in text
    assert 'down_revision: Union[str, Sequence[str], None] = "20260826_0036"' in text


def test_activity_and_task_gain_durable_sales_opportunity_fk() -> None:
    upgrade = _upgrade()

    assert "ADD COLUMN sales_opportunity_id TEXT" in upgrade

    assert (
        "activity_sales_opportunity_id_fkey"
        in upgrade
    )
    assert (
        "task_sales_opportunity_id_fkey"
        in upgrade
    )

    assert (
        "REFERENCES commercial.sales_opportunity(sales_opportunity_id)"
        in upgrade
    )
    assert "ON DELETE RESTRICT" in upgrade


def test_crm3a_does_not_add_fk_to_replaceable_pr3() -> None:
    upgrade = _upgrade()

    assert (
        "REFERENCES commercial.opportunity(opportunity_id)"
        not in upgrade
    )


def test_crm_anchor_requires_legacy_source_provenance() -> None:
    upgrade = _upgrade()

    assert (
        "activity_sales_opportunity_requires_source_check"
        in upgrade
    )
    assert (
        "task_sales_opportunity_requires_source_check"
        in upgrade
    )

    assert (
        "sales_opportunity_id IS NULL"
        in upgrade
    )
    assert "OR opportunity_id IS NOT NULL" in upgrade


def test_existing_promoted_pr3_work_is_backfilled() -> None:
    upgrade = _upgrade()

    assert "UPDATE commercial.activity AS a" in upgrade
    assert "UPDATE commercial.task AS t" in upgrade

    assert "s.source_kind = 'pr3'" in upgrade
    assert "s.source_opportunity_id = a.opportunity_id" in upgrade
    assert "s.source_opportunity_id = t.opportunity_id" in upgrade


def test_sales_opportunity_work_indexes_are_added() -> None:
    upgrade = _upgrade()

    assert "idx_commercial_activity_sales_opportunity" in upgrade
    assert "idx_commercial_task_sales_opportunity" in upgrade


def test_api_views_append_sales_opportunity_anchor() -> None:
    upgrade = _upgrade()

    assert "CREATE OR REPLACE VIEW api.v_commercial_activity AS" in upgrade
    assert "CREATE OR REPLACE VIEW api.v_commercial_task AS" in upgrade

    activity_view = upgrade.split(
        "CREATE OR REPLACE VIEW api.v_commercial_activity AS",
        1,
    )[1].split("FROM commercial.activity", 1)[0]

    task_view = upgrade.split(
        "CREATE OR REPLACE VIEW api.v_commercial_task AS",
        1,
    )[1].split("FROM commercial.task", 1)[0]

    assert activity_view.rstrip().endswith("sales_opportunity_id")
    assert task_view.rstrip().endswith("sales_opportunity_id")


def test_downgrade_fails_closed_for_non_reconstructible_links() -> None:
    downgrade = _downgrade()

    assert "s.source_kind <> 'pr3'" in downgrade
    assert (
        "s.source_opportunity_id IS DISTINCT FROM a.opportunity_id"
        in downgrade
    )
    assert (
        "s.source_opportunity_id IS DISTINCT FROM t.opportunity_id"
        in downgrade
    )
    assert "RAISE EXCEPTION" in downgrade


def test_downgrade_restores_legacy_views_and_drops_anchor_columns() -> None:
    downgrade = _downgrade()

    assert "DROP VIEW IF EXISTS api.v_commercial_activity" in downgrade
    assert "DROP VIEW IF EXISTS api.v_commercial_task" in downgrade

    assert "DROP COLUMN sales_opportunity_id" in downgrade
    assert "CREATE VIEW api.v_commercial_activity AS" in downgrade
    assert "CREATE VIEW api.v_commercial_task AS" in downgrade


def test_crm3a_does_not_change_command_idempotency_vocabulary() -> None:
    text = _text()

    assert "command_idempotency_command_kind_check" not in text
