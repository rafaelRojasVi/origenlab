"""Wave 1B September-campaign extraction: facts, fail-closed refusals, determinism.

Every database here is a disposable synthetic SQLite file built from the
repository's own schema SQL. Every address is on the reserved
``example.invalid`` domain — no fixture in this file may carry a real one.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from origenlab_email_pipeline.contact_domain_suppression import (
    CONTACT_DOMAIN_SUPPRESSION_SCHEMA_SQL,
)
from origenlab_email_pipeline.contact_email_suppression import (
    CONTACT_EMAIL_SUPPRESSION_SCHEMA_SQL,
)
from origenlab_email_pipeline.migration.wave1b_extract import (
    ATTEMPT_RESULTS,
    RECIPIENT_STATES,
    SEPTEMBER_CAMPAIGN_IDS,
    ExtractionRefused,
    ExtractionRequest,
    extract,
    redact_address,
)
from origenlab_email_pipeline.outbound_campaign_schema import OUTBOUND_CAMPAIGN_SCHEMA_SQL
from origenlab_email_pipeline.outreach_contact_state import OUTREACH_CONTACT_STATE_SCHEMA_SQL

SNAPSHOT = "2026-09-05T04:24:25Z"
RUN_AT = "2026-09-20T12:00:00Z"
BEFORE = "2026-09-01T00:00:00Z"
AFTER = "2026-09-12T00:00:00Z"

MAILBOX = "operator@example.invalid"
SENT_FOLDER = "[Gmail]/Enviados"

#: Deliberately sensitive-looking. If any of these strings reaches any byte of
#: any artifact, the free-text policy has failed.
SECRET_NOTE = "PRIVATE NOTE: Dr. Quiroga at +56 9 1111 2222 said the budget is confidential"
SECRET_REASON = "PRIVATE REASON: escalated after the lawyer letter from Fiscalia"
SECRET_EVIDENCE = "PRIVATE EVIDENCE: quoted thread with rival@competitor.invalid attached"
SECRET_SUPPRESSION_TEXT = "PRIVATE SUPPRESSION: personal request relayed by their assistant"
SECRET_ERROR_DETAIL = "PRIVATE ERROR: 550 mailbox unavailable for sent1@example.invalid"

SENSITIVE_STRINGS = (
    SECRET_NOTE,
    SECRET_REASON,
    SECRET_EVIDENCE,
    SECRET_SUPPRESSION_TEXT,
    SECRET_ERROR_DETAIL,
)


def _build_db(path: Path, *, mutate: str | None = None) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(OUTBOUND_CAMPAIGN_SCHEMA_SQL)
    conn.executescript(CONTACT_EMAIL_SUPPRESSION_SCHEMA_SQL)
    conn.executescript(CONTACT_DOMAIN_SUPPRESSION_SCHEMA_SQL)
    conn.executescript(OUTREACH_CONTACT_STATE_SCHEMA_SQL)

    for campaign_id, target in ((SEPTEMBER_CAMPAIGN_IDS[0], 2), (SEPTEMBER_CAMPAIGN_IDS[1], 3)):
        conn.execute(
            "INSERT INTO outbound_campaign (campaign_id, name, sender_email, sender_name,"
            " subject, target_attempt_count, baseline_attempt_count, status, created_at,"
            " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                campaign_id,
                f"name-{campaign_id}",
                "sender@example.invalid",
                "Sender",
                "a subject that must never leave the source database",
                target,
                0,
                "paused",
                BEFORE,
                AFTER,
            ),
        )

    # final: one sent, one candidate. wave2: one in_flight (unresolved), two candidates.
    recipients = [
        (1, SEPTEMBER_CAMPAIGN_IDS[0], "sent1@example.invalid", "sent", AFTER),
        (2, SEPTEMBER_CAMPAIGN_IDS[0], "cand1@example.invalid", "candidate", None),
        (3, SEPTEMBER_CAMPAIGN_IDS[1], "flight@example.invalid", "reserved", None),
        (4, SEPTEMBER_CAMPAIGN_IDS[1], "cand2@example.invalid", "candidate", None),
        (5, SEPTEMBER_CAMPAIGN_IDS[1], "cand3@example.invalid", "candidate", None),
    ]
    for rid, campaign_id, email, state, sent_at in recipients:
        conn.execute(
            "INSERT INTO outbound_campaign_recipient (id, campaign_id, email, email_norm,"
            " state, sent_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (rid, campaign_id, email, email, state, sent_at, BEFORE, AFTER),
        )

    attempts = [
        (1, SEPTEMBER_CAMPAIGN_IDS[0], 1, "sent1@example.invalid", "accepted", AFTER, None),
        (2, SEPTEMBER_CAMPAIGN_IDS[1], 3, "flight@example.invalid", "in_flight", AFTER, None),
    ]
    for aid, campaign_id, rid, email, result, attempted_at, resolved_at in attempts:
        conn.execute(
            "INSERT INTO outbound_send_attempt (id, campaign_id, recipient_id, email_norm,"
            " batch_id, attempt_seq, attempted_at, mode, result, resolved_at, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (aid, campaign_id, rid, email, "batch-1", 1, attempted_at, "live", result,
             resolved_at, attempted_at),
        )

    conn.execute(
        "INSERT INTO manual_contact_status (email_norm, status, updated_at)"
        " VALUES (?,?,?)",
        ("hold-new@example.invalid", "hold", AFTER),
    )
    conn.execute(
        "INSERT INTO manual_contact_status (email_norm, status, updated_at)"
        " VALUES (?,?,?)",
        ("hold-old@example.invalid", "hold", BEFORE),
    )
    conn.execute(
        "INSERT INTO contact_email_suppression (email, suppression_reason_code, updated_at)"
        " VALUES (?,?,?)",
        ("bounced-new@example.invalid", "bounce_no_such_user", AFTER),
    )
    conn.execute(
        "INSERT INTO contact_email_suppression (email, suppression_reason_code, updated_at)"
        " VALUES (?,?,?)",
        ("bounced-old@example.invalid", "bounce_other", BEFORE),
    )
    conn.execute(
        "INSERT INTO contact_domain_suppression (domain_norm, updated_at) VALUES (?,?)",
        ("new.example.invalid", AFTER),
    )
    conn.execute(
        "INSERT INTO contact_domain_suppression (domain_norm, updated_at) VALUES (?,?)",
        ("old.example.invalid", BEFORE),
    )
    conn.execute(
        "INSERT INTO outreach_contact_state (contact_email_norm, state, last_contacted_at,"
        " updated_at) VALUES (?,?,?,?)",
        ("touched-new@example.invalid", "contacted", AFTER, AFTER),
    )
    conn.execute(
        "INSERT INTO outreach_contact_state (contact_email_norm, state, last_contacted_at,"
        " updated_at) VALUES (?,?,?,?)",
        ("touched-old@example.invalid", "contacted", BEFORE, BEFORE),
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file TEXT NOT NULL,
            folder TEXT,
            message_id TEXT,
            subject TEXT,
            sender TEXT,
            recipients TEXT,
            date_raw TEXT,
            date_iso TEXT,
            body TEXT
        )
        """
    )

    # 1 = ordinary non-campaign mail sent after the snapshot: it belongs in the
    #     delta and the old campaign-only definition would have missed it.
    # 2 = the same kind of mail, but before the snapshot: it does not.
    # 3 = a campaign address also reached by hand: an overlap.
    # 4 = an outreach-state address also reached by hand: another overlap.
    sent_rows = [
        ("manualsent@example.invalid", "2026-09-12T09:15:00+00:00"),
        ("oldsent@example.invalid", "2026-08-01T09:15:00+00:00"),
        ("sent1@example.invalid", "2026-09-13T09:15:00+00:00"),
        ("touched-new@example.invalid", "2026-09-14T09:15:00+00:00"),
    ]
    for index, (address, date_iso) in enumerate(sent_rows, start=1):
        conn.execute(
            "INSERT INTO emails (source_file, folder, subject, sender, recipients,"
            " date_iso, body) VALUES (?,?,?,?,?,?,?)",
            (
                f"gmail:{MAILBOX}/{index}",
                SENT_FOLDER,
                "a subject that must never leave the source database",
                MAILBOX,
                address,
                date_iso,
                "a body that must never leave the source database",
            ),
        )

    # Unrestricted operator free text, on every column the policy names.
    conn.execute(
        "UPDATE outreach_contact_state SET notes = ? WHERE contact_email_norm = ?",
        (SECRET_NOTE, "touched-new@example.invalid"),
    )
    conn.execute(
        "UPDATE manual_contact_status SET reason = ?, evidence = ? WHERE email_norm = ?",
        (SECRET_REASON, SECRET_EVIDENCE, "hold-new@example.invalid"),
    )
    conn.execute(
        "UPDATE contact_email_suppression SET suppression_reason_text = ? WHERE email = ?",
        (SECRET_SUPPRESSION_TEXT, "bounced-new@example.invalid"),
    )
    conn.execute(
        "UPDATE contact_domain_suppression SET suppression_reason_text = ?"
        " WHERE domain_norm = ?",
        (SECRET_SUPPRESSION_TEXT, "new.example.invalid"),
    )
    conn.execute("UPDATE outbound_send_attempt SET error_detail = ? WHERE id = 1",
                 (SECRET_ERROR_DETAIL,))

    if mutate == "no_emails_table":
        conn.execute("DROP TABLE emails")
    if mutate == "undatable_sent":
        conn.execute(
            "UPDATE emails SET date_iso = NULL WHERE recipients = 'manualsent@example.invalid'"
        )
    if mutate == "unparsable_sent":
        conn.execute(
            "UPDATE emails SET date_iso = 'martes pasado'"
            " WHERE recipients = 'manualsent@example.invalid'"
        )
    if mutate == "wrong_sent_folder":
        conn.execute("UPDATE emails SET folder = '[Gmail]/Borradores'")
    if mutate == "orphan_attempt":
        conn.execute(
            "INSERT INTO outbound_send_attempt (id, campaign_id, recipient_id, email_norm,"
            " batch_id, attempt_seq, attempted_at, mode, result, created_at)"
            " VALUES (99, ?, 4242, 'ghost@example.invalid', 'b', 7, ?, 'live', 'accepted', ?)",
            (SEPTEMBER_CAMPAIGN_IDS[0], AFTER, AFTER),
        )
    if mutate == "unsupported_state":
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute(
            "UPDATE sqlite_master SET sql = replace(sql, "
            "\"CHECK (state IN ('candidate','selected','reserved','sent','blocked','bounced',"
            "'replied','inactive'))\", '') "
            "WHERE type='table' AND name='outbound_campaign_recipient'"
        )
        conn.commit()
        conn.close()
        conn = sqlite3.connect(path)
        conn.execute(
            "UPDATE outbound_campaign_recipient SET state='teleported' WHERE id=2"
        )
    if mutate == "missing_column":
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute(
            "UPDATE sqlite_master SET sql = replace(sql, 'bounce_state TEXT,', '') "
            "WHERE type='table' AND name='outbound_campaign_recipient'"
        )

    conn.commit()
    conn.close()
    return path


