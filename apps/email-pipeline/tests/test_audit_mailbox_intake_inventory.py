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

from origenlab_email_pipeline.business_filter_rules import INTERNAL_DOMAINS  # noqa: E402
from origenlab_email_pipeline.contacto_gmail_source import (  # noqa: E402
    CONTACTO_GMAIL_SOURCE_PREFIX,
    LEGACY_LABDELIVERY_SOURCE_LIKE,
)
from origenlab_email_pipeline.qa.mailbox_intake_inventory import (  # noqa: E402
    IdentityRules,
    build_inventory,
    classify_intake_folder,
    classify_intake_lane,
    connect_read_only,
    default_identity_rules,
    inventory_from_path,
    sender_identity,
)

# Fixture identities are fictional. `.test` and `.invalid` are reserved by RFC 2606 and
# resolve nowhere, so no fixture in this file can name a real mailbox by accident.
RULES = IdentityRules(
    current_domains=("current.test",),
    legacy_domains=("former.test",),
)
OURS = "buzon@current.test"
FORMER = "buzon@former.test"
CLIENT = "compras@instituto.test"
SUPPLIER = "ventas@proveedor.test"
NOISE = "ruido@remitente.invalid"

# Source-file prefixes come from the modules that own them, never spelled out here: a test
# that copies an address is a test that keeps working after that address stops being real.
GMAIL = CONTACTO_GMAIL_SOURCE_PREFIX.rstrip("/")
_LEGACY_MAILBOX = LEGACY_LABDELIVERY_SOURCE_LIKE.strip("%")
LEGACY_MBOX = f"/data/mbox/{_LEGACY_MAILBOX}/{_LEGACY_MAILBOX}"
BACKUP_MBOX = "/data/mbox/backup4/Archivo de datos de Outlook"

# (source_file, folder, sender, date)
ROWS = [
    # Current identity — primary evidence lane.
    (f"{GMAIL}/INBOX", "INBOX", CLIENT, "2026-04-01T10:00:00Z"),
    (f"{GMAIL}/[Gmail]/Enviados", "[Gmail]/Enviados", OURS, "2026-04-02T10:00:00Z"),
    # Current identity — the four non-primary classes.
    (f"{GMAIL}/[Gmail]/Todos", "[Gmail]/Todos", SUPPLIER, "2026-05-01T10:00:00Z"),
    (f"{GMAIL}/[Gmail]/Borradores", "[Gmail]/Borradores", OURS, "2026-05-02T10:00:00Z"),
    (f"{GMAIL}/[Gmail]/Spam", "[Gmail]/Spam", NOISE, "2026-05-03T10:00:00Z"),
    (f"{GMAIL}/[Gmail]/Papelera", "[Gmail]/Papelera", CLIENT, "2026-05-04T10:00:00Z"),
    # Former identity writing into the current mailbox — cross-identity evidence.
    (f"{GMAIL}/INBOX", "INBOX", FORMER, "2026-06-01T10:00:00Z"),
    # Legacy lanes.
    (f"{LEGACY_MBOX}/Bandeja de entrada/mbox", f"{LEGACY_MBOX}/Bandeja de entrada", CLIENT, "2018-01-01T10:00:00Z"),
    (f"{LEGACY_MBOX}/Elementos enviados/mbox", f"{LEGACY_MBOX}/Elementos enviados", FORMER, "2018-01-02T10:00:00Z"),
    (f"{BACKUP_MBOX}/Bandeja de entrada/mbox", f"{BACKUP_MBOX}/Bandeja de entrada", SUPPLIER, "2015-01-01T10:00:00Z"),
    # A spam subfolder hanging under an inbox must not count as inbox.
    (f"{BACKUP_MBOX}/Bandeja de entrada/McAfee Anti-Spam/mbox", f"{BACKUP_MBOX}/Bandeja de entrada/McAfee Anti-Spam", NOISE, "2015-02-01T10:00:00Z"),
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
        (OURS, "current"),
        ("Alguien <BUZON@CURRENT.TEST>", "current"),
        (FORMER, "legacy"),
        (CLIENT, "external"),
        ("", "unknown"),
        (None, "unknown"),
    ],
)
def test_sender_identity(sender: str | None, expected: str) -> None:
    assert sender_identity(sender, rules=RULES) == expected


