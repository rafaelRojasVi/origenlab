"""Tests for commercial intel v1 query helpers (refactor safety)."""

from __future__ import annotations

import sqlite3

import pytest

from origenlab_email_pipeline.commercial.commercial_intel_queries import (
    SQL_COMMERCIAL_EMAIL_SIGNAL_FACT_FOR_ROLLUP,
    SQL_CONTACT_ROLLUP_FOR_CONTACT_CANDIDATES,
    SQL_OPPORTUNITY_FACT_FOR_OPP_CANDIDATES,
    SQL_ORG_ROLLUP_FOR_OPPORTUNITY_INSERT,
    SQL_ORG_ROLLUP_FOR_ORG_CANDIDATES,
    derive_existing_client_domains,
    derive_internal_domains,
    derive_vendor_domains,
    fetch_candidate_suppressed_counts,
    fetch_emails_for_commercial_build,
    selected_email_where_clause,
    table_exists,
)


def test_selected_email_where_clause_watermark_only() -> None:
    sql, params = selected_email_where_clause(42, None)
    assert sql == "WHERE id > ?"
    assert params == (42,)


def test_selected_email_where_clause_includes_reprocess_cutoff() -> None:
    sql, params = selected_email_where_clause(10, 7)
    assert "date_iso >=" in sql
    assert params[0] == 10
    assert isinstance(params[1], str) and len(params[1]) >= 10


def test_table_exists_and_missing() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t1 (x INT)")
    assert table_exists(conn, "t1") is True
    assert table_exists(conn, "missing") is False
    conn.close()


def test_derive_internal_domains_returns_canonical_operator_domains() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE emails (sender TEXT)")
    conn.execute(
        "INSERT INTO emails VALUES (?)",
        ("External <external@soviquim.cl>",),
    )
    conn.commit()

    assert derive_internal_domains(conn) == {
        "origenlab.cl",
        "labdelivery.cl",
    }
    conn.close()


def test_derive_internal_domains_does_not_promote_frequent_external_domains() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE emails (sender TEXT)")

    for _ in range(50):
        conn.execute(
            "INSERT INTO emails VALUES (?)",
            ("Marketplace <notice@mercadopublico.cl>",),
        )

    for _ in range(40):
        conn.execute(
            "INSERT INTO emails VALUES (?)",
            ("External Gmail <buyer@googlemail.com>",),
        )

    for _ in range(30):
        conn.execute(
            "INSERT INTO emails VALUES (?)",
            ("Supplier <sales@soviquim.cl>",),
        )

    conn.commit()

    got = derive_internal_domains(conn)

    assert got == {
        "origenlab.cl",
        "labdelivery.cl",
    }
    assert "mercadopublico.cl" not in got
    assert "googlemail.com" not in got
    assert "soviquim.cl" not in got

    conn.close()


def test_derive_vendor_domains_empty_without_contact_master() -> None:
    conn = sqlite3.connect(":memory:")
    assert derive_vendor_domains(conn) == set()
    conn.close()


def test_derive_existing_client_domains_empty_without_org_master() -> None:
    conn = sqlite3.connect(":memory:")
    assert derive_existing_client_domains(conn) == set()
    conn.close()


def test_fetch_emails_for_commercial_build_rebuild_columns_and_restores_row_factory() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE emails (
          id INTEGER,
          source_file TEXT,
          date_iso TEXT,
          sender TEXT,
          recipients TEXT,
          subject TEXT,
          top_reply_clean TEXT,
          full_body_clean TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO emails VALUES (1,'f','2021-01-01','s','r','sub','top','full')",
    )
    conn.commit()

    def _plain_factory(cur, row):
        return row

    conn.row_factory = _plain_factory
    rows = fetch_emails_for_commercial_build(
        conn, rebuild=True, last_watermark=0, reprocess_days=None
    )
    assert conn.row_factory is _plain_factory
    assert [c for c in rows[0].keys()] == [
        "id",
        "source_file",
        "date_iso",
        "sender",
        "recipients",
        "subject",
        "top_reply_clean",
        "full_body_clean",
    ]
    assert rows[0]["top_reply_clean"] == "top"
    assert rows[0]["full_body_clean"] == "full"
    conn.close()


def test_fetch_emails_incremental_uses_watermark() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE emails (
          id INTEGER,
          source_file TEXT,
          date_iso TEXT,
          sender TEXT,
          recipients TEXT,
          subject TEXT,
          top_reply_clean TEXT,
          full_body_clean TEXT
        )
        """
    )
    conn.execute("INSERT INTO emails VALUES (1,'f',NULL,'s','r','a','','')")
    conn.execute("INSERT INTO emails VALUES (5,'f',NULL,'s','r','b','','')")
    conn.commit()
    rows = fetch_emails_for_commercial_build(
        conn, rebuild=False, last_watermark=1, reprocess_days=None
    )
    assert [int(r["id"]) for r in rows] == [5]
    conn.close()


def test_locked_sql_signal_fact_scan_columns() -> None:
    s = SQL_COMMERCIAL_EMAIL_SIGNAL_FACT_FOR_ROLLUP
    assert "FROM commercial_email_signal_fact" in s
    for col in (
        "email_id",
        "signal_kind",
        "reason_code",
        "confidence_score",
        "strength_score",
    ):
        assert col in s, col


@pytest.mark.parametrize(
    "name,sql_snippet",
    [
        ("org_for_opportunity", SQL_ORG_ROLLUP_FOR_OPPORTUNITY_INSERT),
        ("org_for_candidates", SQL_ORG_ROLLUP_FOR_ORG_CANDIDATES),
        ("contact_for_candidates", SQL_CONTACT_ROLLUP_FOR_CONTACT_CANDIDATES),
        ("opp_for_candidates", SQL_OPPORTUNITY_FACT_FOR_OPP_CANDIDATES),
    ],
)
def test_locked_select_sql_contains_evidence_threshold(name: str, sql_snippet: str) -> None:
    assert "evidence_email_count >= 2" in sql_snippet, name


def test_fetch_candidate_suppressed_counts_empty_tables() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE organization_candidate (status TEXT)"
    )
    conn.execute("CREATE TABLE contact_candidate (status TEXT)")
    conn.execute("CREATE TABLE opportunity_candidate (status TEXT)")
    conn.commit()
    assert fetch_candidate_suppressed_counts(conn) == (0, 0, 0)
    conn.close()
