"""Phase 4.5: static safety regressions for commercial deal Postgres mirror."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from origenlab_email_pipeline.commercial.commercial_deal_mirror_read_model import (
    FORBIDDEN_MIRROR_JSON_KEYS,
)
from origenlab_email_pipeline.dashboard_postgres_sync import build_parser
from origenlab_email_pipeline.postgres_dashboard_api.schemas import CommercialDealRow

_REPO = Path(__file__).resolve().parents[1]

_MIGRATION = _REPO / "alembic/versions/20260526_0018_commercial_deal_mirror.py"
_READ_MODEL = (
    _REPO / "src/origenlab_email_pipeline/commercial/commercial_deal_mirror_read_model.py"
)
_PG_MIRROR = _REPO / "src/origenlab_email_pipeline/commercial_deal_postgres_mirror.py"
_PG_API = _REPO / "src/origenlab_email_pipeline/postgres_dashboard_api/commercial_deals.py"
_CLOUD_SYNC = _REPO / "scripts/ops/sync_dashboard_mirror_to_cloud.sh"
_STANDALONE_SYNC = _REPO / "scripts/sync/sync_commercial_deals_postgres_mirror.py"

_ALLOWED_PG_DEAL_COLUMNS = frozenset(
    {
        "deal_key",
        "sync_run_id",
        "client_org_name",
        "supplier_org_name",
        "deal_status",
        "margin_status",
        "reconciliation_status",
        "freight_status",
        "client_sale_net_clp",
        "client_iva_amount_clp",
        "client_sale_gross_clp",
        "client_payment_received_clp",
        "supplier_invoice_total_decimal",
        "supplier_invoice_total_minor",
        "supplier_amount_paid_decimal",
        "supplier_amount_paid_minor",
        "margin_net_clp",
        "margin_pct",
        "updated_at",
        "product_line_summaries",
        "cost_summaries_by_type",
        "payment_summaries_masked",
        "margin_blockers",
        "synced_at",
    }
)

_FORBIDDEN_SQL_COLUMN_WORDS = frozenset(
    {
        "transfer_id",
        "operation_id",
        "extract_snippet",
        "operator_note",
        "source_preview_path",
        "source_preview_sha256",
        "notes_json",
        "legacy_purchase_event_id",
        "client_contact_email",
        "supplier_contact_email",
        "client_po_number",
        "client_invoice_number",
        "supplier_po_number",
        "ref_code",
        "counterparty_email",
        "subject",
        "source_path",
        "source_file",
        "body",
        "full_text",
        "email_body",
    }
)

# margin_notes may be read from SQLite header to derive margin_pct; never persisted to Postgres.
_INTERNAL_SQLITE_ONLY = frozenset({"margin_notes"})

_SELECT_BLOCK_RE = re.compile(
    r"SELECT\s+([\s\S]*?)\s+FROM\s+",
    re.IGNORECASE,
)


def _sql_select_lists(source: str) -> list[str]:
    return [m.group(1) for m in _SELECT_BLOCK_RE.finditer(source)]


def _forbidden_words_in_select_list(select_list: str) -> list[str]:
    hits: list[str] = []
    lower = select_list.lower()
    for word in _FORBIDDEN_SQL_COLUMN_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", lower):
            hits.append(word)
    return hits


def test_alembic_commercial_deal_table_columns_are_allowlisted() -> None:
    text = _MIGRATION.read_text(encoding="utf-8")
    assert "CREATE TABLE commercial.deal" in text
    for word in _FORBIDDEN_SQL_COLUMN_WORDS:
        assert word not in text, f"migration must not define column {word}"
    for col in _ALLOWED_PG_DEAL_COLUMNS:
        assert col in text, f"migration missing allowed column {col}"


def test_mirror_read_model_sql_selects_exclude_private_columns() -> None:
    text = _READ_MODEL.read_text(encoding="utf-8")
    exported_block = text.split('row: dict[str, Any] = {', 1)[-1]
    assert '"margin_notes"' not in exported_block
    violations: list[str] = []
    for select_list in _sql_select_lists(text):
        hits = _forbidden_words_in_select_list(select_list)
        violations.extend(h for h in hits if h not in _INTERNAL_SQLITE_ONLY)
    assert violations == [], f"forbidden columns in mirror SQL SELECT: {sorted(set(violations))}"


def test_margin_notes_read_but_not_exported() -> None:
    text = _READ_MODEL.read_text(encoding="utf-8")
    assert "margin_notes" in text
    assert "margin_pct_from_notes" in text
    exported_block = text.split('row: dict[str, Any] = {', 1)[-1]
    assert "margin_notes" not in exported_block.split("assert_mirror_payload_safe")[0]


def test_postgres_api_select_matches_allowlisted_columns_only() -> None:
    text = _PG_API.read_text(encoding="utf-8")
    violations: list[str] = []
    for select_list in _sql_select_lists(text):
        violations.extend(_forbidden_words_in_select_list(select_list))
    assert violations == [], f"API SQL SELECT leaked: {sorted(set(violations))}"
    assert "margin_notes" not in text


def test_postgres_mirror_uses_readonly_sqlite_and_allowlisted_insert() -> None:
    text = _PG_MIRROR.read_text(encoding="utf-8")
    assert "connect_sqlite_readonly" in text
    assert "DELETE FROM" in text
    for word in _FORBIDDEN_SQL_COLUMN_WORDS:
        assert word not in text.split("INSERT INTO", 1)[-1], f"INSERT mentions {word}"


def test_dashboard_sync_include_commercial_deals_is_opt_in_flag() -> None:
    parser = build_parser()
    args = parser.parse_args([])
    assert args.include_commercial_deals is False
    args_on = parser.parse_args(["--include-commercial-deals"])
    assert args_on.include_commercial_deals is True
    action = next(a for a in parser._actions if getattr(a, "dest", None) == "include_commercial_deals")
    assert action.default is False


def test_cloud_dashboard_sync_script_does_not_auto_include_commercial_deals() -> None:
    text = _CLOUD_SYNC.read_text(encoding="utf-8")
    assert "include-commercial-deals" not in text
    assert "sync_dashboard_postgres_mirror.py" in text


def test_cloud_dashboard_sync_script_does_not_auto_include_equipment_opportunities() -> None:
    """The legacy equipment writer is manual/backfill opt-in now (PHASE W1) —
    the tracked cloud-mirror script must not resurrect it automatically."""
    text = _CLOUD_SYNC.read_text(encoding="utf-8")
    assert "include-equipment-opportunities" not in text
    assert "sync_dashboard_postgres_mirror.py" in text


def test_standalone_sync_script_documents_opt_in() -> None:
    text = _STANDALONE_SYNC.read_text(encoding="utf-8")
    assert "Opt-in" in text or "opt-in" in text
    assert "sync_commercial_deals" in text


def test_pydantic_deal_row_excludes_private_fields() -> None:
    fields = set(CommercialDealRow.model_fields.keys())
    forbidden = fields & FORBIDDEN_MIRROR_JSON_KEYS
    assert forbidden == set()
    assert "margin_notes" not in fields
    assert "client_contact_email" not in fields