def _request(db: Path, out: Path, **overrides) -> ExtractionRequest:
    kwargs = {
        "sqlite_path": db,
        "output_dir": out,
        "campaign_ids": SEPTEMBER_CAMPAIGN_IDS,
        "wave1a_snapshot_at": SNAPSHOT,
        "operator_manifest": None,
        "run_timestamp": RUN_AT,
        "expected_remaining_candidates": None,
        "gmail_user": MAILBOX,
        "sent_folders": (SENT_FOLDER,),
    }
    kwargs.update(overrides)
    return ExtractionRequest(**kwargs)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_digests(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): _digest(p) for p in sorted(root.rglob("*")) if p.is_file()
    }


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --- happy path -------------------------------------------------------------


def test_extract_writes_the_expected_artifacts(tmp_path: Path) -> None:
    db = _build_db(tmp_path / "src.sqlite")
    result = extract(_request(db, tmp_path / "out"))

    assert result.archive.name.endswith(".tar.gz")
    assert result.sidecar.name.endswith(".tar.gz.sha256")
    assert result.archive_sha256 == _digest(result.archive)
    for rel in (
        "manifest.json",
        "SHA256SUMS",
        "README.md",
        "schema/source_schema.json",
        "exact/outbound_campaign.jsonl",
        "exact/outbound_campaign_recipient.jsonl",
        "exact/outbound_send_attempt.jsonl",
        "derived/remaining_candidates.jsonl",
        "derived/unresolved_in_flight_attempts.jsonl",
        "delta/contact_email_suppression.jsonl",
        "delta/contact_domain_suppression.jsonl",
        "delta/outreach_contact_state.jsonl",
        "delta/manual_contact_status.jsonl",
        "delta/campaign_prior_contact.jsonl",
        "delta/sent_history_prior_contact.jsonl",
        "delta/outreach_prior_contact.jsonl",
        "delta/combined_prior_contact.jsonl",
        "reports/reconciliation.json",
        "reports/sent_history.json",
        "reports/freshness.json",
    ):
        assert (result.bundle_dir / rel).is_file(), rel


