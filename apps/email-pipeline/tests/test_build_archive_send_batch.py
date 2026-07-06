from __future__ import annotations

import csv
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

_TDIR = Path(__file__).resolve().parent
if str(_TDIR) not in sys.path:
    sys.path.insert(0, str(_TDIR))

from domain_fixture import assert_domain_in_collection
from gmail_sent_history_seed import (
    seed_minimal_sent_history_for_preflight,
    seed_minimal_sent_history_for_preflight_extended_emails,
)

from origenlab_email_pipeline.archive_outreach_queue import (
    ARCHIVE_CANDIDATE_SORT_COMPANY_INTRO,
    ARCHIVE_CANDIDATE_SORT_LEGACY,
)
from origenlab_email_pipeline.archive_send_batch_builder import (
    AUDIT_CSV_NAME,
    AUDIT_SUMMARY_JSON_NAME,
    BUILD_SUMMARY_JSON_NAME,
    REVIEW_REQUIRED_CSV_NAME,
    SEND_READY_CSV_NAME,
    SHORTLIST_COMMERCIAL_PRECHECK_CSV_NAME,
    SHORTLIST_CSV_NAME,
    SHORTLIST_GATE_AUDIT_CSV_NAME,
    build_archive_send_batch,
)

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "leads" / "build_archive_send_batch.py"


def _seed_db(path: Path, *, with_sent_preflight: bool = True) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE contact_master (
          email TEXT PRIMARY KEY,
          contact_name_best TEXT,
          domain TEXT,
          organization_name_guess TEXT,
          organization_type_guess TEXT,
          first_seen_at TEXT,
          last_seen_at TEXT,
          total_emails INTEGER,
          inbound_emails INTEGER,
          outbound_emails INTEGER,
          quote_email_count INTEGER,
          invoice_email_count INTEGER,
          purchase_email_count INTEGER,
          business_doc_email_count INTEGER,
          quote_doc_count INTEGER,
          invoice_doc_count INTEGER,
          top_equipment_tags TEXT,
          confidence_score REAL
        );
        CREATE TABLE organization_master (
          domain TEXT PRIMARY KEY,
          organization_name_guess TEXT,
          organization_type_guess TEXT,
          first_seen_at TEXT,
          last_seen_at TEXT,
          total_emails INTEGER,
          total_contacts INTEGER,
          quote_email_count INTEGER,
          invoice_email_count INTEGER,
          purchase_email_count INTEGER,
          business_doc_email_count INTEGER,
          quote_doc_count INTEGER,
          invoice_doc_count INTEGER,
          top_equipment_tags TEXT,
          key_contacts TEXT
        );
        CREATE TABLE emails (recipients TEXT, source_file TEXT, folder TEXT);
        CREATE TABLE contact_email_suppression (email TEXT PRIMARY KEY, suppression_reason_code TEXT);
        CREATE TABLE outreach_contact_state (contact_email_norm TEXT PRIMARY KEY, state TEXT);
        CREATE TABLE supplier_master (domain_norm TEXT);
         CREATE TABLE opportunity_signals (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          signal_type TEXT NOT NULL,
          entity_kind TEXT NOT NULL,
          entity_key TEXT NOT NULL,
          email_id INTEGER,
          attachment_id INTEGER,
          score REAL,
          details_json TEXT,
          created_at TEXT
        );
        CREATE TABLE contact_candidate (
          contact_email TEXT PRIMARY KEY,
          org_domain TEXT,
          status TEXT NOT NULL DEFAULT 'new',
          suppression_flags TEXT NOT NULL DEFAULT '',
          rationale_text TEXT NOT NULL DEFAULT '',
          confidence_score REAL NOT NULL DEFAULT 0,
          strength_score REAL NOT NULL DEFAULT 0,
          evidence_count INTEGER NOT NULL DEFAULT 0,
          display_name TEXT,
          provenance_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL DEFAULT ''
        );
        """
    )
    conn.execute(
        """
        INSERT INTO organization_master (
          domain, organization_name_guess, organization_type_guess, first_seen_at, last_seen_at,
          total_emails, total_contacts, quote_email_count, invoice_email_count, purchase_email_count,
          business_doc_email_count, quote_doc_count, invoice_doc_count, top_equipment_tags, key_contacts
        ) VALUES ('buyer.cl','Buyer','business','2021-01-01','2026-01-01',120,10,10,2,2,0,0,0,'','')
        """
    )
    conn.executemany(
        """
        INSERT INTO contact_master (
          email, contact_name_best, domain, organization_name_guess, organization_type_guess,
          first_seen_at, last_seen_at, total_emails, inbound_emails, outbound_emails,
          quote_email_count, invoice_email_count, purchase_email_count, business_doc_email_count,
          quote_doc_count, invoice_doc_count, top_equipment_tags, confidence_score
        ) VALUES (?, ?, 'buyer.cl', 'Buyer Org', 'business', '2021-01-01', ?, ?, 1, 1, ?, 0, 0, 0, 0, 0, '', ?)
        """,
        [
            ("ready@buyer.cl", "Ready", "2026-01-10", 80, 8, 0.9),
            ("review@buyer.cl", "Review", "2026-01-09", 70, 6, 0.8),
            ("weak@buyer.cl", "Weak", "2026-01-08", 1, 0, 0.1),
            ("blocked@buyer.cl", "Blocked", "2026-01-07", 65, 5, 0.8),
            ("suppressed@buyer.cl", "Suppressed", "2026-01-06", 60, 5, 0.8),
            ("manual@buyer.cl", "Manual", "2026-01-05", 60, 5, 0.8),
        ],
    )
    conn.execute(
        "INSERT INTO contact_email_suppression (email, suppression_reason_code) VALUES ('blocked@buyer.cl', 'manual_do_not_contact')"
    )
    conn.execute(
        """
        INSERT INTO contact_candidate (
          contact_email, org_domain, status, suppression_flags, rationale_text,
          confidence_score, strength_score, evidence_count, created_at, updated_at
        ) VALUES ('ready@buyer.cl', 'buyer.cl', 'approved', '', 'ok', 0.9, 0.9, 3, 't', 't')
        """
    )
    conn.execute(
        """
        INSERT INTO contact_candidate (
          contact_email, org_domain, status, suppression_flags, rationale_text,
          confidence_score, strength_score, evidence_count, created_at, updated_at
        ) VALUES ('manual@buyer.cl', 'buyer.cl', 'approved', '', 'ok', 0.9, 0.9, 3, 't', 't')
        """
    )
    conn.execute(
        """
        INSERT INTO contact_candidate (
          contact_email, org_domain, status, suppression_flags, rationale_text,
          confidence_score, strength_score, evidence_count, created_at, updated_at
        ) VALUES ('suppressed@buyer.cl', 'buyer.cl', 'suppressed', 'MANUAL_SUPPRESS', 'blocked', 0.9, 0.9, 3, 't', 't')
        """
    )
    if with_sent_preflight:
        seed_minimal_sent_history_for_preflight(conn)
    conn.commit()
    conn.close()


def _seed_manual_domain_suppress_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE contact_master (
          email TEXT PRIMARY KEY,
          contact_name_best TEXT,
          domain TEXT,
          organization_name_guess TEXT,
          organization_type_guess TEXT,
          first_seen_at TEXT,
          last_seen_at TEXT,
          total_emails INTEGER,
          inbound_emails INTEGER,
          outbound_emails INTEGER,
          quote_email_count INTEGER,
          invoice_email_count INTEGER,
          purchase_email_count INTEGER,
          business_doc_email_count INTEGER,
          quote_doc_count INTEGER,
          invoice_doc_count INTEGER,
          top_equipment_tags TEXT,
          confidence_score REAL
        );
        CREATE TABLE organization_master (
          domain TEXT PRIMARY KEY,
          organization_name_guess TEXT,
          organization_type_guess TEXT,
          first_seen_at TEXT,
          last_seen_at TEXT,
          total_emails INTEGER,
          total_contacts INTEGER,
          quote_email_count INTEGER,
          invoice_email_count INTEGER,
          purchase_email_count INTEGER,
          business_doc_email_count INTEGER,
          quote_doc_count INTEGER,
          invoice_doc_count INTEGER,
          top_equipment_tags TEXT,
          key_contacts TEXT
        );
        CREATE TABLE emails (recipients TEXT, source_file TEXT, folder TEXT);
        CREATE TABLE contact_email_suppression (email TEXT PRIMARY KEY, suppression_reason_code TEXT);
        CREATE TABLE outreach_contact_state (contact_email_norm TEXT PRIMARY KEY, state TEXT);
        CREATE TABLE supplier_master (domain_norm TEXT);
        CREATE TABLE opportunity_signals (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          signal_type TEXT NOT NULL,
          entity_kind TEXT NOT NULL,
          entity_key TEXT NOT NULL,
          email_id INTEGER,
          attachment_id INTEGER,
          score REAL,
          details_json TEXT,
          created_at TEXT
        );
        CREATE TABLE contact_candidate (
          contact_email TEXT PRIMARY KEY,
          org_domain TEXT,
          status TEXT NOT NULL DEFAULT 'new',
          suppression_flags TEXT NOT NULL DEFAULT '',
          rationale_text TEXT NOT NULL DEFAULT '',
          confidence_score REAL NOT NULL DEFAULT 0,
          strength_score REAL NOT NULL DEFAULT 0,
          evidence_count INTEGER NOT NULL DEFAULT 0,
          display_name TEXT,
          provenance_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL DEFAULT ''
        );
        """
    )
    conn.execute(
        """
        INSERT INTO organization_master (
          domain, organization_name_guess, organization_type_guess, first_seen_at, last_seen_at,
          total_emails, total_contacts, quote_email_count, invoice_email_count, purchase_email_count,
          business_doc_email_count, quote_doc_count, invoice_doc_count, top_equipment_tags, key_contacts
        ) VALUES ('blocked.com','Blocked','business','2021-01-01','2026-01-01',120,10,10,2,2,0,0,0,'','')
        """
    )
    conn.execute(
        """
        INSERT INTO contact_master (
          email, contact_name_best, domain, organization_name_guess, organization_type_guess,
          first_seen_at, last_seen_at, total_emails, inbound_emails, outbound_emails,
          quote_email_count, invoice_email_count, purchase_email_count, business_doc_email_count,
          quote_doc_count, invoice_doc_count, top_equipment_tags, confidence_score
        ) VALUES (
          'x@blocked.com', 'X', 'blocked.com', 'Blocked', 'business',
          '2024-01-01', '2026-01-01', 80, 40, 40, 0, 0, 0, 0, 0, 0, '', 0.9
        )
        """
    )
    conn.execute(
        """
        INSERT INTO contact_candidate (
          contact_email, org_domain, status, suppression_flags, rationale_text,
          confidence_score, strength_score, evidence_count, created_at, updated_at
        ) VALUES ('x@blocked.com', 'blocked.com', 'approved', '', 'ok', 0.9, 0.9, 3, 't', 't')
        """
    )
    seed_minimal_sent_history_for_preflight(conn)
    conn.commit()
    conn.close()


