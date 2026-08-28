from __future__ import annotations

import sys
from pathlib import Path

_TDIR = Path(__file__).resolve().parent.parent
if str(_TDIR) not in sys.path:
    sys.path.insert(0, str(_TDIR))

from origenlab_email_pipeline.db import insert_attachment, insert_email
from origenlab_email_pipeline.historical_quote_register.candidates import (
    SENT_FOLDERS,
    fetch_send_candidates,
    is_gmail_sent_folder,
    is_legacy_mbox_sent_folder,
    is_sent_folder,
)
from historical_quote_register.sqlite_fixture import build_fixture_db, open_writable_fixture


def test_fetch_send_candidates_only_returns_sent_folders(tmp_path):
    db_path = build_fixture_db(tmp_path)
    conn = open_writable_fixture(db_path)
    insert_email(
        conn, source_file="gmail:contacto@origenlab.cl/[Gmail]/Enviados",
        folder=SENT_FOLDERS[0], message_id="<sent-1@origenlab.cl>",
        subject="Cotización equipo X", sender="contacto@origenlab.cl",
        recipients="compras@cliente.cl", date_raw="2026-05-10", date_iso="2026-05-10T10:00:00",
        body="", has_attachments=True, attachment_count=1,
    )
    insert_email(
        conn, source_file="gmail:contacto@origenlab.cl/INBOX",
        folder="INBOX", message_id="<inbox-1@cliente.cl>",
        subject="RE: Cotización equipo X", sender="compras@cliente.cl",
        recipients="contacto@origenlab.cl", date_raw="2026-05-11", date_iso="2026-05-11T09:00:00",
        body="", has_attachments=False,
    )
    conn.commit()
    conn.close()

    ro = __import__(
        "origenlab_email_pipeline.historical_quote_register.db_readonly", fromlist=["open_readonly"]
    ).open_readonly(db_path)
    candidates = fetch_send_candidates(ro)

    assert len(candidates) == 1
    assert candidates[0].subject == "Cotización equipo X"
    assert candidates[0].folder == SENT_FOLDERS[0]


def test_fetch_send_candidates_orders_by_date(tmp_path):
    db_path = build_fixture_db(tmp_path)
    conn = open_writable_fixture(db_path)
    for i, date in enumerate(["2026-06-01T00:00:00", "2026-05-01T00:00:00"]):
        insert_email(
            conn, source_file=f"gmail:contacto@origenlab.cl/{SENT_FOLDERS[0]}",
            folder=SENT_FOLDERS[0], message_id=f"<m{i}@origenlab.cl>",
            subject="Cotización", sender="contacto@origenlab.cl",
            recipients="a@cliente.cl", date_raw=date, date_iso=date, body="",
        )
    conn.commit()
    conn.close()

    from origenlab_email_pipeline.historical_quote_register.db_readonly import open_readonly
    candidates = fetch_send_candidates(open_readonly(db_path))
    assert [c.date_iso for c in candidates] == ["2026-05-01T00:00:00", "2026-06-01T00:00:00"]


def test_fetch_send_candidates_matches_legacy_mbox_folder_by_suffix(tmp_path):
    """The legacy mbox archive's `folder` value is a full absolute path baked
    in at ingestion time and varies by data_root/machine. A fixture row under
    a *different* absolute path but the same trailing '/Elementos enviados'
    segment must still be matched — exact-literal matching would silently
    miss it on a re-ingested archive."""
    db_path = build_fixture_db(tmp_path)
    conn = open_writable_fixture(db_path)
    legacy_folder = (
        "/some/other/data_root/mbox/contacto@labdelivery.cl/"
        "contacto@labdelivery.cl/Elementos enviados"
    )
    insert_email(
        conn, source_file="mbox:contacto@labdelivery.cl/Elementos enviados",
        folder=legacy_folder, message_id="<legacy-1@labdelivery.cl>",
        subject="Cotización legacy", sender="contacto@labdelivery.cl",
        recipients="compras@cliente.cl", date_raw="2018-01-01", date_iso="2018-01-01T00:00:00",
        body="", has_attachments=False,
    )
    conn.commit()
    conn.close()

    from origenlab_email_pipeline.historical_quote_register.db_readonly import open_readonly
    candidates = fetch_send_candidates(open_readonly(db_path))

    assert len(candidates) == 1
    assert candidates[0].folder == legacy_folder
    assert candidates[0].subject == "Cotización legacy"


def test_is_sent_folder_predicates():
    gmail_folder = SENT_FOLDERS[0]
    legacy_folder = (
        "/some/other/data_root/mbox/contacto@labdelivery.cl/"
        "contacto@labdelivery.cl/Elementos enviados"
    )
    # A stray, unrelated backup folder sharing the trailing segment — an
    # accepted tradeoff of suffix matching (see task-2-report.md fix report).
    stray_backup_folder = (
        "/home/rafael/data/origenlab-email/mbox/backup/"
        "Archivo de datos de Outlook/Elementos enviados"
    )

    assert is_gmail_sent_folder(gmail_folder) is True
    assert is_gmail_sent_folder(legacy_folder) is False

    assert is_legacy_mbox_sent_folder(legacy_folder) is True
    assert is_legacy_mbox_sent_folder(gmail_folder) is False

    assert is_sent_folder(gmail_folder) is True
    assert is_sent_folder(legacy_folder) is True
    assert is_sent_folder(stray_backup_folder) is True
    assert is_sent_folder("INBOX") is False
    assert is_sent_folder(None) is False
