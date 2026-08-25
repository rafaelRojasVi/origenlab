"""ARCH-3C structural tests for durable operator audit events."""

from __future__ import annotations

from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic/versions/20260824_0034_operator_events.py"
)


def _text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_operator_event_migration_follows_command_idempotency() -> None:
    text = _text()

    assert 'revision: str = "20260824_0034"' in text
    assert ('down_revision: Union[str, Sequence[str], None] = "20260824_0033"') in text


def test_operator_event_is_durable_commercial_state() -> None:
    text = _text()

    assert "CREATE TABLE commercial.opportunity_operator_event" in text
    assert "public.opportunity_operator_event" not in text

    # Human audit state must survive PR3 rebuild/replacement.
    assert "REFERENCES commercial.opportunity" not in text
    assert "ON DELETE CASCADE" not in text


def test_operator_event_is_append_only() -> None:
    text = _text()

    assert "payload JSONB NOT NULL" in text
    assert "'operator_state_changed'" in text

    grant = text.split(
        "GRANT SELECT, INSERT ON",
        1,
    )[1].split(
        "TO origenlab_api_rw;",
        1,
    )[0]

    assert "commercial.opportunity_operator_event" in grant
    assert "UPDATE" not in grant
    assert "DELETE" not in grant


def test_operator_event_is_not_exposed_to_read_role() -> None:
    text = _text()

    assert "TO origenlab_api_ro" not in text


def test_operator_event_has_timeline_index() -> None:
    text = _text()

    assert "CREATE INDEX idx_commercial_operator_event_opportunity_created" in text
    assert "opportunity_id," in text
    assert "created_at DESC" in text
    assert "event_id DESC" in text


def test_operator_event_downgrade_drops_commercial_table() -> None:
    text = _text()

    assert "DROP TABLE IF EXISTS commercial.opportunity_operator_event" in text