def _seed_volume_archive_contacts_only(path: Path, *, n: int) -> None:
    """Many gate-clean buyer.cl contacts for shortlist sizing tests."""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE contact_master (
          email TEXT PRIMARY KEY,
          contact_name_best TEXT,
          domain TEXT,
          organization_name_guess TEXT,
          organization_type_guess TEXT,
          first_seen_at TEXT,
          last_seen_at TEXT,
          total_emails INTEGER,
          inbound_emails INTEGER,
          outbound_emails INTEGER,
          quote_email_count INTEGER,
          invoice_email_count INTEGER,
          purchase_email_count INTEGER,
          business_doc_email_count INTEGER,
          quote_doc_count INTEGER,
          invoice_doc_count INTEGER,
          top_equipment_tags TEXT,
          confidence_score REAL
        );
        CREATE TABLE organization_master (
          domain TEXT PRIMARY KEY,
          organization_name_guess TEXT,
          organization_type_guess TEXT,
          first_seen_at TEXT,
          last_seen_at TEXT,
          total_emails INTEGER,
          total_contacts INTEGER,
          quote_email_count INTEGER,
          invoice_email_count INTEGER,
          purchase_email_count INTEGER,
          business_doc_email_count INTEGER,
          quote_doc_count INTEGER,
          invoice_doc_count INTEGER,
          top_equipment_tags TEXT,
          key_contacts TEXT
        );
        CREATE TABLE emails (recipients TEXT, source_file TEXT, folder TEXT);
        CREATE TABLE contact_email_suppression (email TEXT PRIMARY KEY, suppression_reason_code TEXT);
        CREATE TABLE outreach_contact_state (contact_email_norm TEXT PRIMARY KEY, state TEXT);
        CREATE TABLE supplier_master (domain_norm TEXT);
        CREATE TABLE opportunity_signals (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          signal_type TEXT NOT NULL,
          entity_kind TEXT NOT NULL,
          entity_key TEXT NOT NULL,
          email_id INTEGER,
          attachment_id INTEGER,
          score REAL,
          details_json TEXT,
          created_at TEXT
        );
        CREATE TABLE contact_candidate (
          contact_email TEXT PRIMARY KEY,
          org_domain TEXT,
          status TEXT NOT NULL DEFAULT 'new',
          suppression_flags TEXT NOT NULL DEFAULT '',
          rationale_text TEXT NOT NULL DEFAULT '',
          confidence_score REAL NOT NULL DEFAULT 0,
          strength_score REAL NOT NULL DEFAULT 0,
          evidence_count INTEGER NOT NULL DEFAULT 0,
          display_name TEXT,
          provenance_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL DEFAULT ''
        );
        """
    )
    conn.execute(
        """
        INSERT INTO organization_master (
          domain, organization_name_guess, organization_type_guess, first_seen_at, last_seen_at,
          total_emails, total_contacts, quote_email_count, invoice_email_count, purchase_email_count,
          business_doc_email_count, quote_doc_count, invoice_doc_count, top_equipment_tags, key_contacts
        ) VALUES ('buyer.cl','Buyer','business','2021-01-01','2026-01-01',120,10,10,2,2,0,0,0,'','')
        """
    )
    cm_rows: list[tuple[object, ...]] = []
    for i in range(n):
        em = f"vol{i:03d}@buyer.cl"
        tot = 40 + (i % 7)
        cm_rows.append(
            (
                em,
                f"U{i}",
                "buyer.cl",
                "Buyer Org",
                "business",
                "2021-01-01",
                "2026-01-01",
                tot,
                tot // 2,
                tot // 2,
                2,
                0,
                0,
                0,
                0,
                0,
                "",
            0.75,
            )
        )
    conn.executemany(
        """
        INSERT INTO contact_master (
          email, contact_name_best, domain, organization_name_guess, organization_type_guess,
          first_seen_at, last_seen_at, total_emails, inbound_emails, outbound_emails,
          quote_email_count, invoice_email_count, purchase_email_count, business_doc_email_count,
          quote_doc_count, invoice_doc_count, top_equipment_tags, confidence_score
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        cm_rows,
    )
    conn.executemany(
        """
        INSERT INTO contact_candidate (
          contact_email, org_domain, status, suppression_flags, rationale_text,
          confidence_score, strength_score, evidence_count, created_at, updated_at
        ) VALUES (?, ?, ?, '', 'ok', 0.8, 0.8, 2, 't', 't')
        """,
        [(r[0], "buyer.cl", "approved") for r in cm_rows],
    )
    seed_minimal_sent_history_for_preflight(conn)
    conn.commit()
    conn.close()