def test_extract_measures_campaigns_recipients_and_attempts(tmp_path: Path) -> None:
    db = _build_db(tmp_path / "src.sqlite")
    result = extract(_request(db, tmp_path / "out"))
    assert result.counts["outbound_campaign"] == 2
    assert result.counts["outbound_campaign_recipient"] == 5
    assert result.counts["outbound_send_attempt"] == 2
    assert result.counts["remaining_candidates"] == 3
    assert result.counts["unresolved_in_flight_attempts"] == 1


def test_campaign_rows_carry_identity_and_lifecycle_but_never_the_subject(
    tmp_path: Path,
) -> None:
    db = _build_db(tmp_path / "src.sqlite")
    result = extract(_request(db, tmp_path / "out"))
    rows = _load_jsonl(result.bundle_dir / "exact/outbound_campaign.jsonl")

    assert {r["campaign_id"] for r in rows} == set(SEPTEMBER_CAMPAIGN_IDS)
    for row in rows:
        assert row["status"] == "paused"
        assert row["created_at"] == BEFORE
        assert "subject" not in row
        assert len(row["subject_sha256"]) == 64


def test_unresolved_in_flight_attempt_is_isolated(tmp_path: Path) -> None:
    db = _build_db(tmp_path / "src.sqlite")
    result = extract(_request(db, tmp_path / "out"))
    rows = _load_jsonl(result.bundle_dir / "derived/unresolved_in_flight_attempts.jsonl")
    assert len(rows) == 1
    assert rows[0]["result"] == "in_flight"
    assert rows[0]["resolved_at"] is None


