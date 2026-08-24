"""Regression tests for scripts/mart/build_business_mart.py."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from origenlab_email_pipeline.core.mart.build_business_mart_cli import (
    normalize_mart_date_slack_days,
    run_build_business_mart_from_argv,
)
from origenlab_email_pipeline.core.mart.internal_domains import (
    resolve_mart_internal_domains,
)
from origenlab_email_pipeline.freshness_dates import MART_DATE_SLACK_DAYS_DEFAULT

REPO = Path(__file__).resolve().parents[1]
_SRC = REPO / "src"
_SCRIPT = REPO / "scripts" / "mart" / "build_business_mart.py"


def test_build_business_mart_cli_runner_importable() -> None:
    assert callable(run_build_business_mart_from_argv)


def test_mart_internal_domains_default_to_canonical_operator_domains() -> None:
    got = resolve_mart_internal_domains([])
    assert got == {"origenlab.cl", "labdelivery.cl"}
    assert "googlemail.com" not in got
    assert "mercadopublico.cl" not in got


def test_mart_internal_domains_explicit_values_are_additive() -> None:
    got = resolve_mart_internal_domains(["EXAMPLE.INTERNAL ", "origenlab.cl"])
    assert got == {
        "origenlab.cl",
        "labdelivery.cl",
        "example.internal",
    }


@pytest.mark.parametrize("invalid", [-1, 99999])
def test_normalize_mart_date_slack_days_invalid_uses_default(invalid: int) -> None:
    assert normalize_mart_date_slack_days(invalid) == MART_DATE_SLACK_DAYS_DEFAULT


def test_normalize_mart_date_slack_days_valid_unchanged() -> None:
    assert normalize_mart_date_slack_days(30) == 30


def test_build_business_mart_source_imports_counter() -> None:
    contact_org = (
        REPO
        / "src"
        / "origenlab_email_pipeline"
        / "core"
        / "mart"
        / "contact_org_builder.py"
    )
    text = contact_org.read_text(encoding="utf-8")
    assert "from collections import Counter" in text or "Counter, defaultdict" in text
    assert "doc_aggs.doc_counts_by_email.get(int(email_id), Counter())" in text


def test_build_mart_default_uses_email_body_scan(tmp_path: Path) -> None:
    db = tmp_path / "emails.sqlite"
    conn = sqlite3.connect(db)
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    from origenlab_email_pipeline.db import init_schema
    from origenlab_email_pipeline.sqlite_migrate import (
        SchemaLayer,
        migrate_sqlite_schema,
    )

    init_schema(conn)
    migrate_sqlite_schema(conn, layers={SchemaLayer.ARCHIVE_AND_MART})
    conn.execute(
        """
        INSERT INTO emails (
          source_file, message_id, date_iso, folder, sender, recipients,
          subject, body, full_body_clean, top_reply_clean
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "gmail:contacto@origenlab.cl/INBOX",
            "msg-1",
            "2026-05-15T10:00:00",
            "INBOX",
            "Buyer <buyer@lab.cl>",
            "contacto@origenlab.cl",
            "Subject",
            "body",
            "body",
            "body",
        ),
    )
    conn.commit()
    conn.close()

    env = {**os.environ, "PYTHONPATH": str(_SRC), "ORIGENLAB_SQLITE_PATH": str(db)}
    cp = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--rebuild",
            "--limit-emails",
            "5",
            "--internal-domain",
            "origenlab.cl",
        ],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert cp.returncode == 0, (cp.stdout + cp.stderr)[-4000:]
    assert "Scanned emails (for mart):" in cp.stdout
    assert "Scanned email mart features:" not in cp.stdout


def test_build_mart_use_email_mart_features_fails_when_empty(tmp_path: Path) -> None:
    db = tmp_path / "emails.sqlite"
    conn = sqlite3.connect(db)
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    from origenlab_email_pipeline.db import init_schema
    from origenlab_email_pipeline.sqlite_migrate import (
        SchemaLayer,
        migrate_sqlite_schema,
    )

    init_schema(conn)
    migrate_sqlite_schema(conn, layers={SchemaLayer.ARCHIVE_AND_MART})
    conn.close()

    env = {**os.environ, "PYTHONPATH": str(_SRC), "ORIGENLAB_SQLITE_PATH": str(db)}
    cp = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--rebuild",
            "--use-email-mart-features",
            "--internal-domain",
            "origenlab.cl",
        ],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    combined = cp.stdout + cp.stderr
    assert cp.returncode != 0
    assert (
        "email_mart_features is empty; run build-email-mart-features --apply first"
        in combined
    )


