"""Read-only tests for the mailbox source/intake inventory."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "qa" / "audit_mailbox_intake_inventory.py"

sys.path.insert(0, str(REPO / "src"))

from origenlab_email_pipeline.qa.mailbox_intake_inventory import (  # noqa: E402
    build_inventory,
    classify_intake_folder,
    classify_intake_lane,
    connect_read_only,
    inventory_from_path,
    sender_identity,
)

GMAIL = "gmail:contacto@origenlab.cl"
LEGACY_MBOX = "/data/mbox/contacto@labdelivery.cl/contacto@labdelivery.cl"
BACKUP_MBOX = "/data/mbox/backup4/Archivo de datos de Outlook"

# (source_file, folder, sender, date)
ROWS = [
    # Current identity — primary evidence lane.
    (f"{GMAIL}/INBOX", "INBOX", "cliente@universidad.cl", "2026-04-01T10:00:00Z"),
    (f"{GMAIL}/[Gmail]/Enviados", "[Gmail]/Enviados", "contacto@origenlab.cl", "2026-04-02T10:00:00Z"),
    # Current identity — the four non-primary classes.
    (f"{GMAIL}/[Gmail]/Todos", "[Gmail]/Todos", "proveedor@equipos.es", "2026-05-01T10:00:00Z"),
    (f"{GMAIL}/[Gmail]/Borradores", "[Gmail]/Borradores", "contacto@origenlab.cl", "2026-05-02T10:00:00Z"),
    (f"{GMAIL}/[Gmail]/Spam", "[Gmail]/Spam", "ruido@spam.example", "2026-05-03T10:00:00Z"),
    (f"{GMAIL}/[Gmail]/Papelera", "[Gmail]/Papelera", "otro@cliente.cl", "2026-05-04T10:00:00Z"),
    # Legacy identity writing into the current mailbox — cross-identity evidence.
    (f"{GMAIL}/INBOX", "INBOX", "contacto@labdelivery.cl", "2026-06-01T10:00:00Z"),
    # Legacy lanes.
    (f"{LEGACY_MBOX}/Bandeja de entrada/mbox", f"{LEGACY_MBOX}/Bandeja de entrada", "viejo@cliente.cl", "2018-01-01T10:00:00Z"),
    (f"{LEGACY_MBOX}/Elementos enviados/mbox", f"{LEGACY_MBOX}/Elementos enviados", "contacto@labdelivery.cl", "2018-01-02T10:00:00Z"),
    (f"{BACKUP_MBOX}/Bandeja de entrada/mbox", f"{BACKUP_MBOX}/Bandeja de entrada", "alguien@proveedor.cl", "2015-01-01T10:00:00Z"),
    # A spam subfolder hanging under an inbox must not count as inbox.
    (f"{BACKUP_MBOX}/Bandeja de entrada/McAfee Anti-Spam/mbox", f"{BACKUP_MBOX}/Bandeja de entrada/McAfee Anti-Spam", "x@spam.example", "2015-02-01T10:00:00Z"),
]


def _seed(path: Path, *, with_mart: bool = True) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE emails (
            id INTEGER PRIMARY KEY,
            source_file TEXT NOT NULL,
            folder TEXT,
            message_id TEXT,
            sender TEXT,
            date_iso TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO emails (source_file, folder, message_id, sender, date_iso) VALUES (?,?,?,?,?)",
        [(sf, fo, f"<m{i}@x>", f"Nombre Apellido <{se}>", d) for i, (sf, fo, se, d) in enumerate(ROWS)],
    )
    if with_mart:
        conn.executescript(
            """
            CREATE TABLE email_mart_features (
                email_id INTEGER PRIMARY KEY,
                source_file TEXT,
                folder TEXT,
                sender_email TEXT,
                mart_date_iso TEXT
            );
            """
        )
        conn.executemany(
            "INSERT INTO email_mart_features (email_id, source_file, folder, sender_email, mart_date_iso)"
            " VALUES (?,?,?,?,?)",
            [(i + 1, sf, fo, se, d) for i, (sf, fo, se, d) in enumerate(ROWS)],
        )
    conn.commit()
    conn.close()


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "emails.sqlite"
    _seed(path)
    return path


@pytest.mark.parametrize(
    ("folder", "expected"),
    [
        ("INBOX", "primary_evidence"),
        ("Bandeja de entrada", "primary_evidence"),
        ("[Gmail]/Enviados", "primary_evidence"),
        ("Elementos enviados", "primary_evidence"),
        ("[Gmail]/Todos", "archived"),
        ("All Mail", "archived"),
        ("[Gmail]/Borradores", "metadata_only"),
        ("Drafts", "metadata_only"),
        ("[Gmail]/Spam", "excluded_spam"),
        ("Correo no deseado", "excluded_spam"),
        ("[Gmail]/Papelera", "excluded_trash"),
        ("Elementos eliminados", "excluded_trash"),
        ("", "unclassified"),
    ],
)
def test_folder_classification(folder: str, expected: str) -> None:
    assert classify_intake_folder(folder) == expected


def test_spam_subfolder_under_inbox_is_spam_not_inbox() -> None:
    assert classify_intake_folder("Bandeja de entrada/McAfee Anti-Spam") == "excluded_spam"


def test_trash_and_sent_subfolder_precedence() -> None:
    assert classify_intake_folder("Bandeja de entrada/Elementos enviados") == "primary_evidence"
    assert classify_intake_folder("Bandeja de entrada/Elementos eliminados") == "excluded_trash"


@pytest.mark.parametrize(
    ("source_file", "expected"),
    [
        (f"{GMAIL}/INBOX", "current_origenlab"),
        (f"{LEGACY_MBOX}/Bandeja de entrada/mbox", "legacy_labdelivery"),
        (f"{BACKUP_MBOX}/Bandeja de entrada/mbox", "legacy_outlook_backup"),
        ("gmail:otra@cuenta.cl/INBOX", "other"),
        (None, "other"),
    ],
)
def test_lane_classification(source_file: str | None, expected: str) -> None:
    assert classify_intake_lane(source_file) == expected


@pytest.mark.parametrize(
    ("sender", "expected"),
    [
        ("contacto@origenlab.cl", "current"),
        ("Alguien <contacto@ORIGENLAB.cl>", "current"),
        ("contacto@labdelivery.cl", "legacy"),
        ("cliente@universidad.cl", "external"),
        ("", "unknown"),
        (None, "unknown"),
    ],
)
def test_sender_identity(sender: str | None, expected: str) -> None:
    assert sender_identity(sender) == expected


def test_inventory_groups_by_lane_and_class(db: Path) -> None:
    inv = inventory_from_path(db)
    assert inv.total_rows == len(ROWS)
    assert inv.lane_totals == {
        "current_origenlab": 7,
        "legacy_labdelivery": 2,
        "legacy_outlook_backup": 2,
    }
    assert inv.intake_class_totals == {
        "archived": 1,
        "excluded_spam": 2,
        "excluded_trash": 1,
        "metadata_only": 1,
        "primary_evidence": 6,
    }


def test_drafts_and_spam_and_trash_are_never_automatic_intake(db: Path) -> None:
    inv = inventory_from_path(db)
    for group in inv.groups:
        if group.intake_class in {"metadata_only", "excluded_spam", "excluded_trash"}:
            assert not group.eligible_for_automatic_intake
            assert not group.replay_candidate


def test_archived_is_a_replay_candidate_but_not_automatic_intake(db: Path) -> None:
    archived = [g for g in inventory_from_path(db).groups if g.intake_class == "archived"]
    assert len(archived) == 1
    assert archived[0].replay_candidate
    assert not archived[0].eligible_for_automatic_intake


def test_cross_identity_is_flagged_only_in_the_current_lane(db: Path) -> None:
    inv = inventory_from_path(db)
    assert inv.cross_identity_total == 1
    current_inbox = next(
        g for g in inv.groups
        if g.lane == "current_origenlab" and g.intake_class == "primary_evidence"
    )
    assert current_inbox.cross_identity == 1
    assert current_inbox.sent_by_legacy_identity == 1
    # The legacy lane's own sent mail is not "cross identity" — it is simply legacy.
    legacy_sent = next(
        g for g in inv.groups
        if g.lane == "legacy_labdelivery" and g.intake_class == "primary_evidence"
    )
    assert legacy_sent.cross_identity == 0
    assert legacy_sent.sent_by_legacy_identity == 1


def test_sent_received_split_and_date_span(db: Path) -> None:
    inv = inventory_from_path(db)
    current_primary = next(
        g for g in inv.groups
        if g.lane == "current_origenlab" and g.intake_class == "primary_evidence"
    )
    assert current_primary.rows == 3
    assert current_primary.sent_by_current_identity == 1
    assert current_primary.received == 1
    assert current_primary.first_date == "2026-04-01"
    assert current_primary.last_date == "2026-06-01"


def test_falls_back_to_emails_when_mart_is_missing(tmp_path: Path) -> None:
    path = tmp_path / "no_mart.sqlite"
    _seed(path, with_mart=False)
    inv = inventory_from_path(path)
    assert inv.total_rows == len(ROWS)
    # Header-form senders ("Nombre <a@b>") must still resolve to an identity.
    assert inv.cross_identity_total == 1


def test_connection_is_read_only(db: Path) -> None:
    conn = connect_read_only(db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO emails (source_file) VALUES ('x')")
    finally:
        conn.close()


def test_build_inventory_does_not_change_the_database(db: Path) -> None:
    before = db.stat().st_size, db.read_bytes()
    inventory_from_path(db)
    assert (db.stat().st_size, db.read_bytes()) == before


def test_script_output_is_json_and_leaks_no_addresses(db: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db)],
        capture_output=True,
        text=True,
        check=True,
    )
    report = json.loads(proc.stdout)
    assert report["read_only"] is True
    assert report["stable_snapshot"] is True
    assert report["total_rows"] == len(ROWS)
    assert report["automatic_intake_rows"] == 6
    assert report["replay_candidate_rows"] == 1
    assert report["cross_identity_total"] == 1
    # No address, domain or message-id from the fixture may reach the output.
    for secret in ("@", "universidad.cl", "labdelivery.cl", "origenlab.cl", "<m0@x>"):
        assert secret not in proc.stdout.replace(str(db), "")


def test_script_writes_optional_json_file(db: Path, tmp_path: Path) -> None:
    out = tmp_path / "nested" / "inventory.json"
    subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db), "--json-out", str(out)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(out.read_text())["total_rows"] == len(ROWS)