def test_deltas_contain_only_rows_after_the_wave1a_snapshot(tmp_path: Path) -> None:
    db = _build_db(tmp_path / "src.sqlite")
    result = extract(_request(db, tmp_path / "out"))
    for rel, key in (
        ("delta/contact_email_suppression.jsonl", "email"),
        ("delta/contact_domain_suppression.jsonl", "domain_norm"),
        ("delta/outreach_contact_state.jsonl", "contact_email_norm"),
        ("delta/manual_contact_status.jsonl", "email_norm"),
    ):
        rows = _load_jsonl(result.bundle_dir / rel)
        assert len(rows) == 1, rel
        assert "new" in rows[0][key], rel


def test_campaign_prior_contact_captures_accepted_and_sent_after_the_snapshot(
    tmp_path: Path,
) -> None:
    db = _build_db(tmp_path / "src.sqlite")
    result = extract(_request(db, tmp_path / "out"))
    rows = _load_jsonl(result.bundle_dir / "delta/campaign_prior_contact.jsonl")
    by_address = {r["address"]: r for r in rows}
    assert "sent1@example.invalid" in by_address
    assert by_address["sent1@example.invalid"]["accepted_attempt_ids"] == [1]
    assert "flight@example.invalid" not in by_address


def test_expected_count_mismatch_is_a_warning_not_truth(tmp_path: Path) -> None:
    db = _build_db(tmp_path / "src.sqlite")
    result = extract(_request(db, tmp_path / "out", expected_remaining_candidates=279))
    assert result.counts["remaining_candidates"] == 3
    assert any("279" in w and "3" in w for w in result.warnings)


# --- fail closed ------------------------------------------------------------


def test_missing_campaign_is_refused(tmp_path: Path) -> None:
    db = _build_db(tmp_path / "src.sqlite")
    request = _request(db, tmp_path / "out", campaign_ids=(*SEPTEMBER_CAMPAIGN_IDS, "absent"))
    with pytest.raises(ExtractionRefused, match="campaign"):
        extract(request)


def test_duplicate_requested_campaign_id_is_refused(tmp_path: Path) -> None:
    db = _build_db(tmp_path / "src.sqlite")
    request = _request(db, tmp_path / "out", campaign_ids=(SEPTEMBER_CAMPAIGN_IDS[0],) * 2)
    with pytest.raises(ExtractionRefused, match="duplicate"):
        extract(request)


def test_unsupported_recipient_state_is_refused(tmp_path: Path) -> None:
    db = _build_db(tmp_path / "src.sqlite", mutate="unsupported_state")
    with pytest.raises(ExtractionRefused, match="state"):
        extract(_request(db, tmp_path / "out"))


def test_broken_foreign_reference_is_refused(tmp_path: Path) -> None:
    db = _build_db(tmp_path / "src.sqlite", mutate="orphan_attempt")
    with pytest.raises(ExtractionRefused, match="reference"):
        extract(_request(db, tmp_path / "out"))


def test_unexpected_schema_is_refused(tmp_path: Path) -> None:
    db = _build_db(tmp_path / "src.sqlite", mutate="missing_column")
    with pytest.raises(ExtractionRefused, match="schema"):
        extract(_request(db, tmp_path / "out"))


def test_output_directory_inside_the_repository_is_refused(tmp_path: Path) -> None:
    db = _build_db(tmp_path / "src.sqlite")
    inside = Path(__file__).resolve().parent / "wave1b_forbidden_output"
    with pytest.raises(Exception, match="Git working tree"):
        extract(_request(db, inside))
    assert not inside.exists()


def test_output_collision_is_refused(tmp_path: Path) -> None:
    db = _build_db(tmp_path / "src.sqlite")
    out = tmp_path / "out"
    first = extract(_request(db, out))
    with pytest.raises(Exception, match="already exists"):
        extract(_request(db, out))
    assert first.bundle_dir.is_file() is False and first.bundle_dir.is_dir()


def test_refusal_when_read_only_cannot_be_proven(tmp_path: Path, monkeypatch) -> None:
    db = _build_db(tmp_path / "src.sqlite")
    from origenlab_email_pipeline.migration import wave1b_extract as module

    def _writable(path, **_kwargs):
        return sqlite3.connect(path)

    monkeypatch.setattr(module, "open_source_readonly", _writable)
    with pytest.raises(Exception, match="query_only"):
        extract(_request(db, tmp_path / "out"))


