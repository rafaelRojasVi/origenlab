"""Tests for read-only function surface planner (synthetic fixtures + repo smoke)."""

from __future__ import annotations

import csv
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "qa" / "plan_function_surface.py"


def _load():
    name = "plan_function_surface_pytest"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _write_synthetic_tree(root: Path) -> None:
    src = root / "src" / "origenlab_email_pipeline"
    scripts = root / "scripts" / "qa"
    src.mkdir(parents=True)
    scripts.mkdir(parents=True)

    (src / "sample_module.py").write_text(
        '''"""Sample module."""
import sqlite3

class PublicWidget:
    """A widget."""

    def public_method(self) -> None:
        return None

    def _private_method(self) -> None:
        return None

def public_fn(x: int) -> int:
    return x + 1

def _private_fn() -> None:
    pass
''',
        encoding="utf-8",
    )

    (scripts / "sample_script.py").write_text(
        '''#!/usr/bin/env python3
import argparse
import subprocess

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    subprocess.run(["echo", "hi"])
    conn = sqlite3.connect(":memory:")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
''',
        encoding="utf-8",
    )

    (scripts / "gmail_ingest_sample.py").write_text(
        '''import imaplib
# Gmail API ingest
GMAIL_FOLDER = "[Gmail]/Sent"
''',
        encoding="utf-8",
    )

    (scripts / "send_purge_sample.py").write_text(
        '''import smtplib
# purge old rows
DELETE FROM outreach
send_message(msg)
''',
        encoding="utf-8",
    )

    (scripts / "postgres_sample.py").write_text(
        '''import psycopg
# alembic upgrade
ORIGENLAB_POSTGRES_URL = "postgresql://localhost/test"
''',
        encoding="utf-8",
    )


def test_counts_functions_and_classes(tmp_path: Path) -> None:
    m = _load()
    _write_synthetic_tree(tmp_path)
    src = tmp_path / "src" / "origenlab_email_pipeline"
    scripts = tmp_path / "scripts"
    result = m.scan_roots(src, scripts)
    sample = next(x for x in result.modules if x.path.endswith("sample_module.py"))
    assert sample.class_count == 1
    assert sample.public_class_count == 1
    assert sample.function_count == 4  # 2 methods + 2 module functions
    assert sample.public_function_count == 2
    assert sample.private_function_count == 2


def test_detects_public_private_functions(tmp_path: Path) -> None:
    m = _load()
    _write_synthetic_tree(tmp_path)
    src = tmp_path / "src" / "origenlab_email_pipeline"
    scripts = tmp_path / "scripts"
    result = m.scan_roots(src, scripts)
    fns = [f for f in result.functions if f.path.endswith("sample_module.py")]
    names = {f.qualname for f in fns if f.is_public}
    assert "public_fn" in names
    assert "PublicWidget.public_method" in names
    priv = {f.qualname for f in fns if not f.is_public}
    assert "_private_fn" in priv
    assert "PublicWidget._private_method" in priv


def test_detects_main_guard_and_argparse(tmp_path: Path) -> None:
    m = _load()
    _write_synthetic_tree(tmp_path)
    src = tmp_path / "src" / "origenlab_email_pipeline"
    scripts = tmp_path / "scripts"
    result = m.scan_roots(src, scripts)
    script = next(x for x in result.modules if x.path.endswith("sample_script.py"))
    assert script.has_main_guard
    assert script.has_argparse
    assert script.has_subprocess
    assert script.has_sqlite_write_markers
    assert script.has_apply_flag


def test_detects_sqlite_write_markers(tmp_path: Path) -> None:
    m = _load()
    p = tmp_path / "t.py"
    p.write_text("cur.execute('UPDATE t SET x=1'); conn.commit()\n", encoding="utf-8")
    mod, _ = m.scan_file(p, "scripts/qa/t.py", "script")
    assert mod.has_sqlite_write_markers


def test_detects_gmail_send_purge_postgres_markers(tmp_path: Path) -> None:
    m = _load()
    _write_synthetic_tree(tmp_path)
    src = tmp_path / "src" / "origenlab_email_pipeline"
    scripts = tmp_path / "scripts"
    result = m.scan_roots(src, scripts)
    by_path = {x.path: x for x in result.modules}
    assert by_path["scripts/qa/gmail_ingest_sample.py"].has_gmail_markers
    assert by_path["scripts/qa/send_purge_sample.py"].has_send_markers
    assert by_path["scripts/qa/send_purge_sample.py"].has_purge_markers
    assert by_path["scripts/qa/postgres_sample.py"].has_postgres_markers