def _seed_company_intro_priority_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE contact_master (
          email TEXT PRIMARY KEY,
          contact_name_best TEXT,
          domain TEXT,
          organization_name_guess TEXT,
          organization_type_guess TEXT,
          first_seen_at TEXT,
          last_seen_at TEXT,
          total_emails INTEGER,
          inbound_emails INTEGER,
          outbound_emails INTEGER,
          quote_email_count INTEGER,
          invoice_email_count INTEGER,
          purchase_email_count INTEGER,
          business_doc_email_count INTEGER,
          quote_doc_count INTEGER,
          invoice_doc_count INTEGER,
          top_equipment_tags TEXT,
          confidence_score REAL
        );
        CREATE TABLE organization_master (
          domain TEXT PRIMARY KEY,
          organization_name_guess TEXT,
          organization_type_guess TEXT,
          first_seen_at TEXT,
          last_seen_at TEXT,
          total_emails INTEGER,
          total_contacts INTEGER,
          quote_email_count INTEGER,
          invoice_email_count INTEGER,
          purchase_email_count INTEGER,
          business_doc_email_count INTEGER,
          quote_doc_count INTEGER,
          invoice_doc_count INTEGER,
          top_equipment_tags TEXT,
          key_contacts TEXT
        );
        CREATE TABLE contact_email_suppression (email TEXT PRIMARY KEY, suppression_reason_code TEXT);
        CREATE TABLE outreach_contact_state (contact_email_norm TEXT PRIMARY KEY, state TEXT);
        CREATE TABLE supplier_master (domain_norm TEXT);
         CREATE TABLE opportunity_signals (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          signal_type TEXT NOT NULL,
          entity_kind TEXT NOT NULL,
          entity_key TEXT NOT NULL,
          email_id INTEGER,
          attachment_id INTEGER,
          score REAL,
          details_json TEXT,
          created_at TEXT
        );
        CREATE TABLE contact_candidate (
          contact_email TEXT PRIMARY KEY,
          org_domain TEXT,
          status TEXT NOT NULL DEFAULT 'new',
          suppression_flags TEXT NOT NULL DEFAULT '',
          rationale_text TEXT NOT NULL DEFAULT '',
          confidence_score REAL NOT NULL DEFAULT 0,
          strength_score REAL NOT NULL DEFAULT 0,
          evidence_count INTEGER NOT NULL DEFAULT 0,
          display_name TEXT,
          provenance_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE emails (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          recipients TEXT,
          source_file TEXT,
          folder TEXT,
          sender TEXT,
          date_iso TEXT,
          date_raw TEXT
        );
        """
    )
    conn.executemany(
        """
        INSERT INTO contact_master (
          email, contact_name_best, domain, organization_name_guess, organization_type_guess,
          first_seen_at, last_seen_at, total_emails, inbound_emails, outbound_emails,
          quote_email_count, invoice_email_count, purchase_email_count, business_doc_email_count,
          quote_doc_count, invoice_doc_count, top_equipment_tags, confidence_score
        ) VALUES (?, ?, ?, 'Acme Org', 'business', '2021-01-01', ?, ?, 1, 1, ?, 0, 0, 0, 0, 0, '', ?)
        """,
        [
            # Warmth capped by contact volume term; keep corp below heavy but out of weak band.
            ("corp@acme.cl", "Corp", "acme.cl", "2026-01-10", 80, 0, 0.9),
            ("corp2@acme.cl", "Corp2", "acme.cl", "2026-01-10", 80, 0, 0.9),
            ("heavy@gmail.com", "Heavy", "gmail.com", "2026-01-10", 120, 0, 0.95),
        ],
    )
    conn.executemany(
        """
        INSERT INTO contact_candidate (
          contact_email, org_domain, status, suppression_flags, rationale_text,
          confidence_score, strength_score, evidence_count, created_at, updated_at
        ) VALUES (?, ?, ?, '', 'x', 0.8, 0.8, 2, 't', 't')
        """,
        [
            ("corp@acme.cl", "acme.cl", "approved"),
            ("corp2@acme.cl", "acme.cl", "approved"),
            ("heavy@gmail.com", "gmail.com", "suppressed"),
        ],
    )
    conn.execute(
        """
        INSERT INTO emails (recipients, source_file, folder, sender, date_iso, date_raw)
        VALUES (
          'corp@acme.cl',
          'pst',
          'Sent Items',
          '"Tatiana" <ventas@labdelivery.cl>',
          '2026-01-20T12:00:00+00:00',
          ''
        )
        """
    )
    seed_minimal_sent_history_for_preflight_extended_emails(conn)
    conn.commit()
    conn.close()


def _seed_personal_domain_policy_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE contact_master (
          email TEXT PRIMARY KEY,
          contact_name_best TEXT,
          domain TEXT,
          organization_name_guess TEXT,
          organization_type_guess TEXT,
          first_seen_at TEXT,
          last_seen_at TEXT,
          total_emails INTEGER,
          inbound_emails INTEGER,
          outbound_emails INTEGER,
          quote_email_count INTEGER,
          invoice_email_count INTEGER,
          purchase_email_count INTEGER,
          business_doc_email_count INTEGER,
          quote_doc_count INTEGER,
          invoice_doc_count INTEGER,
          top_equipment_tags TEXT,
          confidence_score REAL
        );
        CREATE TABLE organization_master (
          domain TEXT PRIMARY KEY,
          organization_name_guess TEXT,
          organization_type_guess TEXT,
          first_seen_at TEXT,
          last_seen_at TEXT,
          total_emails INTEGER,
          total_contacts INTEGER,
          quote_email_count INTEGER,
          invoice_email_count INTEGER,
          purchase_email_count INTEGER,
          business_doc_email_count INTEGER,
          quote_doc_count INTEGER,
          invoice_doc_count INTEGER,
          top_equipment_tags TEXT,
          key_contacts TEXT
        );
        CREATE TABLE emails (recipients TEXT, source_file TEXT, folder TEXT);
        CREATE TABLE contact_email_suppression (email TEXT PRIMARY KEY, suppression_reason_code TEXT);
        CREATE TABLE outreach_contact_state (contact_email_norm TEXT PRIMARY KEY, state TEXT);
        CREATE TABLE supplier_master (domain_norm TEXT);
        CREATE TABLE opportunity_signals (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          signal_type TEXT NOT NULL,
          entity_kind TEXT NOT NULL,
          entity_key TEXT NOT NULL,
          email_id INTEGER,
          attachment_id INTEGER,
          score REAL,
          details_json TEXT,
          created_at TEXT
        );
        CREATE TABLE contact_candidate (
          contact_email TEXT PRIMARY KEY,
          org_domain TEXT,
          status TEXT NOT NULL DEFAULT 'new',
          suppression_flags TEXT NOT NULL DEFAULT '',
          rationale_text TEXT NOT NULL DEFAULT '',
          confidence_score REAL NOT NULL DEFAULT 0,
          strength_score REAL NOT NULL DEFAULT 0,
          evidence_count INTEGER NOT NULL DEFAULT 0,
          display_name TEXT,
          provenance_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL DEFAULT ''
        );
        """
    )
    conn.execute(
        """
        INSERT INTO organization_master (
          domain, organization_name_guess, organization_type_guess, first_seen_at, last_seen_at,
          total_emails, total_contacts, quote_email_count, invoice_email_count, purchase_email_count,
          business_doc_email_count, quote_doc_count, invoice_doc_count, top_equipment_tags, key_contacts
        ) VALUES ('gmail.com','Gmail','consumer','2021-01-01','2026-01-01',60,5,5,2,2,0,0,0,'','')
        """
    )
    conn.execute(
        """
        INSERT INTO contact_master (
          email, contact_name_best, domain, organization_name_guess, organization_type_guess,
          first_seen_at, last_seen_at, total_emails, inbound_emails, outbound_emails,
          quote_email_count, invoice_email_count, purchase_email_count, business_doc_email_count,
          quote_doc_count, invoice_doc_count, top_equipment_tags, confidence_score
        ) VALUES (
          'buyer.personal@gmail.com', 'Buyer Personal', 'gmail.com', 'Gmail', 'consumer',
          '2024-01-01', '2026-01-01', 20, 10, 10, 5, 1, 1, 0, 0, 0, '', 0.9
        )
        """
    )
    conn.execute(
        """
        INSERT INTO contact_candidate (
          contact_email, org_domain, status, suppression_flags, rationale_text,
          confidence_score, strength_score, evidence_count, created_at, updated_at
        ) VALUES ('buyer.personal@gmail.com', 'gmail.com', 'approved', '', 'ok', 0.9, 0.9, 3, 't', 't')
        """
    )
    seed_minimal_sent_history_for_preflight(conn)
    conn.commit()
    conn.close()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_build_archive_send_batch_happy_path_outputs_and_classification(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    _seed_db(db)
    out_dir = tmp_path / "out"

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        result = build_archive_send_batch(
            conn=conn,
            db_path=db,
            out_dir=out_dir,
            gmail_user="contacto@origenlab.cl",
            fetch_cap=1000,
            audit_limit=500,
            shortlist_limit=25,
            sent_folders=("[Gmail]/Enviados",),
            strict_contact_graph_noise=True,
            allow_weak_warmth=True,
            skip_commercial_precheck=False,
            sent_folder_defaults_used=False,
        )
    finally:
        conn.close()

    expected_files = [
        AUDIT_CSV_NAME,
        AUDIT_SUMMARY_JSON_NAME,
        SHORTLIST_CSV_NAME,
        SHORTLIST_GATE_AUDIT_CSV_NAME,
        SHORTLIST_COMMERCIAL_PRECHECK_CSV_NAME,
        SEND_READY_CSV_NAME,
        REVIEW_REQUIRED_CSV_NAME,
        BUILD_SUMMARY_JSON_NAME,
    ]
    for filename in expected_files:
        assert (out_dir / filename).is_file()

    send_ready = _read_csv(out_dir / SEND_READY_CSV_NAME)
    review_required = _read_csv(out_dir / REVIEW_REQUIRED_CSV_NAME)
    gate_audit = _read_csv(out_dir / SHORTLIST_GATE_AUDIT_CSV_NAME)
    precheck = _read_csv(out_dir / SHORTLIST_COMMERCIAL_PRECHECK_CSV_NAME)
    summary = json.loads((out_dir / BUILD_SUMMARY_JSON_NAME).read_text(encoding="utf-8"))

    send_emails = {r["contact_email"] for r in send_ready}
    review_emails = {r["contact_email"] for r in review_required}
    assert "ready@buyer.cl" in send_emails
    assert "review@buyer.cl" in review_emails
    assert "weak@buyer.cl" in review_emails
    assert "suppressed@buyer.cl" in review_emails
    assert summary["commercial_precheck_policy"] == "advisory"
    assert summary["strict_commercial_drop"] is False
    assert summary["advisory_commercial_drop_rows"] >= 1

    gate_blocked = {r["contact_email"] for r in gate_audit if r["gate_eligible"] != "yes"}
    commercial_suppressed = {
        r["contact_email"]
        for r in precheck
        if r["contact_candidate_status"].strip().lower() == "suppressed"
    }
    assert not (gate_blocked & send_emails)
    assert not (commercial_suppressed & send_emails)
    suppressed_row = next(r for r in review_required if r["contact_email"] == "suppressed@buyer.cl")
    assert suppressed_row["final_decision_path"] == "advisory_commercial_drop"

    for key in (
        "archive_audited_rows",
        "archive_eligible_rows",
        "archive_blocked_rows",
        "shortlist_rows",
        "gate_ok_rows",
        "gate_blocked_rows",
        "commercially_suppressed_rows",
        "commercial_review_rows",
        "manual_suppressed_rows",
        "policy_personal_domain_review_rows",
        "weak_warmth_review_rows",
        "advisory_commercial_drop_rows",
        "final_drop_rows",
        "send_ready_rows",
        "review_required_rows",
        "gmail_user",
        "db_path",
        "strict_commercial_drop",
        "commercial_precheck_policy",
        "archive_candidate_sort",
    ):
        assert key in summary
    assert summary["archive_candidate_sort"] == ARCHIVE_CANDIDATE_SORT_COMPANY_INTRO
    assert summary["send_ready_rows"] == len(send_ready)
    assert summary["review_required_rows"] == len(review_required)
    assert result.summary["send_ready_rows"] == len(send_ready)
    assert "outbound_run" in summary
    assert summary["outbound_run"]["lane"] == "archive"
    assert summary["outbound_run"]["sent_folder_defaults_used"] is False
    assert summary["outbound_run"]["strict_contact_graph_noise"] is True
    for k in ("schema_version", "gmail_user", "sqlite_path", "sent_folders_resolved", "counts"):
        assert k in summary["outbound_run"]
    assert "sent_preflight" in summary
    sp = summary["sent_preflight"]
    assert sp["ok"] is True
    assert sp["override_used"] is False
    assert sp["sent_row_count"] >= 1
    assert sp["parsed_recipient_count"] >= 1
    assert sp["gmail_user"] == "contacto@origenlab.cl"
    assert all("decision_path" in row for row in precheck)
    assert all("final_decision_path" in row for row in send_ready + review_required)


def test_build_archive_send_batch_shortlist_company_intro_before_legacy_warmth_order(tmp_path: Path) -> None:
    db = tmp_path / "prio.sqlite"
    _seed_company_intro_priority_db(db)
    out_intro = tmp_path / "out_intro"
    out_legacy = tmp_path / "out_legacy"

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        build_archive_send_batch(
            conn=conn,
            db_path=db,
            out_dir=out_intro,
            gmail_user="contacto@origenlab.cl",
            fetch_cap=1000,
            audit_limit=500,
            shortlist_limit=3,
            sent_folders=("[Gmail]/Enviados",),
            strict_contact_graph_noise=True,
            allow_weak_warmth=True,
            skip_commercial_precheck=False,
            sent_folder_defaults_used=False,
            archive_candidate_sort=ARCHIVE_CANDIDATE_SORT_COMPANY_INTRO,
        )
    finally:
        conn.close()

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        build_archive_send_batch(
            conn=conn,
            db_path=db,
            out_dir=out_legacy,
            gmail_user="contacto@origenlab.cl",
            fetch_cap=1000,
            audit_limit=500,
            shortlist_limit=3,
            sent_folders=("[Gmail]/Enviados",),
            strict_contact_graph_noise=True,
            allow_weak_warmth=True,
            skip_commercial_precheck=False,
            sent_folder_defaults_used=False,
            archive_candidate_sort=ARCHIVE_CANDIDATE_SORT_LEGACY,
        )
    finally:
        conn.close()

    intro_sl = _read_csv(out_intro / SHORTLIST_CSV_NAME)
    legacy_sl = _read_csv(out_legacy / SHORTLIST_CSV_NAME)
    assert [r["contact_email"] for r in intro_sl] == ["corp@acme.cl", "corp2@acme.cl", "heavy@gmail.com"]
    # Legacy tie-break uses contact_email; corp2@ sorts before corp@ at equal warmth/volume.
    assert [r["contact_email"] for r in legacy_sl] == ["heavy@gmail.com", "corp2@acme.cl", "corp@acme.cl"]

    intro_summary = json.loads((out_intro / BUILD_SUMMARY_JSON_NAME).read_text(encoding="utf-8"))
    assert intro_summary["archive_candidate_sort"] == ARCHIVE_CANDIDATE_SORT_COMPANY_INTRO
    assert intro_summary.get("shortlist_labdelivery_touch_rows", 0) >= 1

    assert "last_contacted_by_labdelivery" in intro_sl[0]
    assert intro_sl[0]["contact_email"] == "corp@acme.cl"
    assert str(intro_sl[0].get("last_contacted_by_labdelivery") or "").lower() in {"true", "1", "yes"}
    assert "2026-01-20" in (intro_sl[0].get("labdelivery_last_contact_at") or "")
    assert intro_sl[1]["contact_email"] == "corp2@acme.cl"
    assert str(intro_sl[1].get("last_contacted_by_labdelivery") or "").lower() not in {"true", "1", "yes"}

    intro_send = _read_csv(out_intro / SEND_READY_CSV_NAME)
    assert intro_send and intro_send[0]["contact_email"] == "corp@acme.cl"
    assert not any(r["contact_email"] == "heavy@gmail.com" for r in intro_send)
    assert "last_contacted_by_labdelivery" in intro_send[0]


def test_build_archive_send_batch_large_shortlist_limit(tmp_path: Path) -> None:
    db = tmp_path / "vol.sqlite"
    _seed_volume_archive_contacts_only(db, n=160)
    out_dir = tmp_path / "out_vol"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        build_archive_send_batch(
            conn=conn,
            db_path=db,
            out_dir=out_dir,
            gmail_user="contacto@origenlab.cl",
            fetch_cap=5000,
            audit_limit=220,
            shortlist_limit=100,
            sent_folders=("[Gmail]/Enviados",),
            strict_contact_graph_noise=True,
            allow_weak_warmth=True,
            skip_commercial_precheck=False,
            sent_folder_defaults_used=False,
        )
    finally:
        conn.close()
    sl = _read_csv(out_dir / SHORTLIST_CSV_NAME)
    assert len(sl) == 100
    assert "last_contacted_by_labdelivery" in sl[0]
    assert "labdelivery_last_contact_at" in sl[0]
    summary = json.loads((out_dir / BUILD_SUMMARY_JSON_NAME).read_text(encoding="utf-8"))
    assert summary["shortlist_rows"] == 100


def test_build_archive_send_batch_cli_works_without_refresh_sent(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    _seed_db(db)
    out_dir = tmp_path / "out_cli"
    run = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--db",
            str(db),
            "--out-dir",
            str(out_dir),
            "--build-batch",
            "--shortlist-limit",
            "10",
            "--audit-limit",
            "100",
            "--allow-weak-warmth",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr + run.stdout
    assert (out_dir / BUILD_SUMMARY_JSON_NAME).is_file()
    assert (out_dir / SHORTLIST_CSV_NAME).is_file()


def test_build_archive_send_batch_cli_default_is_audit_only(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    _seed_db(db)
    out_dir = tmp_path / "out_default_audit"
    run = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--db",
            str(db),
            "--out-dir",
            str(out_dir),
            "--audit-limit",
            "100",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr + run.stdout
    assert (out_dir / AUDIT_CSV_NAME).is_file()
    assert (out_dir / AUDIT_SUMMARY_JSON_NAME).is_file()
    assert (out_dir / BUILD_SUMMARY_JSON_NAME).is_file()
    assert not (out_dir / SHORTLIST_CSV_NAME).is_file()
    assert not (out_dir / SEND_READY_CSV_NAME).is_file()
    assert not (out_dir / REVIEW_REQUIRED_CSV_NAME).is_file()
    assert "--build-batch" in run.stdout


def test_build_archive_send_batch_cli_rejects_audit_only_and_build_batch(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    _seed_db(db)
    out_dir = tmp_path / "out_conflict"
    run = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--db",
            str(db),
            "--out-dir",
            str(out_dir),
            "--audit-only",
            "--build-batch",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode != 0
    assert "cannot be used together" in run.stderr


def test_build_archive_send_batch_cli_rejects_postgres_audit_without_build_batch(
    tmp_path: Path,
) -> None:
    db = tmp_path / "t.sqlite"
    _seed_db(db)
    out_dir = tmp_path / "out_pg"
    run = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--db",
            str(db),
            "--out-dir",
            str(out_dir),
            "--write-postgres-audit",
            "--postgres-url",
            "postgresql://u:p@h/db",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode != 0
    assert "--write-postgres-audit requires --build-batch" in run.stderr


def test_build_archive_send_batch_personal_domain_client_signal_routes_to_review(tmp_path: Path) -> None:
    db = tmp_path / "policy.sqlite"
    _seed_personal_domain_policy_db(db)
    out_off = tmp_path / "out_off"
    out_on = tmp_path / "out_on"

    conn_off = sqlite3.connect(str(db))
    conn_off.row_factory = sqlite3.Row
    try:
        build_archive_send_batch(
            conn=conn_off,
            db_path=db,
            out_dir=out_off,
            gmail_user="contacto@origenlab.cl",
            fetch_cap=1000,
            audit_limit=500,
            shortlist_limit=25,
            sent_folders=("[Gmail]/Enviados",),
            strict_contact_graph_noise=True,
            allow_weak_warmth=True,
            skip_commercial_precheck=False,
            route_personal_domain_with_client_signals_to_review=False,
            sent_folder_defaults_used=False,
        )
    finally:
        conn_off.close()

    conn_on = sqlite3.connect(str(db))
    conn_on.row_factory = sqlite3.Row
    try:
        result_on = build_archive_send_batch(
            conn=conn_on,
            db_path=db,
            out_dir=out_on,
            gmail_user="contacto@origenlab.cl",
            fetch_cap=1000,
            audit_limit=500,
            shortlist_limit=25,
            sent_folders=("[Gmail]/Enviados",),
            strict_contact_graph_noise=True,
            allow_weak_warmth=True,
            skip_commercial_precheck=False,
            route_personal_domain_with_client_signals_to_review=True,
            sent_folder_defaults_used=False,
        )
    finally:
        conn_on.close()

    off_send = _read_csv(out_off / SEND_READY_CSV_NAME)
    off_review = _read_csv(out_off / REVIEW_REQUIRED_CSV_NAME)
    on_send = _read_csv(out_on / SEND_READY_CSV_NAME)
    on_review = _read_csv(out_on / REVIEW_REQUIRED_CSV_NAME)
    on_summary = json.loads((out_on / BUILD_SUMMARY_JSON_NAME).read_text(encoding="utf-8"))

    assert any(r["contact_email"] == "buyer.personal@gmail.com" for r in off_send)
    assert not any(r["contact_email"] == "buyer.personal@gmail.com" for r in off_review)
    assert any(r["contact_email"] == "buyer.personal@gmail.com" for r in on_review)
    assert not any(r["contact_email"] == "buyer.personal@gmail.com" for r in on_send)
    assert any(
        r["contact_email"] == "buyer.personal@gmail.com"
        and r["final_decision_path"] == "policy_personal_domain_review"
        for r in on_review
    )
    assert on_summary["policy_personal_domain_review_rows"] >= 1
    assert result_on.summary["policy_personal_domain_review_rows"] >= 1


def test_build_archive_send_batch_strict_commercial_drop_omits_suppressed(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    _seed_db(db)
    out_dir = tmp_path / "out_strict"

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        build_archive_send_batch(
            conn=conn,
            db_path=db,
            out_dir=out_dir,
            gmail_user="contacto@origenlab.cl",
            fetch_cap=1000,
            audit_limit=500,
            shortlist_limit=25,
            sent_folders=("[Gmail]/Enviados",),
            strict_contact_graph_noise=True,
            allow_weak_warmth=True,
            skip_commercial_precheck=False,
            strict_commercial_drop=True,
            sent_folder_defaults_used=False,
        )
    finally:
        conn.close()

    send_ready = _read_csv(out_dir / SEND_READY_CSV_NAME)
    review_required = _read_csv(out_dir / REVIEW_REQUIRED_CSV_NAME)
    emails_out = {r["contact_email"] for r in send_ready + review_required}
    assert "suppressed@buyer.cl" not in emails_out
    summary = json.loads((out_dir / BUILD_SUMMARY_JSON_NAME).read_text(encoding="utf-8"))
    assert summary["commercial_precheck_policy"] == "strict_drop"
    assert summary["strict_commercial_drop"] is True


def _seed_shortlist_one_per_domain_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE contact_master (
          email TEXT PRIMARY KEY,
          contact_name_best TEXT,
          domain TEXT,
          organization_name_guess TEXT,
          organization_type_guess TEXT,
          first_seen_at TEXT,
          last_seen_at TEXT,
          total_emails INTEGER,
          inbound_emails INTEGER,
          outbound_emails INTEGER,
          quote_email_count INTEGER,
          invoice_email_count INTEGER,
          purchase_email_count INTEGER,
          business_doc_email_count INTEGER,
          quote_doc_count INTEGER,
          invoice_doc_count INTEGER,
          top_equipment_tags TEXT,
          confidence_score REAL
        );
        CREATE TABLE organization_master (
          domain TEXT PRIMARY KEY,
          organization_name_guess TEXT,
          organization_type_guess TEXT,
          first_seen_at TEXT,
          last_seen_at TEXT,
          total_emails INTEGER,
          total_contacts INTEGER,
          quote_email_count INTEGER,
          invoice_email_count INTEGER,
          purchase_email_count INTEGER,
          business_doc_email_count INTEGER,
          quote_doc_count INTEGER,
          invoice_doc_count INTEGER,
          top_equipment_tags TEXT,
          key_contacts TEXT
        );
        CREATE TABLE emails (recipients TEXT, source_file TEXT, folder TEXT);
        CREATE TABLE contact_email_suppression (email TEXT PRIMARY KEY, suppression_reason_code TEXT);
        CREATE TABLE outreach_contact_state (contact_email_norm TEXT PRIMARY KEY, state TEXT);
        CREATE TABLE supplier_master (domain_norm TEXT);
        CREATE TABLE opportunity_signals (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          signal_type TEXT NOT NULL,
          entity_kind TEXT NOT NULL,
          entity_key TEXT NOT NULL,
          email_id INTEGER,
          attachment_id INTEGER,
          score REAL,
          details_json TEXT,
          created_at TEXT
        );
        CREATE TABLE contact_candidate (
          contact_email TEXT PRIMARY KEY,
          org_domain TEXT,
          status TEXT NOT NULL DEFAULT 'new',
          suppression_flags TEXT NOT NULL DEFAULT '',
          rationale_text TEXT NOT NULL DEFAULT '',
          confidence_score REAL NOT NULL DEFAULT 0,
          strength_score REAL NOT NULL DEFAULT 0,
          evidence_count INTEGER NOT NULL DEFAULT 0,
          display_name TEXT,
          provenance_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL DEFAULT ''
        );
        """
    )
    conn.executescript(
        """
        INSERT INTO organization_master (
          domain, organization_name_guess, organization_type_guess, first_seen_at, last_seen_at,
          total_emails, total_contacts, quote_email_count, invoice_email_count, purchase_email_count,
          business_doc_email_count, quote_doc_count, invoice_doc_count, top_equipment_tags, key_contacts
        ) VALUES
          ('buyer.cl','Buyer','business','2021-01-01','2026-01-01',120,10,10,2,2,0,0,0,'',''),
          ('other.cl','Other','business','2021-01-01','2026-01-01',80,5,5,1,1,0,0,0,'','');
        INSERT INTO contact_master (
          email, contact_name_best, domain, organization_name_guess, organization_type_guess,
          first_seen_at, last_seen_at, total_emails, inbound_emails, outbound_emails,
          quote_email_count, invoice_email_count, purchase_email_count, business_doc_email_count,
          quote_doc_count, invoice_doc_count, top_equipment_tags, confidence_score
        ) VALUES
          ('first@buyer.cl','First','buyer.cl','Buyer','business','2021-01-01','2026-01-20',80,40,40,8,0,0,0,0,0,'',0.9),
          ('second@buyer.cl','Second','buyer.cl','Buyer','business','2021-01-01','2026-01-19',75,38,37,7,0,0,0,0,0,'',0.85),
          ('only@other.cl','Only','other.cl','Other','business','2021-01-01','2026-01-18',70,35,35,6,0,0,0,0,0,'',0.8);
        INSERT INTO contact_candidate (
          contact_email, org_domain, status, suppression_flags, rationale_text,
          confidence_score, strength_score, evidence_count, created_at, updated_at
        ) VALUES
          ('first@buyer.cl', 'buyer.cl', 'approved', '', 'ok', 0.9, 0.9, 3, 't', 't'),
          ('second@buyer.cl', 'buyer.cl', 'approved', '', 'ok', 0.85, 0.85, 3, 't', 't'),
          ('only@other.cl', 'other.cl', 'approved', '', 'ok', 0.8, 0.8, 3, 't', 't');
        """
    )
    seed_minimal_sent_history_for_preflight(conn)
    conn.commit()
    conn.close()


def test_build_archive_send_batch_shortlist_one_per_domain(tmp_path: Path) -> None:
    db = tmp_path / "dom.sqlite"
    _seed_shortlist_one_per_domain_db(db)
    out_dir = tmp_path / "out_dom"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        build_archive_send_batch(
            conn=conn,
            db_path=db,
            out_dir=out_dir,
            gmail_user="contacto@origenlab.cl",
            fetch_cap=1000,
            audit_limit=500,
            shortlist_limit=10,
            sent_folders=("[Gmail]/Enviados",),
            strict_contact_graph_noise=True,
            allow_weak_warmth=True,
            skip_commercial_precheck=False,
            sent_folder_defaults_used=False,
            shortlist_one_per_domain=True,
        )
    finally:
        conn.close()

    shortlist = _read_csv(out_dir / SHORTLIST_CSV_NAME)
    emails = [r["contact_email"] for r in shortlist]
    assert set(emails) == {"first@buyer.cl", "only@other.cl"}
    assert len([e for e in emails if e.endswith("@buyer.cl")]) == 1
    summary = json.loads((out_dir / BUILD_SUMMARY_JSON_NAME).read_text(encoding="utf-8"))
    assert summary["shortlist_one_per_domain"] is True
    assert summary["shortlist_rows"] == 2


def test_build_archive_send_batch_manual_suppress_email_omits_from_outputs(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    _seed_db(db)
    out_dir = tmp_path / "out_manual"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        build_archive_send_batch(
            conn=conn,
            db_path=db,
            out_dir=out_dir,
            gmail_user="contacto@origenlab.cl",
            fetch_cap=1000,
            audit_limit=500,
            shortlist_limit=25,
            sent_folders=("[Gmail]/Enviados",),
            strict_contact_graph_noise=True,
            allow_weak_warmth=True,
            skip_commercial_precheck=False,
            manual_suppress_emails=("manual@buyer.cl",),
            sent_folder_defaults_used=False,
        )
    finally:
        conn.close()
    send_ready = _read_csv(out_dir / SEND_READY_CSV_NAME)
    review_required = _read_csv(out_dir / REVIEW_REQUIRED_CSV_NAME)
    emails_out = {r["contact_email"] for r in send_ready + review_required}
    assert "manual@buyer.cl" not in emails_out
    summary = json.loads((out_dir / BUILD_SUMMARY_JSON_NAME).read_text(encoding="utf-8"))
    assert summary["manual_suppressed_rows"] >= 1
    assert "manual@buyer.cl" in set(summary.get("manual_suppress_emails", []))


def test_build_archive_send_batch_manual_suppress_domain_omits_from_outputs(tmp_path: Path) -> None:
    db = tmp_path / "dom.sqlite"
    _seed_manual_domain_suppress_db(db)
    out_dir = tmp_path / "out_dom"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        build_archive_send_batch(
            conn=conn,
            db_path=db,
            out_dir=out_dir,
            gmail_user="contacto@origenlab.cl",
            fetch_cap=1000,
            audit_limit=500,
            shortlist_limit=25,
            sent_folders=("[Gmail]/Enviados",),
            strict_contact_graph_noise=True,
            allow_weak_warmth=True,
            skip_commercial_precheck=False,
            manual_suppress_domains=("blocked.com",),
            sent_folder_defaults_used=False,
        )
    finally:
        conn.close()
    send_ready = _read_csv(out_dir / SEND_READY_CSV_NAME)
    review_required = _read_csv(out_dir / REVIEW_REQUIRED_CSV_NAME)
    emails_out = {r["contact_email"] for r in send_ready + review_required}
    assert "x@blocked.com" not in emails_out
    summary = json.loads((out_dir / BUILD_SUMMARY_JSON_NAME).read_text(encoding="utf-8"))
    assert summary["manual_suppressed_rows"] >= 1
    assert_domain_in_collection(
        "blocked.com",
        frozenset(summary.get("manual_suppress_domains", [])),
    )


def test_build_archive_send_batch_audit_only_writes_audit_files(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    _seed_db(db)
    out_dir = tmp_path / "audit_only_out"

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        result = build_archive_send_batch(
            conn=conn,
            db_path=db,
            out_dir=out_dir,
            gmail_user="contacto@origenlab.cl",
            fetch_cap=1000,
            audit_limit=500,
            shortlist_limit=25,
            sent_folders=("[Gmail]/Enviados",),
            strict_contact_graph_noise=True,
            audit_only=True,
            sent_folder_defaults_used=False,
        )
    finally:
        conn.close()

    assert (out_dir / AUDIT_CSV_NAME).is_file()
    assert (out_dir / AUDIT_SUMMARY_JSON_NAME).is_file()
    assert (out_dir / BUILD_SUMMARY_JSON_NAME).is_file()
    assert not (out_dir / SHORTLIST_CSV_NAME).is_file()
    assert result.summary["audit_only"] is True
    assert result.summary["commercial_precheck_policy"] == "n/a_audit_only"
    assert result.summary["outbound_run"]["lane"] == "archive"
    audit_sum = json.loads((out_dir / AUDIT_SUMMARY_JSON_NAME).read_text(encoding="utf-8"))
    assert "outbound_run" in audit_sum


def test_build_archive_send_batch_cli_audit_only(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    _seed_db(db)
    out_dir = tmp_path / "out_audit_cli"
    run = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--db",
            str(db),
            "--out-dir",
            str(out_dir),
            "--audit-only",
            "--audit-limit",
            "100",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr + run.stdout
    assert (out_dir / AUDIT_CSV_NAME).is_file()
    assert not (out_dir / SHORTLIST_CSV_NAME).is_file()