# --- safety invariants ------------------------------------------------------


def test_the_source_database_is_byte_identical_after_extraction(tmp_path: Path) -> None:
    db = _build_db(tmp_path / "src.sqlite")
    before = _digest(db)
    before_stat = db.stat()
    extract(_request(db, tmp_path / "out"))
    assert _digest(db) == before
    assert db.stat().st_size == before_stat.st_size
    assert not Path(str(db) + "-wal").exists()


def test_a_neighbouring_wave1a_bundle_is_untouched(tmp_path: Path) -> None:
    wave1a = tmp_path / "20260905T042425Z_wave1a_v1_safety_bundle"
    (wave1a / "exact").mkdir(parents=True)
    (wave1a / "manifest.json").write_text('{"wave":"1a"}\n', encoding="utf-8")
    (wave1a / "exact" / "outbound_campaign.jsonl").write_text('{"campaign_id":"v1"}\n', "utf-8")
    before = _tree_digests(wave1a)

    db = _build_db(tmp_path / "src.sqlite")
    extract(_request(db, tmp_path / "out"))

    assert _tree_digests(wave1a) == before


def test_no_forbidden_field_appears_in_any_artifact(tmp_path: Path) -> None:
    db = _build_db(tmp_path / "src.sqlite")
    result = extract(_request(db, tmp_path / "out"))
    forbidden = ("subject", "body", "body_html", "snippet", "password", "token", "secret")
    for path in sorted(result.bundle_dir.rglob("*.jsonl")):
        for row in _load_jsonl(path):
            for key in row:
                assert key not in forbidden, f"{path.name}:{key}"
    blob = "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted(result.bundle_dir.rglob("*"))
        if p.is_file() and p.suffix in {".json", ".jsonl", ".md"}
    )
    assert "a subject that must never leave the source database" not in blob


def test_every_address_in_the_artifact_is_on_a_reserved_domain(tmp_path: Path) -> None:
    db = _build_db(tmp_path / "src.sqlite")
    result = extract(_request(db, tmp_path / "out"))
    import re

    pattern = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+")
    for path in sorted(result.bundle_dir.rglob("*")):
        if not path.is_file() or path.suffix not in {".json", ".jsonl"}:
            continue
        for address in pattern.findall(path.read_text(encoding="utf-8")):
            assert address.endswith("example.invalid"), f"{path.name}: unexpected domain"


def test_redact_address_never_leaks_the_original(tmp_path: Path) -> None:
    redacted = redact_address("someone@a-real-domain.example")
    assert "a-real-domain" not in redacted
    assert "someone" not in redacted
    assert redacted.endswith("@example.invalid")
    assert redacted == redact_address("someone@a-real-domain.example")


def test_console_summary_carries_no_address(tmp_path: Path) -> None:
    db = _build_db(tmp_path / "src.sqlite")
    result = extract(_request(db, tmp_path / "out"))
    text = "\n".join(result.console_lines)
    assert "@" not in text or all(
        part.endswith("example.invalid") for part in text.split() if "@" in part
    )
    assert "sent1@example.invalid" not in text


# --- determinism and idempotency -------------------------------------------


def test_two_runs_with_the_same_arguments_are_byte_identical(tmp_path: Path) -> None:
    db = _build_db(tmp_path / "src.sqlite")
    first = extract(_request(db, tmp_path / "out-a"))
    second = extract(_request(db, tmp_path / "out-b"))

    assert first.content_digest == second.content_digest
    assert first.archive_sha256 == second.archive_sha256
    assert _tree_digests(first.bundle_dir) == _tree_digests(second.bundle_dir)


def test_the_run_timestamp_does_not_move_the_content_digest(tmp_path: Path) -> None:
    db = _build_db(tmp_path / "src.sqlite")
    first = extract(_request(db, tmp_path / "out-a"))
    second = extract(_request(db, tmp_path / "out-b", run_timestamp="2026-09-21T09:30:00Z"))
    assert first.content_digest == second.content_digest


# --- declared vocabulary tracks the schema owner ----------------------------


def test_declared_state_vocabularies_match_the_schema_check_constraints() -> None:
    sql = OUTBOUND_CAMPAIGN_SCHEMA_SQL
    for value in RECIPIENT_STATES:
        assert f"'{value}'" in sql
    for value in ATTEMPT_RESULTS:
        assert f"'{value}'" in sql
    assert len(RECIPIENT_STATES) == 8
    assert len(ATTEMPT_RESULTS) == 4


# --- 1. sensitive free text never leaves the source database ----------------


def _every_artifact_byte(result) -> bytes:
    """Every byte of every artifact, including the compressed tarball."""
    import gzip

    blobs = [p.read_bytes() for p in sorted(result.bundle_dir.rglob("*")) if p.is_file()]
    blobs.append(result.archive.read_bytes())
    blobs.append(gzip.decompress(result.archive.read_bytes()))
    blobs.append(result.sidecar.read_bytes())
    blobs.append("\n".join(result.console_lines).encode("utf-8"))
    return b"\n".join(blobs)


