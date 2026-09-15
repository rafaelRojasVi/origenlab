from __future__ import annotations

import csv
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from origenlab_email_pipeline.candidate_export_gate import (
    REASON_ACTIVE_COMMERCIAL_ENGAGEMENT,
    REASON_OUTREACH_SNOOZED,
    REASON_SENT_HISTORY,
    REASON_SUPPRESSION,
    GateContext,
    evaluate_export_eligibility,
)
from origenlab_email_pipeline.marketing_export_context import (
    load_active_commercial_hold_norms,
)

REPO = Path(__file__).resolve().parents[1]
PROCESS = REPO / "scripts" / "leads" / "process_broad_marketing_contacts.py"


def _ctx(**overrides) -> GateContext:
    base = dict(
        sent_recipient_norms=frozenset(),
        suppressed_norms=frozenset(),
        outreach_state_by_email={},
        supplier_domains=frozenset(),
        blocked_domains=frozenset(),
    )
    base.update(overrides)
    return GateContext(**base)


def test_default_gate_still_blocks_sent_history() -> None:
    result = evaluate_export_eligibility(
        contact_email="old@lab.cl",
        institution_name="Lab",
        ctx=_ctx(sent_recipient_norms=frozenset({"old@lab.cl"})),
    )
    assert result.eligible is False
    assert result.reasons == (REASON_SENT_HISTORY,)


def test_repeat_mode_allows_sent_contacted_and_replied_history() -> None:
    for outreach_state in (None, "contacted", "replied"):
        states = {} if outreach_state is None else {"old@lab.cl": outreach_state}
        result = evaluate_export_eligibility(
            contact_email="old@lab.cl",
            institution_name="Lab",
            ctx=_ctx(
                sent_recipient_norms=frozenset({"old@lab.cl"}),
                outreach_state_by_email=states,
                allow_prior_outreach_history=True,
            ),
        )
        assert result.eligible is True
        assert result.reasons == ()


def test_repeat_mode_keeps_hard_blocks() -> None:
    suppressed = evaluate_export_eligibility(
        contact_email="bad@lab.cl",
        institution_name="Lab",
        ctx=_ctx(
            sent_recipient_norms=frozenset({"bad@lab.cl"}),
            suppressed_norms=frozenset({"bad@lab.cl"}),
            allow_prior_outreach_history=True,
        ),
    )
    assert suppressed.eligible is False
    assert suppressed.reasons == (REASON_SUPPRESSION,)

    snoozed = evaluate_export_eligibility(
        contact_email="later@lab.cl",
        institution_name="Lab",
        ctx=_ctx(
            outreach_state_by_email={"later@lab.cl": "snoozed"},
            allow_prior_outreach_history=True,
        ),
    )
    assert snoozed.eligible is False
    assert snoozed.reasons == (REASON_OUTREACH_SNOOZED,)

    commercial = evaluate_export_eligibility(
        contact_email="quote@lab.cl",
        institution_name="Lab",
        ctx=_ctx(
            commercial_hold_norms=frozenset({"quote@lab.cl"}),
            allow_prior_outreach_history=True,
        ),
    )
    assert commercial.eligible is False
    assert commercial.reasons == (REASON_ACTIVE_COMMERCIAL_ENGAGEMENT,)


def test_load_active_commercial_hold_norms_from_sqlite() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE outbound_commercial_hold (
          email_norm TEXT PRIMARY KEY,
          reasons_json TEXT NOT NULL,
          source_count INTEGER NOT NULL,
          refreshed_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO outbound_commercial_hold VALUES (?,?,?,?)",
        ("Buyer@Lab.CL", "[]", 1, "2026-09-15T00:00:00Z"),
    )
    try:
        assert load_active_commercial_hold_norms(conn) == frozenset({"buyer@lab.cl"})
    finally:
        conn.close()


def _seed_processor_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE emails (
          recipients TEXT, source_file TEXT, folder TEXT, date_iso TEXT, date_raw TEXT
        );
        CREATE TABLE outreach_contact_state (
          contact_email_norm TEXT PRIMARY KEY, state TEXT NOT NULL,
          first_contacted_at TEXT, last_contacted_at TEXT, source TEXT, notes TEXT,
          updated_at TEXT NOT NULL, updated_by TEXT, lead_id INTEGER
        );
        CREATE TABLE contact_email_suppression (
          email TEXT PRIMARY KEY, suppression_reason_code TEXT, suppression_reason_text TEXT,
          suppression_source TEXT, last_bounced_at TEXT, updated_at TEXT, updated_by TEXT
        );
        CREATE TABLE contact_domain_suppression (
          domain_norm TEXT PRIMARY KEY,
          suppression_reason_text TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          updated_by TEXT NOT NULL
        );
        CREATE TABLE supplier_import_batch (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          source_filename TEXT NOT NULL,
          file_sha256 TEXT NOT NULL,
          imported_at TEXT NOT NULL
        );
        CREATE TABLE supplier_master (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          domain_norm TEXT NOT NULL UNIQUE,
          trade_name TEXT,
          notes TEXT,
          is_exclusion INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE supplier_priority_snapshot (
          supplier_id INTEGER NOT NULL,
          batch_id INTEGER NOT NULL,
          tier TEXT NOT NULL,
          rank_in_list INTEGER NOT NULL,
          confidence_score REAL,
          confidence_label TEXT,
          category_context TEXT,
          PRIMARY KEY (supplier_id, batch_id)
        );
        """
    )
    conn.execute(
        "INSERT INTO emails VALUES (?,?,?,?,?)",
        (
            "To: old@lab-example.cl",
            "gmail:contacto@origenlab.cl/m1",
            "[Gmail]/Enviados",
            "2026-09-01T00:00:00Z",
            "",
        ),
    )
    conn.execute(
        "INSERT INTO outreach_contact_state VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "old@lab-example.cl",
            "contacted",
            "2026-09-01T00:00:00Z",
            "2026-09-01T00:00:00Z",
            "campaign",
            "",
            "2026-09-01T00:00:00Z",
            "pytest",
            None,
        ),
    )
    conn.commit()
    conn.close()


def test_broad_processor_allow_prior_outreach_ignores_legacy_master(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    _seed_processor_db(db)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "reviewed_marketing_contacts.csv").write_text(
        "institution_name,region,city,type,contact_email,contact_label,source_url,confidence,fit_signal\n"
        "Laboratorio Ejemplo,RM,Santiago,laboratorio,old@lab-example.cl,Encargado de Laboratorio,"
        "https://lab-example.cl/laboratorio/equipamiento/contacto,high,laboratorio analisis investigacion\n",
        encoding="utf-8",
    )
    (ws / "do_not_repeat_master.csv").write_text(
        "email_norm,source_kinds,source_count,first_seen_at,last_seen_at,notes\n"
        "old@lab-example.cl,gmail_sent;outreach_state,2,,,\n",
        encoding="utf-8",
    )

    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    result = subprocess.run(
        [
            sys.executable,
            str(PROCESS),
            "--db",
            str(db),
            "--workspace",
            str(ws),
            "--gmail-user",
            "contacto@origenlab.cl",
            "--sent-folder",
            "[Gmail]/Enviados",
            "--allow-prior-outreach",
        ],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout

    with (ws / "send_ready_marketing.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert [row["contact_email"] for row in rows] == ["old@lab-example.cl"]
