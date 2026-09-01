"""Structural tests for the manual sales-opportunity provenance migration.

CRM-1 (20260825_0035) allowed only `source_kind = 'pr3'`. The dashboard
rebuild needs operators to start commercial work manually, without first
promoting a PR3 opportunity. This migration widens exactly two CHECK
constraints -- no column/index/view change -- and must refuse to downgrade
once any manual row exists (their `source_kind` value would become invalid
under the narrower constraint).
"""

from __future__ import annotations

import re
from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic/versions/20260901_0042_manual_sales_opportunity_v1.py"
)


def _text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _upgrade() -> str:
    return _text().split("def downgrade() -> None:", 1)[0]


def _downgrade() -> str:
    return _text().split("def downgrade() -> None:", 1)[1]


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def test_revision_follows_customer_quote_business_numbering_v1() -> None:
    text = _text()

    assert 'revision: str = "20260901_0042"' in text
    assert 'down_revision: Union[str, Sequence[str], None] = "20260831_0041"' in text


def test_widens_source_kind_check_to_include_manual() -> None:
    upgrade = _norm(_upgrade())

    assert (
        "ALTER TABLE commercial.sales_opportunity"
        " DROP CONSTRAINT sales_opportunity_source_kind_check" in upgrade
    )
    # `_norm` collapses all whitespace (including newlines/indentation) to a
    # single space, so this must match the exact token sequence the SQL
    # literal in Step 3 produces once collapsed -- not a guess at spacing.
    assert (
        "ADD CONSTRAINT sales_opportunity_source_kind_check"
        " CHECK ( source_kind IN ( 'pr3', 'manual' ) )" in upgrade
    )


def test_widens_command_idempotency_kind_check() -> None:
    upgrade = _norm(_upgrade())

    assert (
        "ALTER TABLE commercial.command_idempotency"
        " DROP CONSTRAINT command_idempotency_command_kind_check" in upgrade
    )
    assert "'sales_opportunity_create_manual'" in upgrade
    # Every prior command_kind value must still be allowed -- this is a
    # widen, never a narrow.
    for kind in (
        "'activity_create'",
        "'task_create'",
        "'sales_opportunity_promote'",
        "'customer_quote_create'",
    ):
        assert kind in upgrade


def test_downgrade_fails_closed_if_any_manual_sales_opportunity_exists() -> None:
    downgrade = _norm(_downgrade())

    assert (
        "SELECT count(*) INTO manual_count FROM commercial.sales_opportunity"
        " WHERE source_kind = 'manual'" in downgrade
    )
    assert "RAISE EXCEPTION" in downgrade

    guard_index = downgrade.index("manual_count")
    drop_index = downgrade.index(
        "DROP CONSTRAINT sales_opportunity_source_kind_check"
    )
    assert guard_index < drop_index


def test_downgrade_restores_narrower_constraints() -> None:
    downgrade = _norm(_downgrade())

    assert (
        "ADD CONSTRAINT sales_opportunity_source_kind_check"
        " CHECK ( source_kind IN ( 'pr3' ) )" in downgrade
    )

    # Isolate just the restored command_idempotency CHECK clause (from its
    # ADD CONSTRAINT up to the next ALTER TABLE statement) so this assertion
    # doesn't trip on the fail-closed guard above it, which legitimately
    # contains the substring 'manual' in its WHERE clause.
    command_idempotency_restore = downgrade.split(
        "ADD CONSTRAINT command_idempotency_command_kind_check", 1
    )[1].split("ALTER TABLE commercial.sales_opportunity", 1)[0]
    assert "'sales_opportunity_create_manual'" not in command_idempotency_restore