def test_operator_free_text_never_appears_in_any_artifact_byte(tmp_path: Path) -> None:
    db = _build_db(tmp_path / "src.sqlite")
    result = extract(_request(db, tmp_path / "out"))
    blob = _every_artifact_byte(result)
    for secret in SENSITIVE_STRINGS:
        assert secret.encode("utf-8") not in blob, secret[:30]
        # Not even a distinctive fragment of it.
        assert secret.split(":")[1].strip()[:20].encode("utf-8") not in blob


def test_free_text_columns_are_replaced_by_presence_and_digest(tmp_path: Path) -> None:
    from origenlab_email_pipeline.migration.free_text import free_text_digest

    db = _build_db(tmp_path / "src.sqlite")
    result = extract(_request(db, tmp_path / "out"))

    outreach = _load_jsonl(result.bundle_dir / "delta/outreach_contact_state.jsonl")[0]
    assert "notes" not in outreach
    assert outreach["notes_present"] is True
    assert outreach["notes_sha256"] == free_text_digest(SECRET_NOTE)

    manual = _load_jsonl(result.bundle_dir / "delta/manual_contact_status.jsonl")[0]
    assert "reason" not in manual and "evidence" not in manual
    assert manual["reason_present"] is True
    assert manual["evidence_sha256"] == free_text_digest(SECRET_EVIDENCE)

    attempt = next(
        r
        for r in _load_jsonl(result.bundle_dir / "exact/outbound_send_attempt.jsonl")
        if r["id"] == 1
    )
    assert "error_detail" not in attempt
    assert attempt["error_detail_sha256"] == free_text_digest(SECRET_ERROR_DETAIL)


def test_absent_free_text_records_presence_false_and_no_digest(tmp_path: Path) -> None:
    db = _build_db(tmp_path / "src.sqlite")
    result = extract(_request(db, tmp_path / "out"))
    domain = _load_jsonl(result.bundle_dir / "delta/contact_domain_suppression.jsonl")[0]
    assert domain["suppression_reason_text_present"] is True

    attempt = next(
        r
        for r in _load_jsonl(result.bundle_dir / "exact/outbound_send_attempt.jsonl")
        if r["id"] == 2
    )
    assert attempt["error_detail_present"] is False
    assert "error_detail_sha256" not in attempt


def test_structured_reason_codes_survive_but_unknown_values_are_digested(
    tmp_path: Path,
) -> None:
    db = _build_db(tmp_path / "src.sqlite")
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE contact_email_suppression SET suppression_reason_code = ? WHERE email = ?",
        ("bounce_no_such_user", "bounced-new@example.invalid"),
    )
    conn.execute(
        "UPDATE outbound_campaign_recipient SET block_reason = ? WHERE id = 2",
        ("free-form operator musing about Dr. Quiroga",),
    )
    conn.commit()
    conn.close()

    result = extract(_request(db, tmp_path / "out"))
    suppression = _load_jsonl(result.bundle_dir / "delta/contact_email_suppression.jsonl")[0]
    assert suppression["suppression_reason_code"] == "bounce_no_such_user"

    recipient = next(
        r
        for r in _load_jsonl(result.bundle_dir / "exact/outbound_campaign_recipient.jsonl")
        if r["id"] == 2
    )
    assert recipient["block_reason_present"] is True
    assert "Quiroga" not in json.dumps(recipient)


def test_the_free_text_policy_is_documented_in_the_bundle(tmp_path: Path) -> None:
    db = _build_db(tmp_path / "src.sqlite")
    result = extract(_request(db, tmp_path / "out"))
    policy = json.loads(
        (result.bundle_dir / "reports/reconciliation.json").read_text(encoding="utf-8")
    )["free_text_policy"]
    assert "notes" in policy["free_text_columns"]["outreach_contact_state"]
    assert "CANNOT reconstruct" in policy["digest_semantics"]
    readme = (result.bundle_dir / "README.md").read_text(encoding="utf-8")
    assert "cannot reconstruct the content" in readme


# --- 2. the combined prior-contact definition -------------------------------


def _combined(result) -> dict[str, dict]:
    rows = _load_jsonl(result.bundle_dir / "delta/combined_prior_contact.jsonl")
    return {row["address"]: row for row in rows}


def test_ordinary_post_snapshot_sent_mail_enters_the_delta(tmp_path: Path) -> None:
    """The old campaign-only definition missed exactly this address."""
    db = _build_db(tmp_path / "src.sqlite")
    result = extract(_request(db, tmp_path / "out"))
    combined = _combined(result)

    assert "manualsent@example.invalid" in combined
    assert combined["manualsent@example.invalid"]["source_categories"] == ["sent_history"]

    campaign_only = {
        r["address"]
        for r in _load_jsonl(result.bundle_dir / "delta/campaign_prior_contact.jsonl")
    }
    assert "manualsent@example.invalid" not in campaign_only