def test_risk_buckets_for_synthetic_files(tmp_path: Path) -> None:
    m = _load()
    _write_synthetic_tree(tmp_path)
    src = tmp_path / "src" / "origenlab_email_pipeline"
    scripts = tmp_path / "scripts"
    result = m.scan_roots(src, scripts)
    by_path = {x.path: x for x in result.modules}
    assert by_path["scripts/qa/send_purge_sample.py"].risk_bucket == "send_or_purge"
    assert by_path["scripts/qa/gmail_ingest_sample.py"].risk_bucket == "gmail_ingest"
    assert by_path["scripts/qa/postgres_sample.py"].risk_bucket == "postgres_mirror_or_migration"
    assert by_path["scripts/qa/sample_script.py"].risk_bucket == "writes_sqlite"


def test_creates_expected_csv_and_summary_outputs(tmp_path: Path) -> None:
    m = _load()
    _write_synthetic_tree(tmp_path)
    src = tmp_path / "src" / "origenlab_email_pipeline"
    scripts = tmp_path / "scripts"
    out = tmp_path / "out"
    result = m.scan_roots(src, scripts)
    m.write_reports(result, out)

    expected = [
        "summary.md",
        "module_inventory.csv",
        "function_inventory.csv",
        "bucket_summary.csv",
        "risk_inventory.csv",
        "largest_files.csv",
        "largest_functions.csv",
        "script_entrypoints.csv",
        "public_surface.csv",
    ]
    for name in expected:
        assert (out / name).is_file(), name

    with (out / "module_inventory.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) >= 5
    assert "Function surface audit" in (out / "summary.md").read_text(encoding="utf-8")


def test_json_stdout_only(tmp_path: Path) -> None:
    m = _load()
    _write_synthetic_tree(tmp_path)
    src = tmp_path / "src" / "origenlab_email_pipeline"
    scripts = tmp_path / "scripts"
    out = tmp_path / "json-out"
    result = m.scan_roots(src, scripts)
    summary = m.build_json_summary(result, out)
    assert summary["file_counts"]["total"] >= 5
    assert "likely_bucket_counts" in summary
    assert "largest_files_top10" in summary


def test_cli_json_flag(tmp_path: Path) -> None:
    _write_synthetic_tree(tmp_path)
    src = tmp_path / "src" / "origenlab_email_pipeline"
    scripts = tmp_path / "scripts"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--src-dir",
            str(src),
            "--scripts-dir",
            str(scripts),
            "--out-dir",
            str(tmp_path / "cli-out"),
            "--json",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    idx = proc.stdout.index("{")
    report = json.loads(proc.stdout[idx:])
    assert report["file_counts"]["total"] >= 5


def test_repo_smoke_includes_known_files() -> None:
    m = _load()
    result = m.scan_roots(REPO / "src" / "origenlab_email_pipeline", REPO / "scripts")
    paths = {mod.path for mod in result.modules}
    assert "src/origenlab_email_pipeline/warm_case_sender_rules.py" in paths
    assert "scripts/qa/audit_institution_grouping.py" in paths
    assert "src/origenlab_email_pipeline/operator_cli/constants.py" in paths


def test_repo_cli_exits_zero() -> None:
    out = REPO / "reports" / "local" / "function-surface-audit-test-fixture"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--out-dir", str(out), "--json"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "warm_case_sender_rules.py" in proc.stdout or (out / "module_inventory.csv").exists()


def test_bucket_summary_aggregates_by_likely_bucket() -> None:
    m = _load()
    result = m.scan_roots(REPO / "src" / "origenlab_email_pipeline", REPO / "scripts")
    rows = m.build_bucket_summary(result.modules)
    assert rows
    warm = next((r for r in rows if r["likely_bucket"] == "warm_cases"), None)
    assert warm is not None
    assert warm["suggested_next_action"] == "characterize_then_split_later"
    assert warm["file_count"] >= 1


def test_planner_scripts_not_send_or_purge_risk() -> None:
    m = _load()
    result = m.scan_roots(REPO / "src" / "origenlab_email_pipeline", REPO / "scripts")
    by_path = {mod.path: mod for mod in result.modules}
    for rel in (
        "scripts/qa/plan_function_surface.py",
        "scripts/qa/plan_source_quality.py",
        "scripts/qa/plan_script_consolidation.py",
    ):
        assert by_path[rel].risk_bucket != "send_or_purge", rel


def test_likely_bucket_warm_cases_and_operator_cli() -> None:
    m = _load()
    assert m.classify_likely_bucket("src/origenlab_email_pipeline/warm_case_sender_rules.py") == "warm_cases"
    assert m.classify_likely_bucket("src/origenlab_email_pipeline/operator_cli/constants.py") == "operator_cli"
    assert m.classify_likely_bucket("scripts/qa/audit_institution_grouping.py") == "qa_reports"


def test_chilecompra_rule_does_not_steal_more_specific_buckets() -> None:
    """A bare "chilecompra" substring would out-rank later, narrower rules."""
    m = _load()
    cases = {
        "scripts/leads/fetch_chilecompra.py": "lead_research",
        "scripts/qa/extract_chilecompra_lab_buyers_from_xlsx.py": "qa_reports",
        "scripts/qa/audit_chilecompra_links.py": "qa_reports",
        "scripts/ingest/03_chilecompra_gmail_imap.py": "gmail_ingest",
        "src/origenlab_email_pipeline/operator_cli/chilecompra_auto_refresh.py": (
            "operator_cli"
        ),
        "src/origenlab_email_pipeline/chilecompra_to_postgres_mirror.py": (
            "postgres_mirror"
        ),
        "src/origenlab_email_pipeline/warm_case_chilecompra.py": "warm_cases",
    }
    for path, expected in cases.items():
        assert m.classify_likely_bucket(path) == expected, path


def test_retagged_buckets_from_unknown_review_audit() -> None:
    m = _load()
    assert (
        m.classify_likely_bucket(
            "src/origenlab_email_pipeline/campaigns/presentacion_origenlab_quality.py"
        )
        == "campaigns"
    )
    assert (
        m.classify_likely_bucket(
            "src/origenlab_email_pipeline/equipment_deepsearch_vetted_queue.py"
        )
        == "equipment"
    )
    assert (
        m.classify_likely_bucket("src/origenlab_email_pipeline/chilecompra_api.py")
        == "equipment"
    )
    assert (
        m.classify_likely_bucket(
            "src/origenlab_email_pipeline/chilecompra_anexo_evidence/planner.py"
        )
        == "equipment"
    )
    assert (
        m.classify_likely_bucket("src/origenlab_email_pipeline/catalog/catalog_builder.py")
        == "catalog"
    )
    for module in (
        "acquire",
        "archive",
        "detect",
        "extract",
        "models",
        "redaction",
        "walkthrough",
    ):
        assert (
            m.classify_likely_bucket(
                f"src/origenlab_email_pipeline/chilecompra_anexo_evidence/{module}.py"
            )
            == "equipment"
        )
    assert (
        m.classify_likely_bucket("src/origenlab_email_pipeline/read/today_workspace.py")
        == "read_modules"
    )
    assert (
        m.classify_likely_bucket("scripts/reports/generate_client_report.py")
        == "client_reports"
    )
    assert (
        m.classify_likely_bucket("src/origenlab_email_pipeline/ndr_bounce_extraction.py")
        == "ndr"
    )
    assert (
        m.classify_likely_bucket("src/origenlab_email_pipeline/core/research_automation.py")
        == "research_lab"
    )
    assert m.classify_likely_bucket("src/origenlab_email_pipeline/config.py") == "core_infrastructure"
    assert (
        m.classify_likely_bucket("src/origenlab_email_pipeline/mart_core_postgres_migrate.py")
        == "postgres_mirror"
    )


def test_manual_html_outreach_outbound_bucket_and_non_read_only_risk() -> None:
    m = _load()
    rel = "src/origenlab_email_pipeline/manual_html_outreach_batch.py"
    path = REPO / "src" / "origenlab_email_pipeline" / "manual_html_outreach_batch.py"
    mod, _ = m.scan_file(path, rel, "src")
    assert mod.likely_bucket == "outbound_safety"
    assert mod.risk_bucket != "read_only"


def test_unknown_review_count_dropped_on_full_repo_scan() -> None:
    m = _load()
    result = m.scan_roots(REPO / "src" / "origenlab_email_pipeline", REPO / "scripts")
    unknown = sum(1 for mod in result.modules if mod.likely_bucket == "unknown_review")
    assert unknown < 118
    assert unknown <= 15
