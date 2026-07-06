from __future__ import annotations

import csv
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from domain_fixture import assert_domain_in_collection

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "qa" / "export_supplier_domain_false_positive_audit.py"


def _run(db: Path, out: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db), "--out", str(out), *extra],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _seed_full(db: Path) -> None:
    from origenlab_email_pipeline.leads_schema import ensure_leads_tables
    from origenlab_email_pipeline.supplier_schema import ensure_supplier_tables

    conn = sqlite3.connect(str(db))
    ensure_leads_tables(conn)
    ensure_supplier_tables(conn)
    conn.execute(
        """
        INSERT INTO supplier_import_batch (id, source_filename, file_sha256, imported_at)
        VALUES (1, 'test_workbook.xlsx', 'abc', '2026-04-01T00:00:00Z')
        """
    )
    conn.executemany(
        """
        INSERT INTO supplier_master (
          domain_norm, trade_name, notes, is_exclusion, created_at, updated_at
        ) VALUES (?, ?, ?, ?, '2026-04-01T00:00:00Z', '2026-04-01T00:00:00Z')
        """,
        [
            ("sag.gob.cl", None, "exclusion row", 1),
            ("vendor-seller.cl", "Vendor Seller Inc", "B2B", 0),
            ("orphan-supplier.com", "Orphan Co", "", 0),
        ],
    )
    conn.executemany(
        """
        INSERT INTO supplier_priority_snapshot (
          supplier_id, batch_id, tier, rank_in_list, confidence_score, confidence_label, category_context
        ) VALUES (?, 1, ?, 1, 0.9, 'high', NULL)
        """,
        [(1, "exclusion"), (2, "top50"), (3, "anexo")],
    )
    conn.executemany(
        """
        INSERT INTO lead_master (
          id, source_name, source_record_id, org_name, domain, domain_norm,
          fit_bucket, priority_score, last_seen_at, upstream_sync_state
        ) VALUES (?,?,?,?,?,?,?,?,?,'active')
        """,
        [
            (501, "s", "l501", "Servicio Agricola", "sag.gob.cl", "sag.gob.cl", "high_fit", 9.0, "2026-04-21T00:00:00Z"),
            (502, "s", "l502", "Buyer Org", "vendor-seller.cl", "vendor-seller.cl", "medium_fit", 5.0, "2026-04-21T00:00:00Z"),
        ],
    )
    conn.commit()
    conn.close()


def test_government_domain_flagged_with_matching_leads(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    out = tmp_path / "audit.csv"
    _seed_full(db)
    run = _run(db, out, "--limit", "100")
    assert run.returncode == 0, run.stderr + run.stdout
    rows = _rows(out)
    by_dom = {r["domain_norm"]: r for r in rows}
    assert "sag.gob.cl" in by_dom
    r = by_dom["sag.gob.cl"]
    assert "gov_gob_cl_domain" in r["likely_false_positive_reason"]
    assert r["recommended_action"] == "review_supplier_exclusion"
    assert int(r["matching_high_fit_count"]) >= 1
    assert "501" in r["example_lead_ids"]


def test_true_supplier_not_institutional_flag(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    out = tmp_path / "audit.csv"
    _seed_full(db)
    run = _run(db, out, "--limit", "100")
    assert run.returncode == 0, run.stderr + run.stdout
    rows = _rows(out)
    by_dom = {r["domain_norm"]: r for r in rows}
    assert "vendor-seller.cl" in by_dom
    r = by_dom["vendor-seller.cl"]
    assert r["likely_false_positive_reason"] == ""
    assert r["recommended_action"] == "likely_true_supplier"


def test_zero_lead_domain_excluded_by_default(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    out = tmp_path / "audit.csv"
    _seed_full(db)
    run = _run(db, out, "--limit", "100")
    assert run.returncode == 0, run.stderr + run.stdout
    rows = _rows(out)
    doms = {r["domain_norm"] for r in rows}
    assert "orphan-supplier.com" not in doms


def test_include_zero_lead_domains(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    out = tmp_path / "audit.csv"
    _seed_full(db)
    run = _run(db, out, "--limit", "100", "--include-zero-lead-domains")
    assert run.returncode == 0, run.stderr + run.stdout
    rows = _rows(out)
    by_dom = {r["domain_norm"]: r for r in rows}
    assert_domain_in_collection("orphan-supplier.com", frozenset(by_dom.keys()))
    assert by_dom["orphan-supplier.com"]["recommended_action"] == "no_matching_leads"


def test_csv_columns_stable(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    out = tmp_path / "audit.csv"
    _seed_full(db)
    run = _run(db, out, "--limit", "10")
    assert run.returncode == 0, run.stderr + run.stdout
    rows = _rows(out)
    assert rows
    assert list(rows[0].keys()) == [
        "domain_norm",
        "supplier_name",
        "supplier_tier",
        "is_exclusion",
        "supplier_source",
        "supplier_notes",
        "matching_lead_count",
        "matching_high_fit_count",
        "matching_medium_fit_count",
        "example_lead_ids",
        "example_organization_names",
        "likely_false_positive_reason",
        "recommended_action",
    ]


def test_no_writes_to_supplier(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    out = tmp_path / "audit.csv"
    _seed_full(db)
    before = db.read_bytes()
    run = _run(db, out, "--limit", "50")
    assert run.returncode == 0, run.stderr + run.stdout
    after = db.read_bytes()
    assert before == after
