"""CRM-4A structural tests for durable canonical CRM organization/contact.

These assert the migration *text* only. CRM-4A introduces durable canonical
schema plus nullable durable links from ``commercial.sales_opportunity``; it
must not backfill, must not create PR3/mart/identity rows, and must not change
sales-opportunity promotion behaviour.
"""

from __future__ import annotations

import re
from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic/versions/20260827_0038_crm_organization_contact_v1.py"
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


def test_crm4a_follows_crm3a() -> None:
    text = _text()

    assert 'revision: str = "20260827_0038"' in text
    assert 'down_revision: Union[str, Sequence[str], None] = "20260826_0037"' in text


def test_creates_durable_canonical_organization_table() -> None:
    upgrade = _norm(_upgrade())

    assert "CREATE TABLE commercial.organization (" in upgrade
    assert "organization_id TEXT PRIMARY KEY" in upgrade
    assert "display_name TEXT NOT NULL" in upgrade
    assert "primary_domain TEXT" in upgrade
    assert "version INTEGER NOT NULL DEFAULT 1" in upgrade
    assert "created_by TEXT NOT NULL" in upgrade
    assert "updated_by TEXT NOT NULL" in upgrade
    assert "created_at TIMESTAMPTZ NOT NULL DEFAULT now()" in upgrade
    assert "updated_at TIMESTAMPTZ NOT NULL DEFAULT now()" in upgrade

    # Nonblank / max-length / provenance / version / domain guards.
    assert "length(trim(organization_id)) > 0" in upgrade
    assert "length(organization_id) <= 128" in upgrade
    assert "length(trim(display_name)) > 0" in upgrade
    assert "length(display_name) <= 500" in upgrade
    assert "length(trim(created_by)) > 0" in upgrade
    assert "length(trim(updated_by)) > 0" in upgrade
    assert "version >= 1" in upgrade
    assert "length(primary_domain) <= 253" in upgrade


def test_organization_id_convention_is_documented_not_enforced() -> None:
    text = _text()

    # The durable CRM ID convention is documented, but the DB must not depend
    # on PR2 deterministic account IDs.
    assert "org_<32 hex>" in text
    assert "LIKE 'org" not in text
    assert "~ '^org_" not in text
    assert "regexp" not in text.lower()


def test_creates_organization_source_with_pr2_account_only() -> None:
    upgrade = _norm(_upgrade())

    assert "CREATE TABLE commercial.organization_source (" in upgrade
    assert (
        "REFERENCES commercial.organization(organization_id) ON DELETE CASCADE"
        in upgrade
    )
    assert "source_kind TEXT NOT NULL" in upgrade
    assert "source_id TEXT NOT NULL" in upgrade
    assert "source_kind IN ( 'pr2_account' )" in upgrade
    assert (
        "CONSTRAINT uq_organization_source UNIQUE (source_kind, source_id)" in upgrade
    )
    assert "CREATE INDEX idx_organization_source_organization" in upgrade
    assert "ON commercial.organization_source ( organization_id )" in upgrade


def test_creates_durable_canonical_contact_table() -> None:
    upgrade = _norm(_upgrade())

    assert "CREATE TABLE commercial.contact (" in upgrade
    assert "contact_id TEXT PRIMARY KEY" in upgrade
    assert (
        "organization_id TEXT REFERENCES commercial.organization(organization_id) "
        "ON DELETE RESTRICT" in upgrade
    )
    assert "display_name TEXT" in upgrade
    assert "primary_email TEXT" in upgrade
    assert "role_title TEXT" in upgrade
    assert "version INTEGER NOT NULL DEFAULT 1" in upgrade

    # primary_email must NOT be globally unique.
    assert "UNIQUE (primary_email)" not in upgrade
    assert "primary_email TEXT UNIQUE" not in upgrade
    assert "uq_contact_primary_email" not in upgrade

    assert "CREATE INDEX idx_contact_organization" in upgrade
    assert "ON commercial.contact ( organization_id )" in upgrade
    assert "CREATE INDEX idx_contact_primary_email" in upgrade
    assert "ON commercial.contact ( primary_email )" in upgrade


def test_creates_contact_source_with_pr2_contact_only() -> None:
    upgrade = _norm(_upgrade())

    assert "CREATE TABLE commercial.contact_source (" in upgrade
    assert "REFERENCES commercial.contact(contact_id) ON DELETE CASCADE" in upgrade
    assert "source_kind IN ( 'pr2_contact' )" in upgrade
    assert "CONSTRAINT uq_contact_source UNIQUE (source_kind, source_id)" in upgrade
    assert "CREATE INDEX idx_contact_source_contact" in upgrade
    assert "ON commercial.contact_source ( contact_id )" in upgrade


def test_sales_opportunity_gains_nullable_durable_crm_fks() -> None:
    upgrade_raw = _upgrade()
    upgrade = _norm(upgrade_raw)

    assert "ALTER TABLE commercial.sales_opportunity" in upgrade
    assert "ADD COLUMN organization_id TEXT" in upgrade
    assert "ADD COLUMN primary_crm_contact_id TEXT" in upgrade

    assert "sales_opportunity_organization_id_fkey" in upgrade
    assert "sales_opportunity_primary_crm_contact_id_fkey" in upgrade

    assert (
        "FOREIGN KEY (organization_id) "
        "REFERENCES commercial.organization(organization_id) ON DELETE RESTRICT"
        in upgrade
    )
    assert (
        "FOREIGN KEY (primary_crm_contact_id) "
        "REFERENCES commercial.contact(contact_id) ON DELETE RESTRICT" in upgrade
    )

    # Nullable: the ALTER that adds the columns carries no NOT NULL / DEFAULT.
    alter = _norm(
        upgrade_raw.split("ALTER TABLE commercial.sales_opportunity", 1)[1].split(
            '"""', 1
        )[0]
    )
    assert "ADD COLUMN organization_id TEXT," in alter
    assert "ADD COLUMN primary_crm_contact_id TEXT," in alter
    assert "NOT NULL" not in alter
    assert "DEFAULT" not in alter

    assert "CREATE INDEX idx_sales_opportunity_organization" in upgrade
    assert "CREATE INDEX idx_sales_opportunity_primary_crm_contact" in upgrade