def test_pre_snapshot_sent_evidence_does_not_enter_the_delta(tmp_path: Path) -> None:
    db = _build_db(tmp_path / "src.sqlite")
    result = extract(_request(db, tmp_path / "out"))
    assert "oldsent@example.invalid" not in _combined(result)


def test_duplicates_across_sources_collapse_but_keep_their_categories(
    tmp_path: Path,
) -> None:
    db = _build_db(tmp_path / "src.sqlite")
    result = extract(_request(db, tmp_path / "out"))
    combined = _combined(result)

    assert combined["sent1@example.invalid"]["source_categories"] == [
        "campaign_accepted",
        "sent_history",
    ]
    assert combined["sent1@example.invalid"]["multiple_sources"] is True
    assert combined["touched-new@example.invalid"]["source_categories"] == [
        "outreach_state",
        "sent_history",
    ]

    addresses = [row["address"] for row in _load_jsonl(
        result.bundle_dir / "delta/combined_prior_contact.jsonl"
    )]
    assert len(addresses) == len(set(addresses))


def test_reconciliation_reports_raw_totals_overlaps_and_the_deduplicated_total(
    tmp_path: Path,
) -> None:
    db = _build_db(tmp_path / "src.sqlite")
    result = extract(_request(db, tmp_path / "out"))
    block = json.loads(
        (result.bundle_dir / "reports/reconciliation.json").read_text(encoding="utf-8")
    )["combined_prior_contact"]

    raw = block["raw_by_source"]
    assert raw["campaign_accepted"] == 1
    assert raw["sent_history"] == 3
    assert raw["outreach_state"] == 1
    assert block["raw_total_before_dedup"] == 5
    assert block["overlaps"]["campaign_accepted+sent_history"] == 1
    assert block["overlaps"]["sent_history+outreach_state"] == 1
    assert block["overlaps"]["all_three"] == 0
    assert block["deduplicated_total"] == 3
    assert block["deduplicated_total"] == result.counts["delta_combined_prior_contact"]
    assert block["complete"] is True


@pytest.mark.parametrize(
    "mutation, fragment",
    [
        ("no_emails_table", "emails"),
        ("undatable_sent", "date_iso"),
        ("unparsable_sent", "date_iso"),
        ("wrong_sent_folder", "preflight"),
    ],
)
def test_missing_or_unusable_sent_history_refuses_the_run(
    tmp_path: Path, mutation: str, fragment: str
) -> None:
    db = _build_db(tmp_path / "src.sqlite", mutate=mutation)
    with pytest.raises(ExtractionRefused, match=fragment):
        extract(_request(db, tmp_path / "out"))


def test_a_mailbox_with_no_ingested_sent_row_refuses_the_run(tmp_path: Path) -> None:
    db = _build_db(tmp_path / "src.sqlite")
    with pytest.raises(ExtractionRefused, match="preflight"):
        extract(_request(db, tmp_path / "out", gmail_user="nobody@example.invalid"))


def test_a_stale_sent_ingest_is_reported_as_an_incomplete_baseline(
    tmp_path: Path,
) -> None:
    db = _build_db(tmp_path / "src.sqlite")
    conn = sqlite3.connect(db)
    # Campaign activity in October, Sent ingest still stuck in September.
    conn.execute("UPDATE outbound_send_attempt SET attempted_at = '2026-10-01T00:00:00Z'")
    conn.commit()
    conn.close()

    result = extract(_request(db, tmp_path / "out"))
    assert any("NOT complete" in w for w in result.warnings)
    block = json.loads(
        (result.bundle_dir / "reports/reconciliation.json").read_text(encoding="utf-8")
    )["combined_prior_contact"]
    assert block["complete"] is False
    assert block["incomplete_reasons"]


def test_the_sent_history_report_records_the_mailbox_and_reused_functions(
    tmp_path: Path,
) -> None:
    db = _build_db(tmp_path / "src.sqlite")
    result = extract(_request(db, tmp_path / "out"))
    report = json.loads(
        (result.bundle_dir / "reports/sent_history.json").read_text(encoding="utf-8")
    )
    assert report["mailbox"]["gmail_user"] == MAILBOX
    assert report["mailbox"]["sent_folders"] == [SENT_FOLDER]
    assert report["gmail_network_calls"] == 0
    assert (
        "marketing_export_context.sent_history_where"
        in report["canonical_implementation_reused"]
    )
    assert "password" not in json.dumps(report).lower()


