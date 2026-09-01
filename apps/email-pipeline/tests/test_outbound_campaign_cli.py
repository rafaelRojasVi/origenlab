"""Tests for the outbound campaign operator CLI. No real Gmail sends anywhere."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_CLI_PATH = Path(__file__).resolve().parents[1] / "scripts" / "campaigns" / "outbound_campaign_cli.py"
_spec = importlib.util.spec_from_file_location("outbound_campaign_cli", _CLI_PATH)
outbound_campaign_cli = importlib.util.module_from_spec(_spec)
sys.modules["outbound_campaign_cli"] = outbound_campaign_cli
_spec.loader.exec_module(outbound_campaign_cli)  # type: ignore[union-attr]


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "t.sqlite"


def _run(db_path: Path, *args: str, capsys) -> tuple[int, str]:
    code = outbound_campaign_cli.main([*args, "--db", str(db_path)])
    out = capsys.readouterr().out
    return code, out


def test_init_creates_hielscher_campaign(db_path: Path, capsys) -> None:
    code, out = _run(
        db_path, "init",
        "--campaign-id", "hielscher-sonicators-2026",
        "--name", "Hielscher Sonicadores Laboratorio",
        "--sender-email", "contacto@origenlab.cl",
        "--sender-name", "Tatiana Vivanco | OrigenLab",
        "--subject", "Sonicadores Hielscher para laboratorio | OrigenLab",
        "--target", "2000", "--baseline", "874",
        capsys=capsys,
    )
    assert code == 0
    code2, status_out = _run(db_path, "status", "--campaign-id", "hielscher-sonicators-2026", capsys=capsys)
    assert code2 == 0
    payload = json.loads(status_out)
    assert payload["target"] == 2000
    assert payload["baseline"] == 874
    assert payload["remaining"] == 1126


def test_duplicate_init_fails_cleanly(db_path: Path, capsys) -> None:
    args = (
        "init", "--campaign-id", "hielscher-sonicators-2026", "--name", "N",
        "--sender-email", "s@x.cl", "--sender-name", "S", "--subject", "Subj",
        "--target", "2000", "--baseline", "874",
    )
    code1, _ = _run(db_path, *args, capsys=capsys)
    assert code1 == 0
    code2, _ = _run(db_path, *args, capsys=capsys)
    assert code2 == 1


def test_contact_status_set_and_show_roundtrip(db_path: Path, capsys) -> None:
    _run(db_path, "contact-status", "set", "--email", "carolinalobo@pharmaisa.cl", "--status", "inactive",
         "--reason", "No longer works at Pharma Isa", "--effective-at", "2026-04-06", capsys=capsys)
    code, out = _run(db_path, "contact-status", "show", "--email", "carolinalobo@pharmaisa.cl", capsys=capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["status"] == "inactive"


def test_candidates_add_and_select_blocks_carolina(db_path: Path, capsys) -> None:
    _run(
        db_path, "init", "--campaign-id", "hielscher-sonicators-2026", "--name", "N",
        "--sender-email", "contacto@origenlab.cl", "--sender-name", "S",
        "--subject", "Subj", "--target", "2000", "--baseline", "874", capsys=capsys,
    )
    _run(db_path, "contact-status", "set", "--email", "carolinalobo@pharmaisa.cl", "--status", "inactive", capsys=capsys)
    _run(
        db_path, "candidates", "add", "--campaign-id", "hielscher-sonicators-2026",
        "--email", "carolinalobo@pharmaisa.cl", "--email", "good@x.cl", capsys=capsys,
    )
    code, out = _run(
        db_path, "select", "--campaign-id", "hielscher-sonicators-2026", "--n", "10",
        "--gmail-user", "contacto@origenlab.cl", capsys=capsys,
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["reserved"] == 1
    assert payload["blocked"] == 1

    code2, batch_out = _run(db_path, "batch", "show", "--campaign-id", "hielscher-sonicators-2026", capsys=capsys)
    batch = json.loads(batch_out)
    assert [r["email_norm"] for r in batch] == ["good@x.cl"]


def test_send_dry_run_end_to_end(db_path: Path, tmp_path: Path, capsys) -> None:
    _run(
        db_path, "init", "--campaign-id", "hielscher-sonicators-2026", "--name", "N",
        "--sender-email", "contacto@origenlab.cl", "--sender-name", "S",
        "--subject", "Subj", "--target", "2000", "--baseline", "874", capsys=capsys,
    )
    _run(db_path, "candidates", "add", "--campaign-id", "hielscher-sonicators-2026", "--email", "good@x.cl", capsys=capsys)
    _run(db_path, "select", "--campaign-id", "hielscher-sonicators-2026", "--n", "10", "--gmail-user", "contacto@origenlab.cl", capsys=capsys)

    html_file = tmp_path / "campaign.html"
    html_file.write_text("<html><body>Hola</body></html>", encoding="utf-8")

    with patch("outbound_campaign_cli.send_campaign_batch") as mock_send:
        from origenlab_email_pipeline.outbound_campaign_sender import SendOutcome
        mock_send.return_value = [SendOutcome(recipient_id=1, email="good@x.cl", mode="dry_run", result="accepted", gmail_message_id=None, error=None)]
        code, out = _run(db_path, "send", "--campaign-id", "hielscher-sonicators-2026", "--html", str(html_file), capsys=capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["mode"] == "dry_run"
    assert payload["accepted"] == 1
    mock_send.assert_called_once()
    _, kwargs = mock_send.call_args
    assert kwargs["live"] is False


def test_send_no_longer_accepts_out_flag(db_path: Path, tmp_path: Path, capsys) -> None:
    """--out was removed from `send` -- it never wrote anything and duplicated `export`."""
    html_file = tmp_path / "campaign.html"
    html_file.write_text("<html></html>", encoding="utf-8")
    with pytest.raises(SystemExit):
        outbound_campaign_cli.main([
            "send", "--campaign-id", "hielscher-sonicators-2026", "--html", str(html_file),
            "--out", "/tmp/whatever.csv", "--db", str(db_path),
        ])


def test_export_respects_an_explicit_downloads_style_path(db_path: Path, tmp_path: Path, capsys) -> None:
    """Explicit operator exports may target Downloads -- that's what it's for. Only
    default/implicit writes must avoid it, and export is never implicit."""
    _run(
        db_path, "init", "--campaign-id", "hielscher-sonicators-2026", "--name", "N",
        "--sender-email", "contacto@origenlab.cl", "--sender-name", "S",
        "--subject", "Subj", "--target", "2000", "--baseline", "874", capsys=capsys,
    )
    _run(db_path, "candidates", "add", "--campaign-id", "hielscher-sonicators-2026", "--email", "good@x.cl", capsys=capsys)
    fake_downloads = tmp_path / "Downloads" / "batch.csv"
    code, out = _run(
        db_path, "export", "--campaign-id", "hielscher-sonicators-2026", "--out", str(fake_downloads), capsys=capsys,
    )
    assert code == 0
    assert fake_downloads.is_file()
    content = fake_downloads.read_text(encoding="utf-8")
    assert "good@x.cl" in content


def test_export_json_format_from_extension(db_path: Path, tmp_path: Path, capsys) -> None:
    _run(
        db_path, "init", "--campaign-id", "hielscher-sonicators-2026", "--name", "N",
        "--sender-email", "contacto@origenlab.cl", "--sender-name", "S",
        "--subject", "Subj", "--target", "2000", "--baseline", "874", capsys=capsys,
    )
    _run(db_path, "candidates", "add", "--campaign-id", "hielscher-sonicators-2026", "--email", "good@x.cl", capsys=capsys)
    out_json = tmp_path / "export.json"
    code, _ = _run(db_path, "export", "--campaign-id", "hielscher-sonicators-2026", "--out", str(out_json), capsys=capsys)
    assert code == 0
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload[0]["email_norm"] == "good@x.cl"


def test_no_command_writes_a_batch_artifact_by_default(db_path: Path, tmp_path: Path, capsys) -> None:
    """init/candidates/select/send(dry-run)/reconcile never write any file besides the DB."""
    html_file = tmp_path / "campaign.html"
    html_file.write_text("<html></html>", encoding="utf-8")
    files_before = {p for p in tmp_path.rglob("*") if p.is_file()}

    _run(
        db_path, "init", "--campaign-id", "hielscher-sonicators-2026", "--name", "N",
        "--sender-email", "contacto@origenlab.cl", "--sender-name", "S",
        "--subject", "Subj", "--target", "2000", "--baseline", "874", capsys=capsys,
    )
    _run(db_path, "candidates", "add", "--campaign-id", "hielscher-sonicators-2026", "--email", "good@x.cl", capsys=capsys)
    _run(db_path, "select", "--campaign-id", "hielscher-sonicators-2026", "--n", "10", "--gmail-user", "contacto@origenlab.cl", capsys=capsys)
    with patch("outbound_campaign_cli.send_campaign_batch") as mock_send:
        from origenlab_email_pipeline.outbound_campaign_sender import SendOutcome
        mock_send.return_value = [SendOutcome(recipient_id=1, email="good@x.cl", mode="dry_run", result="accepted", gmail_message_id=None, error=None)]
        _run(db_path, "send", "--campaign-id", "hielscher-sonicators-2026", "--html", str(html_file), capsys=capsys)
    _run(db_path, "reconcile", "--campaign-id", "hielscher-sonicators-2026", "--gmail-user", "contacto@origenlab.cl", capsys=capsys)

    files_after = {p for p in tmp_path.rglob("*") if p.is_file()}
    # Only the SQLite DB (and its WAL/SHM siblings) may have appeared; no batch CSV/JSON.
    new_files = files_after - files_before
    unexpected = {p for p in new_files if p.suffix.lower() not in (".sqlite", "", "-wal", "-shm") and "sqlite" not in p.name}
    assert unexpected == set(), f"unexpected new files: {unexpected}"


def test_select_uses_strict_gate_blocking_supplier_domain_even_when_manually_added(db_path: Path, tmp_path: Path, capsys) -> None:
    """Regression for the vendor/noise-selection hardening point: a manually-added
    candidate on a known supplier domain (e.g. a Kalstein-style lab equipment vendor)
    must never be reserved, using the existing canonical supplier_master filter --
    no ad-hoc domain list is added anywhere in this CLI."""
    from origenlab_email_pipeline.db import connect as real_connect, init_schema
    from origenlab_email_pipeline.supplier_schema import ensure_supplier_tables

    conn = real_connect(db_path)
    init_schema(conn)
    ensure_supplier_tables(conn)
    conn.execute(
        "INSERT INTO supplier_master (domain_norm, trade_name, created_at, updated_at) "
        "VALUES ('kalstein.cl', 'Kalstein', 't', 't')"
    )
    conn.commit()
    conn.close()

    _run(
        db_path, "init", "--campaign-id", "hielscher-sonicators-2026", "--name", "N",
        "--sender-email", "contacto@origenlab.cl", "--sender-name", "S",
        "--subject", "Subj", "--target", "2000", "--baseline", "874", capsys=capsys,
    )
    _run(
        db_path, "candidates", "add", "--campaign-id", "hielscher-sonicators-2026",
        "--email", "ventas@kalstein.cl", "--email", "good@x.cl", capsys=capsys,
    )
    code, out = _run(
        db_path, "select", "--campaign-id", "hielscher-sonicators-2026", "--n", "10",
        "--gmail-user", "contacto@origenlab.cl", capsys=capsys,
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["reserved"] == 1
    assert payload["blocked"] == 1
    batch_code, batch_out = _run(db_path, "batch", "show", "--campaign-id", "hielscher-sonicators-2026", capsys=capsys)
    batch = json.loads(batch_out)
    assert [r["email_norm"] for r in batch] == ["good@x.cl"]


def test_select_strict_noise_profile_blocks_reply_local_on_mail_graph(db_path: Path, capsys) -> None:
    """Direct proof that `select` now uses strict_contact_graph_noise=True (via
    gate_context_for_archive_batch) rather than the weaker lead-only profile: a
    'reply@' machine-style local part is noise only in strict mode."""
    _run(
        db_path, "init", "--campaign-id", "hielscher-sonicators-2026", "--name", "N",
        "--sender-email", "contacto@origenlab.cl", "--sender-name", "S",
        "--subject", "Subj", "--target", "2000", "--baseline", "874", capsys=capsys,
    )
    _run(
        db_path, "candidates", "add", "--campaign-id", "hielscher-sonicators-2026",
        "--email", "reply@some-legit-domain.cl", capsys=capsys,
    )
    code, out = _run(
        db_path, "select", "--campaign-id", "hielscher-sonicators-2026", "--n", "10",
        "--gmail-user", "contacto@origenlab.cl", capsys=capsys,
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["reserved"] == 0
    assert payload["blocked"] == 1


def test_reconcile_runs_against_empty_campaign(db_path: Path, capsys) -> None:
    _run(
        db_path, "init", "--campaign-id", "hielscher-sonicators-2026", "--name", "N",
        "--sender-email", "contacto@origenlab.cl", "--sender-name", "S",
        "--subject", "Subj", "--target", "2000", "--baseline", "874", capsys=capsys,
    )
    code, out = _run(db_path, "reconcile", "--campaign-id", "hielscher-sonicators-2026", "--gmail-user", "contacto@origenlab.cl", capsys=capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["checked"] == 0


def test_default_db_path_never_resolves_under_downloads(monkeypatch, tmp_path: Path) -> None:
    """Sanity check on the config default — resolved_sqlite_path must never live under Downloads."""
    from origenlab_email_pipeline.config import load_settings

    monkeypatch.delenv("ORIGENLAB_SQLITE_PATH", raising=False)
    monkeypatch.setenv("ORIGENLAB_DATA_ROOT", str(tmp_path / "data"))
    resolved = load_settings().resolved_sqlite_path()
    assert "Downloads" not in str(resolved)
