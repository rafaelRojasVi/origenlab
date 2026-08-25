"""ARCH-3B8 structural tests for durable command idempotency."""

from __future__ import annotations

from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic/versions/20260824_0033_commercial_command_idempotency.py"
)


def _text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_idempotency_migration_follows_arch3a() -> None:
    text = _text()

    assert 'revision: str = "20260824_0033"' in text
    assert ('down_revision: Union[str, Sequence[str], None] = "20260824_0032"') in text


def test_idempotency_table_is_durable_commercial_state() -> None:
    text = _text()

    assert "CREATE TABLE commercial.command_idempotency" in text

    assert (
        "PRIMARY KEY (\n"
        "            operator_key,\n"
        "            idempotency_key\n"
        "          )" in text
    )


def test_idempotency_is_scoped_to_supported_create_commands() -> None:
    text = _text()

    assert "'activity_create'" in text
    assert "'task_create'" in text


def test_idempotency_persists_request_fingerprint_and_result() -> None:
    text = _text()

    assert "request_fingerprint TEXT NOT NULL" in text
    assert "result_id TEXT" in text
    assert "^[0-9a-f]{64}$" in text


def test_idempotency_is_not_exposed_to_read_role() -> None:
    text = _text()

    assert "CREATE VIEW api.v_commercial_command_idempotency" not in text

    assert "TO origenlab_api_ro" not in text


def test_writer_has_no_delete_privilege_on_idempotency() -> None:
    text = _text()

    assert (
        "GRANT SELECT, INSERT, UPDATE ON\n"
        "              commercial.command_idempotency" in text
    )

    assert "GRANT DELETE" not in text
    assert "GRANT SELECT, INSERT, UPDATE, DELETE" not in text
