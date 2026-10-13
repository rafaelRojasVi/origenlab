"""Shared lead export SQL helpers: stable fragments, same semantics as lead_upstream_reconcile."""

from __future__ import annotations

import sqlite3

import pytest

from origenlab_email_pipeline.lead_export_queries import (
    sql_cte_best_org_match,
    sql_left_join_best_org_match,
    sql_upstream_active_lead_master,
)
from origenlab_email_pipeline.lead_upstream_reconcile import sql_upstream_active
from origenlab_email_pipeline.leads_schema import ensure_leads_tables_ddl_base, finalize_lead_master_source_keys


def test_upstream_active_matches_reconcile_module() -> None:
    assert sql_upstream_active_lead_master("lm") == sql_upstream_active("lm")
    assert sql_upstream_active_lead_master("x") == sql_upstream_active("x")


def test_left_join_contains_min_id_rule() -> None:
    j = sql_left_join_best_org_match()
    assert "MIN(m2.id)" in j
    assert "lead_matches_existing_orgs m1" in j
    assert " m ON m.lead_id = lm.id" in j


@pytest.mark.parametrize(
    "variant,expect_cols",
    [
        ("archive_only", "m1.lead_id, m1.already_in_archive_flag"),
        ("org_and_archive", "m1.matched_org_name"),
        ("org_domain_archive", "m1.matched_domain"),
    ],
)
def test_left_join_variants(variant: str, expect_cols: str) -> None:
    j = sql_left_join_best_org_match(variant=variant)  # type: ignore[arg-type]
    assert expect_cols in j


def test_cte_matches_join_columns_org_domain() -> None:
    cte = sql_cte_best_org_match()
    sql_left_join_best_org_match(variant="org_domain_archive")
    # Same SELECT column set as org_domain_archive join subquery
    assert "m1.matched_domain" in cte
    assert "m1.matched_org_name" in cte
    assert "MIN(m2.id)" in cte


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA foreign_keys=ON")
    return c


def test_query_fragments_execute_on_minimal_schema() -> None:
    """JOIN / CTE SQL runs; one row per lead as before centralization."""
    conn = _conn()
    ensure_leads_tables_ddl_base(conn)
    finalize_lead_master_source_keys(conn)
    conn.executescript(
        """
        INSERT INTO lead_master (
          id, source_name, source_record_id, org_name, fit_bucket, priority_score, status,
          first_seen_at, last_seen_at
        ) VALUES
          (1, 's', 'a', 'O1', 'high_fit', 5.0, 'nuevo', 't', 't'),
          (2, 's', 'b', 'O2', 'high_fit', 4.0, 'nuevo', 't', 't');
        INSERT INTO lead_matches_existing_orgs (
          id, lead_id, matched_domain, matched_org_name, match_type, confidence_score, already_in_archive_flag
        ) VALUES
          (20, 1, 'd1', 'ArchOrg', 'domain', 1.0, 1),
          (5, 1, 'd1', 'Older', 'domain', 1.0, 0);
        """
    )
    conn.commit()

    up = sql_upstream_active_lead_master("lm")
    join_o = sql_left_join_best_org_match(variant="org_and_archive")
    rows = conn.execute(
        f"""
        SELECT lm.id, m.matched_org_name, COALESCE(m.already_in_archive_flag, 0)
        FROM lead_master lm
        {join_o}
        WHERE {up}
        ORDER BY lm.id
        """
    ).fetchall()
    assert rows[0][0] == 1
    assert rows[0][1] == "Older"
    assert rows[0][2] == 0
    assert rows[1][0] == 2
    assert rows[1][1] is None

    cte = sql_cte_best_org_match()
    conn.execute(
        f"WITH {cte} SELECT bm.lead_id, bm.matched_org_name FROM best_match bm ORDER BY bm.lead_id"
    ).fetchall()
    r = conn.execute(
        f"WITH {cte} SELECT bm.lead_id, bm.matched_org_name FROM best_match bm WHERE bm.lead_id = 1"
    ).fetchone()
    assert r is not None
    assert r[1] == "Older"