def test_default_identity_rules_partition_the_canonical_domain_list() -> None:
    """The production vocabulary is derived, not restated — and it is a real partition."""
    rules = default_identity_rules()
    canonical = {d.strip().lower() for d in INTERNAL_DOMAINS if d and d.strip()}
    assert set(rules.current_domains) | set(rules.legacy_domains) == canonical
    assert not set(rules.current_domains) & set(rules.legacy_domains)
    assert rules.current_domains and rules.legacy_domains


def test_identity_rules_refuse_a_domain_that_is_both_identities() -> None:
    with pytest.raises(ValueError, match="both the current and the former"):
        IdentityRules(current_domains=("shared.test",), legacy_domains=("shared.test",))


def test_inventory_groups_by_lane_and_class(db: Path) -> None:
    inv = inventory_from_path(db, rules=RULES)
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
    inv = inventory_from_path(db, rules=RULES)
    for group in inv.groups:
        if group.intake_class in {"metadata_only", "excluded_spam", "excluded_trash"}:
            assert not group.eligible_for_automatic_intake
            assert not group.replay_candidate


def test_archived_is_a_replay_candidate_but_not_automatic_intake(db: Path) -> None:
    archived = [g for g in inventory_from_path(db, rules=RULES).groups if g.intake_class == "archived"]
    assert len(archived) == 1
    assert archived[0].replay_candidate
    assert not archived[0].eligible_for_automatic_intake


def test_cross_identity_is_flagged_only_in_the_current_lane(db: Path) -> None:
    inv = inventory_from_path(db, rules=RULES)
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
    inv = inventory_from_path(db, rules=RULES)
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
    inv = inventory_from_path(path, rules=RULES)
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
    inventory_from_path(db, rules=RULES)
    assert (db.stat().st_size, db.read_bytes()) == before


def test_script_output_is_json_and_leaks_no_addresses(db: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db),
         "--current-domain", RULES.current_domains[0],
         "--legacy-domain", RULES.legacy_domains[0]],
        capture_output=True,
        text=True,
        check=True,
    )
    report = json.loads(proc.stdout)
    assert report["identity_vocabulary"] == "explicit"
    assert report["read_only"] is True
    assert report["stable_snapshot"] is True
    assert report["total_rows"] == len(ROWS)
    assert report["automatic_intake_rows"] == 6
    assert report["replay_candidate_rows"] == 1
    assert report["cross_identity_total"] == 1
    # No address, domain or message-id from the fixture may reach the output.
    body = proc.stdout.replace(str(db), "")
    for secret in ("@", "current.test", "former.test", "instituto.test", "<m0@x>"):
        assert secret not in body, f"the audit leaked {secret!r}"


def test_script_writes_optional_json_file(db: Path, tmp_path: Path) -> None:
    out = tmp_path / "nested" / "inventory.json"
    subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db), "--json-out", str(out)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(out.read_text())["total_rows"] == len(ROWS)


def test_script_defaults_to_the_repository_identity_vocabulary(db: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db)],
        capture_output=True, text=True, check=True,
    )
    report = json.loads(proc.stdout)
    assert report["identity_vocabulary"] == "repository_default"
    assert report["identity_domains_current"] >= 1
    assert report["identity_domains_legacy"] >= 1
    # Fixture senders are fictional, so under the real vocabulary none of them is us.
    assert report["cross_identity_total"] == 0


def test_script_refuses_half_an_identity_vocabulary(db: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db), "--current-domain", "only.test"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 2
    assert "half a vocabulary" in proc.stderr


def test_script_refuses_a_domain_claimed_by_both_identities(db: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db),
         "--current-domain", "shared.test", "--legacy-domain", "shared.test"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 2
    assert "both the current and the former" in proc.stderr
