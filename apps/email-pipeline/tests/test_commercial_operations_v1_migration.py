"""ARCH-3A structural tests for durable commercial operations state."""

from __future__ import annotations

from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic/versions/20260824_0032_commercial_operations_v1.py"
)


def _migration_text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_arch3a_migration_is_based_on_arch2a_head() -> None:
    text = _migration_text()

    assert 'revision: str = "20260824_0032"' in text
    assert ('down_revision: Union[str, Sequence[str], None] = "20260822_0031"') in text


def test_arch3a_creates_durable_operator_objects() -> None:
    text = _migration_text()

    assert "CREATE TABLE commercial.opportunity_operator_state" in text
    assert "CREATE TABLE commercial.activity" in text
    assert "CREATE TABLE commercial.task" in text


def test_arch3a_operator_state_does_not_fk_to_replaceable_pr3() -> None:
    text = _migration_text()

    operator_state = text.split(
        "CREATE TABLE commercial.opportunity_operator_state",
        1,
    )[1].split(
        "CREATE INDEX idx_commercial_operator_state_status",
        1,
    )[0]

    activity = text.split(
        "CREATE TABLE commercial.activity",
        1,
    )[1].split(
        "CREATE INDEX idx_commercial_activity_opportunity",
        1,
    )[0]

    task = text.split(
        "CREATE TABLE commercial.task",
        1,
    )[1].split(
        "CREATE INDEX idx_commercial_task_status_due",
        1,
    )[0]

    for durable_table in (operator_state, activity, task):
        assert "REFERENCES commercial.opportunity" not in durable_table
        assert "ON DELETE CASCADE" not in durable_table


def test_arch3a_confirmation_status_is_explicit() -> None:
    text = _migration_text()

    assert "'confirmed'" in text
    assert "'rejected'" in text
    assert "'needs_review'" in text


def test_arch3a_activity_supports_daily_operator_work() -> None:
    text = _migration_text()

    for activity_type in (
        "call",
        "whatsapp",
        "meeting",
        "email",
        "note",
        "quote",
        "follow_up",
        "other",
    ):
        assert f"'{activity_type}'" in text


def test_arch3a_tasks_have_work_queue_state() -> None:
    text = _migration_text()

    for status in ("open", "done", "cancelled"):
        assert f"'{status}'" in text

    for priority in ("low", "normal", "high", "urgent"):
        assert f"'{priority}'" in text


def test_arch3a_exposes_read_views() -> None:
    text = _migration_text()

    assert "CREATE VIEW api.v_commercial_opportunity_operator_state AS" in text
    assert "CREATE VIEW api.v_commercial_activity AS" in text
    assert "CREATE VIEW api.v_commercial_task AS" in text


def test_arch3a_rw_role_never_gets_pr3_mutation_grants() -> None:
    text = _migration_text()

    rw_section = text.split(
        "WHERE rolname = 'origenlab_api_rw'",
        1,
    )[1]

    assert "commercial.opportunity_operator_state" in rw_section
    assert "commercial.activity" in rw_section
    assert "commercial.task" in rw_section

    # The write role may read PR3 API views, but it must never receive
    # mutation privileges on the replaceable PR3 projection tables.
    forbidden_mutation_targets = (
        "commercial.opportunity,",
        "commercial.opportunity_event",
        "commercial.opportunity_evidence",
        "commercial.opportunity_conflict",
    )

    mutation_grant = rw_section.split(
        "GRANT SELECT, INSERT, UPDATE ON",
        1,
    )[1].split(
        "TO origenlab_api_rw;",
        1,
    )[0]

    for table in forbidden_mutation_targets:
        assert table not in mutation_grant


def test_arch3a_requires_crm_context_for_activity_and_task() -> None:
    text = _migration_text()

    attachment_constraint = (
        "opportunity_id IS NOT NULL\n"
        "            OR account_id IS NOT NULL\n"
        "            OR contact_id IS NOT NULL"
    )

    # One constraint belongs to activity, one to task.
    assert text.count(attachment_constraint) == 2


def test_arch3a_task_completion_matches_status() -> None:
    text = _migration_text()

    assert "status = 'done'" in text
    assert "AND completed_at IS NOT NULL" in text
    assert "status <> 'done'" in text
    assert "AND completed_at IS NULL" in text


def test_arch3a_optional_operator_values_cannot_be_blank() -> None:
    text = _migration_text()

    assert (
        "manual_stage IS NULL\n            OR length(trim(manual_stage)) > 0"
    ) in text

    assert ("owner_key IS NULL\n            OR length(trim(owner_key)) > 0") in text


def test_arch3a_rw_role_has_no_delete_privilege() -> None:
    text = _migration_text()

    rw_section = text.split(
        "WHERE rolname = 'origenlab_api_rw'",
        1,
    )[1]

    assert "GRANT SELECT, INSERT, UPDATE ON" in rw_section
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON" not in rw_section
    assert "GRANT DELETE" not in rw_section


def test_arch3a_task_has_mutation_audit_and_version() -> None:
    text = _migration_text()

    task = text.split(
        "CREATE TABLE commercial.task",
        1,
    )[1].split(
        "CREATE INDEX idx_commercial_task_status_due",
        1,
    )[0]

    assert "version INTEGER NOT NULL DEFAULT 1" in task
    assert "CHECK (version >= 1)" in task
    assert "created_by TEXT NOT NULL" in task
    assert "updated_by TEXT NOT NULL" in task
    assert "CHECK (length(trim(updated_by)) > 0)" in task

    task_view = text.split(
        "CREATE VIEW api.v_commercial_task AS",
        1,
    )[1].split(
        "FROM commercial.task",
        1,
    )[0]

    assert "version," in task_view
    assert "created_by," in task_view
    assert "updated_by," in task_view
