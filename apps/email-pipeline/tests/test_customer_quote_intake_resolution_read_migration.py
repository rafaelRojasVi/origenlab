"""Narrow read access to lead_intel.prospect Gmail evidence for the
Cotizaciones intake resolver (CRM-Q2B). Purely additive: a new view + two
guarded grants, no existing table/column touched."""

from __future__ import annotations

from pathlib import Path

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260902_0046_customer_quote_intake_resolution_read_v1.py"
)


def _read_migration() -> str:
    return _MIGRATION_PATH.read_text(encoding="utf-8")


def test_migration_file_exists_with_correct_revision_chain() -> None:
    source = _read_migration()
    assert 'revision: str = "20260902_0046"' in source
    assert (
        'down_revision: Union[str, Sequence[str], None] = "20260902_0045"'
        in source
    )


def _select_clause() -> str:
    """Just the CREATE VIEW's column list -- excludes the COMMENT ON VIEW
    statement, which legitimately names the excluded fields to document why
    they are absent."""

    upgrade = _read_migration().split("def upgrade()")[1].split("def downgrade()")[0]
    view_sql = upgrade.split("CREATE VIEW api.v_lead_intel_prospect_evidence AS")[1]
    return view_sql.split("FROM lead_intel.prospect")[0]


def test_creates_narrow_lead_intel_evidence_view() -> None:
    upgrade = _read_migration().split("def upgrade()")[1].split("def downgrade()")[0]
    assert "CREATE VIEW api.v_lead_intel_prospect_evidence" in upgrade
    assert "FROM lead_intel.prospect" in upgrade

    select_clause = _select_clause()
    for safe_field in (
        "organization_name",
        "contact_name",
        "email",
        "domain",
        "gmail_sent_count",
        "gmail_received_count",
        "gmail_last_contacted_at",
    ):
        assert safe_field in select_clause
    for excluded_field in (
        "evidence_note",
        "risk_flags",
        "block_or_review_reason",
        "spanish_message_angle",
    ):
        assert excluded_field not in select_clause


def test_grants_read_access_to_both_roles() -> None:
    upgrade = _read_migration().split("def upgrade()")[1].split("def downgrade()")[0]
    assert "GRANT USAGE ON SCHEMA lead_intel" in upgrade
    assert "origenlab_api_ro" in upgrade
    assert "origenlab_api_rw" in upgrade
    assert "GRANT SELECT ON api.v_lead_intel_prospect_evidence" in upgrade


def test_migration_touches_no_existing_table() -> None:
    upgrade = _read_migration().split("def upgrade()")[1].split("def downgrade()")[0]
    for forbidden in ("ALTER TABLE", "CREATE TABLE", "DROP TABLE", "DROP COLUMN"):
        assert forbidden not in upgrade


def test_downgrade_drops_the_view_and_revokes_grants() -> None:
    downgrade = _read_migration().split("def downgrade()")[1]
    assert "DROP VIEW IF EXISTS api.v_lead_intel_prospect_evidence" in downgrade
    assert "REVOKE" in downgrade