def test_no_subject_body_or_raw_recipient_payload_reaches_the_sent_delta(
    tmp_path: Path,
) -> None:
    db = _build_db(tmp_path / "src.sqlite")
    result = extract(_request(db, tmp_path / "out"))
    rows = _load_jsonl(result.bundle_dir / "delta/sent_history_prior_contact.jsonl")
    assert rows
    for row in rows:
        assert set(row) == {
            "address",
            "sent_message_count",
            "first_sent_at",
            "last_sent_at",
        }
    blob = _every_artifact_byte(result)
    assert b"a body that must never leave the source database" not in blob
    assert b"a subject that must never leave the source database" not in blob


def test_the_sent_history_extract_shares_the_live_gate_predicate() -> None:
    """The extractor must not own a second copy of the Sent-row predicate."""
    from origenlab_email_pipeline.marketing_export_context import sent_history_where
    from origenlab_email_pipeline.migration import sent_history as module

    assert module.sent_history_where is sent_history_where
    from origenlab_email_pipeline.business_mart import emails_in

    assert module.emails_in is emails_in


# --- 3. private filesystem permissions --------------------------------------


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_every_written_artifact_is_owner_only(tmp_path: Path) -> None:
    out = tmp_path / "out"
    db = _build_db(tmp_path / "src.sqlite")
    result = extract(_request(db, out))

    assert _mode(out) == 0o700
    assert _mode(result.bundle_dir) == 0o700
    for path in sorted(result.bundle_dir.rglob("*")):
        expected = 0o700 if path.is_dir() else 0o600
        assert _mode(path) == expected, f"{path.name}: {oct(_mode(path))}"
    assert _mode(result.archive) == 0o600
    assert _mode(result.sidecar) == 0o600


def test_no_temporary_file_survives_and_none_was_permissive(tmp_path: Path) -> None:
    out = tmp_path / "out"
    db = _build_db(tmp_path / "src.sqlite")
    result = extract(_request(db, out))
    leftovers = [p for p in out.rglob(".*.tmp") ]
    assert leftovers == []
    assert all(_mode(p) == 0o600 for p in out.rglob("*") if p.is_file())
    del result


def test_tar_members_are_private_and_deterministic(tmp_path: Path) -> None:
    import tarfile

    db = _build_db(tmp_path / "src.sqlite")
    result = extract(_request(db, tmp_path / "out"))
    with tarfile.open(result.archive, "r:gz") as tar:
        members = tar.getmembers()
    assert members
    for member in members:
        assert member.mode == 0o600, member.name
        assert member.uid == 0 and member.gid == 0
        assert member.uname == "" and member.gname == ""


def test_an_existing_owner_owned_output_root_is_tightened_not_widened(
    tmp_path: Path,
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    out.chmod(0o755)
    neighbour = out / "unrelated"
    neighbour.mkdir()
    neighbour.chmod(0o755)
    (neighbour / "keep.txt").write_text("not ours\n", encoding="utf-8")

    db = _build_db(tmp_path / "src.sqlite")
    extract(_request(db, out))

    assert _mode(out) == 0o700
    # Existing content is never recursively chmodded.
    assert _mode(neighbour) == 0o755


def test_a_group_or_world_writable_output_root_is_refused(tmp_path: Path) -> None:
    from origenlab_email_pipeline.migration.bundle import OutputPathError

    out = tmp_path / "out"
    out.mkdir()
    out.chmod(0o777)  # mkdir's mode is masked by the umask; set it outright.
    db = _build_db(tmp_path / "src.sqlite")
    with pytest.raises(OutputPathError, match="writable"):
        extract(_request(db, out))
    assert not list(out.glob("*wave1b*"))


def test_a_symlinked_output_root_is_refused(tmp_path: Path) -> None:
    from origenlab_email_pipeline.migration.bundle import OutputPathError

    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    db = _build_db(tmp_path / "src.sqlite")
    with pytest.raises(OutputPathError, match="symbolic link"):
        extract(_request(db, link))
    assert list(real.iterdir()) == []


def test_an_output_root_that_is_a_file_is_refused(tmp_path: Path) -> None:
    from origenlab_email_pipeline.migration.bundle import OutputPathError

    target = tmp_path / "out"
    target.write_text("not a directory\n", encoding="utf-8")
    db = _build_db(tmp_path / "src.sqlite")
    with pytest.raises(OutputPathError, match="not a directory"):
        extract(_request(db, target))


def test_the_manifest_records_the_permission_policy(tmp_path: Path) -> None:
    db = _build_db(tmp_path / "src.sqlite")
    result = extract(_request(db, tmp_path / "out"))
    permissions = json.loads(
        (result.bundle_dir / "manifest.json").read_text(encoding="utf-8")
    )["permissions"]
    assert permissions["file_mode"] == "0o600"
    assert permissions["bundle_dir_mode"] == "0o700"
    assert "atomic rename" in permissions["write_strategy"]
