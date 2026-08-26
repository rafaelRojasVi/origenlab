"""CRM-1 structural tests for durable human-owned sales opportunities."""

from __future__ import annotations

from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic/versions/20260825_0035_sales_opportunity_v1.py"
)


def _text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_sales_opportunity_migration_follows_operator_events() -> None:
    text = _text()

    assert 'revision: str = "20260825_0035"' in text
    assert 'down_revision: Union[str, Sequence[str], None] = "20260824_0034"' in text


def test_sales_opportunity_is_durable_human_state() -> None:
    text = _text()

    assert "CREATE TABLE commercial.sales_opportunity" in text
    assert "sales_opportunity_id TEXT PRIMARY KEY" in text
    assert "source_opportunity_id TEXT NOT NULL" in text

    # PR3 is a replaceable machine projection. CRM state must not physically
    # depend on it.
    assert "REFERENCES commercial.opportunity(opportunity_id)" not in text


def test_sales_opportunity_snapshots_identity_at_promotion() -> None:
    text = _text()

    assert "account_id TEXT" in text
    assert "primary_contact_id TEXT" in text


def test_pr3_source_can_only_be_promoted_once() -> None:
    text = _text()

    assert "CONSTRAINT uq_sales_opportunity_source" in text
    assert "source_kind,\n              source_opportunity_id" in text


def test_crm1_only_allows_initial_new_stage() -> None:
    text = _text()

    assert "stage TEXT NOT NULL DEFAULT 'new'" in text

    stage_check = "stage IN (\n                'new'\n              )"
    assert stage_check in text


def test_sales_opportunity_requires_human_owner() -> None:
    text = _text()

    assert "owner_key TEXT NOT NULL" in text
    assert "created_by TEXT NOT NULL" in text


def test_sales_opportunity_creation_has_durable_event() -> None:
    text = _text()

    assert "CREATE TABLE commercial.sales_opportunity_event" in text
    assert "REFERENCES commercial.sales_opportunity(sales_opportunity_id)" in text
    assert "'created'" in text
    assert "payload JSONB NOT NULL" in text


def test_sales_opportunity_events_are_inside_durable_boundary() -> None:
    text = _text()

    assert "ON DELETE RESTRICT" in text


def test_promotion_reuses_command_idempotency_contract() -> None:
    text = _text()

    assert "'activity_create'" in text
    assert "'task_create'" in text
    assert "'sales_opportunity_promote'" in text
    assert "command_idempotency_command_kind_check" in text


def test_sales_opportunity_read_views_are_exposed() -> None:
    text = _text()

    assert "CREATE VIEW api.v_commercial_sales_opportunity AS" in text
    assert "CREATE VIEW api.v_commercial_sales_opportunity_event AS" in text
    assert "TO origenlab_api_ro" in text


def test_writer_cannot_update_or_delete_sales_opportunity() -> None:
    text = _text()

    assert (
        "GRANT SELECT, INSERT ON\n"
        "              commercial.sales_opportunity,\n"
        "              commercial.sales_opportunity_event" in text
    )

    assert (
        "GRANT SELECT, INSERT, UPDATE ON\n"
        "              commercial.sales_opportunity" not in text
    )

    assert (
        "GRANT SELECT, INSERT, UPDATE, DELETE ON\n"
        "              commercial.sales_opportunity" not in text
    )


def test_downgrade_restores_previous_idempotency_command_set() -> None:
    text = _text()

    downgrade = text.split("def downgrade() -> None:", 1)[1]

    assert "'activity_create'" in downgrade
    assert "'task_create'" in downgrade
    assert "'sales_opportunity_promote'" not in downgrade