def test_build_mart_use_email_mart_features_succeeds_when_populated(
    tmp_path: Path,
) -> None:
    db = tmp_path / "emails.sqlite"
    conn = sqlite3.connect(db)
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    from origenlab_email_pipeline.core.mart.build_email_mart_features_cli import (
        email_mart_feature_row_values,
    )
    from origenlab_email_pipeline.core.mart.email_mart_features import (
        compute_email_mart_feature,
    )
    from origenlab_email_pipeline.db import init_schema
    from origenlab_email_pipeline.sqlite_migrate import (
        SchemaLayer,
        migrate_sqlite_schema,
    )

    init_schema(conn)
    migrate_sqlite_schema(conn, layers={SchemaLayer.ARCHIVE_AND_MART})
    cur = conn.execute(
        """
        INSERT INTO emails (
          source_file, message_id, date_iso, folder, sender, recipients,
          subject, body, full_body_clean, top_reply_clean
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "gmail:contacto@origenlab.cl/INBOX",
            "msg-1",
            "2026-05-15T10:00:00",
            "INBOX",
            "Buyer <buyer@lab.cl>",
            "contacto@origenlab.cl",
            "Cotización",
            "cotización equipos",
            "cotización equipos",
            "cotización equipos",
        ),
    )
    email_id = int(cur.lastrowid)
    feature = compute_email_mart_feature(
        email_id=email_id,
        message_id="msg-1",
        source_file="gmail:contacto@origenlab.cl/INBOX",
        folder="INBOX",
        sender="Buyer <buyer@lab.cl>",
        recipients="contacto@origenlab.cl",
        subject="Cotización",
        top_reply_clean="cotización equipos",
        full_body_clean="cotización equipos",
        date_iso="2026-05-15T10:00:00",
        internal_domains=frozenset({"origenlab.cl"}),
        mart_date_slack_days=30,
        computed_at="2026-06-09T12:00:00+00:00",
    )
    conn.execute(
        """
        INSERT INTO email_mart_features (
          email_id, message_id, source_file, folder, sender_email, sender_domain,
          recipient_emails_json, external_targets_json, direction, is_noise,
          is_quote_email, is_invoice_email, is_purchase_email, equipment_tags_json,
          mart_date_iso, body_len, feature_source_hash, computed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        email_mart_feature_row_values(feature),
    )
    conn.commit()
    conn.close()

    env = {**os.environ, "PYTHONPATH": str(_SRC), "ORIGENLAB_SQLITE_PATH": str(db)}
    cp = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--rebuild",
            "--use-email-mart-features",
            "--internal-domain",
            "origenlab.cl",
        ],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert cp.returncode == 0, (cp.stdout + cp.stderr)[-4000:]
    assert "Scanned email mart features: 1" in cp.stdout
    assert "[timing] email_feature_scan_seconds=" in cp.stdout

    conn2 = sqlite3.connect(db)
    contacts = conn2.execute("SELECT COUNT(*) FROM contact_master").fetchone()[0]
    conn2.close()
    assert contacts >= 1


def test_build_business_mart_rebuild_limit_emails_no_name_error(tmp_path: Path) -> None:
    """Email scan uses Counter() default when doc_aggs has no row for an email_id."""
    db = tmp_path / "emails.sqlite"
    conn = sqlite3.connect(db)
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    from origenlab_email_pipeline.db import init_schema
    from origenlab_email_pipeline.sqlite_migrate import (
        SchemaLayer,
        migrate_sqlite_schema,
    )

    init_schema(conn)
    migrate_sqlite_schema(conn, layers={SchemaLayer.ARCHIVE_AND_MART})
    conn.execute(
        """
        INSERT INTO emails (
          source_file, message_id, date_iso, folder, sender, recipients,
          subject, body, full_body_clean, top_reply_clean
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "gmail:contacto@origenlab.cl/INBOX",
            "msg-1",
            "2026-05-15T10:00:00",
            "INBOX",
            "Buyer <buyer@lab.cl>",
            "contacto@origenlab.cl",
            "Cotización equipos",
            "necesitamos cotización",
            "necesitamos cotización",
            "necesitamos cotización",
        ),
    )
    conn.commit()
    conn.close()

    env = {**os.environ, "PYTHONPATH": str(_SRC), "ORIGENLAB_SQLITE_PATH": str(db)}
    cp = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--rebuild",
            "--limit-emails",
            "5",
            "--internal-domain",
            "origenlab.cl",
        ],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert cp.returncode == 0, (cp.stdout + cp.stderr)[-4000:]
    assert "NameError" not in (cp.stdout + cp.stderr)

    conn2 = sqlite3.connect(db)
    contacts = conn2.execute("SELECT COUNT(*) FROM contact_master").fetchone()[0]
    orgs = conn2.execute("SELECT COUNT(*) FROM organization_master").fetchone()[0]
    signals = conn2.execute("SELECT COUNT(*) FROM opportunity_signals").fetchone()[0]
    conn2.close()
    assert contacts >= 0
    assert orgs >= 0
    assert signals >= 0