def test_pr2_identity_snapshots_are_preserved_unchanged() -> None:
    text = _text()

    # account_id / primary_contact_id remain rebuildable PR2 provenance.
    assert "DROP COLUMN account_id" not in text
    assert "DROP COLUMN primary_contact_id" not in text
    assert "RENAME COLUMN account_id" not in text
    assert "RENAME COLUMN primary_contact_id" not in text


def test_no_migration_time_backfill_or_identity_reads() -> None:
    upgrade = _upgrade()

    # No PR3 / mart / identity backfill of durable CRM.
    assert "INSERT INTO commercial." not in upgrade
    assert "UPDATE commercial." not in upgrade
    assert "api.v_commercial_opportunity" not in upgrade
    assert "mart" not in upgrade.lower()
    assert "backfill" not in upgrade.lower()


def test_does_not_touch_promotion_or_idempotency_contract() -> None:
    text = _text()

    assert "command_idempotency" not in text
    assert "sales_opportunity_promote" not in text
    assert "request_fingerprint" not in text
    # No activity/task identity churn.
    assert "commercial.activity" not in text
    assert "commercial.task" not in text


def test_view_appends_new_columns_without_reordering_old_ones() -> None:
    upgrade = _upgrade()

    assert "CREATE OR REPLACE VIEW api.v_commercial_sales_opportunity AS" in upgrade

    view = upgrade.split(
        "CREATE OR REPLACE VIEW api.v_commercial_sales_opportunity AS",
        1,
    )[1].split("FROM commercial.sales_opportunity", 1)[0]

    columns = [line.strip().rstrip(",") for line in view.strip().splitlines()]
    columns = [c for c in columns if c and c != "SELECT"]

    assert columns == [
        "sales_opportunity_id",
        "source_kind",
        "source_opportunity_id",
        "account_id",
        "primary_contact_id",
        "title",
        "stage",
        "owner_key",
        "created_by",
        "created_at",
        "version",
        "updated_by",
        "updated_at",
        "organization_id",
        "primary_crm_contact_id",
    ]


def test_downgrade_fails_closed_on_durable_org_contact_data() -> None:
    downgrade = _norm(_downgrade())

    assert "FROM commercial.organization" in downgrade
    assert "FROM commercial.organization_source" in downgrade
    assert "FROM commercial.contact" in downgrade
    assert "FROM commercial.contact_source" in downgrade
    assert "organization_id IS NOT NULL" in downgrade
    assert "primary_crm_contact_id IS NOT NULL" in downgrade
    assert "RAISE EXCEPTION" in downgrade


def test_downgrade_restores_exact_pre_crm4a_view_and_drops_new_objects() -> None:
    downgrade = _downgrade()

    # CREATE OR REPLACE cannot drop appended columns: rebuild the 0037 shape.
    assert "DROP VIEW IF EXISTS api.v_commercial_sales_opportunity" in downgrade
    assert "CREATE VIEW api.v_commercial_sales_opportunity AS" in downgrade

    restored_view = downgrade.split(
        "CREATE VIEW api.v_commercial_sales_opportunity AS", 1
    )[1].split("FROM commercial.sales_opportunity", 1)[0]
    assert "organization_id" not in restored_view
    assert "primary_crm_contact_id" not in restored_view

    assert "DROP COLUMN organization_id" in downgrade
    assert "DROP COLUMN primary_crm_contact_id" in downgrade

    # FK-safe teardown order: source tables, then contact, then organization.
    order = [
        downgrade.index('DROP TABLE IF EXISTS commercial.contact_source"'),
        downgrade.index('DROP TABLE IF EXISTS commercial.organization_source"'),
        downgrade.index('DROP TABLE IF EXISTS commercial.contact"'),
        downgrade.index('DROP TABLE IF EXISTS commercial.organization"'),
    ]
    assert order == sorted(order)


def test_permissions_follow_least_privilege_no_new_mutation_surface() -> None:
    upgrade = _upgrade()

    # CRM-4A adds no writer grants at all: no mutation surface on new tables.
    assert "GRANT INSERT" not in upgrade
    assert "GRANT UPDATE" not in upgrade
    assert "GRANT DELETE" not in upgrade

    # Sales-opportunity read grants are reasserted for the replaced view.
    assert "GRANT SELECT ON" in upgrade
    assert "api.v_commercial_sales_opportunity" in upgrade


def test_sales_opportunity_contact_must_match_durable_organization() -> None:
    upgrade = _norm(_upgrade())
    downgrade = _norm(_downgrade())

    assert (
        "CONSTRAINT uq_contact_id_organization UNIQUE (contact_id, organization_id)"
    ) in upgrade

    assert ("FOREIGN KEY (primary_crm_contact_id, organization_id)") in upgrade

    assert ("REFERENCES commercial.contact(contact_id, organization_id)") in upgrade

    assert "sales_opportunity_primary_contact_organization_fkey" in upgrade
    assert (
        "DROP CONSTRAINT sales_opportunity_primary_contact_organization_fkey"
    ) in downgrade
